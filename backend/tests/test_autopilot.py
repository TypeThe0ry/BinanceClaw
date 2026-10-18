import json
import time

import pytest

from binanceclaw.brain.schema import PlanError, parse_plan
from binanceclaw.exchange.stream import PriceStream

from .conftest import plan, use


def timed(*actions, next_check=None, wake=None):
    p = plan(*actions)
    if next_check is not None:
        p["next_check_minutes"] = next_check
    if wake is not None:
        p["wake_move_pct"] = wake
    return p


def tick(svc, symbol, price):
    svc.stream.on_message(json.dumps({"stream": f"{symbol.lower()}@miniTicker", "data": {"s": symbol, "c": str(price)}}))


def past_gap(svc):
    svc.db.kv_set("auto_last_start", time.time() - 3600)


# ---------------------------------------------------------------- plan + booking
def test_plan_timing_fields_are_optional_and_validated():
    assert parse_plan('{"summary":"s","confidence":0.5}').next_check_minutes is None
    p = parse_plan('{"summary":"s","confidence":0.5,"next_check_minutes":15,"wake_move_pct":0.01}')
    assert (p.next_check_minutes, p.wake_move_pct) == (15, 0.01)
    with pytest.raises(PlanError):
        parse_plan('{"summary":"s","confidence":0.5,"next_check_minutes":0}')


def test_ai_picks_next_check_clamped_to_owner_limits(svc):
    use(svc, timed(next_check=2), timed(next_check=1000), timed(next_check=45, wake=0.004), plan())
    for want in (5, 240, 45):                          # min gap 5, max gap 240
        svc.run_cycle("manual")
        assert svc.next_cycle_at() == pytest.approx(time.time() + want * 60, abs=5)
    assert svc.autopilot.wake_move() == 0.004
    svc.run_cycle("manual")                            # names nothing: default interval and threshold
    assert svc.next_cycle_at() == pytest.approx(time.time() + 60 * 60, abs=5)
    assert svc.autopilot.wake_move() == svc.settings.schedule.move_trigger_pct


def test_model_sees_why_it_was_woken_and_its_budget(svc):
    p = use(svc, plan())
    c = svc.run_cycle("price_move", "BTC 较上一轮 +3.00%")
    auto = c.context["autonomy"]
    assert auto["this_round"] == {"trigger": "价格波动", "why": "BTC 较上一轮 +3.00%"}
    assert auto["model_calls_left_today"] == 48
    assert "autonomy" in p.calls[0][1][0]["content"] and "next_check_minutes" in p.calls[0][0]
    assert svc.autopilot.calls_today() == 1


# ---------------------------------------------------------------- waking up
def test_price_move_wakes_the_ai_only_after_min_gap(svc):
    use(svc, timed(wake=0.01))
    tick(svc, "BTCUSDT", 80000)
    svc.run_cycle("manual")
    assert svc.autopilot.due() is None                 # nothing moved
    tick(svc, "BTCUSDT", 81200)                        # +1.5%, above the AI's own 1% threshold
    assert svc.autopilot.due()[0] == "price_move"      # a manual round doesn't hold off autonomous ones
    svc.autopilot.started("price_move")                # an autonomous round just started: inside the gap
    assert svc.autopilot.due() is None
    past_gap(svc)
    trig, why = svc.autopilot.due()
    assert trig == "price_move" and "BTC" in why and "+1.50%" in why


def test_price_level_futures_and_fills_wake_the_ai(svc):
    past_gap(svc)
    svc.db.kv_set("next_cycle_at", time.time() + 3600)
    svc.events.emit("price_alert", {"symbol": "BTCUSDT", "op": "below", "price": 76000, "now": 75900, "note": "失效位"})
    svc.events.emit("futures", {"symbol": "ETHUSDT", "op": "STOP_HIT", "text": "ETH 触发止损"})
    svc.events.emit("futures", {"symbol": "ETHUSDT", "op": "OPEN_LONG", "text": "opened"})   # its own action: no wake
    trig, why = svc.autopilot.due()
    assert trig == "price_level" and "跌破 76000" in why and "ETH 触发止损" in why and "opened" not in why
    svc.autopilot.started(trig)
    past_gap(svc)
    assert svc.autopilot.due() is None


def test_approved_trade_wakes_the_ai(svc):
    use(svc, plan(("BUY", "BTCUSDT", 800)))
    aid = svc.run_cycle("manual").risk[0]["approval_id"]
    assert svc.resolve_approval(aid, True, "web")[0]
    past_gap(svc)
    assert svc.autopilot.due()[0] == "fill"


