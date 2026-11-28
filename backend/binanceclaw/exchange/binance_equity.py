"""Binance Stocks REST client (/sapi/v1/equity/*): real US stocks and ETFs, executed and held by
Binance's broker partners, quoted in USD and paid in USDC.

* Symbols are plain tickers (NVDA, SPY), not pairs.
* A buy of a tokenization-enabled stock can settle as its tokenized asset (NVDA -> NVDAB in the
  wallet, ``tokenize=true``); otherwise the shares are held by the broker and no endpoint lists
  them, so BinanceClaw keeps its own ledger of the shares it bought.
* Orders are acknowledged (status S/F) and filled asynchronously; GET order/detail has the fill.
* The trading disclaimer must be signed once before the first order (error 486410)."""
from __future__ import annotations

import urllib.parse
import uuid

import httpx

from .binance_spot import BinanceSpot, ExchangeError

DISCLAIMER_MISSING = 486410
FILLED = "FILLED"
OPEN_STATUSES = ("NEW", "ACCEPTED", "PARTIALLY_FILLED")
DONE_STATUSES = ("FILLED", "CANCELED", "EXPIRED", "REJECTED")
FILLED_STATUSES = "FILLED,PARTIALLY_FILLED,CANCELED,EXPIRED"      # every status that can carry a fill
HISTORY_PAGE = 100


def _num(x: float) -> str:
    return f"{x:.9f}".rstrip("0").rstrip(".")


class BinanceEquity(BinanceSpot):
    PREFIX = "/sapi/v1/equity"

    def _keyed(self, path: str, params: dict | None = None):
        """MARKET_DATA endpoints: API key header, no signature."""
        if not self._key:
            raise ExchangeError("Binance API key not configured")
        q = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        try:
            r = self._http.request("GET", f"{self.PREFIX}{path}" + (f"?{q}" if q else ""), headers={"X-MBX-APIKEY": self._key})
        except httpx.HTTPError as e:
            raise ExchangeError(f"GET {path}: {type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                raise ExchangeError(f"GET {path}: HTTP {r.status_code}") from None
            raise ExchangeError(f"GET {path}: {body.get('msg')}", body.get("code"))
        if not r.content.strip():
            return None                       # quote: unknown / halted ticker answers with an empty body
        return r.json()

    def _signed(self, method: str, path: str, params: dict | None = None):
        r = self._request(method, f"{self.PREFIX}{path}", params, signed=True)
        if isinstance(r, dict) and r.get("code") not in (None, 0, 200, "000000") and r.get("msg") and "status" not in r:
            raise ExchangeError(f"{method} {path}: {r.get('msg')}", r.get("code"))
        return r

    # ---------------------------------------------------------------- market data
    def stock_info(self) -> dict[str, dict]:
        """ticker -> exchangeInfo row (tradability, fractionable, stepSize, minNotional, ...)."""
        r = self._keyed("/market/exchangeInfo") or {}
        return {s["symbol"]: s for s in r.get("symbols", [])}

    def tokenized_assets(self) -> list[dict]:
        """[{assetCode: NVDAB, underlyingEquitySymbol: NVDA, multiplier: "1", multiplierValid}]"""
        return self._keyed("/market/tokenized-assets") or []

    def stock_quote(self, symbol: str) -> dict | None:
        """{bidPrice, askPrice, ...} or None when Binance has no live quote (unknown, halted, delisted)."""
        r = self._keyed("/market/quote", {"symbol": symbol})
        return r or None

    # ---------------------------------------------------------------- trading
    def sign_stock_disclaimer(self) -> dict:
        return self._signed("POST", "/account/disclaimer")

    def stock_market_order(self, symbol: str, side: str, client_order_id: str, *, notional: float | None = None,
                           qty: float | None = None, tokenize: bool = True) -> dict:
        """MARKET order: a BUY spends ``notional`` USDC, a SELL sells ``qty`` shares. Returns the ack."""
        p = {"symbol": symbol, "side": side, "orderType": "MARKET", "quoteAsset": "USDC",
             "clientOrderId": client_order_id}
        if side == "BUY":
            p.update(notional=f"{notional:.2f}", walletType="MAIN", tokenize=str(bool(tokenize)).lower())
        else:
            p["quantity"] = _num(qty)
        r = self._signed("POST", "/order/place", p)
        if r.get("status") == "F":
            raise ExchangeError(f"{symbol} 下单被拒绝")
        return r

    def stock_order(self, client_order_id: str) -> dict:
        return self._signed("GET", "/order/detail", {"clientOrderId": client_order_id})

    # ---------------------------------------------------------------- history (where holdings come from)
    def order_history(self, start_ms: int, end_ms: int, page: int = 1, size: int = HISTORY_PAGE) -> dict:
        """{total, page, size, rows: [{orderId, symbol, side, filledQty, filledTotal, status, createdAt, ...}]}"""
        return self._signed("GET", "/order/history", {"startTime": start_ms, "endTime": end_ms, "orderStatus": FILLED_STATUSES,
                                                       "current": page, "size": size}) or {}

    def tokenized_history(self, start_ms: int, end_ms: int, last_id: int | None = None, size: int = HISTORY_PAGE) -> dict:
        """Mint / redeem between a stock and its token: {rows: [{underlyingAsset, underlyingAssetAmount,
        tokenizedAsset, tokenizedAssetAmount, convertType MINT|REDEEM, status P|S|F, createdAt}], hasMore, nextLastId}"""
        return self._signed("GET", "/tokenized/history", {"startTime": start_ms, "endTime": end_ms, "lastId": last_id,
                                                          "size": size}) or {}

    # ---------------------------------------------------------------- funding wallet
    def funding_assets(self) -> dict[str, float]:
        """Funding wallet balances: stock sells settle there, and tokens minted by a buy by default."""
        r = self._request("POST", "/sapi/v1/asset/get-funding-asset", {}, signed=True) or []
        return {a["asset"]: float(a.get("free") or 0) for a in r if float(a.get("free") or 0) > 0}

    def funding_to_spot(self, asset: str, amount: float) -> dict:
        return self._request("POST", "/sapi/v1/asset/transfer", {"type": "FUNDING_MAIN", "asset": asset,
                                                                 "amount": _num(amount)}, signed=True)


def stock_cid() -> str:
    """clientOrderId: Binance Stocks wants 32-36 characters of [A-Za-z0-9-_]."""
    return "bcs" + uuid.uuid4().hex[:29]
