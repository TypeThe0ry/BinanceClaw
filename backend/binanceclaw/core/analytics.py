"""Performance analytics of the account in the current mode: the equity curve's risk-adjusted
stats plus BinanceClaw's closed trades, compared with holding BTC over the same window."""
from __future__ import annotations

import logging
import time

from sqlmodel import select

from ..db import EquitySnapshot, FuturesPosition, Order
from ..exchange.binance_spot import ExchangeError
from . import metrics as m

log = logging.getLogger("binanceclaw")

RANGES = {"7d": 7 * m.DAY, "30d": 30 * m.DAY, "90d": 90 * m.DAY, "1y": 365 * m.DAY, "all": None}
MAX_DD_POINTS = 240


class Analytics:
    def __init__(self, svc):
        self.svc = svc
        self.db = svc.db

    def _equity(self, mode: str, since: float | None) -> list[tuple[float, float]]:
        stmt = select(EquitySnapshot.ts, EquitySnapshot.total).where(EquitySnapshot.mode == mode)
        if since:
            stmt = stmt.where(EquitySnapshot.ts >= since)
        return [(t, v) for t, v in self.db.all(stmt.order_by(EquitySnapshot.ts))]

    def _trades(self, mode: str, since: float | None) -> tuple[list[dict], list[dict]]:
        orders = self.db.all(select(Order).where(Order.mode == mode, Order.status.in_(("filled", "submitted")))
                             .order_by(Order.ts))
        # cost basis needs the buys from before the window too; only the sells inside it count
        spot = [t for t in m.spot_round_trips(orders) if not since or t["ts"] >= since]
        q = self.svc.settings.quote
        for t in spot:
            t["symbol"] = t["symbol"][: -len(q)] if t["symbol"].endswith(q) else t["symbol"]
        stmt = select(FuturesPosition).where(FuturesPosition.mode == mode, FuturesPosition.status == "closed")
        fut = [{"ts": p.closed or p.opened, "symbol": p.symbol, "side": p.side, "pnl": p.realized,
                "ret": p.realized / p.margin if p.margin else 0.0, "reason": p.close_reason}
               for p in self.db.all(stmt) if not since or (p.closed or p.opened) >= since]
        return spot, sorted(fut, key=lambda x: x["ts"])

    def _btc_return(self, start: float, end: float) -> float | None:
        hourly = end - start < 3 * m.DAY
        step = 3600 if hourly else m.DAY
        try:
            k = self.svc._klines(f"BTC{self.svc.settings.quote}", "1h" if hourly else "1d",
                                       min(int((end - start) // step) + 2, 1000))
            px, *_ = self.svc.market_snapshot()
        except ExchangeError as e:
            log.info("BTC benchmark unavailable: %s", e)
            return None
        # the candle the window starts in
        first = next((c for c in k if c["t"] + step > start), None)
        now = px.get("BTC")
        return round(now / first["o"] - 1, 5) if first and now and first["o"] else None

    def report(self, rng: str = "30d", now: float | None = None) -> dict:
        if rng not in RANGES:
            raise ValueError(f"unknown range {rng!r}; use one of {', '.join(RANGES)}")
        now = now or time.time()
        mode = self.svc.settings.mode
        since = now - RANGES[rng] if RANGES[rng] else None
        pts = self._equity(mode, since)
        eq = m.equity_stats(pts)
        dd = eq.pop("drawdown")
        if len(dd) > MAX_DD_POINTS:          # the chart needs the shape, not every snapshot
            stride = len(dd) / MAX_DD_POINTS
            dd = [dd[int(i * stride)] for i in range(MAX_DD_POINTS)] + [dd[-1]]
        spot, fut = self._trades(mode, since)
        allt = sorted(spot + fut, key=lambda x: x["ts"])
        btc = self._btc_return(pts[0][0], now) if eq["points"] >= 2 else None
        return {
            "range": rng, "mode": mode, "equity": eq, "drawdown": dd,
            "trades": m.trade_stats([t["pnl"] for t in allt], [t["ret"] for t in allt]),
            "spot": m.trade_stats([t["pnl"] for t in spot], [t["ret"] for t in spot]),
            "futures": m.trade_stats([t["pnl"] for t in fut], [t["ret"] for t in fut]),
            "by_symbol": _by_symbol(allt),
            "benchmark": {"name": "BTC", "return": btc,
                          "excess": round(eq["total_return"] - btc, 5) if btc is not None and eq["total_return"] is not None else None},
        }

    def for_prompt(self) -> dict | None:
        """The 30-day numbers the model sees each round, so it can tell a drawdown from noise."""
        r = self.report("30d")
        e, t = r["equity"], r["trades"]
        if e["points"] < 2 and not t["trades"]:
            return None
        out = {k: e[k] for k in ("span_days", "total_return", "max_drawdown", "sharpe", "volatility") if e.get(k) is not None}
        if r["benchmark"]["return"] is not None:
            out["btc_return"] = r["benchmark"]["return"]
        if t["trades"]:
            out["closed_trades"] = {k: t[k] for k in ("trades", "win_rate", "profit_factor", "payoff", "max_loss_streak")}
        return out


def _by_symbol(trades: list[dict]) -> list[dict]:
    g: dict[str, list[float]] = {}
    for t in trades:
        g.setdefault(t["symbol"], []).append(t["pnl"])
    rows = [{"symbol": s, "trades": len(p), "wins": sum(1 for x in p if x > 0), "pnl": round(sum(p), 4)} for s, p in g.items()]
    return sorted(rows, key=lambda r: r["pnl"], reverse=True)
