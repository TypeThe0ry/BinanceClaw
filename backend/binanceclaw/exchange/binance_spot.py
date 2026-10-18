"""Minimal Binance REST clients (HMAC-SHA256): spot, Simple Earn flexible, spot<->USDⓈ-M
transfer, and (in binance_futures.py) USDⓈ-M perpetuals. No withdrawal, margin-borrow or
sub-account endpoints exist anywhere in this package, on purpose."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from dataclasses import dataclass

import httpx

BASE_URL = "https://api.binance.com"
# binance.com's own market pages read coin categories ("tags") from this public endpoint;
# the REST API (api.binance.com) does not expose them.
TAGS_URL = "https://www.binance.com/bapi/asset/v2/public/asset-service/product/get-products"


class ExchangeError(RuntimeError):
    def __init__(self, msg: str, code: int | None = None):
        super().__init__(msg)
        self.code = code


@dataclass
class SymbolRules:
    symbol: str
    base: str
    quote: str
    status: str
    step_size: float
    min_qty: float
    min_notional: float
    tick_size: float
    quote_precision: int


def _f(x) -> float:
    return float(x) if x not in (None, "") else 0.0


class BinanceSpot:
    TIME_PATH = "/api/v3/time"

    def __init__(self, api_key: str = "", api_secret: str = "", base_url: str = BASE_URL, timeout: float = 15):
        self._key = api_key
        self._secret = api_secret.encode()
        self._http = httpx.Client(base_url=base_url, timeout=timeout,
                                  headers={"User-Agent": "BinanceClaw/1.0"})
        self._offset_ms = 0
        self._offset_ts = 0.0

    # ---------------------------------------------------------------- transport
    def _request(self, method: str, path: str, params: dict | None = None, signed: bool = False):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {}
        if signed:
            if not self._key or not self._secret:
                raise ExchangeError("Binance API key/secret not configured")
            self._sync_time()
            params["timestamp"] = int(time.time() * 1000) + self._offset_ms
            params.setdefault("recvWindow", 10000)
            query = urllib.parse.urlencode(params)
            sig = hmac.new(self._secret, query.encode(), hashlib.sha256).hexdigest()
            query = f"{query}&signature={sig}"
            headers["X-MBX-APIKEY"] = self._key
        else:
            query = urllib.parse.urlencode(params)
        url = f"{path}?{query}" if query else path
        try:
            r = self._http.request(method, url, headers=headers)
        except httpx.HTTPError as e:
            raise ExchangeError(f"{method} {path}: {type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            try:
                body = r.json()
                raise ExchangeError(f"{method} {path}: {body.get('msg')}", body.get("code"))
            except (ValueError, json.JSONDecodeError):
                raise ExchangeError(f"{method} {path}: HTTP {r.status_code}") from None
        return r.json()

    def _sync_time(self):
        if time.time() - self._offset_ts < 600:
            return
        server = self._request("GET", self.TIME_PATH)["serverTime"]
        self._offset_ms = int(server - time.time() * 1000)
        self._offset_ts = time.time()

    # ---------------------------------------------------------------- public
    def prices(self, symbols: list[str]) -> dict[str, float]:
        data = self._request("GET", "/api/v3/ticker/price", {"symbols": json.dumps(symbols, separators=(",", ":"))})
        return {d["symbol"]: float(d["price"]) for d in data}

    def spot_markets(self) -> list[dict]:
        """Every listed spot pair, any quote and any status (one exchangeInfo call, ~MBs: cache it).

        Status is kept rather than filtered: Binance marks a pair BREAK while trading is suspended
        (TON was, for example), and such assets must still be findable and selectable."""
        info = self._request("GET", "/api/v3/exchangeInfo", {"permissions": "SPOT"})
        return [{"symbol": s["symbol"], "base": s["baseAsset"], "quote": s["quoteAsset"], "status": s["status"]}
                for s in info["symbols"] if s.get("isSpotTradingAllowed", True)]

    def asset_tags(self) -> dict[str, list[str]]:
        """Binance's categories per base asset, e.g. {"SOL": ["Layer1_Layer2", "Solana", "pos"]}."""
        try:
            r = self._http.get(TAGS_URL, params={"includeEtf": "true"}, timeout=8)
            r.raise_for_status()
            rows = r.json().get("data") or []
        except (httpx.HTTPError, ValueError) as e:
            raise ExchangeError(f"读取币安币种分类失败：{e}") from e
        out: dict[str, set] = {}
        for row in rows:
            base = row.get("b")
            if base:
                out.setdefault(base, set()).update(t for t in (row.get("tags") or []) if t)
        return {b: sorted(t) for b, t in out.items()}

    def all_tickers_24h(self) -> dict[str, dict]:
        rows = self._request("GET", "/api/v3/ticker/24hr")
        return {d["symbol"]: {"price": float(d["lastPrice"]), "change_pct": float(d["priceChangePercent"]) / 100,
                              "quote_volume": float(d["quoteVolume"])} for d in rows}

    def all_prices(self) -> dict[str, float]:
        """Every spot pair's last price (one request, ~2k rows)."""
        return {d["symbol"]: float(d["price"]) for d in self._request("GET", "/api/v3/ticker/price")}

    def ticker_24h(self, symbols: list[str]) -> dict[str, dict]:
        data = self._request("GET", "/api/v3/ticker/24hr", {"symbols": json.dumps(symbols, separators=(",", ":"))})
        return {d["symbol"]: {"price": float(d["lastPrice"]), "change_pct": float(d["priceChangePercent"]) / 100,
                              "high": float(d["highPrice"]), "low": float(d["lowPrice"]),
                              "quote_volume": float(d["quoteVolume"])} for d in data}

    def book(self, symbols: list[str]) -> dict[str, tuple[float, float]]:
        data = self._request("GET", "/api/v3/ticker/bookTicker", {"symbols": json.dumps(symbols, separators=(",", ":"))})
        return {d["symbol"]: (float(d["bidPrice"]), float(d["askPrice"])) for d in data}

    def klines(self, symbol: str, interval: str = "1d", limit: int = 30) -> list[dict]:
        rows = self._request("GET", "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        return [{"t": r[0] / 1000, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                 "c": float(r[4]), "v": float(r[5]), "close_t": r[6] / 1000} for r in rows]

    def symbol_rules(self, symbols: list[str]) -> dict[str, SymbolRules]:
        info = self._request("GET", "/api/v3/exchangeInfo", {"symbols": json.dumps(symbols, separators=(",", ":"))})
        out = {}
        for s in info["symbols"]:
            f = {x["filterType"]: x for x in s.get("filters", [])}
            lot = f.get("LOT_SIZE", {})
            notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
            out[s["symbol"]] = SymbolRules(
                symbol=s["symbol"], base=s["baseAsset"], quote=s["quoteAsset"], status=s["status"],
                step_size=_f(lot.get("stepSize")), min_qty=_f(lot.get("minQty")),
                min_notional=_f(notional.get("minNotional")),
                tick_size=_f(f.get("PRICE_FILTER", {}).get("tickSize")),
                quote_precision=int(s.get("quoteAssetPrecision", s.get("quotePrecision", 8))),
            )
        return out

    # ---------------------------------------------------------------- signed, read-only
    def balances(self) -> dict[str, float]:
        acct = self._request("GET", "/api/v3/account", {"omitZeroBalances": "true"}, signed=True)
        return {b["asset"]: float(b["free"]) for b in acct["balances"] if float(b["free"]) > 0}

    def account(self) -> dict:
        return self._request("GET", "/api/v3/account", {"omitZeroBalances": "true"}, signed=True)

    def api_restrictions(self) -> dict:
        return self._request("GET", "/sapi/v1/account/apiRestrictions", signed=True)

    def order_status(self, symbol: str, client_order_id: str) -> dict:
        return self._request("GET", "/api/v3/order", {"symbol": symbol, "origClientOrderId": client_order_id}, signed=True)

    # ---------------------------------------------------------------- signed, trading
    def market_buy_quote(self, symbol: str, quote_usd: float, client_order_id: str) -> dict:
        """Spend exactly ``quote_usd`` of the quote asset (MARKET + quoteOrderQty)."""
        return self._request("POST", "/api/v3/order", {
            "symbol": symbol, "side": "BUY", "type": "MARKET",
            "quoteOrderQty": f"{quote_usd:.2f}", "newClientOrderId": client_order_id,
            "newOrderRespType": "FULL",
        }, signed=True)

    def market_sell_qty(self, symbol: str, qty: str, client_order_id: str) -> dict:
        return self._request("POST", "/api/v3/order", {
            "symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": qty,
            "newClientOrderId": client_order_id, "newOrderRespType": "FULL",
        }, signed=True)

    # ---------------------------------------------------------------- Simple Earn (flexible)
    def earn_products(self, asset: str) -> list[dict]:
        r = self._request("GET", "/sapi/v1/simple-earn/flexible/list", {"asset": asset, "size": 100}, signed=True)
        return r.get("rows", [])

    def earn_positions(self) -> list[dict]:
        r = self._request("GET", "/sapi/v1/simple-earn/flexible/position", {"size": 100}, signed=True)
        return r.get("rows", [])

    def earn_subscribe(self, product_id: str, amount: float) -> dict:
        return self._request("POST", "/sapi/v1/simple-earn/flexible/subscribe", {
            "productId": product_id, "amount": f"{amount:.8f}".rstrip("0").rstrip("."),
            "autoSubscribe": "false", "sourceAccount": "SPOT"}, signed=True)

    def earn_redeem(self, product_id: str, amount: float) -> dict:
        return self._request("POST", "/sapi/v1/simple-earn/flexible/redeem", {
            "productId": product_id, "amount": f"{amount:.8f}".rstrip("0").rstrip("."), "destAccount": "SPOT"}, signed=True)

    # ---------------------------------------------------------------- spot <-> USDⓈ-M futures wallet
    def transfer(self, direction: str, asset: str, amount: float) -> dict:
        """direction: MAIN_UMFUTURE (spot -> futures) or UMFUTURE_MAIN (futures -> spot). Nothing else."""
        if direction not in ("MAIN_UMFUTURE", "UMFUTURE_MAIN"):
            raise ExchangeError(f"transfer type {direction} not allowed")
        return self._request("POST", "/sapi/v1/asset/transfer", {
            "type": direction, "asset": asset, "amount": f"{amount:.8f}".rstrip("0").rstrip(".")}, signed=True)
