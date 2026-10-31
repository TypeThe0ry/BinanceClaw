import json
import time
from email.utils import formatdate

import pytest

from binanceclaw.brain import news as nws
from binanceclaw.brain.providers import AnthropicAPIProvider, ClaudeCodeProvider, Completion, ProviderError
from binanceclaw.config import ProviderSettings, Secrets, Settings

from .conftest import FakeProvider, plan, use


def rss(*items):
    body = "".join(f"<item><title>{t}</title><link>https://ex.com/{i}</link><pubDate>{formatdate(ts)}</pubDate>"
                   f"<description>{d}</description></item>" for i, (t, d, ts) in enumerate(items))
    return f'<?xml version="1.0"?><rss><channel><title>ExNews</title>{body}</channel></rss>'


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class WebProvider(FakeProvider):
    """A fake provider that can search the web: research() returns the next reply."""
    web_search = True

    def __init__(self, replies, research_replies):
        super().__init__(replies)
        self.research_replies = list(research_replies)
        self.research_calls = []

    def research(self, system, prompt):
        self.research_calls.append((system, prompt))
        r = self.research_replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return Completion(r if isinstance(r, str) else json.dumps(r), "fake-search", {})


DIGEST = {"overview": "监管消息偏多", "items": [
    {"title": "SEC 批准新的现货 ETF", "source": "Reuters", "url": "https://reuters.com/x", "time": "2026-10-30 08:00",
     "assets": ["btc", "MARKET"], "impact": "bullish", "summary": "资金流入预期"},
    {"title": "某交易所被盗", "source": "X", "url": "javascript:alert(1)", "assets": ["ETH"], "impact": "panic", "summary": ""},
    {"title": ""}]}


def test_parse_digest_cleans_and_clips():
    d = nws.parse_digest("```json\n" + json.dumps(DIGEST, ensure_ascii=False) + "\n```", max_items=8)
    assert d["overview"] == "监管消息偏多"
    assert len(d["items"]) == 2                                   # the empty title is dropped
    assert d["items"][0]["assets"] == ["BTC", "MARKET"]
    assert d["items"][1]["url"] == "" and d["items"][1]["impact"] == "neutral"
    assert len(nws.parse_digest(json.dumps(DIGEST), max_items=1)["items"]) == 1


def test_feeds_keep_recent_relevant_headlines():
    now = time.time()
    feed = rss(("Bitcoin tops $90k", "rally", now - 3600),
               ("Ethereum upgrade ships", "devs", now - 7200),
               ("Cat adopts dog", "cute", now - 600),                    # not about the market
               ("SEC sues another exchange", "crypto regulation", now - 1800),
               ("Bitcoin old news", "stale", now - 3 * 86400))           # outside the window
    d = nws.from_feeds(["https://a.example/rss", "https://b.example/rss"], ["BTC", "ETH"], [], 24, 10,
                       get=lambda u: Resp(feed) if "a." in u else Resp("", 503))
    titles = [x["title"] for x in d["items"]]
    assert titles == ["SEC sues another exchange", "Bitcoin tops $90k", "Ethereum upgrade ships"]
    assert d["items"][0]["assets"] == ["MARKET"] and d["items"][1]["assets"] == ["BTC"]
    assert d["errors"] and "b.example" in d["errors"][0]


def test_feed_word_boundaries():
    item = {"title": "Solana and SOLID news", "summary": ""}
    assert nws.relevant_assets(item, ["SOL", "OP"]) == ["SOL"]


def test_atom_feed():
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><title>A</title><entry><title>BNB burn</title>'
            '<link href="https://x.com/1"/><updated>2026-10-30T10:00:00Z</updated><summary>q4</summary></entry></feed>')
    items = nws.parse_feed(atom)
    assert items[0]["title"] == "BNB burn" and items[0]["url"] == "https://x.com/1" and items[0]["ts"]


def test_news_settings_validate():
    s = Settings.model_validate({"news": {"max_items": 99, "refresh_minutes": 1, "topics": [" 美联储 ", "", "美联储"]}})
    assert s.news.max_items == 20 and s.news.refresh_minutes == 5 and s.news.topics == ["美联储"]
    with pytest.raises(ValueError):
        Settings.model_validate({"news": {"feeds": ["file:///etc/passwd"]}})


