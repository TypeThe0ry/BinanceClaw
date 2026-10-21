"""Technical summary per asset, computed in code so the model reads a few labelled numbers
instead of guessing trend and volatility from raw closes. Pure functions over kline dicts
({"o","h","l","c",...}, oldest first)."""
from __future__ import annotations

import time


def sma(xs: list[float], n: int) -> float | None:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def ema(xs: list[float], n: int) -> float | None:
    if len(xs) < n:
        return None
    k, e = 2 / (n + 1), sum(xs[:n]) / n
    for x in xs[n:]:
        e = x * k + e * (1 - k)
    return e


def rsi(xs: list[float], n: int = 14) -> float | None:
    """Wilder's RSI."""
    if len(xs) <= n:
        return None
    d = [b - a for a, b in zip(xs, xs[1:], strict=False)]
    up = sum(max(x, 0) for x in d[:n]) / n
    dn = sum(max(-x, 0) for x in d[:n]) / n
    for x in d[n:]:
        up = (up * (n - 1) + max(x, 0)) / n
        dn = (dn * (n - 1) + max(-x, 0)) / n
    if dn == 0:
        return 100.0 if up > 0 else 50.0
    return 100 - 100 / (1 + up / dn)


def atr(k: list[dict], n: int = 14) -> float | None:
    if len(k) <= n:
        return None
    tr = [max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"])) for a, b in zip(k, k[1:], strict=False)]
    a = sum(tr[:n]) / n
    for x in tr[n:]:
        a = (a * (n - 1) + x) / n
    return a


def ema_series(xs: list[float], n: int) -> list[float]:
    """EMA at every bar from the n-th on (seeded with the first n-bar SMA)."""
    if len(xs) < n:
        return []
    k, e = 2 / (n + 1), sum(xs[:n]) / n
    out = [e]
    for x in xs[n:]:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def macd(xs: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> list[tuple[float, float, float]]:
    """(macd, signal, histogram) for every bar where all three exist, oldest first."""
    f, s = ema_series(xs, fast), ema_series(xs, slow)
    line = [a - b for a, b in zip(f[slow - fast:], s, strict=False)]
    sig = ema_series(line, signal)
    return [(m, g, m - g) for m, g in zip(line[signal - 1:], sig, strict=False)]


def bollinger(xs: list[float], n: int = 20, k: float = 2.0) -> tuple[float, float, float] | None:
    """(middle, upper, lower) with a population standard deviation over the last n closes."""
    if len(xs) < n:
        return None
    w = xs[-n:]
    mid = sum(w) / n
    sd = (sum((x - mid) ** 2 for x in w) / n) ** 0.5
    return mid, mid + k * sd, mid - k * sd


def closed(k: list[dict], now: float | None = None) -> list[dict]:
    """Drop the still-forming last candle (its volume is only part of the period)."""
    now = time.time() if now is None else now
    return k[:-1] if k and k[-1].get("close_t", 0) > now else k


def volume_ratio(k: list[dict], n: int = 20) -> float | None:
    """Last closed candle's volume over the average of the n before it."""
    k = closed(k)
    if len(k) <= n:
        return None
    base = sum(x.get("v", 0) for x in k[-n - 1:-1]) / n
    return k[-1].get("v", 0) / base if base > 0 else None


def _macd_read(xs: list[float], price: float) -> dict | None:
    """A golden / death cross on the latest bar, else which side of zero the MACD line is on;
    the histogram as a share of price and whether it is growing."""
    m = macd(xs)
    if len(m) < 2 or not price:
        return None
    eps = price * 1e-9                                  # float noise around zero is not a cross
    h0, h1 = m[-2][2], m[-1][2]
    state = ("bull_cross" if h0 <= eps < h1 else "bear_cross" if h0 >= -eps > h1
             else "bull" if m[-1][0] > 0 else "bear")
    return {"state": state, "hist_pct": round(h1 / price, 5), "rising": h1 > h0}


def _trend(price: float, fast: float | None, slow: float | None) -> str | None:
    if fast is None or slow is None:
        return None
    if price > fast > slow:
        return "up"
    if price < fast < slow:
        return "down"
    return "flat"


def summarize(price: float, daily: list[dict], h4: list[dict]) -> dict:
    """Multi-timeframe read for one asset. Keys are omitted when there is too little history."""
    dc = [x["c"] for x in daily]
    hc = [x["c"] for x in h4]
    out: dict = {}
    t1d = _trend(price, sma(dc, 20), sma(dc, 50) or sma(dc, 30))
    t4h = _trend(price, ema(hc, 12), ema(hc, 26))
    if t1d:
        out["trend_1d"] = t1d
    if t4h:
        out["trend_4h"] = t4h
    r1, r4 = rsi(dc), rsi(hc)
    if r1 is not None:
        out["rsi14_1d"] = round(r1, 1)
    if r4 is not None:
        out["rsi14_4h"] = round(r4, 1)
    a = atr(daily)
    if a and price:
        out["atr14_pct"] = round(a / price, 4)      # typical daily range, a volatility yardstick
    if len(hc) >= 7:
        out["change_24h"] = round(price / hc[-7] - 1, 4)
    s20 = sma(dc, 20)
    if s20:
        out["vs_sma20"] = round(price / s20 - 1, 4)
    for tf, xs in (("1d", dc), ("4h", hc)):
        m = _macd_read(xs, price)
        if m:
            out[f"macd_{tf}"] = m
    bb = bollinger(dc)
    if bb and bb[1] > bb[2]:
        out["bb_1d"] = {"pct_b": round((price - bb[2]) / (bb[1] - bb[2]), 2),   # 0 = lower band, 1 = upper
                        "width": round((bb[1] - bb[2]) / bb[0], 4)}
    for tf, k in (("1d", daily), ("4h", h4)):
        v = volume_ratio(k)
        if v is not None:
            out[f"vol_ratio_{tf}"] = round(v, 2)
    if t1d and t4h:
        out["regime"] = ("uptrend" if t1d == t4h == "up" else "downtrend" if t1d == t4h == "down"
                         else "pullback" if t1d == "up" and t4h == "down"
                         else "rebound" if t1d == "down" and t4h == "up" else "range")
    return out


def derivatives(oi: list[dict], funding: list[dict]) -> dict:
    """Perp positioning: open-interest change (hourly history, ``{"t","oi","oi_usd"}``) and recent funding
    (``{"t","rate"}``). Rising OI with price = new money behind the move; persistently positive funding =
    crowded longs."""
    out: dict = {}
    oi = sorted(oi, key=lambda x: x["t"])
    if oi:
        out["oi_usd"] = round(oi[-1]["oi_usd"], 0)
        for label, h in (("4h", 4), ("24h", 24)):
            if len(oi) > h and oi[-1 - h]["oi"] > 0:
                out[f"oi_change_{label}"] = round(oi[-1]["oi"] / oi[-1 - h]["oi"] - 1, 4)
    rates = [x["rate"] for x in sorted(funding, key=lambda x: x["t"])]
    if rates:
        last = rates[-3:]                                  # 3 settlements = 24 h on the usual 8 h schedule
        out["funding_avg_3"] = round(sum(last) / len(last), 6)
        out["funding_positive_streak"] = next((i for i, r in enumerate(reversed(rates)) if r <= 0), len(rates))
    return out


def breadth(market: dict) -> dict | None:
    """Market-wide read across the round's assets: how many are in a daily uptrend, and BTC's regime."""
    rows = [v for v in market.values() if isinstance(v, dict) and v.get("trend_1d")]
    if not rows:
        return None
    up = sum(1 for v in rows if v["trend_1d"] == "up")
    down = sum(1 for v in rows if v["trend_1d"] == "down")
    btc = market.get("BTC") or {}
    return {"assets": len(rows), "uptrend_share": round(up / len(rows), 2), "downtrend_share": round(down / len(rows), 2),
            "btc_regime": btc.get("regime"), "btc_rsi14_1d": btc.get("rsi14_1d")}
