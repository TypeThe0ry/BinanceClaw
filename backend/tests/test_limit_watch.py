"""挂单盯盘: the AI re-checks approved limit plans; it may cancel them or move them to a safer price on its
own, a price that fills sooner needs the owner, and the amount never changes."""
import json
import time

from sqlmodel import select

from binanceclaw.db import Approval, LimitOrder, Order

from .conftest import use


def limit(svc, side="BUY", symbol="ETH", usd=60, price=2400):
    plan = {"side": side, "symbol": symbol, "usd": usd, "type": "limit", "price": price, "hours": 48,
            "reason": "回调到支撑位", "invalidation": "跌破 2200 或 ETF 连续净流出"}
    use(svc, "方案：" + json.dumps(plan, ensure_ascii=False))
    svc.chat("挂单")
    a = svc.db.pending_approvals()[0]
    assert svc.resolve_approval(a.id, True, "web")[0]
    return svc.open_limit_orders()[-1]


def verdict(lo, action, **kw):
    return json.dumps({"orders": [{"id": lo.id, "action": action, "reason": "测试理由", **kw}]}, ensure_ascii=False)


def test_not_due_until_the_interval_or_a_sharp_move(svc):
    lo = limit(svc)
    assert lo.review_price == 2500
    assert svc.limit_watch_due() == []
    svc.client.prices_map["ETHUSDT"] = 2400.0 * 1.01               # 4% below the price at placing
    assert [x.id for x in svc.limit_watch_due()] == [lo.id]
    svc.client.prices_map["ETHUSDT"] = 2500.0
    assert [x.id for x in svc.limit_watch_due(now=time.time() + 31 * 60)] == [lo.id]
    svc.update_settings({"limit_watch": {"enabled": False}})
    assert svc.limit_watch_due(now=time.time() + 31 * 60) == []


def test_ai_sees_the_plan_reason_and_invalidation(svc):
    lo = limit(svc)
    p = use(svc, verdict(lo, "keep"))
    assert svc.watch_limit_orders(force=True)[0]["result"] == "keep"
    prompt = p.calls[0][1][0]["content"]
    assert "回调到支撑位" in prompt and "跌破 2200" in prompt and lo.id in prompt
    lo = svc.db.get(LimitOrder, lo.id)
    assert lo.status == "open" and lo.reviewed_at and lo.review.startswith("继续挂")


def test_ai_cancels_without_approval_by_default(svc):
    lo = limit(svc)
    use(svc, verdict(lo, "cancel"))
    assert svc.watch_limit_orders(force=True)[0]["result"] == "cancelled"
    lo = svc.db.get(LimitOrder, lo.id)
    assert lo.status == "cancelled" and "AI 撤单" in lo.result and lo.history[-1]["action"] == "cancel"
    svc.client.prices_map["ETHUSDT"] = 2000.0
    assert svc.check_limit_orders() == 0 and not svc.db.all(select(Order))


def test_ai_lowers_a_buy_on_its_own_same_amount(svc):
    lo = limit(svc)
    use(svc, verdict(lo, "reprice", price=2300))
    assert svc.watch_limit_orders(force=True)[0]["result"] == "repriced"
    lo = svc.db.get(LimitOrder, lo.id)
    assert (lo.price, lo.origin_price, lo.quote_usd) == (2300, 2400, 60)
    assert not [a for a in svc.db.pending_approvals() if a.kind == "limit"]
    svc.client.prices_map["ETHUSDT"] = 2350.0                       # old level, no longer fills
    assert svc.check_limit_orders() == 0
    svc.client.prices_map["ETHUSDT"] = 2290.0
    assert svc.check_limit_orders() == 1


def test_raising_a_buy_needs_approval(svc):
    lo = limit(svc)
    use(svc, verdict(lo, "reprice", price=2450))
    assert svc.watch_limit_orders(force=True)[0]["result"] == "approval"
    assert svc.db.get(LimitOrder, lo.id).price == 2400              # unchanged until approved
    a = [a for a in svc.db.pending_approvals() if a.kind == "limit"][0]
    assert a.payload["op"] == "reprice" and a.payload["price"] == 2450 and a.quote_usd == 60
    ok, msg = svc.resolve_approval(a.id, True, "web")
    assert ok and "2400 → 2450" in msg
    assert svc.db.get(LimitOrder, lo.id).price == 2450


def test_sell_direction_is_mirrored(svc):
    lo = svc.db.add(LimitOrder(expires=time.time() + 3600, symbol="BTCUSDT", side="SELL", quote_usd=50, price=85000))
    use(svc, verdict(lo, "reprice", price=86000), verdict(lo, "reprice", price=82000))
    assert svc.watch_limit_orders(force=True)[0]["result"] == "repriced"
    assert svc.watch_limit_orders(force=True)[0]["result"] == "approval"
    assert svc.db.get(LimitOrder, lo.id).price == 86000


def test_prices_crossing_the_market_are_refused(svc):
    lo = limit(svc)
    use(svc, verdict(lo, "reprice", price=2600))
    assert svc.watch_limit_orders(force=True)[0]["result"].startswith("rejected")
    assert svc.db.get(LimitOrder, lo.id).price == 2400 and not [a for a in svc.db.pending_approvals() if a.kind == "limit"]


def test_approval_switch_routes_every_change_to_the_owner(svc):
    svc.update_settings({"limit_watch": {"require_approval": True}})
    lo = limit(svc)
    use(svc, verdict(lo, "cancel"))
    assert svc.watch_limit_orders(force=True)[0]["result"] == "approval"
    assert svc.db.get(LimitOrder, lo.id).status == "open"
    a = [a for a in svc.db.pending_approvals() if a.kind == "limit"][0]
    assert svc.resolve_approval(a.id, True, "web")[0]
    assert svc.db.get(LimitOrder, lo.id).status == "cancelled"


def test_change_requests_lapse_when_the_plan_ends(svc):
    lo = limit(svc)
    use(svc, verdict(lo, "reprice", price=2450))
    svc.watch_limit_orders(force=True)
    a = [a for a in svc.db.pending_approvals() if a.kind == "limit"][0]
    svc.cancel_limit_order(lo.id, "web")
    assert svc.db.get(Approval, a.id).status == "expired"


def test_model_failure_keeps_the_plan_and_waits(svc):
    lo = limit(svc)
    use(svc, "不是 JSON")
    assert svc.watch_limit_orders(force=True) == []
    lo = svc.db.get(LimitOrder, lo.id)
    assert lo.status == "open" and lo.price == 2400 and lo.reviewed_at
