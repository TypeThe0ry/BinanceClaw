"""Account valuation history: periodic snapshots of the account total, plus a backfill from
Binance's daily account snapshots, so the home page can show the change over 1D / 7D / 30D / 90D / 1Y."""
from __future__ import annotations

import logging
import time

from sqlmodel import delete, desc, func, select

from ..db import Cycle, EquitySnapshot
from ..exchange.binance_spot import ExchangeError

log = logging.getLogger("binanceclaw")

DAY = 86400
RANGES = {"1d": DAY, "7d": 7 * DAY, "30d": 30 * DAY, "90d": 90 * DAY, "1y": 365 * DAY}
RECORD_EVERY = 900           # seconds between periodic snapshots
MAX_POINTS = 240             # per chart; older detail is bucketed away


class Valuation:
    def __init__(self, svc):
        self.svc = svc
        self.db = svc.db

    # ------------------------------------------------------------------ recording
    def record(self) -> EquitySnapshot | None:
        """Store the current account total (what the home page headline shows)."""
        try:
            prices, *_ = self.svc.market_snapshot()
            total = self.svc.account_portfolio(prices).total()
        except ExchangeError as e:
            log.info("valuation snapshot skipped: %s", e)
            return None
        if total <= 0:
            return None
        return self.db.add(EquitySnapshot(mode=self.svc.settings.mode, total=round(total, 2)))

    def import_cycles(self):
        """One-off: seed the history from decision rounds recorded before snapshots existed."""
        if self.db.kv_get("equity_cycles_imported"):
            return
        rows = []
        for c in self.db.all(select(Cycle).order_by(Cycle.ts)):
            ctx = c.context or {}
            total = ctx.get("account_total_usd", (ctx.get("portfolio") or {}).get("total_usd"))
            if total:
                rows.append(EquitySnapshot(ts=c.ts, mode=c.mode, total=float(total), source="cycle"))
        with self.db.write_lock, self.db.session() as s:
            s.add_all(rows)
            s.commit()
        self.db.kv_set("equity_cycles_imported", True)

    def backfill(self) -> int:
        """Live mode: fill the days before our own history from Binance's daily spot snapshots
        (Binance keeps about a month of them). Values are in BTC; converted at that day's close."""
        now = time.time()
        if self.svc.settings.mode != "live" or now - self.db.kv_get("equity_backfill_at", 0) < 12 * 3600:
            return 0
        self.db.kv_set("equity_backfill_at", now)
        first = self.db.all(select(func.min(EquitySnapshot.ts)).where(EquitySnapshot.mode == "live",
                                                                      EquitySnapshot.source != "binance"))[0]
        have = {int(t // DAY) for t in self.db.all(select(EquitySnapshot.ts).where(
            EquitySnapshot.mode == "live", EquitySnapshot.source == "binance"))}
        end = min(first or now, now)
        start = max(now - 30 * DAY + 3600, end - 30 * DAY + 3600)
        if end - start < DAY:
            return 0
        try:
            snaps = self.svc.client.account_snapshot(start, end)
            closes = {int(k["t"] // DAY): k["c"] for k in self.svc.client.klines(
                f"BTC{self.svc.settings.quote}", "1d", 40)}
        except ExchangeError as e:
            log.info("valuation backfill skipped: %s", e)
            return 0
        rows = []
        for v in snaps:
            ts = v.get("updateTime", 0) / 1000
            day = int(ts // DAY)
            btc = float((v.get("data") or {}).get("totalAssetOfBtc") or 0)
            if ts >= end or day in have or btc <= 0 or day not in closes:
                continue
            rows.append(EquitySnapshot(ts=ts, mode="live", total=round(btc * closes[day], 2), source="binance"))
            have.add(day)
        if rows:
            with self.db.write_lock, self.db.session() as s:
                s.add_all(rows)
                s.commit()
        return len(rows)

    def clear(self, mode: str):
        with self.db.write_lock, self.db.session() as s:
            s.exec(delete(EquitySnapshot).where(EquitySnapshot.mode == mode))
            s.commit()

    # ------------------------------------------------------------------ reading
    def history(self, rng: str = "7d", now: float | None = None) -> dict:
        """Points for one range plus, for every range, the value it started from.

        ``base`` of a range is the last snapshot at or before its start; when there is none the
        earliest snapshot inside it is used and ``full`` is false (the range is only partly
        covered). A range with no snapshot at all has ``base`` None ("insufficient data")."""
        if rng not in RANGES:
            raise ValueError(f"unknown range {rng!r}; use one of {', '.join(RANGES)}")
        now = now or time.time()
        mode = self.svc.settings.mode
        span = RANGES[rng]
        base_of = {k: self._base(mode, now - s, s) for k, s in RANGES.items()}
        start = now - span
        rows = self.db.all(select(EquitySnapshot.ts, EquitySnapshot.total)
                           .where(EquitySnapshot.mode == mode, EquitySnapshot.ts > start).order_by(EquitySnapshot.ts))
        pts = _bucket([(t, v) for t, v in rows], start, now, MAX_POINTS)
        b = base_of[rng]
        if b and (not pts or b["t"] < pts[0]["t"]):
            pts.insert(0, {"t": b["t"], "total": b["total"]})
        return {"range": rng, "mode": mode, "start": start, "now": now, "points": pts, "bases": base_of}

    def _base(self, mode: str, start: float, span: float) -> dict | None:
        # a snapshot older than the range start by more than a quarter range would misstate the change
        before = self.db.all(select(EquitySnapshot).where(EquitySnapshot.mode == mode, EquitySnapshot.ts <= start,
                                                          EquitySnapshot.ts >= start - max(span / 4, 2 * RECORD_EVERY))
                             .order_by(desc(EquitySnapshot.ts)).limit(1))
        if before:
            return {"t": before[0].ts, "total": before[0].total, "full": True}
        after = self.db.all(select(EquitySnapshot).where(EquitySnapshot.mode == mode, EquitySnapshot.ts > start)
                            .order_by(EquitySnapshot.ts).limit(1))
        if not after:
            return None
        # within a recording interval (or 5 % of the range) of the start still counts as covering it
        full = after[0].ts - start <= max(span * 0.05, 2 * RECORD_EVERY)
        return {"t": after[0].ts, "total": after[0].total, "full": full}


def _bucket(rows: list[tuple[float, float]], start: float, end: float, n: int) -> list[dict]:
    """Keep at most ``n`` points: the last snapshot of each equal time bucket."""
    if len(rows) <= n:
        return [{"t": t, "total": v} for t, v in rows]
    width = (end - start) / n
    out: dict[int, tuple[float, float]] = {}
    for t, v in rows:
        out[min(int((t - start) // width), n - 1)] = (t, v)
    return [{"t": t, "total": v} for t, v in out.values()]
