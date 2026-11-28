"""Binance Stocks: a stock and its token (NVDA / NVDAB) are one holding; buys, sells, approvals, ledger."""
from sqlmodel import select

from binanceclaw.db import StockLot, StockTrade

from .conftest import plan, use


def run(svc, *stocks, actions=()):
    use(svc, plan(*actions, stocks=list(stocks)))
    return svc.run_cycle("manual")


def test_small_buy_executes_into_ledger_paper(svc):
    c = run(svc, {"side": "BUY", "symbol": "NVDA", "usd": 100})
    v = c.risk[0]
    assert v["kind"] == "stock" and v["decision"] == "execute", v
    (lot,) = svc.db.all(select(StockLot))
    assert lot.symbol == "NVDA" and 0.55 < lot.qty < 0.56 and lot.cost_usd == 100
    assert 9899 < svc.broker.balances()["USDT"] < 9901
    h = svc.stock_holdings()["holdings"][0]
    assert h["symbol"] == "NVDA" and abs(h["value"] - 100) < 1
    assert "STOCKS" in svc.portfolio({"USDT": 1.0}).values()


def test_large_buy_waits_for_approval(svc):
    c = run(svc, {"side": "BUY", "symbol": "aapl", "usd": 500})
    v = c.risk[0]
    assert v["decision"] == "approve" and v["symbol"] == "AAPL"
    ok, msg = svc.resolve_approval(v["approval_id"], True, "web")
    assert ok, msg
    assert svc.sdesk.lots()["AAPL"].qty > 2


def test_unknown_ticker_rejected(svc):
    v = run(svc, {"side": "BUY", "symbol": "ZZZZ", "usd": 50}).risk[0]
    assert v["decision"] == "reject" and "没有 ZZZZ" in v["reason"]


def test_spot_action_on_stock_token_becomes_stock_trade(svc):
    c = run(svc, actions=[("BUY", "NVDABUSDT", 50)])
    (v,) = c.risk
    assert v["kind"] == "stock" and v["symbol"] == "NVDA" and v["decision"] == "execute"


def test_token_and_shares_merge_into_one_holding(svc):
    svc.sdesk._book("NVDA", 1.0, 170)
    svc.client.balances_map = {**getattr(svc.client, "balances_map", {}), "NVDAB": 2.0}
    h = svc.sdesk.holdings({"NVDAB": 2.0})["NVDA"]
    assert h["qty"] == 3.0 and h["shares"] == 1.0 and h["token"] == "NVDAB"
    assert abs(h["value"] - 540) < 1


def test_sell_uses_ledger_shares(svc):
    svc.settings_store.update({"limits": {"allow_sell": True}})
    svc._rebuild()
    run(svc, {"side": "BUY", "symbol": "NVDA", "usd": 100})
    c = run(svc, {"side": "SELL", "symbol": "NVDA", "usd": 1000})
    v = c.risk[0]
    assert v["decision"] == "approve" and v["approved_usd"] <= 101
    ok, msg = svc.resolve_approval(v["approval_id"], True, "web")
    assert ok, msg
    assert "NVDA" not in svc.sdesk.lots()


def test_live_buy_signs_disclaimer_and_tokenizes(svc):
    svc.broker.mode = "live"
    svc.earn.ensure_stable = lambda target, need, shrink=False: need
    ok, msg = svc.sdesk.buy("NVDA", 50, "auto")
    assert ok, msg
    ec = svc.eclient
    assert ec.calls[0] == ("disclaimer",)
    assert ec.calls[1] == ("order", "NVDA", "BUY", 50, None, True)
    assert "NVDAB" in msg and not svc.sdesk.lots()          # tokenized: the holding is the token in the wallet
    ok, msg = svc.sdesk.buy("AAPL", 50, "auto")              # no token: shares go to the ledger
    assert ok and svc.sdesk.lots()["AAPL"].qty > 0
    assert ec.calls[-1][-1] is False


