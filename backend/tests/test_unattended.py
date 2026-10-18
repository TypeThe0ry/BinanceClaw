"""免审批 (approval-free windows), trading style and the owner's own market view."""
import datetime as dt
import time

import pytest

from binanceclaw.config import ApprovalFree, ConfirmationRequired
from binanceclaw.notify.telegram import TelegramBot

from .conftest import plan, use
from .test_api import client  # noqa: F401  (fixture)
from .test_futures import long_btc, on, run


def free(svc, **kw):
    svc.update_settings({"approval_free": {"mode": "always", **kw}})


def _bot(svc):
    bot = TelegramBot.__new__(TelegramBot)
    bot.svc, bot._confirms, sent = svc, {}, []
    bot.send = lambda text, buttons=None: sent.append((text, buttons))
    return bot, sent


# ---------------------------------------------------------------- approval-free
@pytest.mark.parametrize("start,end,at,want", [
    ("00:00", "08:00", "03:00", True), ("00:00", "08:00", "08:00", False),
    ("23:30", "07:30", "23:45", True), ("23:30", "07:30", "06:00", True), ("23:30", "07:30", "12:00", False),
])
def test_window_crosses_midnight(start, end, at, want):
    h, m = map(int, at.split(":"))
    assert ApprovalFree(mode="window", start=start, end=end).active(dt.datetime(2026, 10, 18, h, m)) is want


def test_bad_time_is_rejected(svc):
    with pytest.raises(ValueError):
        svc.update_settings({"approval_free": {"mode": "window", "start": "25:00"}})
    assert ApprovalFree(start="7:5").start == "07:05"


def test_large_buy_waits_for_approval_by_default_and_executes_when_free(svc):
    use(svc, plan(("BUY", "BTCUSDT", 500)))
    c = svc.run_cycle("manual")
    assert c.risk[0]["decision"] == "approve"
    free(svc)
    use(svc, plan(("BUY", "BTCUSDT", 500)))          # settings changes rebuild the provider
    c = svc.run_cycle("manual")
    assert c.risk[0]["decision"] == "execute" and "免审批" in c.risk[0]["reason"]
    assert c.risk[0]["result"].startswith("[模拟盘] 买入")
    assert c.context["approval"]["unattended"] is True


def test_hard_limits_still_apply_when_free(svc):
    free(svc)
    use(svc, plan(("BUY", "BTCUSDT", 5000)))
    c = svc.run_cycle("manual")
    assert c.risk[0]["decision"] == "execute"
    assert c.risk[0]["approved_usd"] == svc.settings.limits.max_order_usd       # capped, not waved through
    svc.update_settings({"limits": {"max_orders_per_day": 1}})
    use(svc, plan(("BUY", "ETHUSDT", 300)))
    assert svc.run_cycle("manual").risk[0]["decision"] == "reject"


def test_window_outside_hours_keeps_approval(svc):
    now = svc.now()
    start = (now + dt.timedelta(hours=2)).strftime("%H:%M")
    end = (now + dt.timedelta(hours=3)).strftime("%H:%M")
    svc.update_settings({"approval_free": {"mode": "window", "start": start, "end": end}})
    assert not svc.approval_free_active()
    use(svc, plan(("BUY", "BTCUSDT", 500)))
    c = svc.run_cycle("manual")
    assert c.risk[0]["decision"] == "approve" and "approval" not in c.context


def test_sells_and_futures_only_when_included(svc):
    svc.update_settings({"limits": {"allow_sell": True}})
    free(svc)
    use(svc, plan(("BUY", "BTCUSDT", 800)), plan(("SELL", "BTCUSDT", 300)))
    assert svc.run_cycle("manual").risk[0]["decision"] == "execute"
    assert svc.run_cycle("manual").risk[0]["decision"] == "approve"          # sells not included
    free(svc, include_sell=True)
    use(svc, plan(("SELL", "BTCUSDT", 300)))
    assert svc.run_cycle("manual").risk[0]["decision"] == "execute"

    on(svc)
    free(svc, include_sell=True)
    assert run(svc, long_btc()).risk[0]["decision"] == "approve"            # futures not included
    free(svc, include_futures=True)
    c = run(svc, long_btc())
    assert c.risk[0]["decision"] == "execute", c.risk[0]
    assert len(svc.fbroker.open_rows()) == 1


