"""USDⓈ-M perpetual futures REST client (fapi). One-way or hedge position mode, isolated margin,
MARKET entries/exits and exchange-side STOP_MARKET / TAKE_PROFIT_MARKET protection.

Since 2025-12-09 conditional orders live in the Algo Order API (POST /fapi/v1/algoOrder,
``triggerPrice`` + ``closePosition``); /fapi/v1/order rejects them with -4120."""
from __future__ import annotations

from dataclasses import dataclass

from .binance_spot import BinanceSpot, ExchangeError, _f

FAPI_URL = "https://fapi.binance.com"


@dataclass
class PerpRules:
    symbol: str
    base: str
    status: str
    step_size: float
    min_qty: float
    max_qty: float
    min_notional: float
    tick_size: float


def _num(x: float) -> str:
    return f"{x:.8f}".rstrip("0").rstrip(".")


class BinanceFutures(BinanceSpot):
    TIME_PATH = "/fapi/v1/time"

    def __init__(self, api_key: str = "", api_secret: str = "", base_url: str = FAPI_URL, timeout: float = 15):
        super().__init__(api_key, api_secret, base_url, timeout)

    # ---------------------------------------------------------------- public
    def perp_rules(self) -> dict[str, PerpRules]:
        """Every TRADING USDT-margined PERPETUAL contract."""
        info = self._request("GET", "/fapi/v1/exchangeInfo")
        out = {}
        for s in info["symbols"]:
            if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT" or s.get("status") != "TRADING":
                continue
            f = {x["filterType"]: x for x in s.get("filters", [])}
            lot, mlot = f.get("LOT_SIZE", {}), f.get("MARKET_LOT_SIZE", {})
            # MARKET orders are checked against MARKET_LOT_SIZE; satisfy both
            step = max(_f(lot.get("stepSize")), _f(mlot.get("stepSize")))
            max_q = min(x for x in (_f(lot.get("maxQty")), _f(mlot.get("maxQty"))) if x > 0) if (lot or mlot) else 0.0
            out[s["symbol"]] = PerpRules(
                symbol=s["symbol"], base=s["baseAsset"], status=s["status"], step_size=step,
                min_qty=max(_f(lot.get("minQty")), _f(mlot.get("minQty"))), max_qty=max_q,
                min_notional=_f(f.get("MIN_NOTIONAL", {}).get("notional")),
                tick_size=_f(f.get("PRICE_FILTER", {}).get("tickSize")))
        return out

    def premium(self) -> dict[str, dict]:
        rows = self._request("GET", "/fapi/v1/premiumIndex")
        return {r["symbol"]: {"mark": float(r["markPrice"]), "index": float(r["indexPrice"]),
                              "funding_rate": float(r["lastFundingRate"] or 0), "next_funding": r["nextFundingTime"] / 1000}
                for r in rows}

    def tickers_24h(self) -> dict[str, dict]:
        rows = self._request("GET", "/fapi/v1/ticker/24hr")
        return {d["symbol"]: {"price": float(d["lastPrice"]), "change_pct": float(d["priceChangePercent"]) / 100,
                              "quote_volume": float(d["quoteVolume"])} for d in rows}

    def klines(self, symbol: str, interval: str = "1d", limit: int = 30) -> list[dict]:
        rows = self._request("GET", "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        return [{"t": r[0] / 1000, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                 "c": float(r[4]), "v": float(r[5]), "close_t": r[6] / 1000} for r in rows]

    # ---------------------------------------------------------------- signed, read
    def account(self) -> dict:
        return self._request("GET", "/fapi/v3/account", signed=True)

    def positions(self) -> list[dict]:
        """v2 positionRisk: still carries leverage and marginType (v3 dropped them). Non-zero only."""
        rows = self._request("GET", "/fapi/v2/positionRisk", signed=True)
        return [r for r in rows if float(r.get("positionAmt") or 0) != 0]

    def user_trades(self, symbol: str, start_ms: int) -> list[dict]:
        return self._request("GET", "/fapi/v1/userTrades", {"symbol": symbol, "startTime": start_ms, "limit": 1000}, signed=True)

    def dual_side(self) -> bool:
        return bool(self._request("GET", "/fapi/v1/positionSide/dual", signed=True).get("dualSidePosition"))

    def order_status(self, symbol: str, client_order_id: str) -> dict:
        return self._request("GET", "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_order_id}, signed=True)

    def open_algo_orders(self, symbol: str | None = None) -> list[dict]:
        r = self._request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol}, signed=True)
        return r if isinstance(r, list) else r.get("orders", [])

    # ---------------------------------------------------------------- signed, trading
    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED"):
        try:
            self._request("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type}, signed=True)
        except ExchangeError as e:
            if e.code != -4046:                     # "No need to change margin type"
                raise

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        return self._request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(leverage)}, signed=True)

    def market_order(self, symbol: str, side: str, qty: float, client_order_id: str, reduce_only: bool = False,
                     position_side: str | None = None) -> dict:
        """One-way mode: ``reduce_only`` closes. Hedge mode: pass ``position_side`` (LONG/SHORT); reduceOnly
        must not be sent there — an opposite-side order on the same positionSide can only reduce it."""
        return self._request("POST", "/fapi/v1/order", {
            "symbol": symbol, "side": side, "type": "MARKET", "quantity": _num(qty),
            "positionSide": position_side,
            "reduceOnly": "true" if reduce_only and not position_side else None,
            "newClientOrderId": client_order_id, "newOrderRespType": "RESULT"}, signed=True)

    def protect(self, symbol: str, side: str, kind: str, trigger: float, client_algo_id: str,
                position_side: str | None = None) -> dict:
        """Exchange-side exit for the whole position. kind: STOP_MARKET | TAKE_PROFIT_MARKET."""
        if kind not in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
            raise ExchangeError(f"unsupported protective order {kind}")
        return self._request("POST", "/fapi/v1/algoOrder", {
            "algoType": "CONDITIONAL", "symbol": symbol, "side": side, "type": kind,
            "triggerPrice": _num(trigger), "closePosition": "true", "workingType": "MARK_PRICE",
            "priceProtect": "true", "clientAlgoId": client_algo_id, "positionSide": position_side}, signed=True)

    def cancel_algo(self, symbol: str, algo_id) -> dict:
        return self._request("DELETE", "/fapi/v1/algoOrder", {"symbol": symbol, "algoId": algo_id}, signed=True)

    def cancel_all_algo(self, symbol: str) -> dict:
        return self._request("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol}, signed=True)


