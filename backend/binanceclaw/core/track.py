"""Track record of the AI's own trades: did the price move its way afterwards?

Every spot fill that came from a decision round (auto-executed or approved, not 手动分配
rebalancing) is scored at fixed horizons: a BUY wins when the price is higher 24 h / 7 d later,
a SELL wins when it is lower, in both cases by more than a round-trip fee. The price at the
horizon is read once from Binance's hourly candles and stored on the order, so the history is
fetched only once per order. Futures positions are scored on their realised P&L."""
from __future__ import annotations

import datetime as dt
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import select

from ..db import Cycle, FuturesPosition, Order
from ..exchange.binance_spot import ExchangeError

log = logging.getLogger("binanceclaw")

HORIZONS = {"24h": 86400, "7d": 7 * 86400}
FEE = 0.002              # a buy and a sell at ~0.1 % each: a "win" has to clear this
MAX_FETCH = 20           # horizon prices fetched per call; the rest wait for the next one


def _ret(side: str, entry: float, px: float) -> float:
    return px / entry - 1 if side == "BUY" else entry / px - 1


def _summary(rets: list[float]) -> dict:
    if not rets:
        return {"evaluated": 0, "wins": 0, "win_rate": None, "avg_return": None}
    wins = sum(1 for r in rets if r > FEE)
    return {"evaluated": len(rets), "wins": wins, "win_rate": round(wins / len(rets), 3),
            "avg_return": round(sum(rets) / len(rets), 4)}


class TrackRecord:
    def __init__(self, svc):
        self.svc = svc

    def _orders(self) -> list[Order]:
        s = self.svc.settings
        rows = self.svc.db.all(select(Order).where(Order.mode == s.mode, Order.status.in_(("filled", "submitted")),
                                                   Order.via.in_(("auto", "approved")), Order.cycle_id != "")
                               .order_by(Order.ts))
        rebal: dict[str, set] = {}
        for cid in {o.cycle_id for o in rows}:
            c = self.svc.db.get(Cycle, cid)
            rebal[cid] = {r.get("symbol") for r in (c.risk or []) if r.get("source") == "rebalance"} if c else set()
        return [o for o in rows if o.price > 0 and o.symbol not in rebal.get(o.cycle_id, set())]

    def _horizon_price(self, o: Order, secs: int) -> float | None:
        try:
            k = self.svc.client.klines(o.symbol, "1h", 1, start=o.ts + secs)
        except ExchangeError as e:
            log.info("horizon price for %s unavailable: %s", o.symbol, e)
            return None
        if not k or abs(k[0]["t"] - (o.ts + secs)) > 2 * 3600:
            return None
        return k[0]["o"]

    def _fill_outcomes(self, orders: list[Order]):
        """Store the price at each horizon that has passed and is not stored yet."""
        now = time.time()
        todo = [(o, h, secs) for o in orders for h, secs in HORIZONS.items()
                if o.ts + secs <= now and h not in ((o.detail or {}).get("outcome") or {})][:MAX_FETCH]
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=min(8, len(todo))) as ex:
            got = list(ex.map(lambda t: self._horizon_price(t[0], t[2]), todo))
        by_order: dict[str, dict] = {}
        for (o, h, _), px in zip(todo, got, strict=True):
            if px:
                by_order.setdefault(o.id, dict((o.detail or {}).get("outcome") or {}))[h] = px
        for o in orders:
            if o.id in by_order:
                o.detail = {**(o.detail or {}), "outcome": by_order[o.id]}
                self.svc.db.update(Order, o.id, detail=o.detail)

    def compute(self, prices: dict[str, float] | None = None, recent: int = 10) -> dict:
        s = self.svc.settings
        q = s.quote
        orders = self._orders()
        self._fill_outcomes(orders)
        toks = {o.symbol[: -len(q)] for o in orders[-recent:]}
        px = {**self.svc.asset_prices(toks - set(prices or {})), **(prices or {})} if toks else {}
        rets: dict[str, list[float]] = {h: [] for h in HORIZONS}
        side_rets: dict[str, list[float]] = {"BUY": [], "SELL": []}
        for o in orders:
            out = (o.detail or {}).get("outcome") or {}
            for h in HORIZONS:
                if out.get(h):
                    r = _ret(o.side, o.price, out[h])
                    rets[h].append(r)
                    if h == "24h":
                        side_rets[o.side].append(r)
        rows = []
        for o in reversed(orders[-recent:]):
            out = (o.detail or {}).get("outcome") or {}
            tok = o.symbol[: -len(q)]
            row = {"time": dt.datetime.fromtimestamp(o.ts, self.svc.tz()).strftime("%m-%d %H:%M"), "side": o.side,
                   "symbol": tok, "usd": round(o.quote_usd, 2), "entry": o.price}
            for h in HORIZONS:
                if out.get(h):
                    row[f"ret_{h}"] = round(_ret(o.side, o.price, out[h]), 4)
            if px.get(tok):
                row["ret_now"] = round(_ret(o.side, o.price, px[tok]), 4)
            rows.append(row)
        closed = self.svc.db.all(select(FuturesPosition).where(FuturesPosition.mode == s.mode,
                                                               FuturesPosition.status == "closed"))
        fw = sum(1 for p in closed if p.realized > 0)
        return {
            "rule": f"BUY 之后价格上涨、SELL 之后价格下跌，且幅度超过 {FEE:.1%} 往返手续费，算作胜",
            "trades": len(orders),
            "pending_24h": sum(1 for o in orders if not ((o.detail or {}).get("outcome") or {}).get("24h")),
            **{h: _summary(r) for h, r in rets.items()},
            "by_side_24h": {k: _summary(v) for k, v in side_rets.items()},
            "futures": {"trades": len(closed), "wins": fw, "win_rate": round(fw / len(closed), 3) if closed else None,
                        "realized": round(sum(p.realized for p in closed), 2)},
            "recent": rows,
        }

    def for_prompt(self, prices: dict[str, float]) -> dict | None:
        """The compact version the model sees each round; None before the first AI trade."""
        t = self.compute(prices, recent=8)
        if not t["trades"] and not t["futures"]["trades"]:
            return None
        out = {k: t[k] for k in ("rule", "trades", "pending_24h", "24h", "7d", "by_side_24h", "recent")}
        if t["futures"]["trades"]:
            out["futures"] = t["futures"]
        return out
