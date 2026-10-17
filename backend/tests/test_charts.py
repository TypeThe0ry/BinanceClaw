import time

from binanceclaw.notify import charts


def _kl(n=60, step=14400):
    now = time.time()
    out = []
    p = 80000.0
    for i in range(n):
        o = p
        p = p * (1.01 if i % 3 else 0.985)
        out.append({"t": now - (n - i) * step, "o": o, "h": max(o, p) * 1.004, "l": min(o, p) * 0.996, "c": p, "v": 10 + i})
    return out


def test_charts_render_png():
    kl = _kl()
    png = charts.candles("BTCUSDT", kl, [{"t": kl[10]["t"], "side": "BUY", "label": "$200"},
                                         {"t": time.time(), "side": "BUY", "label": "$600?", "pending": True}])
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000
    assert charts.equity([{"t": k["t"], "total": k["c"]} for k in kl])[:4] == b"\x89PNG"
    assert charts.equity([])[:4] == b"\x89PNG"
    from binanceclaw.config import Settings
    s = Settings()
    tg = {n: {"tokens": c.tokens, "band": list(c.band)} for n, c in s.categories.items()}
    assert charts.allocation({"BTC": 0.02, "STABLE": 0.96}, tg)[:4] == b"\x89PNG"
