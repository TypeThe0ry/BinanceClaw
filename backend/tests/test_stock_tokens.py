"""Tokenized stocks (QQQB, AVGOB, SMHB ...) are discovered from exchangeInfo, not a hard-coded list."""
from binanceclaw.exchange.binance_spot import is_stock_token

from .test_api import client  # noqa: F401  (fixture)


def _sym(base, bid_up):
    return {"baseAsset": base, "filters": [{"filterType": "PERCENT_PRICE_BY_SIDE", "bidMultiplierUp": bid_up,
                                            "bidMultiplierDown": "0.8", "askMultiplierUp": "1.2", "askMultiplierDown": "0.9"}]}


def test_stock_tokens_recognised_from_exchange_info():
    for base in ("AVGOB", "AMDB", "SMHB", "QQQB", "NVDAB", "MUB"):
        assert is_stock_token(_sym(base, "1.1"))
    for base in ("BNB", "ARB", "SHIB", "CKB", "TRB", "BB"):          # coins ending in B have the wider crypto band
        assert not is_stock_token(_sym(base, "1.2"))
    assert not is_stock_token(_sym("BTC", "1.1"))
    assert not is_stock_token({"baseAsset": "AVGOB", "filters": []})


def _list_avgob(svc):
    svc.client.prices_map["AVGOBUSDT"] = 325.0
    svc.client.prices_map["SMHBUSDT"] = 504.0
    orig = svc.client.spot_markets
    svc.client.spot_markets = lambda: [*orig(), {"symbol": "AVGOBUSDT", "base": "AVGOB", "quote": "USDT", "status": "TRADING", "stock": True},
                                       {"symbol": "SMHBUSDT", "base": "SMHB", "quote": "USDT", "status": "TRADING", "stock": True}]
    svc._markets = None
    svc._tickers = None


def test_new_stock_listings_are_discovered(svc):
    _list_avgob(svc)
    assert set(svc.stock_tokens()) == {"QQQB", "NVDAB", "AVGOB", "SMHB"}
    assert "AVGOB" in {r["token"] for r in svc.markets(tag="tokenized_stock", limit=200)}


def test_ai_sees_every_stock_token(svc):
    _list_avgob(svc)
    svc.update_settings({"allocation": {"mode": "ai", "universe": "all"}})
    s = svc.settings
    scan = svc.stock_scan(s, s.tradable_tokens())
    assert {"AVGOB", "SMHB"} <= set(scan) and scan["AVGOB"]["price"] == 325.0
    assert not set(svc.market_scan(s, [])) & {"AVGOB", "SMHB", "QQQB", "NVDAB"}   # coins only there
    assert svc.set_focus(["AVGOB", "smhbusdt"]) == ["AVGOB", "SMHB"]
    svc._universe = None
    assert {"AVGOB", "SMHB"} <= set(svc.settings.tradable_tokens())


def test_settings_candidates_include_discovered_stocks(client):  # noqa: F811
    _list_avgob(client.svc)
    client.post("/api/auth/login", json={"password": "correct-horse-1"})
    cands = client.get("/api/settings").json()["candidates"]
    assert {"AVGOB", "SMHB", "QQQB", "NVDAB", "BTC"} <= set(cands)


def test_chat_knows_stock_tokens_and_account(svc):
    import json

    from .conftest import use
    _list_avgob(svc)
    p = use(svc, "好的")
    svc.chat("帮我买点 AVGO", "telegram")
    system = p.calls[-1][0]
    data = json.loads(system.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
    assert data["account"]["exchange"].startswith("币安现货")
    st = data["stock_tokens"]
    assert st["AVGOB"] == {"ticker": "AVGO", "price": 325.0, "change_24h": 0.01, "tradable": False}
    assert st["QQQB"]["tradable"] is True                     # in the default targets
    assert "不要问主人用哪个渠道" in system and "子账户" in system
