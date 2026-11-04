"""Chat answers search the web when the model can, and every fact cites a source with a credibility %."""
import json

from sqlmodel import select

from binanceclaw.brain.providers import Completion, ProviderError
from binanceclaw.db import ChatMessage

from .conftest import FakeProvider, use


def src(**d) -> str:
    return "来源：" + json.dumps(d, ensure_ascii=False)


REPLY = "\n".join(["BTC 24 小时涨 3%[1]，ETF 资金流入[2]。", "",
                   src(n=2, name="Reuters", title="ETF 资金流入", url="https://reuters.com/x", time="2026-11-04", credibility=82),
                   src(n=1, name="币安行情", credibility=140),
                   src(n=3, name="X", url="javascript:alert(1)", credibility="高"),
                   "来源：{not json}"])


class WebChat(FakeProvider):
    web_search = True

    def __init__(self, replies, web_replies):
        super().__init__(replies)
        self.web_replies = list(web_replies)
        self.web_calls = []

    def search(self, system, messages):
        self.web_calls.append((system, messages))
        r = self.web_replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return Completion(r, "fake-web", {})


def last_reply(svc):
    return svc.db.all(select(ChatMessage).where(ChatMessage.role == "assistant"))[-1]


def test_chat_searches_the_web_and_keeps_sources(svc):
    p = WebChat([], [REPLY])
    svc.provider = p
    out = svc.chat("BTC 为什么涨")
    assert len(p.web_calls) == 1 and not p.calls
    assert "联网搜索" in p.web_calls[0][0] and "credibility" in p.web_calls[0][0]
    m = last_reply(svc)
    assert m.text == "BTC 24 小时涨 3%[1]，ETF 资金流入[2]。"            # source lines are taken out of the text
    assert m.sources == [{"n": 1, "name": "币安行情", "credibility": 100},
                         {"n": 2, "name": "Reuters", "title": "ETF 资金流入", "url": "https://reuters.com/x",
                          "time": "2026-11-04", "credibility": 82},
                         {"n": 3, "name": "X"}]                           # bad url and credibility dropped
    assert "📎 来源" in out and "[2] Reuters《ETF 资金流入》 2026-11-04 · 可信度 82%\nhttps://reuters.com/x" in out


def test_chat_without_web_search_still_cites(svc):
    p = use(svc, "现在没有相关新闻。\n" + src(n=1, name="AI 推断", credibility=40))
    svc.chat("有什么消息")
    system = p.calls[0][0]
    assert "不能联网" in system and "credibility" in system
    assert last_reply(svc).sources == [{"n": 1, "name": "AI 推断", "credibility": 40}]


def test_chat_web_off_in_settings(svc):
    svc.update_settings({"news": {**svc.settings.news.model_dump(), "chat": False}})
    p = WebChat(["好的。"], [])
    svc.provider = p
    svc.chat("你好")
    assert not p.web_calls and len(p.calls) == 1


def test_failed_web_search_falls_back_to_plain_answer(svc):
    p = WebChat(["没联网也能答。"], [ProviderError("search down")])
    svc.provider = p
    assert svc.chat("BTC 怎么样").startswith("没联网也能答。")
    assert len(p.web_calls) == 1 and "不能联网" in p.calls[0][0]


def test_sources_go_back_into_history(svc):
    p = WebChat([], [REPLY, "继续。"])
    svc.provider = p
    svc.chat("BTC 为什么涨")
    svc.chat("第二条可信吗")
    hist = p.web_calls[1][1]
    assert hist[1]["role"] == "assistant" and "可信度 82%" in hist[1]["content"]

