"""Telegram chat replies formatted like the web 对话 page, and the web chat mirrored to Telegram."""
from sqlmodel import select

from binanceclaw.db import Approval, ChatMessage
from binanceclaw.notify import tgformat
from binanceclaw.notify.telegram import TelegramBot

from .conftest import use


def test_markdown_becomes_telegram_html():
    md = ("## 结论\n**BTC** 现在 80000[1]，*偏多* `4h`\n\n- 第一点\n  - 细节\n1. 步骤\n\n"
          "> ⚠️ 风险：跌破 76000 判断就错了\n\n---\n[币安](https://www.binance.com) & <x>")
    h = tgformat.md_to_html(md)
    assert "<b>结论</b>" in h and "<b>BTC</b> 现在 80000[1]" in h and "<i>偏多</i>" in h and "<code>4h</code>" in h
    assert "• 第一点" in h and "    ◦ 细节" in h and "1. 步骤" in h
    assert "<blockquote>⚠️ 风险：跌破 76000 判断就错了</blockquote>" in h
    assert "币安 (https://www.binance.com)" in h and "&amp; &lt;x&gt;" in h      # links inert, like the web; HTML escaped
    assert "<a " not in h and "snake_case_name" in tgformat.md_to_html("snake_case_name")


def test_tables_fit_a_phone():
    small = tgformat.md_to_html("| 币 | 占比 |\n|---|---:|\n| BTC | 40% |\n| 以太坊 | 20% |")
    assert small.startswith("<pre>") and "以太坊  20%" in small
    wide = tgformat.md_to_html("| 币种 | 当前占比 | 目标占比 | 说明 |\n|---|---|---|---|\n| BTC | 40% | 35% | 超配一点，可以减一些到目标附近 |")
    assert "<pre>" not in wide and "<b>BTC</b>" in wide and "当前占比 40% · 目标占比 35%" in wide


def test_sources_keep_links_and_credibility_colours():
    h = tgformat.sources_html([{"n": 1, "name": "CoinDesk", "title": "ETF 流入", "url": "https://x.com/a?b=1&c=2", "credibility": 85},
                               {"n": 2, "name": "AI 推断", "credibility": 40}])
    assert '<a href="https://x.com/a?b=1&amp;c=2">CoinDesk</a>' in h and "《ETF 流入》" in h and "🟢 可信度 85%" in h
    assert "[2] AI 推断 · 🔴 可信度 40%" in h


def test_split_never_breaks_a_block():
    text = "\n".join([f"<pre>{'x' * 50}\n{'y' * 50}</pre>"] * 100)
    chunks = tgformat.split(text, 1000)
    assert len(chunks) > 1 and all(c.count("<pre>") == c.count("</pre>") for c in chunks)


def _bot(svc):
    bot = TelegramBot.__new__(TelegramBot)
    bot.svc, bot._confirms, bot.chat_id, bot.api = svc, {}, 1, "x"
    bot.http = type("H", (), {"post": lambda *a, **k: None})()
    sent = []
    bot.send = lambda text, buttons=None, html=False: sent.append((text, buttons, html)) or len(sent)
    bot.photo = lambda png, caption="", buttons=None: bot.send(caption, buttons)
    bot.chart_png = lambda *a, **k: None
    return bot, sent


def test_web_chat_shows_up_in_telegram(svc):
    bot, sent = _bot(svc)
    events = []
    svc.events.on(lambda k, d: events.append((k, d)))
    use(svc, '## 看法\n**偏多**\n来源：{"n":1,"name":"币安行情","credibility":98}')
    svc.chat("BTC 怎么看", "web")
    for k, d in events:
        bot.on_event(k, d)
    assert sent[0][0] == "🖥 <b>网页</b>\n<blockquote>BTC 怎么看</blockquote>" and sent[0][2]
    assert sent[1][0].startswith("<b>看法</b>\n<b>偏多</b>") and "🟢 可信度 98%" in sent[1][0] and sent[1][2]
    svc.chat_new("web")
    bot.on_event(*events[-1])
    assert sent[-1][0] == "🆕 网页上开始了新的对话"


def test_telegram_turn_is_not_echoed_and_plan_card_follows_reply(svc):
    bot, sent = _bot(svc)
    events = []
    svc.events.on(lambda k, d: events.append((k, d)))
    use(svc, '好的。\n方案：{"side":"BUY","symbol":"BTC","usd":50,"type":"market","reason":"回调","invalidation":"跌破"}')
    bot._chat("买 50 BTC")
    for k, d in events:
        bot.on_event(k, d)          # Telegram's own turn and the chat plan's approval_created add nothing more
    assert [s[0].split("\n")[0] for s in sent] == ["好的。", "🟡 待批准 [模拟盘]"]
    assert "🟢 买入 BTCUSDT" in sent[1][0] and sent[1][1][0][0][1].startswith("ap:")
    a = svc.db.all(select(Approval))[0]
    assert [(m.channel, m.role) for m in svc.db.all(select(ChatMessage))] == [("telegram", "user"), ("telegram", "assistant")]
    assert a.payload["source"] == "chat"