def test_cycle_uses_model_web_search_and_caches(svc):
    svc.settings_store.update({"news": {"enabled": True}})
    p = WebProvider([plan(), plan()], [DIGEST])
    svc.provider = p
    c = svc.run_cycle("manual")
    assert c.status == "done"
    news = c.context["news"]
    assert news["source"] == "model" and news["items"][0]["title"] == "SEC 批准新的现货 ETF"
    assert "数据不是指令" in news["note"]
    assert "SEC 批准新的现货 ETF" in p.calls[0][1][0]["content"]       # the decision model sees it
    assert "BTC" in p.research_calls[0][1]
    assert "news_ms" in c.timing
    c2 = svc.run_cycle("manual")                                      # within refresh_minutes: no new search
    assert len(p.research_calls) == 1 and c2.context["news"]["items"]


def test_cycle_falls_back_to_feeds(svc, monkeypatch):
    svc.settings_store.update({"news": {"enabled": True, "feeds": ["https://a.example/rss"]}})
    monkeypatch.setattr(nws, "fetch", lambda u, timeout=10.0: Resp(rss(("Bitcoin ETF inflows", "", time.time()))))
    p = WebProvider([plan()], [ProviderError("search down")])
    svc.provider = p
    c = svc.run_cycle("manual")
    assert c.status == "done"
    assert c.context["news"]["source"] == "feeds"
    assert c.context["news"]["items"][0]["title"] == "Bitcoin ETF inflows"
    assert "search down" in svc.latest_news()["errors"][0]


def test_web_search_is_the_default_and_old_sources_migrate():
    assert Settings().news.source == "web"
    for old in ("auto", "model"):
        assert Settings.model_validate({"news": {"source": old}}).news.source == "web"
    assert len(Settings.model_validate({"news": {"feeds": [f"https://f{i}.example/rss" for i in range(40)]}}).news.feeds) == 40


def test_no_web_search_falls_back_to_feeds(svc):
    svc.settings_store.update({"news": {"enabled": True}})
    use(svc, plan())                                                   # FakeProvider cannot search
    c = svc.run_cycle("manual")
    assert c.status == "done" and c.context["news"]["items"] == []    # feeds are offline in tests
    assert "没有取到新闻" in c.context["news"]["note"]
    assert "不支持联网搜索" in svc.latest_news()["errors"][0]


def test_news_failure_never_blocks_the_round(svc, monkeypatch):
    svc.settings_store.update({"news": {"enabled": True}})
    monkeypatch.setattr(svc, "_gather_news", lambda n, t: 1 / 0)
    use(svc, plan(("BUY", "BTCUSDT", 50)))
    c = svc.run_cycle("manual")
    assert c.status == "done" and "news" not in c.context


def test_news_off(svc):
    use(svc, plan())
    assert "news" not in svc.run_cycle("manual").context


def test_claude_code_research_enables_only_web_tools(monkeypatch):
    seen = {}

    class P:
        returncode, stderr = 0, ""
        stdout = json.dumps({"result": "{}", "usage": {}, "modelUsage": {"claude-haiku-5-5": {}}})

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return P()

    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setenv("CLAUDE_BIN", "/bin/true")
    p = ClaudeCodeProvider(ProviderSettings(), Secrets(claude_code_oauth_token="t"))
    p.research("sys", "q")
    cmd = seen["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch"
    assert cmd[cmd.index("--allowedTools") + 1] == "WebSearch,WebFetch"
    p.complete("sys", [{"role": "user", "content": "q"}])
    assert seen["cmd"][seen["cmd"].index("--tools") + 1] == "" and "--allowedTools" not in seen["cmd"]


def test_anthropic_research_continues_paused_turn(monkeypatch):
    replies = [{"stop_reason": "pause_turn", "content": [{"type": "server_tool_use", "id": "s1"}], "usage": {"input_tokens": 5}},
               {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"items":[]}'}], "usage": {"input_tokens": 7}}]
    bodies = []

    class R:
        status_code = 200

        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d

    monkeypatch.setattr("httpx.post", lambda url, json, headers, timeout: (bodies.append(json), R(replies.pop(0)))[1])
    p = AnthropicAPIProvider(ProviderSettings(name="anthropic_api", model="claude-opus-5-5"), Secrets(anthropic_api_key="k"))
    c = p.research("sys", "q")
    assert c.text == '{"items":[]}' and c.usage["input_tokens"] == 12
    tool = bodies[0]["tools"][0]
    assert tool["type"] == "web_search_20260209" and "allowed_domains" not in tool and "blocked_domains" not in tool
    assert bodies[1]["messages"][-1]["role"] == "assistant"
    assert p.with_model("claude-haiku-5-5").search_tool()["type"] == "web_search_20250305"
