"""Deterministic risk checks for USDⓈ-M perpetual actions. Pure functions, no I/O.

Rules that never bend:
* futures must be switched on by the owner, and only whitelisted USDT perpetuals are tradable;
* every open carries a stop-loss on the correct side, within ``max_stop_distance`` and well
  inside the (isolated-margin) liquidation distance;
* size is capped by per-position margin, total margin, spendable stablecoins, the AI budget and
  the maximum loss at the stop; leverage is capped to ``max_leverage``;
* opening is approval-only unless the owner enables auto-open (small size only);
* closing, reducing and *tightening* a stop are always allowed — even while paused — because
  they only lower risk. Loosening a stop is rejected.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from ..config import Settings
from .engine import Check

TAKER_FEE = 0.0005           # per side, used for the loss-at-stop estimate
MAINT_MARGIN = 0.005         # conservative maintenance-margin rate for the liquidation estimate
MIN_STOP_DISTANCE = 0.002    # closer than 0.2% would trigger on noise
OPEN_OPS = ("OPEN_LONG", "OPEN_SHORT")
MAX_FUTURES_ACTIONS = 3


@dataclass
class FuturesView:
    """Everything the checks need, gathered by the service."""
    settings: Settings
    marks: dict[str, float]                       # symbol -> mark price
    rules: dict[str, object]                      # symbol -> PerpRules (step_size, min_qty, min_notional)
    positions: dict[str, dict]                    # symbol -> {side, qty, entry, margin, stop} (BinanceClaw's own)
    foreign: set[str] = field(default_factory=set)   # symbols with an exchange position BinanceClaw did not open
    spendable_usd: float = 0.0                    # stablecoins available for new margin (after stable floor)
    margin_used: float = 0.0                      # margin of open + pending positions
    opens_today: int = 0
    paused: bool = False
    pause_reason: str = ""
    trigger: str | None = None                    # active pause trigger, if any
    budget_left: float = float("inf")
    unattended: bool = False                      # 免审批 is active now (opens skip approval if include_futures)


@dataclass
class FVerdict:
    index: int
    op: str
    symbol: str
    requested_margin: float = 0.0
    margin: float = 0.0
    leverage: int = 0
    stop_loss: float | None = None
    take_profit: float | None = None
    fraction: float = 1.0
    decision: str = "reject"                      # execute | approve | reject
    checks: list[Check] = field(default_factory=list)
    reason: str = ""

    def to_dict(self):
        d = asdict(self)
        d["kind"] = "futures"
        return d


def perp_symbol(raw: str) -> str:
    s = raw.upper().replace("-", "").replace("/", "").replace("_", "").replace("PERP", "").strip()
    return s if s.endswith("USDT") else s + "USDT"


def liq_distance(leverage: int) -> float:
    """Approximate distance from entry to liquidation for an isolated position."""
    return max(1.0 / leverage - MAINT_MARGIN, 0.0)


def floor_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    return float(f"{math.floor(x / step + 1e-9) * step:.12f}")


def evaluate_futures(actions: list[dict], fv: FuturesView) -> list[FVerdict]:
    s = fv.settings
    f = s.futures
    allowed = {f"{t}USDT" for t in f.symbols}
    margin_left = f.max_total_margin_usd - fv.margin_used
    spendable = fv.spendable_usd
    budget_left = fv.budget_left
    opens_left = f.max_opens_per_day - fv.opens_today
    positions = {k: dict(v) for k, v in fv.positions.items()}
    out: list[FVerdict] = []

    for i, a in enumerate(actions):
        op = str(a.get("op", "")).upper()
        sym = perp_symbol(str(a.get("symbol", "")))
        v = FVerdict(i, op, sym)
        out.append(v)

        def fail(name, detail, v=v):
            v.checks.append(Check(name, False, detail))
            v.reason = detail
            v.decision = "reject"

        def ok(name, detail, v=v):
            v.checks.append(Check(name, True, detail))

        if i >= MAX_FUTURES_ACTIONS:
            fail("每轮动作数", f"每轮最多 {MAX_FUTURES_ACTIONS} 个合约动作")
            continue
        if not f.enabled:
            fail("合约开关", "合约交易未开启（配置 → 合约）")
            continue
        if op in OPEN_OPS and sym not in allowed:
            fail("合约白名单", f"{sym} 不在合约白名单 {sorted(allowed)} 中")
            continue
        mark = fv.marks.get(sym)
        if not mark:
            fail("标记价格", f"没有 {sym} 的标记价格")
            continue
        pos = positions.get(sym)

        # ---------------------------------------------------------------- risk-reducing ops
        if op in ("CLOSE", "REDUCE"):
            if not pos:
                fail("持仓", f"BinanceClaw 在 {sym} 没有持仓")
                continue
            frac = 1.0 if op == "CLOSE" else float(a.get("fraction") or 0)
            if not (0 < frac <= 1):
                fail("减仓比例", "REDUCE 需要 0–1 之间的 fraction")
                continue
            v.fraction = frac
            v.margin = round(pos["margin"] * frac, 2)
            v.decision = "execute"
            v.reason = "平仓/减仓只降低风险，自动执行"
            ok("降低风险", f"{'全部平仓' if frac >= 1 else f'减仓 {frac:.0%}'}")
            if frac >= 1:
                positions.pop(sym, None)
            continue
        if op == "SET_STOP":
            if not pos:
                fail("持仓", f"BinanceClaw 在 {sym} 没有持仓")
                continue
            new = float(a.get("stop_loss") or 0)
            long = pos["side"] == "LONG"
            if new <= 0 or (long and new >= mark) or (not long and new <= mark):
                fail("止损方向", f"{'多' if long else '空'}单止损必须在标记价 {mark:g} 的{'下方' if long else '上方'}")
                continue
            old = pos["stop"]
            if (long and new < old) or (not long and new > old):
                fail("只能收紧止损", f"新止损 {new:g} 比原止损 {old:g} 更远，放宽止损不允许")
                continue
            v.stop_loss = new
            v.decision = "execute"
            v.reason = "收紧止损，自动执行"
            ok("收紧止损", f"{old:g} → {new:g}")
            pos["stop"] = new
            continue
        if op not in OPEN_OPS:
            fail("动作", f"未知合约动作 {op!r}")
            continue

        # ---------------------------------------------------------------- opening
        if fv.paused:
            fail("暂停状态", f"已暂停，不开新仓：{fv.pause_reason}")
            continue
        if fv.trigger:
            fail("暂停条件", fv.trigger)
            continue
        rule = fv.rules.get(sym)
        if rule is None:
            fail("合约状态", f"币安没有可交易的 {sym} 永续合约")
            continue
        ok("合约白名单", sym)
        short = op == "OPEN_SHORT"
        if short and not f.allow_short:
            fail("做空", "设置中禁止做空")
            continue
        if sym in fv.foreign:
            fail("已有外部持仓", f"{sym} 在币安上有不是 BinanceClaw 开的持仓，不叠加")
            continue
        if pos:
            fail("已有持仓", f"{sym} 已有{'多' if pos['side'] == 'LONG' else '空'}单；先平仓或调整止损，不加仓不反手")
            continue
        if opens_left <= 0:
            fail("每日开仓数", f"今日开仓已达 {f.max_opens_per_day} 次")
            continue
        lev_req = int(a.get("leverage") or 1)
        lev = max(1, min(lev_req, f.max_leverage))
        v.leverage = lev
        ok("杠杆", f"{lev}x" + (f"（已从 {lev_req}x 下调）" if lev != lev_req else ""))

        stop = float(a.get("stop_loss") or 0)
        if stop <= 0:
            fail("止损", "开仓必须带止损价 stop_loss")
            continue
        if (not short and stop >= mark) or (short and stop <= mark):
            fail("止损方向", f"{'空' if short else '多'}单止损必须在标记价 {mark:g} 的{'上方' if short else '下方'}")
            continue
        dist = abs(mark - stop) / mark
        if dist < MIN_STOP_DISTANCE:
            fail("止损距离", f"止损距离 {dist:.2%} 太近（< {MIN_STOP_DISTANCE:.1%}）")
            continue
        if dist > f.max_stop_distance:
            fail("止损距离", f"止损距离 {dist:.2%} 超过上限 {f.max_stop_distance:.1%}")
            continue
        liq = liq_distance(lev)
        if dist >= liq * 0.8:
            fail("强平保护", f"{lev}x 的强平距离约 {liq:.1%}，止损 {dist:.2%} 不在安全范围内（需 < {liq * 0.8:.1%}）；降低杠杆或收紧止损")
            continue
        ok("止损", f"{stop:g}（距标记价 {dist:.2%}，强平约 {liq:.1%}）")
        tp = a.get("take_profit")
        if tp:
            tp = float(tp)
            if (not short and tp <= mark) or (short and tp >= mark):
                fail("止盈方向", f"止盈价必须在标记价的{'下方' if short else '上方'}")
                continue
            v.take_profit = tp
        v.stop_loss = stop

        req = float(a.get("margin_usd") or 0)
        v.requested_margin = req
        if not (req > 0 and math.isfinite(req)):
            fail("保证金", "开仓需要正数 margin_usd")
            continue
        amt, caps = req, []
        if amt > f.max_position_margin_usd:
            caps.append(f"单仓保证金上限 ${f.max_position_margin_usd:g}")
            amt = f.max_position_margin_usd
        if amt > margin_left:
            caps.append(f"总保证金剩余 ${max(margin_left, 0):,.0f}")
            amt = margin_left
        if amt > spendable:
            caps.append(f"可用稳定币 ${max(spendable, 0):,.0f}")
            amt = spendable
        if amt > budget_left:
            caps.append(f"AI 投入预算剩余 ${max(budget_left, 0):,.0f}")
            amt = budget_left
        loss_cap = f.max_loss_per_trade_usd / (lev * (dist + 2 * TAKER_FEE))
        if amt > loss_cap:
            caps.append(f"止损亏损上限 ${f.max_loss_per_trade_usd:g}")
            amt = loss_cap
        amt = math.floor(max(amt, 0) * 100) / 100
        qty = floor_step(amt * lev / mark, rule.step_size)
        min_notional = max(rule.min_notional, 5.0)
        if amt < 5 or qty < rule.min_qty or qty * mark < min_notional:
            fail("可开仓规模", "；".join(caps) + ("；" if caps else "") +
                 f"保证金 ${amt:,.2f} × {lev}x 低于交易所最小下单额 ${min_notional:g}")
            continue
        ok("仓位规模", f"保证金 ${amt:,.2f} × {lev}x = 名义 ${amt * lev:,.0f}"
           + (f"（已从 ${req:,.0f} 截断：{'；'.join(caps)}）" if caps else ""))
        loss = amt * lev * (dist + 2 * TAKER_FEE)
        ok("止损亏损", f"触发止损约亏 ${loss:,.2f} ≤ ${f.max_loss_per_trade_usd:g}")
        v.margin = amt
        if f.auto_open and amt <= f.auto_open_max_margin_usd:
            v.decision = "execute"
            v.reason = f"已开启自动开仓且保证金 ≤ ${f.auto_open_max_margin_usd:g}，自动执行"
        else:
            v.decision = "approve"
            v.reason = "开仓需要你批准" if not f.auto_open else f"保证金 > ${f.auto_open_max_margin_usd:g}，需要你批准"
            if fv.unattended and s.approval_free.include_futures:
                v.decision = "execute"
                ok("免审批", "主人开启了免审批（包含合约开仓）")
                v.reason = "免审批时段，风控限额内直接开仓"
        opens_left -= 1
        margin_left -= amt
        spendable -= amt
        budget_left -= amt
        positions[sym] = {"side": "SHORT" if short else "LONG", "qty": qty, "entry": mark, "margin": amt, "stop": stop}
    return out
