"""PNG charts for Telegram (matplotlib, headless). Labels are ASCII so no CJK font is needed.

Palette follows the dashboard: pure black, hairline grid, green up / red down,
with hollow vs filled candles so up/down never depends on colour alone.
"""
from __future__ import annotations

import datetime as dt
import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

BG = "#000000"
GRID = "#1C1C1C"
TEXT = "#8C8C8C"
INK = "#F2F2F2"
UP = "#20B26C"
DOWN = "#EF454A"
PENDING = "#F0B90B"
CAT = {"BTC": "#F7931A", "ETH": "#8C8CFF", "US_STOCK": "#20B26C", "STABLE": "#8C8C8C", "OTHER": "#444444"}


def _fig(w=8, h=4.2):
    fig = plt.figure(figsize=(w, h), dpi=150, facecolor=BG)
    return fig


def _style(ax):
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=TEXT, labelsize=8, length=0)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.yaxis.tick_right()


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return buf.getvalue()


def candles(symbol: str, klines: list[dict], markers: list[dict] | None = None, title: str = "",
            interval_seconds: float | None = None, level: float | None = None) -> bytes:
    """markers: [{"t": ts, "side": "BUY"|"SELL", "label": str, "pending": bool}]"""
    fig = _fig()
    gs = fig.add_gridspec(4, 1, hspace=0.05)
    ax = fig.add_subplot(gs[:3, 0])
    axv = fig.add_subplot(gs[3, 0], sharex=ax)
    _style(ax)
    _style(axv)
    if not klines:
        return _png(fig)
    step = interval_seconds or (klines[1]["t"] - klines[0]["t"] if len(klines) > 1 else 3600)
    w = step * 0.62
    for k in klines:
        up = k["c"] >= k["o"]
        col = UP if up else DOWN
        ax.vlines(k["t"], k["l"], k["h"], color=col, linewidth=0.8)
        body_lo, body_hi = sorted((k["o"], k["c"]))
        ax.add_patch(Rectangle((k["t"] - w / 2, body_lo), w, max(body_hi - body_lo, (k["h"] - k["l"]) * 0.002 or 1e-9),
                               facecolor=BG if up else col, edgecolor=col, linewidth=0.8))
        axv.bar(k["t"], k["v"], width=w, color=col, alpha=0.4)
    lo = min(k["l"] for k in klines)
    hi = max(k["h"] for k in klines)
    pad = (hi - lo) * 0.08
    ax.set_ylim(lo - pad, hi + pad * 1.6)
    last = klines[-1]["c"]
    ax.axhline(last, color=TEXT, linewidth=0.6, linestyle=(0, (2, 3)))
    ax.text(1.0, last, f" {last:,.2f}", transform=ax.get_yaxis_transform(), color=BG, fontsize=7.5,
            va="center", ha="left", bbox={"boxstyle": "square,pad=0.25", "fc": INK, "ec": INK})
    if level:
        ax.axhline(level, color=DOWN, linewidth=0.7, linestyle=(0, (4, 3)), alpha=0.8)
    for m in markers or []:
        t = min(max(m["t"], klines[0]["t"]), klines[-1]["t"])
        buy = m.get("side", "BUY") == "BUY"
        col = PENDING if m.get("pending") else (UP if buy else DOWN)
        near = min(klines, key=lambda k, t=t: abs(k["t"] - t))
        y = near["l"] - pad * 0.35 if buy else near["h"] + pad * 0.35
        ax.scatter([t], [y], marker="^" if buy else "v", s=70, color=col, zorder=5, edgecolors=BG, linewidths=0.6)
        if m.get("label"):
            ax.annotate(m["label"], (t, y), xytext=(0, -14 if buy else 10), textcoords="offset points",
                        ha="center", color=col, fontsize=7.5, fontweight="bold")
    ax.set_xlim(klines[0]["t"] - step, klines[-1]["t"] + step * 3)
    ticks = klines[:: max(1, len(klines) // 6)]
    axv.set_xticks([k["t"] for k in ticks])
    fmt = "%m-%d" if step >= 86400 else "%m-%d %H:%M"
    axv.set_xticklabels([dt.datetime.fromtimestamp(k["t"]).strftime(fmt) for k in ticks])
    plt.setp(ax.get_xticklabels(), visible=False)
    axv.set_yticks([])
    first = klines[0]["o"]
    chg = last / first - 1 if first else 0
    ax.set_title(f"{symbol}   {last:,.2f}   {chg:+.2%}" + (f"   {title}" if title else ""),
                 color=INK, fontsize=10, loc="left", fontweight="bold", pad=8)
    return _png(fig)


def equity(points: list[dict], title: str = "Portfolio") -> bytes:
    fig = _fig(8, 3.2)
    ax = fig.add_subplot(111)
    _style(ax)
    pts = [p for p in points if p.get("total") is not None]
    if len(pts) >= 2:
        xs = [p["t"] for p in pts]
        ys = [p["total"] for p in pts]
        col = UP if ys[-1] >= ys[0] else DOWN
        ax.plot(xs, ys, color=col, linewidth=1.6)
        ax.fill_between(xs, ys, min(ys), color=col, alpha=0.08)
        ticks = xs[:: max(1, len(xs) // 5)]
        ax.set_xticks(ticks)
        ax.set_xticklabels([dt.datetime.fromtimestamp(t).strftime("%m-%d") for t in ticks])
        ax.set_title(f"{title}   ${ys[-1]:,.2f}   {ys[-1] / ys[0] - 1:+.2%}", color=INK, fontsize=10,
                     loc="left", fontweight="bold", pad=8)
    else:
        ax.text(0.5, 0.5, "not enough history yet", color=TEXT, ha="center", transform=ax.transAxes)
    return _png(fig)


def allocation(weights: dict[str, float], targets: dict[str, dict]) -> bytes:
    """Horizontal bars: actual weight vs target band per category (labelled with %)."""
    cats = list(targets)
    fig = _fig(8, 0.55 * len(cats) + 0.9)
    ax = fig.add_subplot(111)
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=INK, labelsize=9, length=0)
    for i, c in enumerate(cats):
        y = len(cats) - 1 - i
        lo, hi = targets[c]["band"]
        tvals = [v for v in targets[c]["tokens"].values() if v is not None]
        tgt = sum(tvals) if tvals else None
        w = weights.get(c, 0.0)
        ax.barh(y, 1, color="#141414", height=0.5)
        ax.barh(y, hi - lo, left=lo, color="#2A2A2A", height=0.5)
        ax.barh(y, w, color=CAT.get(c, TEXT), height=0.18)
        if tgt is not None:
            ax.plot([tgt, tgt], [y - 0.3, y + 0.3], color=INK, linewidth=1)
        ok = lo - 1e-9 <= w <= hi + 1e-9
        label = f"{w:.1%}  (target {tgt:.0%})" if tgt is not None else f"{w:.1%}  (AI, max {hi:.0%})"
        ax.text(1.01, y, label, color=INK if ok else PENDING, va="center", fontsize=8.5,
                transform=ax.get_yaxis_transform())
    ax.set_yticks(range(len(cats)))
    ax.set_yticklabels(list(reversed(cats)))
    ax.set_xlim(0, 1)
    ax.set_xticks([])
    ax.set_title("Allocation  (bar = actual, band = allowed, tick = target)", color=TEXT, fontsize=8.5, loc="left", pad=6)
    return _png(fig)
