"""Deterministic risk engine. Pure functions, no I/O.

The AI decides *what* it wants; this module decides what is *allowed*. Every proposed
action is checked against hard limits and either executed automatically, sent for human
approval, capped to a smaller size, or rejected. The model cannot override any of this.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from ..config import Settings


@dataclass
class Portfolio:
    balances: dict[str, float]          # asset -> free units
    prices: dict[str, float]            # token -> price in quote
    settings: Settings

    def values(self) -> dict[str, float]:
        out = {}
        for asset, units in self.balances.items():
            if units <= 0:
                continue
            if asset in self.settings.stables:
                out[asset] = units
            elif self.prices.get(asset, 0) > 0:
                out[asset] = units * self.prices[asset]
        return out

    def total(self) -> float:
        return sum(self.values().values())

    def token_weights(self) -> dict[str, float]:
        t = self.total()
        return {k: v / t for k, v in self.values().items()} if t > 0 else {}

    def category_weights(self) -> dict[str, float]:
        """Weights per risk group (category in "targets" mode, token in "ai" mode)."""
        out = {name: 0.0 for name in self.settings.group_targets()}
        for tok, w in self.token_weights().items():
            g = self.settings.group_of(tok)
            out[g] = out.get(g, 0.0) + w
        return out

    def stable_value(self) -> float:
        return sum(v for k, v in self.values().items() if k in self.settings.stables)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Verdict:
    index: int
    side: str
    symbol: str
    requested_usd: float
    approved_usd: float = 0.0
    decision: str = "reject"            # execute | approve | reject
    checks: list[Check] = field(default_factory=list)
    reason: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class RiskState:
    today_orders: int = 0
    today_notional: float = 0.0
    today_auto_notional: float = 0.0
    deployed_usd: float = 0.0          # net capital the AI has put to work so far (buys - sells, at cost)
    paused: bool = False
    pause_reason: str = ""
    btc_change_7d: float | None = None
    anomaly: str | None = None
    halted: dict[str, str] = field(default_factory=dict)   # symbol -> Binance status, for pairs not TRADING


def pause_trigger(settings: Settings, st: RiskState) -> str | None:
    """Conditions that must pause automatic execution. Returns the reason or None."""
    if st.anomaly:
        return f"行情或交易所异常：{st.anomaly}"
    lim = settings.limits.btc_7d_drop_pause
    if st.btc_change_7d is not None and st.btc_change_7d <= -lim:
        return f"BTC 7 日跌幅 {st.btc_change_7d:.1%}，超过 {lim:.0%} 阈值"
    return None


def normalize_symbol(raw: str, quote: str) -> str:
    s = raw.upper().replace("-", "").replace("/", "").replace("_", "").strip()
    return s if s.endswith(quote) else s + quote


MAX_ACTIONS_PER_PLAN = 3


def evaluate(actions: list[dict], pf: Portfolio, st: RiskState) -> list[Verdict]:
    """Evaluate actions in order; each approved action updates the simulated portfolio so
    later actions see its effect (no double-spending the same stablecoins)."""
    s = pf.settings
    lim = s.limits
    total = pf.total()
    quote_bal = float(pf.balances.get(s.quote, 0.0))
    stable_val = pf.stable_value()
    tok_val = dict(pf.values())
    cat_w = pf.category_weights()
    orders_left = lim.max_orders_per_day - st.today_orders
    notional_left = lim.max_daily_notional_usd - st.today_notional
    auto_left = lim.max_auto_daily_usd - st.today_auto_notional
    budget = s.capital.budget_usd
    budget_left = (budget - st.deployed_usd) if budget is not None else float("inf")
    allowed = set(s.symbols())
    verdicts: list[Verdict] = []

    for i, a in enumerate(actions):
        side = str(a.get("side", "")).upper()
        symbol = normalize_symbol(str(a.get("symbol", "")), s.quote)
        req = float(a.get("quote_usd") or 0)
        v = Verdict(i, side, symbol, req)
        verdicts.append(v)

        def fail(name, detail, v=v):
            v.checks.append(Check(name, False, detail))
            v.reason = detail
            v.decision = "reject"

        if i >= MAX_ACTIONS_PER_PLAN:
            fail("每轮动作数", f"每轮最多 {MAX_ACTIONS_PER_PLAN} 个动作")
            continue
        if st.paused:
            fail("暂停状态", f"自动执行已暂停：{st.pause_reason}")
            continue
        trig = pause_trigger(s, st)
        if trig:
            fail("暂停条件", trig)
            continue
        if side not in ("BUY", "SELL"):
            fail("方向", f"未知方向 {side!r}")
            continue
        if symbol not in allowed:
            fail("币种白名单", f"{symbol} 不在白名单 {sorted(allowed)} 中")
            continue
        v.checks.append(Check("币种白名单", True, symbol))
        if symbol in st.halted:
            fail("交易状态", f"币安目前暂停了 {symbol} 的交易（{st.halted[symbol]}），恢复后才能买卖")
            continue
        if not (req > 0 and math.isfinite(req)):
            fail("金额", "金额必须为正数")
            continue
        token = symbol[: -len(s.quote)]

        if side == "SELL":
            if not lim.allow_sell:
                fail("只买不卖", "规则禁止卖出；如需卖出请你手动操作或在设置中开启（开启后也必须人工批准）")
                continue
            held = tok_val.get(token, 0.0)
            amt = math.floor(min(req, held, lim.max_order_usd))
            if amt < 10:
                fail("持仓", f"{token} 可卖市值 ${held:,.2f} 不足")
                continue
            v.approved_usd = float(amt)
            v.decision = "approve"
            v.checks.append(Check("卖出需人工批准", True, "卖出永远不会自动执行"))
            v.reason = "卖出需要你批准"
            continue

        # ---- BUY ----
        if orders_left <= 0:
            fail("每日笔数", f"今日已达 {lim.max_orders_per_day} 笔上限")
            continue
        v.checks.append(Check("每日笔数", True, f"今日第 {lim.max_orders_per_day - orders_left + 1}/{lim.max_orders_per_day} 笔"))
        amt = req
        caps = []
        if amt > lim.max_order_usd:
            caps.append(f"单笔上限 ${lim.max_order_usd:g}")
            amt = lim.max_order_usd
        if amt > notional_left:
            caps.append(f"每日总额剩余 ${max(notional_left, 0):,.0f}")
            amt = notional_left
        spendable = min(quote_bal, stable_val - lim.stable_floor * total) - total * lim.fee_buffer_ratio
        if amt > spendable:
            caps.append(f"稳定币下限 {lim.stable_floor:.0%}（可用 ${max(spendable, 0):,.0f}）")
            amt = spendable
        if amt > budget_left:
            caps.append(f"AI 投入预算剩余 ${max(budget_left, 0):,.0f}")
            amt = budget_left
        cat = s.group_of(token)
        high = s.group_ceiling(cat)
        room = (high - cat_w.get(cat, 0.0)) * total
        if amt > room:
            label = "单币占比" if s.allocation.mode == "ai" else "区间"
            caps.append(f"{cat} {label}上限 {high:.0%}（剩余空间 ${max(room, 0):,.0f}）")
            amt = room
        amt = math.floor(max(amt, 0))
        if amt < 10:
            fail("可执行金额", "；".join(caps) or "金额过小")
            continue
        v.checks.append(Check("金额限制", True,
                              f"${amt:g}" + (f"（已从 ${req:,.0f} 截断：{'；'.join(caps)}）" if caps else "")))
        stable_after = (stable_val - amt) / total if total else 0
        v.checks.append(Check("稳定币下限", stable_after >= lim.stable_floor - 1e-9, f"买入后 {stable_after:.1%} ≥ {lim.stable_floor:.0%}"))
        v.approved_usd = float(amt)
        if amt <= lim.auto_execute_max_usd and amt <= auto_left:
            v.decision = "execute"
            v.reason = f"≤ ${lim.auto_execute_max_usd:g} 且今日自动额度剩余 ${auto_left:,.0f}，自动执行"
            auto_left -= amt
        elif amt <= lim.auto_execute_max_usd:
            v.decision = "approve"
            v.reason = f"今日自动执行总额已达 ${lim.max_auto_daily_usd:g}，需要你批准"
        else:
            v.decision = "approve"
            v.reason = f"> ${lim.auto_execute_max_usd:g}，需要你批准"
        # apply to simulated state
        orders_left -= 1
        notional_left -= amt
        budget_left -= amt
        quote_bal -= amt
        stable_val -= amt
        tok_val[token] = tok_val.get(token, 0.0) + amt
        cat_w[cat] = cat_w.get(cat, 0.0) + (amt / total if total else 0)
    return verdicts


def recheck(action: dict, pf: Portfolio, st: RiskState) -> Verdict:
    """Re-run all checks for a single action right before execution (e.g. on approval)."""
    return evaluate([action], pf, st)[0]
