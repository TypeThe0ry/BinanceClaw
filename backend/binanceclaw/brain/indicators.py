"""Technical summary per asset, computed in code so the model reads a few labelled numbers
instead of guessing trend and volatility from raw closes. Pure functions over kline dicts
({"o","h","l","c",...}, oldest first)."""
from __future__ import annotations


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
    if t1d and t4h:
        out["regime"] = ("uptrend" if t1d == t4h == "up" else "downtrend" if t1d == t4h == "down"
                         else "pullback" if t1d == "up" and t4h == "down"
                         else "rebound" if t1d == "down" and t4h == "up" else "range")
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