def test_live_unfilled_order_reconciles(svc):
    svc.broker.mode = "live"
    svc.earn.ensure_stable = lambda target, need, shrink=False: need
    svc.eclient.fill = False
    svc.sdesk._await = lambda tr: tr
    ok, msg = svc.sdesk.buy("AAPL", 46, "auto")
    assert ok and "已提交" in msg
    (tr,) = svc.db.all(select(StockTrade))
    assert tr.status == "submitted"
    svc.eclient.orders[tr.client_order_id] = {"status": "FILLED", "filledQty": "0.2", "filledTotal": "46", "avgFilledPrice": "230"}
    svc.sdesk.reconcile()
    assert svc.sdesk.lots()["AAPL"].qty == 0.2


def test_stocks_off_rejects(svc):
    svc.settings_store.update({"stocks": {"enabled": False}})
    svc._rebuild()
    v = run(svc, {"side": "BUY", "symbol": "NVDA", "usd": 50}).risk[0]
    assert v["decision"] == "reject"


def _live(svc):
    svc.broker.mode = "live"
    svc.earn.ensure_stable = lambda target, need, shrink=False: need


def _app_order(ec, symbol, side, qty, total, ts, oid):
    ec.history.append({"orderId": oid, "symbol": symbol, "side": side, "status": "FILLED", "filledQty": str(qty),
                       "filledTotal": str(total), "createdAt": ts})


def test_live_shares_bought_in_binance_app_are_held(svc):
    """Untokenized NVDA bought in the Binance app: no ledger entry, rebuilt from the order history."""
    _live(svc)
    ec = svc.eclient
    _app_order(ec, "NVDA", "BUY", 2, 340, 1_782_000_000_000, 1)
    _app_order(ec, "NVDA", "SELL", 0.5, 90, 1_783_000_000_000, 2)
    _app_order(ec, "AAPL", "BUY", 1, 200, 1_790_000_000_000, 3)
    ec.converts.append({"underlyingAsset": "AAPL", "underlyingAssetAmount": "0.4", "tokenizedAsset": "AAPLB",
                        "convertType": "MINT", "status": "S", "createdAt": 1_791_000_000_000})
    h = svc.stock_holdings()["holdings"]
    rows = {r["symbol"]: r for r in h}
    assert rows["NVDA"]["qty"] == 1.5 and abs(rows["NVDA"]["cost"] - 255) < 1e-6
    assert abs(rows["AAPL"]["qty"] - 0.6) < 1e-9
    assert abs(svc.stocks_ledger_value() - (1.5 * 180 + 0.6 * 230)) < 1
    assert "NVDA" in svc.sdesk.context()["holdings"]


def test_live_history_skips_own_tokenized_buys_and_counts_funding_tokens(svc):
    _live(svc)
    ok, _ = svc.sdesk.buy("NVDA", 90, "auto")                    # tokenized buy: the holding is NVDAB, not shares
    assert ok
    svc.eclient.funding = {"NVDAB": 0.5, "USDC": 3.0}
    svc.sdesk.refresh()
    h = svc.sdesk.holdings({})["NVDA"]
    assert h["shares"] == 0 and h["token_qty"] == 0.5 and h["qty"] == 0.5


def test_live_sell_of_app_shares_goes_through_binance_stocks(svc):
    svc.settings_store.update({"limits": {"allow_sell": True}})
    svc._rebuild()
    _live(svc)
    ec = svc.eclient
    _app_order(ec, "NVDA", "BUY", 1, 170, 1_782_000_000_000, 1)
    ok, msg = svc.sdesk.sell("NVDA", 90, "web")
    assert ok, msg
    order = next(c for c in ec.calls if c[0] == "order")
    assert order[1:3] == ("NVDA", "SELL") and 0.49 < order[4] < 0.51
    assert ec.calls[-1][:2] == ("funding_to_spot", "USDC")       # proceeds land in funding; moved to spot
    assert 0.49 < svc.sdesk.holdings({})["NVDA"]["qty"] < 0.51


def test_live_history_failure_falls_back_to_ledger(svc):
    from binanceclaw.exchange.binance_spot import ExchangeError
    _live(svc)
    svc.sdesk._book("AAPL", 1.0, 230)

    def boom(*a, **k):
        raise ExchangeError("geo")
    svc.eclient.order_history = boom
    assert svc.sdesk.positions()["AAPL"] == (1.0, 230)
