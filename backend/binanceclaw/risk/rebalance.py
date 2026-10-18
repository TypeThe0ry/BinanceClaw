"""Threshold rebalancing for 手动分配 ("targets" mode). Pure functions, no I/O.

The owner sets target weights; when an asset's actual weight drifts from its target by more
than ``allocation.rebalance_drift``, the system proposes the trade that brings it back. Those
trades are ordinary actions: the risk engine caps, rejects or sends them for approval like any
other, so rebalancing never bypasses a limit.
"""
from __future__ import annotations

from .engine import MAX_ACTIONS_PER_PLAN, Portfolio

MIN_TRADE_USD = 10


def drift(pf: Portfolio) -> dict[str, dict]:
    """{token: {target, actual, drift}} for every tradable token that has a target."""
    s = pf.settings
    if s.allocation.mode != "targets":
        return {}
    tradable = set(s.tradable_tokens())
    w = pf.token_weights()
    out = {}
    for name, c in s.categories.items():
        if name == "STABLE":
            continue
        for t, target in c.tokens.items():
            if t in tradable:
                actual = w.get(t, 0.0)
                out[t] = {"target": round(target, 4), "actual": round(actual, 4), "drift": round(actual - target, 4)}
    return out


def rebalance_actions(pf: Portfolio, skip: set[str] | frozenset = frozenset()) -> list[dict]:
    """Trades for the assets whose drift exceeds the threshold (sells first, then the largest drifts).

    ``skip``: tokens that already have a trade waiting for approval, so a pending rebalance is
    not proposed again every round."""
    s = pf.settings
    al = s.allocation
    total = pf.total()
    if al.mode != "targets" or not al.auto_rebalance or total <= 0:
        return []
    out = []
    rows = sorted(drift(pf).items(), key=lambda kv: (kv[1]["drift"] < 0, -abs(kv[1]["drift"])))
    for t, x in rows:
        if t in skip or abs(x["drift"]) <= al.rebalance_drift:
            continue
        side = "SELL" if x["drift"] > 0 else "BUY"
        if side == "SELL" and not s.limits.allow_sell:
            continue
        usd = round(abs(x["drift"]) * total, 2)
        if usd < MIN_TRADE_USD:
            continue
        out.append({
            "side": side, "symbol": f"{t}{s.quote}", "quote_usd": usd, "source": "rebalance",
            "reason": (f"自动再平衡：{t} 当前 {x['actual']:.1%}，目标 {x['target']:.1%}，"
                       f"偏离 {x['drift']:+.1%} 超过阈值 {al.rebalance_drift:.1%}"),
            "invalidation": "目标比例由主人设定；修改目标或关闭自动再平衡即停止",
        })
    return out[:MAX_ACTIONS_PER_PLAN]
