"""Risk-adjusted performance: return, volatility, Sharpe / Sortino / Calmar, drawdowns, and
per-trade statistics (win rate, profit factor, expectancy, streaks).

Pure functions over plain lists, shared by the live account (equity snapshots + BinanceClaw's own
fills) and the backtest lab. Equity series are resampled to one point per period before computing
returns, so a burst of snapshots does not weigh more than a quiet stretch."""
from __future__ import annotations

import math

DAY = 86400
YEAR = 365 * DAY


def resample(points: list[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    """Last value of each ``step``-wide bucket, oldest first. ``points`` are (ts, value)."""
    out: dict[int, tuple[float, float]] = {}
    for t, v in sorted(points):
        if v is not None and v > 0:
            out[int(t // step)] = (t, v)
    return [out[k] for k in sorted(out)]


def _step_for(span: float) -> float:
    # daily returns once there are a few weeks of history; hourly before that, so a young account still gets numbers
    return DAY if span >= 14 * DAY else 3600


def drawdowns(points: list[tuple[float, float]]) -> tuple[float, float, list[dict]]:
    """(max drawdown as a positive fraction, longest time under water in seconds, drawdown series)."""
    peak, peak_t, worst, longest = 0.0, None, 0.0, 0.0
    series = []
    for t, v in points:
        if v >= peak:
            if peak_t is not None and peak > 0:
                longest = max(longest, t - peak_t)
            peak, peak_t = v, t
        dd = 1 - v / peak if peak > 0 else 0.0
        worst = max(worst, dd)
        series.append({"t": t, "dd": round(dd, 5)})
    if points and peak_t is not None and points[-1][1] < peak:
        longest = max(longest, points[-1][0] - peak_t)
    return worst, longest, series


def equity_stats(points: list[tuple[float, float]], risk_free: float = 0.0) -> dict:
    """Stats of an equity curve. Ratios are annualised; None when there is too little history."""
    pts = sorted((t, v) for t, v in points if v is not None and v > 0)
    if len(pts) < 2:
        return {"points": len(pts), "start": None, "end": None, "total_return": None, "cagr": None,
                "volatility": None, "sharpe": None, "sortino": None, "max_drawdown": None,
                "max_drawdown_days": None, "calmar": None, "best_period": None, "worst_period": None,
                "period": None, "drawdown": []}
    span = pts[-1][0] - pts[0][0]
    step = _step_for(span)
    rs = resample(pts, step)
    if rs[0] != pts[0]:
        rs.insert(0, pts[0])
    rets = [b / a - 1 for (_, a), (_, b) in zip(rs, rs[1:], strict=False)]
    per_year = YEAR / step
    total = pts[-1][1] / pts[0][1] - 1
    # CAGR from a few days of history extrapolates wildly: only from a month on
    cagr = (1 + total) ** (YEAR / span) - 1 if span >= 30 * DAY and total > -1 else None
    vol = sharpe = sortino = None
    if len(rets) >= 5:
        mean = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
        rf = risk_free / per_year
        vol = sd * math.sqrt(per_year)
        if sd > 1e-9:
            sharpe = (mean - rf) / sd * math.sqrt(per_year)
        down = [min(r - rf, 0) for r in rets]
        dsd = math.sqrt(sum(d * d for d in down) / len(down))
        if dsd > 1e-9:
            sortino = (mean - rf) / dsd * math.sqrt(per_year)
    mdd, under, series = drawdowns(pts)
    ann = cagr if cagr is not None else (total * YEAR / span if span > 0 else None)
    calmar = ann / mdd if ann is not None and mdd > 0.001 and span >= 30 * DAY else None
    return {
        "points": len(pts), "start": pts[0][1], "end": pts[-1][1], "span_days": round(span / DAY, 2),
        "total_return": round(total, 5), "cagr": _r(cagr), "volatility": _r(vol), "sharpe": _r(sharpe, 2),
        "sortino": _r(sortino, 2), "max_drawdown": round(mdd, 5), "max_drawdown_days": round(under / DAY, 2),
        "calmar": _r(calmar, 2), "best_period": _r(max(rets) if rets else None),
        "worst_period": _r(min(rets) if rets else None), "period": "1d" if step == DAY else "1h",
        "drawdown": series,
    }


def trade_stats(pnls: list[float], returns: list[float] | None = None) -> dict:
    """Statistics of closed trades given their P&L in USD (and optionally their return in %)."""
    n = len(pnls)
    if not n:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": None, "profit_factor": None, "expectancy": None,
                "avg_win": None, "avg_loss": None, "payoff": None, "best": None, "worst": None,
                "max_win_streak": 0, "max_loss_streak": 0, "net": 0.0, "avg_return": None}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gw, gl = sum(wins), -sum(losses)
    streak = {"w": 0, "l": 0}
    best = {"w": 0, "l": 0}
    for p in pnls:
        k = "w" if p > 0 else "l" if p < 0 else None
        for x in ("w", "l"):
            streak[x] = streak[x] + 1 if x == k else 0
            best[x] = max(best[x], streak[x])
    avg_w = gw / len(wins) if wins else None
    avg_l = gl / len(losses) if losses else None
    return {
        "trades": n, "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / n, 4),
        "profit_factor": round(gw / gl, 3) if gl > 0 else None, "expectancy": round(sum(pnls) / n, 4),
        "avg_win": _r(avg_w, 4), "avg_loss": _r(avg_l, 4),
        "payoff": round(avg_w / avg_l, 3) if avg_w and avg_l else None,
        "best": round(max(pnls), 4), "worst": round(min(pnls), 4),
        "max_win_streak": best["w"], "max_loss_streak": best["l"], "net": round(sum(pnls), 4),
        "avg_return": round(sum(returns) / len(returns), 5) if returns else None,
    }


def spot_round_trips(orders) -> list[dict]:
    """Closed spot trades by the average-cost method: every SELL that reduces a position BinanceClaw
    bought is one closed trade, with P&L against the average cost at that moment. ``orders`` are
    objects or dicts with side, symbol, qty, quote_usd, ts — oldest first."""
    pos: dict[str, list[float]] = {}
    out = []
    for o in orders:
        d = o if isinstance(o, dict) else {k: getattr(o, k) for k in ("ts", "symbol", "side", "qty", "quote_usd")}
        sym, qty, usd = d["symbol"], float(d["qty"] or 0), float(d["quote_usd"] or 0)
        p = pos.setdefault(sym, [0.0, 0.0])        # qty, cost
        if d["side"] == "BUY":
            p[0] += qty
            p[1] += usd
        elif p[0] > 1e-12 and qty > 0:
            q = min(qty, p[0])
            avg = p[1] / p[0]
            proceeds = usd * q / qty
            pnl = proceeds - avg * q
            out.append({"ts": d["ts"], "symbol": sym, "qty": q, "cost": avg * q, "pnl": pnl,
                        "ret": pnl / (avg * q) if avg > 0 else 0.0})
            p[0] -= q
            p[1] -= avg * q
    return out


def _r(x, nd: int = 5):
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(x, nd)
