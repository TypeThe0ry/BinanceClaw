"""exchangeInfo fails the whole request with -1121 when one pair is unknown (USDTUSDC)."""
import json

import httpx

from binanceclaw.exchange.binance_spot import BinanceSpot

LISTED = {"USDCUSDT": ("USDC", "USDT"), "BTCUSDT": ("BTC", "USDT")}


def _client():
    calls = []

    def handler(req: httpx.Request):
        syms = json.loads(req.url.params["symbols"])
        calls.append(syms)
        if any(s not in LISTED for s in syms):
            return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})
        return httpx.Response(200, json={"symbols": [
            {"symbol": s, "baseAsset": LISTED[s][0], "quoteAsset": LISTED[s][1], "status": "TRADING",
             "filters": [{"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"}]} for s in syms]})

    c = BinanceSpot()
    c._http = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    return c, calls


def test_unknown_pair_does_not_fail_the_known_one():
    c, calls = _client()
    rules = c.symbol_rules(["USDCUSDT", "USDTUSDC"])
    assert set(rules) == {"USDCUSDT"} and rules["USDCUSDT"].base == "USDC"
    assert calls[0] == ["USDCUSDT", "USDTUSDC"]


def test_only_unknown_pairs_returns_empty():
    c, _ = _client()
    assert c.symbol_rules(["USDTUSDC"]) == {}


def test_all_known_is_one_request():
    c, calls = _client()
    assert set(c.symbol_rules(["BTCUSDT", "USDCUSDT"])) == {"BTCUSDT", "USDCUSDT"}
    assert len(calls) == 1