def test_balance_change_wakes_the_ai(svc, monkeypatch):
    use(svc, plan())
    svc.run_cycle("manual")
    past_gap(svc)
    assert svc.autopilot.due() is None
    bal = svc.spot_balances()
    monkeypatch.setattr(svc, "spot_balances", lambda: {**bal, "USDT": bal["USDT"] + 2000})   # a deposit
    svc.autopilot._balance_checked = 0
    trig, why = svc.autopilot.due()
    assert trig == "balance" and "USDT +2000" in why
    svc.autopilot._balance_checked = 0
    assert svc.autopilot.due() is None                 # reported once


def test_scheduled_check_in_and_paused_account(svc):
    past_gap(svc)
    svc.db.kv_set("next_cycle_at", time.time() + 3600)
    svc.pause("test")
    svc.autopilot.nudge("price_level", "x")
    assert svc.autopilot.due() is None                 # events don't wake a paused account
    svc.db.kv_set("next_cycle_at", time.time() - 1)
    assert svc.autopilot.due()[0] == "ai_scheduled"
    c = svc.run_cycle("ai_scheduled")
    assert c.status == "skipped" and svc.autopilot.calls_today() == 0   # no model call, no budget spent


# ---------------------------------------------------------------- bounds
def test_daily_call_budget(svc):
    alerts = []
    svc.events.on(lambda k, d: alerts.append(d["text"]) if k == "alert" else None)
    svc.update_settings({"schedule": {"max_calls_per_day": 2}})
    use(svc, plan(), plan(), plan())
    svc.run_cycle("price_move")
    svc.run_cycle("ai_scheduled")
    svc.run_cycle("manual")                            # manual rounds are not counted
    assert svc.autopilot.calls_today() == 2
    past_gap(svc)
    svc.db.kv_set("next_cycle_at", time.time() - 1)
    assert svc.autopilot.due() is None
    assert svc.autopilot.due() is None
    assert len([a for a in alerts if "额度已用完" in a]) == 1
    svc.db.kv_set("started_at", time.time() - 30 * 3600)
    svc.watchdog()
    assert not any("没有成功完成" in a for a in alerts)   # a spent budget is not a fault


def test_risk_engine_still_decides_event_rounds(svc):
    use(svc, plan(("BUY", "BTCUSDT", 800), ("BUY", "DOGEUSDT", 50)))
    c = svc.run_cycle("price_move", "BTC 急跌")
    assert [r["decision"] for r in c.risk] == ["approve", "reject"]
    assert svc.db.orders_for_day(svc.today()) == []


def test_fixed_mode_keeps_the_old_timer(svc):
    svc.update_settings({"schedule": {"decision_mode": "fixed"}})
    svc.autopilot.nudge("price_level", "ignored")
    assert svc.db.kv_get("auto_pending") is None
    svc.db.kv_set("next_cycle_at", time.time() + 60)
    assert svc.autopilot.due() is None
    svc.db.kv_set("next_cycle_at", time.time() - 1)
    assert svc.autopilot.due() == ("schedule", "")
    use(svc, timed(next_check=5))
    c = svc.run_cycle("schedule")
    assert "autonomy" not in c.context
    assert svc.next_cycle_at() < time.time()           # the AI's timing is ignored; the loop books the interval


def test_settings_bounds(svc):
    svc.update_settings({"schedule": {"min_gap_minutes": 0, "max_gap_minutes": 3, "max_calls_per_day": 10_000,
                                      "move_trigger_pct": 0.9}})
    sch = svc.settings.schedule
    assert (sch.min_gap_minutes, sch.max_gap_minutes, sch.max_calls_per_day, sch.move_trigger_pct) == (1, 15, 500, 0.2)
    svc.update_settings({"schedule": {"min_gap_minutes": 60, "max_gap_minutes": 30}})
    assert svc.settings.schedule.max_gap_minutes == 60


def test_overview_shows_autopilot(svc):
    use(svc, timed(next_check=30))
    svc.run_cycle("price_move")
    ap = svc.overview()["autopilot"]
    assert ap["decision_mode"] == "auto" and ap["calls_today"] == 1 and ap["next_check_minutes"] == 30
    assert ap["ai_asked_minutes"] == 30


# ---------------------------------------------------------------- stream
def test_price_stream_parses_and_expires():
    s = PriceStream(stale_after=10)
    s.on_message('{"stream":"btcusdt@miniTicker","data":{"e":"24hrMiniTicker","s":"BTCUSDT","c":"80123.5"}}')
    s.on_message("not json")
    s.on_message('{"data":{"s":"ETHUSDT"}}')
    assert s.prices(["BTCUSDT", "ETHUSDT"]) == {"BTCUSDT": 80123.5}
    s._px["BTCUSDT"] = (80123.5, time.time() - 11)
    assert s.prices(["BTCUSDT"]) == {}


def test_live_prices_prefer_stream_then_rest(svc):
    tick(svc, "BTCUSDT", 81000)
    px = svc.live_prices(["BTC", "ETH"])
    assert px == {"BTC": 81000, "ETH": 2500.0}
