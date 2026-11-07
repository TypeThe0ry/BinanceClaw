"""Buying with USDC when the quote is USDT: every stablecoin counts toward what can be spent, and the
shortfall is redeemed from Simple Earn and converted into the quote right before the order."""
import json

import pytest
from sqlmodel import select

from binanceclaw.db import LimitOrder, Order
from binanceclaw.exchange.binance_spot import SymbolRules

from .conftest import use


@pytest.fixture
def usdc_svc(svc):
    """The owner's case: ~$70 USDT, the rest of the stablecoins in USDC (part of it in flexible Earn)."""
    svc.client.prices_map["USDCUSDT"] = 1.0
    rules = svc.client.symbol_rules

    def symbol_rules(symbols):
        out = rules([s for s in symbols if s != "USDCUSDT"])
        if "USDCUSDT" in symbols:
            out["USDCUSDT"] = SymbolRules("USDCUSDT", "USDC", "USDT", "TRADING", 1.0, 1.0, 5.0, 0.0001, 8)
        return out

    svc.client.symbol_rules = symbol_rules
    svc.broker.balances()                                   # initialise the paper ledger (USDT 10,000)
    svc.broker._paper_apply({"USDT": -9930, "USDC": 9000})
    svc.earn.subscribe("USDC", 8900)
    svc.update_settings({"limits": {"max_order_usd": 2000}})
    return svc


def spot(svc, asset):
    return svc.broker.balances().get(asset, 0.0)


def test_risk_counts_usdc_not_only_usdt(usdc_svc):
    prices, *_ = usdc_svc.market_snapshot(0)
    from binanceclaw.risk.engine import evaluate
    v = evaluate([{"side": "BUY", "symbol": "BTCUSDT", "quote_usd": 1000}],
                 usdc_svc.portfolio(prices), usdc_svc.risk_state())[0]
    assert v.decision != "reject" and v.approved_usd == 1000
    assert not any("稳定币下限" in c.detail and "可用" in c.detail for c in v.checks)


def test_buy_redeems_usdc_from_earn_and_converts(usdc_svc):
    ok, msg, _ = usdc_svc.execute("BUY", "BTCUSDT", 1000, via="approved")
    assert ok, msg
    assert spot(usdc_svc, "BTC") > 0.012
    left = usdc_svc.earn.positions(0)["USDC"]["amount"]
    assert 7900 < left < 8100                               # only what the buy needed came out of Earn
    o = usdc_svc.db.all(select(Order))
    assert [(x.symbol, x.side) for x in o] == [("BTCUSDT", "BUY")]   # the conversion is not a trade of the AI's


def test_chat_limit_plan_goes_through_and_triggers(usdc_svc):
    svc = usdc_svc
    use(svc, "挂单\n方案：" + json.dumps({"side": "BUY", "symbol": "BTC", "usd": 1000, "type": "limit",
                                          "price": 79000, "hours": 24, "reason": "r", "invalidation": "i"}))
    reply = svc.chat("挂 75800")
    assert "待批准" in reply and "方案未提交" not in reply
    a = svc.db.pending_approvals()[0]
    assert svc.resolve_approval(a.id, True, "web")[0]
    svc.client.prices_map["BTCUSDT"] = 78900
    assert svc.check_limit_orders() == 1
    lo = svc.db.all(select(LimitOrder))[0]
    assert lo.status == "filled", lo.result
    assert svc.chat_recent_orders()[0]["via"] == "limit"


def test_no_conversion_pair_fails_cleanly(usdc_svc):
    rules = usdc_svc.client.symbol_rules
    usdc_svc.client.symbol_rules = lambda syms: {k: v for k, v in rules(syms).items() if k != "USDCUSDT"}
    usdc_svc.broker._rules.clear()
    ok, msg, _ = usdc_svc.execute("BUY", "BTCUSDT", 1000, via="approved")
    assert not ok and "USDC" in msg
    assert spot(usdc_svc, "BTC") == 0
