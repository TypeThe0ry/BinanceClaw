"""Telegram push settings: per-type mute, digest every N minutes, quiet hours; approvals always go out."""
import datetime as dt
import time

import pytest

from binanceclaw.config import Notify, Settings
from binanceclaw.notify import policy
from binanceclaw.notify.telegram import TelegramBot

from .conftest import use
from .test_allocation import PROPOSAL
from .test_api import client  # noqa: F401  (fixture)


def _bot(svc):
    bot = TelegramBot.__new__(TelegramBot)
    bot.svc, bot._confirms, sent = svc, {}, []
    bot.send = lambda text, buttons=None: sent.append((text, buttons)) or len(sent)
    bot.photo = lambda png, caption="", buttons=None: bot.send(caption, buttons)
    bot.chart_png = lambda *a, **k: None
    bot.futures_chart = lambda *a, **k: None
    return bot, sent


def _at(h, m=0):
    return dt.datetime(2026, 10, 18, h, m)


def test_defaults_and_validation():
    n = Settings().notify
    assert n.mode == "instant" and not n.quiet.enabled
    assert n.types["trade"] and n.types["report"] and not n.types["earn"]     # earn was never pushed before
    s = Settings.model_validate({"notify": {"types": {"approval": False, "critical": False, "trade": False, "bogus": True},
                                            "digest_minutes": 1, "quiet": {"start": "7:5", "end": "23:00"}}})
    assert s.notify.types["approval"] and s.notify.types["critical"] and s.notify.types["question"]
    assert not s.notify.types["trade"] and "bogus" not in s.notify.types
    assert s.notify.digest_minutes == 5 and s.notify.quiet.start == "07:05"
    with pytest.raises(ValueError):
        Settings.model_validate({"notify": {"quiet": {"start": "25:00"}}})


def test_classify():
    c = policy.classify
    assert c("approval_created", {}) == c("approval", {}) == c("allocation", {}) == "approval"
    assert c("order", {"via": "auto", "text": "x"}) == "trade"
    assert c("order", {"via": "auto", "text": "x", "source": "rebalance"}) == "rebalance"
    assert c("order", {"status": "failed"}) == "alerts"
    assert c("futures", {"op": "STOP_HIT", "ok": True}) == "critical"
    assert c("futures", {"op": "OPEN_LONG", "ok": True}) == "trade"
    assert c("futures", {"op": "OPEN_LONG", "ok": False}) == "alerts"
    assert c("alert", {"text": "x"}) == "alerts" and c("alert", {"level": "critical"}) == "critical"
    assert c("paused", {}) == "critical" and c("question", {}) == "question"
    assert c("something_new", {}) is None


def test_route_mute_digest_quiet():
    n = Notify(types={"trade": False})
    assert policy.route(n, "trade", _at(12)) == "drop"
    assert policy.route(n, "report", _at(12)) == "send"
    assert policy.route(n, None, _at(12)) == "send"            # unknown kinds are never silenced
    n = Notify(mode="digest")
    assert policy.route(n, "report", _at(12)) == "hold"
    n = Notify(quiet={"enabled": True, "start": "23:00", "end": "08:00"})
    assert policy.route(n, "report", _at(23, 30)) == "hold"
    assert policy.route(n, "report", _at(3)) == "hold"
    assert policy.route(n, "report", _at(8)) == "send"
    for always in ("approval", "question", "critical"):
        assert policy.route(Notify(mode="digest", quiet={"enabled": True, "start": "00:00", "end": "23:59"}),
                            always, _at(12)) == "send"


def test_digest_due():
    now = _at(12)
    n = Notify(mode="digest", digest_minutes=30)
    assert not policy.digest_due(n, now, None)
    assert not policy.digest_due(n, now, now.timestamp() - 29 * 60)
    assert policy.digest_due(n, now, now.timestamp() - 30 * 60)
    q = Notify(quiet={"enabled": True, "start": "22:00", "end": "08:00"})
    assert not policy.digest_due(q, _at(7, 59), 0)              # still quiet
    assert policy.digest_due(q, _at(8), 0)                       # quiet ended: send what was held


