"""AI 全自主: nothing waits for the owner, every hard limit still applies, and new installs start there."""
import time

import pytest

from binanceclaw.config import ConfirmationRequired, Settings, SettingsStore, fresh_settings
from binanceclaw.db import AiQuestion, LimitOrder

from .conftest import plan, use
from .test_chat_plans import orders, say
from .test_futures import long_btc, on, run
from .test_limit_watch import limit, verdict
from .test_questions import asking


def full(svc):
    svc.update_settings({"autonomy": {"full": True}})


def test_fresh_install_starts_autonomous_but_saved_settings_do_not(tmp_path):
    s = fresh_settings()
    assert s.autonomy.full and s.allocation.mode == "ai"
    assert not Settings().autonomy.full                  # a saved settings.json without the key stays as it was
    old = tmp_path / "old.json"
    old.write_text(Settings().model_dump_json())
    assert not SettingsStore(old).get().autonomy.full


def test_switching_on_is_dangerous(svc):
    with pytest.raises(ConfirmationRequired) as e:
        svc.update_settings({"autonomy": {"full": True}}, guard=True)
    assert [c["field"] for c in e.value.changes] == ["autonomy.full"]


def test_large_buys_and_sells_execute_inside_the_hard_limits(svc):
    assert not svc.settings.limits.allow_sell
    full(svc)
    use(svc, plan(("BUY", "BTCUSDT", 5000)), plan(("SELL", "BTCUSDT", 300)))
    c = svc.run_cycle("manual")
    v = c.risk[0]
    assert v["decision"] == "execute" and "AI 全自主" in v["reason"]
    assert v["approved_usd"] == svc.settings.limits.max_order_usd                # still capped
    assert c.context["approval"]["autonomous"] is True
    assert svc.run_cycle("manual").risk[0]["decision"] == "execute"              # sells no longer blocked
    assert not svc.db.pending_approvals()
    svc.update_settings({"limits": {"max_orders_per_day": 1}})
    use(svc, plan(("BUY", "ETHUSDT", 100)))
    assert svc.run_cycle("manual").risk[0]["decision"] == "reject"


def test_pause_still_stops_everything(svc):
    full(svc)
    svc.pause("测试")
    use(svc, plan(("BUY", "BTCUSDT", 100)))
    c = svc.run_cycle("manual")
    assert c.status == "skipped" and not orders(svc)


def test_chat_plans_execute_without_a_card(svc):
    full(svc)
    use(svc, say(side="BUY", symbol="BTC", usd=500, type="market"))
    reply = svc.chat("买 500 刀 BTC")
    assert "✅ 方案：买入 BTC $500.00，市价（已执行" in reply
    assert [(o.side, o.symbol) for o in orders(svc)] == [("BUY", "BTCUSDT")]
    assert not svc.db.pending_approvals()


def test_chat_limit_plan_starts_watching_at_once(svc):
    full(svc)
    use(svc, say(side="BUY", symbol="ETH", usd=60, type="limit", price=2400))
    svc.chat("2400 挂单买 ETH")
    assert [lo.price for lo in svc.open_limit_orders()] == [2400]


def test_limit_watch_reprices_either_way(svc):
    lo = limit(svc)
    full(svc)
    use(svc, verdict(lo, "reprice", price=2450))
    assert svc.watch_limit_orders(force=True)[0]["result"] == "repriced"
    assert svc.db.get(LimitOrder, lo.id).price == 2450


def test_questions_fall_back_to_the_recommendation_quickly(svc):
    full(svc)
    use(svc, asking(recommended=1), plan())
    c = svc.run_cycle("manual")
    q = svc.db.pending_questions()[0]
    assert q.expires - time.time() <= 30 * 60 + 5
    assert c.context["questions"]["on_timeout"] == "auto"
    svc.db.update(AiQuestion, q.id, expires=time.time() - 1)
    svc.expire_questions()
    assert svc.db.get(AiQuestion, q.id).answer == 1


def test_futures_opens_execute_once_futures_are_enabled(svc):
    on(svc)
    full(svc)
    c = run(svc, long_btc())
    assert c.risk[0]["decision"] == "execute", c.risk[0]