def test_enabling_is_dangerous_and_offers_a_rerun(svc):
    with pytest.raises(ConfirmationRequired) as e:
        svc.update_settings({"approval_free": {"mode": "window", "end": "07:00"}}, guard=True)
    assert {c["field"] for c in e.value.changes} == {"approval_free.mode", "approval_free.end"}
    before = svc.settings_store.get()
    svc.update_settings({"approval_free": {"mode": "window", "end": "07:00"}}, guard=True, confirm=e.value.token)
    assert svc.rerun_offer(before) == ["免审批"]


def test_telegram_sleep_command(svc):
    bot, sent = _bot(svc)
    bot.handle_text("/sleep 23:30-07:30")
    assert svc.settings.approval_free.mode == "off"
    cid = sent[-1][1][0][0][1].split(":")[1]
    bot._resolve_confirm(cid, True)
    af = svc.settings.approval_free
    assert (af.mode, af.start, af.end) == ("window", "23:30", "07:30")
    assert "每天 23:30–07:30" in sent[0][0] or any("23:30–07:30" in t for t, _ in sent)
    bot.handle_text("/sleep nonsense")
    assert "没有生效" in sent[-1][0]


# ---------------------------------------------------------------- style
def test_style_bounds_the_next_check(svc):
    svc.update_settings({"schedule": {"max_gap_minutes": 1440}, "strategy": {"style": "long"}})
    use(svc, plan(), {**plan(), "next_check_minutes": 10})
    for want in (480, 120):                       # long: default 8h, at least 2h
        c = svc.run_cycle("manual")
        assert svc.next_cycle_at() == pytest.approx(time.time() + want * 60, abs=5)
    assert c.context["strategy"]["style"] == "长期"
    svc.update_settings({"strategy": {"style": "short"}})
    use(svc, plan(), {**plan(), "next_check_minutes": 1000})
    for want in (30, 60):                         # short: default 30 min, at most 1h
        svc.run_cycle("manual")
        assert svc.next_cycle_at() == pytest.approx(time.time() + want * 60, abs=5)


def test_schedule_bounds_still_win_over_style(svc):
    svc.update_settings({"strategy": {"style": "long"}})              # max gap stays 240
    assert svc.autopilot.check_range() == (120, 240)


# ---------------------------------------------------------------- owner's view
def test_owner_view_reaches_the_prompt_only_when_set(svc):
    p = use(svc, plan())
    svc.run_cycle("manual")
    assert "owner_view" not in p.calls[0][1][0]["content"]
    svc.update_settings({"outlook": {"market": -5, "assets": {"eth": 2, "btc": 0}, "note": "觉得要回调"}})
    p = use(svc, plan())
    o = svc.settings.outlook
    assert (o.market, o.assets) == (-2, {"ETH": 2})                   # clamped, neutral entries dropped
    c = svc.run_cycle("manual")
    view = c.context["owner_view"]
    assert view["market"] == "强烈看空" and view["assets"] == {"ETH": "强烈看多"} and view["note"] == "觉得要回调"
    assert "owner_view" in p.calls[0][1][0]["content"]


def test_view_and_style_offer_a_rerun_without_confirmation(svc):
    before = svc.settings_store.get()
    svc.update_settings({"outlook": {"market": 1}, "strategy": {"style": "short"}}, guard=True)
    assert svc.rerun_offer(before) == ["交易风格", "个人观点"]


def test_telegram_view_and_style(svc):
    bot, sent = _bot(svc)
    bot.handle_text("/view +1")
    bot.handle_text("/view ETH -2")
    bot.handle_text("/view note 等回调")
    assert svc.settings.outlook.model_dump() == {"market": 1, "assets": {"ETH": -2}, "note": "等回调"}
    assert "ETH 强烈看空" in sent[-2][0]
    bot.handle_text("/style long")
    assert svc.settings.strategy.style == "long"
    bot.handle_text("/view clear")
    assert svc.settings.outlook.market == 0 and not svc.settings.outlook.assets


def test_api_overview_shows_state(client):  # noqa: F811
    client.post("/api/auth/login", json={"password": "correct-horse-1"})
    ov = client.get("/api/overview").json()
    assert ov["approval_free"]["active"] is False and ov["strategy"] == "medium"