def test_dispatch_mutes_holds_and_flushes(svc):
    bot, sent = _bot(svc)
    svc.update_settings({"notify": {"types": {"alerts": False}}})
    bot.dispatch("alert", {"text": "模型调用失败"})
    assert sent == []
    bot.dispatch("alert", {"text": "下单结果未知", "level": "critical"})
    assert "下单结果未知" in sent[-1][0]

    svc.update_settings({"notify": {"mode": "digest", "digest_minutes": 60}})
    bot.dispatch("order", {"via": "auto", "text": "买入 0.01 BTC", "source": "rebalance"})
    bot.dispatch("price_alert", {"symbol": "BTCUSDT", "op": "below", "price": 76000, "now": 75900, "note": ""})
    bot.dispatch("earn", {"text": "USDT 100 已申购活期理财"})          # earn is off by default
    assert len(sent) == 1 and len(svc.db.kv_get("tg_digest")) == 2
    bot.flush_digest()                                          # not due yet
    assert len(sent) == 1
    held = svc.db.kv_get("tg_digest")
    held[0][0] -= 3600
    svc.db.kv_set("tg_digest", held)
    bot.flush_digest()
    text = sent[-1][0]
    assert "消息汇总（2 条" in text and "⚖️ 再平衡 买入 0.01 BTC" in text and "跌破 76,000.00" in text
    assert svc.db.kv_get("tg_digest") == []


def test_approvals_always_pushed_with_buttons(svc):
    bot, sent = _bot(svc)
    svc.update_settings({"notify": {"mode": "digest", "quiet": {"enabled": True, "start": "00:00", "end": "23:59"},
                                    "types": {k: False for k in Notify().types}}})
    a = svc.create_approval("", "BUY", "BTCUSDT", 500, "回调到位", "跌破 70000")
    bot.dispatch("approval_created", {"id": a.id})
    text, buttons = sent[-1]
    assert "待批准" in text and buttons == [[("✅ 批准", f"ap:{a.id}"), ("❌ 拒绝", f"rj:{a.id}")]]
    assert svc.db.get(type(a), a.id).tg_message_id == len(sent)


def test_failed_approval_push_is_retried(svc):
    bot, sent = _bot(svc)
    bot.send = lambda text, buttons=None: None                  # Telegram unreachable
    a = svc.create_approval("", "BUY", "ETHUSDT", 300, "r", "i")
    bot.dispatch("approval_created", {"id": a.id})
    bot.send = lambda text, buttons=None: sent.append((text, buttons)) or 7
    bot.resend_missing_approvals()
    assert sent == []                                           # too soon: the first push may still be on its way
    svc.db.update(type(a), a.id, created=time.time() - 120)
    bot.resend_missing_approvals()
    assert sent[-1][1][0][0] == ("✅ 批准", f"ap:{a.id}")
    bot.resend_missing_approvals()
    assert len(sent) == 1                                       # delivered once


def test_web_allocation_proposal_reaches_telegram(svc):
    bot, sent = _bot(svc)
    use(svc, PROPOSAL)
    prop = svc.propose_allocation("加 10% SOL", "web")
    bot.dispatch("allocation", {"id": prop["id"], "status": "pending", "source": "web"})
    assert "目标配置提案" in sent[-1][0] and sent[-1][1][0][0] == ("✅ 应用", f"alloc_apply:{prop['id']}")
    bot.dispatch("allocation", {"id": prop["id"], "status": "pending", "source": "telegram"})
    assert len(sent) == 1                                       # /alloc already showed it


def test_notify_command(svc):
    bot, sent = _bot(svc)
    bot.handle_text("/notify")
    assert "推送频率：即时推送" in sent[-1][0] and "审批请求：始终推送" in sent[-1][0]
    bot.handle_text("/notify digest 30")
    n = svc.settings.notify
    assert n.mode == "digest" and n.digest_minutes == 30
    bot.handle_text("/notify quiet 23:00-07:30")
    assert svc.settings.notify.quiet.model_dump() == {"enabled": True, "start": "23:00", "end": "07:30"}
    bot.handle_text("/notify off 决策,earn")
    assert not svc.settings.notify.types["decision"]
    bot.handle_text("/notify on 理财")
    assert svc.settings.notify.types["earn"]
    bot.handle_text("/notify off approval")
    assert "不能关闭" in sent[-1][0] and svc.settings.notify.types["approval"]
    bot.handle_text("/notify quiet off")
    bot.handle_text("/notify instant")
    assert svc.settings.notify.mode == "instant" and not svc.settings.notify.quiet.enabled


def test_api_round_trip(client):  # noqa: F811
    client.post("/api/auth/login", json={"password": "correct-horse-1"})
    body = client.get("/api/settings").json()
    assert body["notify_types"]["approval"] == {"label": "审批请求", "always": True}
    assert body["notify_types"]["earn"]["always"] is False
    r = client.put("/api/settings", json={"notify": {"mode": "digest", "types": {"trade": False}}})
    assert r.status_code == 200 and r.json()["rerun"] == []
    assert r.json()["notify"]["mode"] == "digest" and r.json()["notify"]["types"]["trade"] is False
