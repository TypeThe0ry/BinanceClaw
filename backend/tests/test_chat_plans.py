"""Chat trade plans: the chat model proposes, the owner approves, then BinanceClaw trades (market) or waits (limit)."""
import json

from sqlmodel import select

from binanceclaw.db import ChatMessage, LimitOrder, Order

from .conftest import use


def say(**plan) -> str:
    return "可以考虑。\n方案：" + json.dumps({"reason": "回调", "invalidation": "跌破支撑", **plan}, ensure_ascii=False)


def orders(svc):
    return svc.db.all(select(Order))


def test_market_plan_waits_for_approval_then_trades(svc):
    use(svc, say(side="BUY", symbol="BTC", usd=50, type="market"))
    reply = svc.chat("买 50 刀 BTC")
    assert "📝 方案：买入 BTC $50.00，市价（待批准）" in reply and "方案：{" not in reply
    a = svc.db.pending_approvals()[0]
    assert (a.side, a.symbol, a.quote_usd, a.payload["source"]) == ("BUY", "BTCUSDT", 50, "chat")
    msg = svc.db.all(select(ChatMessage).where(ChatMessage.role == "assistant"))[-1]
    assert msg.approvals == [a.id]
    assert not orders(svc)                                    # nothing trades before the owner says yes
    ok, text = svc.resolve_approval(a.id, True, "web")
    assert ok and "买入" in text
    assert [(o.side, o.symbol, o.via) for o in orders(svc)] == [("BUY", "BTCUSDT", "approved")]


def test_rejected_plan_never_trades(svc):
    use(svc, say(side="BUY", symbol="ETH", usd=40))
    svc.chat("买点 ETH")
    a = svc.db.pending_approvals()[0]
    assert svc.resolve_approval(a.id, False, "web") == (True, "已拒绝")
    assert not orders(svc)


def test_limit_plan_places_when_price_is_reached(svc):
    use(svc, say(side="BUY", symbol="ETH", usd=60, type="limit", price=2400, hours=12))
    reply = svc.chat("ETH 跌到 2400 买 60")
    assert "挂单 ≤ 2400，12 小时有效" in reply
    a = svc.db.pending_approvals()[0]
    ok, text = svc.resolve_approval(a.id, True, "web")
    assert ok and text.startswith("已挂单")
    lo = svc.open_limit_orders()[0]
    assert (lo.side, lo.symbol, lo.price, lo.quote_usd) == ("BUY", "ETHUSDT", 2400, 60)
    assert svc.check_limit_orders() == 0 and not orders(svc)          # 2500 now: not yet
    svc.client.prices_map["ETHUSDT"] = 2390.0
    assert svc.check_limit_orders() == 1
    lo = svc.db.get(LimitOrder, lo.id)
    assert lo.status == "filled" and lo.order_id
    assert [(o.side, o.symbol) for o in orders(svc)] == [("BUY", "ETHUSDT")]
    assert svc.check_limit_orders() == 0                              # placed once only


def test_cancelled_and_expired_limit_orders_do_not_trade(svc):
    use(svc, say(side="BUY", symbol="ETH", usd=60, type="limit", price=2400),
        say(side="BUY", symbol="BTC", usd=60, type="limit", price=79000))
    svc.chat("挂单")
    svc.resolve_approval(svc.db.pending_approvals()[0].id, True, "web")
    svc.chat("再挂一单")
    svc.resolve_approval(svc.db.pending_approvals()[0].id, True, "web")
    eth, btc = svc.open_limit_orders()
    assert svc.cancel_limit_order(eth.id) and not svc.cancel_limit_order(eth.id)
    svc.db.update(LimitOrder, btc.id, expires=0)
    svc.client.prices_map.update(ETHUSDT=2000.0, BTCUSDT=70000.0)
    assert svc.check_limit_orders() == 0
    assert [svc.db.get(LimitOrder, x.id).status for x in (eth, btc)] == ["cancelled", "expired"]
    assert not orders(svc)


def test_bad_plans_are_not_submitted(svc):
    use(svc, "\n".join([
        "方案：" + json.dumps({"side": "BUY", "symbol": "ETH", "usd": 50, "type": "limit", "price": 2600}),
        "方案：" + json.dumps({"side": "HOLD", "symbol": "ETH", "usd": 50}),
        "方案：" + json.dumps({"side": "BUY", "symbol": "ETH", "usd": 0}),
        "方案：{not json}",
    ]))
    reply = svc.chat("随便")
    assert reply.count("⚠️ 方案未提交") == 4 and "越过现价" in reply
    assert not svc.db.pending_approvals()


def test_risk_rejected_plan_is_not_submitted(svc):
    svc.update_settings({"limits": {"max_order_usd": 100}})
    use(svc, say(side="BUY", symbol="BTC", usd=50))
    svc.pause("测试")
    reply = svc.chat("买 BTC")
    assert "⚠️ 方案未提交" in reply and not svc.db.pending_approvals()


def test_at_most_three_plans_per_reply(svc):
    line = "方案：" + json.dumps({"side": "BUY", "symbol": "BTC", "usd": 10})
    use(svc, "\n".join([line] * 4))
    reply = svc.chat("多买几次")
    assert len(svc.db.pending_approvals()) == 3 and "一次最多 3 个" in reply


def test_chat_history_returns_plan_state(tmp_path):
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    from binanceclaw.api.app import create_app
    from binanceclaw.config import Secrets
    from binanceclaw.core.service import Service

    from .conftest import FakeFutures, FakeSpot
    svc = Service(data_dir=tmp_path, client=FakeSpot(), fclient=FakeFutures(),
                  secrets=Secrets(admin_password_hash=PasswordHasher().hash("correct-horse-1"), session_secret="x" * 64,
                                  binance_api_key="k", binance_api_secret="s"))
    svc.settings_store.update({"allocation": {"auto_rebalance": False}, "news": {"enabled": False}})
    c = TestClient(create_app(svc))
    c.post("/api/auth/login", json={"password": "correct-horse-1"})
    use(svc, say(side="BUY", symbol="ETH", usd=60, type="limit", price=2400))
    assert "待批准" in c.post("/api/chat", json={"text": "挂单"}).json()["reply"]
    a = svc.db.pending_approvals()[0]
    assert c.post(f"/api/approvals/{a.id}/approve").json()["ok"]
    r = c.get("/api/chat").json()
    plan = r["plans"][a.id]
    assert plan["status"] == "approved" and plan["limit"]["status"] == "open"
    assert r["messages"][-1]["approvals"] == [a.id] and r["messages"][0]["approvals"] == []
    lid = plan["limit"]["id"]
    assert c.post(f"/api/limit-orders/{lid}/cancel").json()["ok"]
    assert not c.post(f"/api/limit-orders/{lid}/cancel").json()["ok"]
    assert c.get("/api/limit-orders?status=cancelled").json()[0]["id"] == lid


def test_telegram_shows_limit_price():
    from binanceclaw.db import Approval
    from binanceclaw.notify.telegram import TelegramBot
    a = Approval(expires=0, symbol="ETHUSDT", side="BUY", quote_usd=60,
                 payload={"source": "chat", "order": "limit", "price": 2400, "hours": 12})
    assert TelegramBot._ap_title(a) == "BUY ETHUSDT  $60  挂单 ≤ 2400（12 小时）"
    a.payload = {"source": "chat", "order": "market"}
    assert TelegramBot._ap_title(a) == "BUY ETHUSDT  $60  市价"
