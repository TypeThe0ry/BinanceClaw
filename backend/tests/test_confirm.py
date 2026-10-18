"""Dangerous settings need a second confirmation, enforced on the server."""
import time

import pytest

from binanceclaw.config import ConfirmationRequired
from binanceclaw.notify.telegram import TelegramBot

from .test_api import client  # noqa: F401  (fixture)


def test_dangerous_change_needs_token_for_exactly_that_change(svc):
    with pytest.raises(ConfirmationRequired) as e:
        svc.update_settings({"limits": {"max_order_usd": 1500}}, guard=True)
    assert e.value.changes == [{"field": "limits.max_order_usd", "label": "单笔上限", "old": 800.0, "new": 1500.0}]
    assert svc.settings.limits.max_order_usd == 800                   # nothing written

    tok = e.value.token
    with pytest.raises(ConfirmationRequired):                         # a token is not reusable for another value
        svc.update_settings({"limits": {"max_order_usd": 3000}}, guard=True, confirm=tok)
    svc.update_settings({"limits": {"max_order_usd": 1500}}, guard=True, confirm=tok)
    assert svc.settings.limits.max_order_usd == 1500


def test_token_expires_and_is_bound_to_old_values(svc):
    changes = [{"field": "earn.enabled", "label": "理财自动申购", "old": True, "new": False}]
    assert not svc._confirm_ok(svc._confirm_token(changes, ttl=-1), changes)
    tok = svc._confirm_token(changes)
    svc.update_settings({"earn": {"enabled": False}}, guard=True, confirm=tok)
    svc.update_settings({"earn": {"enabled": True}})                  # settings moved on since the token was issued
    with pytest.raises(ConfirmationRequired):
        svc.update_settings({"earn": {"enabled": False, "keep_liquid_usd": 1}}, guard=True, confirm=tok)


def test_safe_changes_and_no_ops_pass_without_confirmation(svc):
    svc.update_settings({"schedule": {"cycle_minutes": 30}, "provider": {"model": "x"}}, guard=True)
    svc.update_settings({"limits": {"max_order_usd": 800}}, guard=True)    # unchanged value
    assert svc.settings.schedule.cycle_minutes == 30


@pytest.mark.parametrize("patch", [
    {"earn": {"enabled": False}}, {"earn": {"auto_all": False}}, {"futures": {"max_leverage": 5}},
    {"capital": {"budget_usd": 100}}, {"capital": {"locked": {"BTC": 1}}}, {"allocation": {"mode": "ai"}},
    {"limits": {"allow_sell": True}}, {"live_gate_allow": ["enableFutures"]},
])
def test_each_dangerous_area_is_guarded(svc, patch):
    with pytest.raises(ConfirmationRequired):
        svc.update_settings(patch, guard=True)


def test_presets_are_guarded(svc):
    with pytest.raises(ConfirmationRequired) as e:
        svc.apply_preset("aggressive", guard=True)
    assert {c["field"] for c in e.value.changes} >= {"limits.max_order_usd"}
    svc.apply_preset("aggressive", guard=True, confirm=e.value.token)
    assert svc.settings.risk_preset == "aggressive"


def test_api_returns_409_then_accepts_the_token(client):  # noqa: F811
    client.post("/api/auth/login", json={"password": "correct-horse-1"})
    r = client.put("/api/settings", json={"limits": {"allow_sell": True}})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["changes"][0]["field"] == "limits.allow_sell" and d["confirm_token"]
    assert client.svc.settings.limits.allow_sell is False
    r = client.put("/api/settings", json={"limits": {"allow_sell": True}}, headers={"X-Confirm-Token": "1.bad"})
    assert r.status_code == 409
    r = client.put("/api/settings", json={"limits": {"allow_sell": True}}, headers={"X-Confirm-Token": d["confirm_token"]})
    assert r.status_code == 200 and client.svc.settings.limits.allow_sell is True
    # non-dangerous settings still save in one step
    assert client.put("/api/settings", json={"schedule": {"cycle_minutes": 90}}).status_code == 200
    r = client.post("/api/risk-preset", json={"name": "conservative"})
    assert r.status_code == 409
    r = client.post("/api/risk-preset", json={"name": "conservative"}, headers={"X-Confirm-Token": r.json()["detail"]["confirm_token"]})
    assert r.status_code == 200 and client.svc.settings.risk_preset == "conservative"


def test_telegram_holds_dangerous_change_until_confirmed(svc):
    bot = TelegramBot.__new__(TelegramBot)
    bot.svc, bot._confirms, sent = svc, {}, []
    bot.send = lambda text, buttons=None: sent.append((text, buttons))
    bot.handle_text("/budget 3000")
    assert svc.settings.capital.budget_usd is None
    text, buttons = sent[-1]
    assert "AI 投入预算：不限 → 3000" in text
    cid = buttons[0][0][1].split(":")[1]
    bot._resolve_confirm(cid, True)
    assert svc.settings.capital.budget_usd == 3000
    assert sent[-1][0].startswith("✅")
    bot._resolve_confirm(cid, True)                                     # one use only
    assert "失效" in sent[-1][0]

    bot.handle_text("/lock BTC 0.5")
    cid = sent[-1][1][0][0][1].split(":")[1]
    bot._resolve_confirm(cid, False)
    assert svc.settings.capital.locked == {}
    bot._confirms[cid] = (time.time() - 1, None, "", "")                # expired
    bot._resolve_confirm(cid, True)
    assert "失效" in sent[-1][0]
