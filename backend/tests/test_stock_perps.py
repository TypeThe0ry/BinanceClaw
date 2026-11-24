"""Binance stock perps (USDⓈ-M TradFi perpetuals such as NVDAUSDT): listed, tradable, and signed."""
import httpx

from binanceclaw.exchange.binance_futures import BinanceFutures, PerpRules

from .conftest import plan, use


def _info_client():
    def handler(req: httpx.Request):
        base = {"quoteAsset": "USDT", "status": "TRADING",
                "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01", "maxQty": "1000"},
                            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                            {"filterType": "MIN_NOTIONAL", "notional": "5"}]}
        return httpx.Response(200, json={"symbols": [
            {**base, "symbol": "BTCUSDT", "baseAsset": "BTC", "contractType": "PERPETUAL", "underlyingType": "COIN"},
            {**base, "symbol": "NVDAUSDT", "baseAsset": "NVDA", "contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY"},
            {**base, "symbol": "BTCUSDT_261225", "baseAsset": "BTC", "contractType": "CURRENT_QUARTER"},
        ]})

    c = BinanceFutures()
    c._http = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    return c


def test_perp_rules_include_tradfi_stock_perps():
    rules = _info_client().perp_rules()
    assert set(rules) == {"BTCUSDT", "NVDAUSDT"}
    assert rules["NVDAUSDT"].tradfi and rules["NVDAUSDT"].underlying == "EQUITY"
    assert not rules["BTCUSDT"].tradfi


def _add_nvda(svc, price=180.0):
    fc = svc.fclient
    fc.marks_map["NVDAUSDT"] = price
    fc._rules["NVDAUSDT"] = PerpRules("NVDAUSDT", "NVDA", "TRADING", 0.01, 0.01, 1e5, 5.0, 0.01, tradfi=True, underlying="EQUITY")
    svc.fbroker._rules = None
    svc.fbroker._prem = None


def long_nvda(**kw):
    return {"op": "OPEN_LONG", "symbol": "NVDAUSDT", "margin_usd": 50, "leverage": 2, "stop_loss": 170, **kw}


def test_stock_perp_tradable_without_whitelist(svc):
    _add_nvda(svc)
    svc.update_settings({"futures": {"enabled": True}})
    use(svc, plan(futures=[long_nvda()]))
    c = svc.run_cycle("manual")
    assert c.risk[0]["decision"] == "approve", c.risk[0]
    ok, msg = svc.resolve_approval(c.risk[0]["approval_id"], True, "web")
    assert ok, msg
    (p,) = svc.fbroker.open_rows()
    assert (p.symbol, p.side) == ("NVDAUSDT", "LONG")


def test_stock_perps_off_keeps_whitelist(svc):
    _add_nvda(svc)
    svc.update_settings({"futures": {"enabled": True, "stocks": False}})
    use(svc, plan(futures=[long_nvda()]))
    v = svc.run_cycle("manual").risk[0]
    assert v["decision"] == "reject" and v["checks"][-1]["name"] == "合约白名单"


def test_context_lists_stock_perps(svc):
    _add_nvda(svc)
    svc.update_settings({"futures": {"enabled": True}})
    fv = svc.futures_view(svc.portfolio({"USDT": 1.0}), svc.risk_state())
    ctx = svc.futures_context(fv)
    assert "NVDAUSDT" in ctx["stock_perps"] and ctx["stock_perps"]["NVDAUSDT"]["type"] == "EQUITY"
    assert "BTCUSDT" not in ctx["stock_perps"]
    rows = {r["symbol"]: r for r in svc.futures_markets("NVDA")}
    assert rows["NVDAUSDT"]["tradfi"] is True


def test_live_open_signs_tradfi_agreement_once(svc):
    _add_nvda(svc)
    svc.update_settings({"futures": {"enabled": True}})
    svc.broker.mode = svc.fbroker.mode = "live"
    fc = svc.fclient
    svc.fbroker.open("NVDAUSDT", "LONG", 50, 2, 170, None, "cidn1", {})
    signs = [c for c in fc.calls if c[0] == "sign_tradfi"]
    assert len(signs) == 1
    assert fc.calls.index(signs[0]) < fc.calls.index(("leverage", "NVDAUSDT", 2))
    svc.fbroker.open("BTCUSDT", "LONG", 100, 3, 78000, None, "cidn2", {})
    assert len([c for c in fc.calls if c[0] == "sign_tradfi"]) == 1


def test_markets_tradfi_filter(svc):
    _add_nvda(svc)
    rows = svc.futures_markets("", 300, tradfi=True)
    assert [r["symbol"] for r in rows] == ["NVDAUSDT"]
    assert svc.overview()["futures"]["stocks"] is True
