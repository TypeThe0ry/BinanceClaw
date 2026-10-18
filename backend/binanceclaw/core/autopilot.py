"""When a decision round runs.

fixed: every ``schedule.cycle_minutes``.
auto:  the AI names its own next check-in (``next_check_minutes``) and how big a move should wake it
       (``wake_move_pct``); real-time events wake it early: a price move since the last round, one of
       its price levels being hit, a futures stop / take-profit / liquidation, an approved trade filling,
       or the managed balances changing. Bounded by ``min_gap_minutes`` between rounds, ``max_gap_minutes``
       without one, and ``max_calls_per_day`` model calls.

This only decides timing. Every action a round produces still goes through the risk engine and approvals.
"""
from __future__ import annotations

import time

from ..config import HARD_BOUNDS, STYLES

FUTURES_EVENTS = {"STOP_HIT", "TP_HIT", "LIQUIDATED", "EXCHANGE_CLOSED", "STOP_FAILED"}
TRIGGER_LABELS = {
    "schedule": "定时", "manual": "手动", "ai_scheduled": "AI 预约复查", "price_move": "价格波动",
    "price_level": "关键价位", "futures": "合约事件", "fill": "成交", "balance": "余额变化",
}
BALANCE_CHECK_SECONDS = 60


class Autopilot:
    def __init__(self, svc):
        self.svc = svc
        self._balance_checked = 0.0

    # ------------------------------------------------------------------ state
    @property
    def sched(self):
        return self.svc.settings.schedule

    @property
    def auto(self) -> bool:
        return self.sched.decision_mode == "auto"

    def _kv(self, k, default=None):
        return self.svc.db.kv_get(k, default)

    def _set(self, k, v):
        self.svc.db.kv_set(k, v)

    def calls_today(self) -> int:
        day, n = self._kv("auto_calls", [None, 0])
        return n if day == self.svc.today() else 0

    def count_call(self) -> None:
        self._set("auto_calls", [self.svc.today(), self.calls_today() + 1])

    def calls_left(self) -> int:
        return max(0, self.sched.max_calls_per_day - self.calls_today())

    def check_range(self) -> tuple[int, int]:
        """Check-in bounds: the trading style's range inside schedule.min_gap/max_gap (which always win)."""
        s = self.sched
        lo, hi = STYLES[self.svc.settings.strategy.style]["check_minutes"]
        lo = min(max(lo, s.min_gap_minutes), s.max_gap_minutes)
        return lo, min(max(hi, lo), s.max_gap_minutes)

    def default_check(self) -> int:
        lo, hi = self.check_range()
        return min(max(STYLES[self.svc.settings.strategy.style]["default"] or self.sched.cycle_minutes, lo), hi)

    def wake_move(self) -> float:
        return self._kv("auto_wake_move") or self.sched.move_trigger_pct

    # ------------------------------------------------------------------ events
    def on_event(self, kind: str, data: dict) -> None:
        """EventBus listener: remember what happened so the next tick can wake the AI."""
        if kind == "price_alert":
            word = "跌破" if data.get("op") == "below" else "突破"
            note = f"（{data['note']}）" if data.get("note") else ""
            self.nudge("price_level", f"{data.get('symbol')} {word} {data.get('price'):g}，现价 {data.get('now'):g}{note}")
        elif kind == "futures" and data.get("op") in FUTURES_EVENTS:
            self.nudge("futures", data.get("text") or f"{data.get('symbol')} {data.get('op')}")
        elif kind == "approval" and data.get("status") == "approved":
            self.nudge("fill", f"主人批准的交易已执行：{data.get('text', '')}")

    def nudge(self, trigger: str, detail: str) -> None:
        if not self.auto:
            return
        pending = (self._kv("auto_pending") or [])[-4:]
        self._set("auto_pending", pending + [{"trigger": trigger, "detail": detail[:300], "ts": time.time()}])

    # ------------------------------------------------------------------ deciding
    def due(self, now: float | None = None) -> tuple[str, str] | None:
        """(trigger, why) when a round should start now, else None."""
        now = now or time.time()
        if not self.auto:
            return ("schedule", "") if now >= self.svc.next_cycle_at() else None
        if now - self._kv("auto_last_start", 0) < self.sched.min_gap_minutes * 60:
            return None
        if self.calls_left() <= 0:
            self._budget_alert()
            return None
        if now >= self.svc.next_cycle_at():
            return "ai_scheduled", "到了你上一轮自己定的复查时间"
        if self.svc.paused()[0]:
            return None                    # events don't wake a paused account; check-ins still log a skipped round
        pending = self._kv("auto_pending") or []
        if pending:
            return pending[0]["trigger"], "；".join(p["detail"] for p in pending)
        return self.price_move() or self.balance_change(now)

    def price_move(self) -> tuple[str, str] | None:
        ref = self._kv("auto_ref_prices") or {}
        if not ref:
            return None
        px = self.svc.live_prices(list(ref))
        moves = {t: px[t] / p - 1 for t, p in ref.items() if p and px.get(t)}
        if not moves:
            return None
        tok = max(moves, key=lambda t: abs(moves[t]))
        if abs(moves[tok]) < self.wake_move():
            return None
        return "price_move", f"{tok} 较上一轮 {moves[tok]:+.2%}（{ref[tok]:g} → {px[tok]:g}），超过唤醒阈值 {self.wake_move():.2%}"

    def balance_change(self, now: float) -> tuple[str, str] | None:
        ref = self._kv("auto_ref_balances")
        if ref is None or now - self._balance_checked < BALANCE_CHECK_SECONDS:
            return None
        self._balance_checked = now
        cur = self._balances()
        if cur is None:
            return None
        px = self._usd_prices(set(ref) | set(cur))
        total = sum(q * px.get(a, 0) for a, q in cur.items())
        diffs = {a: cur.get(a, 0) - ref.get(a, 0) for a in set(ref) | set(cur)}
        big = {a: d for a, d in diffs.items() if abs(d) * px.get(a, 0) >= max(20.0, 0.02 * total)}
        if not big:
            return None
        self._set("auto_ref_balances", cur)          # report a given change once
        return "balance", "余额变化：" + "，".join(f"{a} {d:+g}" for a, d in big.items())

    def _balances(self) -> dict[str, float] | None:
        from ..exchange.binance_spot import ExchangeError
        try:
            raw = self.svc.spot_balances()
            for a, amt in self.svc.earn.managed_amounts().items():
                raw[a] = raw.get(a, 0.0) + amt
            return {a: q for a, q in self.svc.managed_balances(raw).items() if q > 0}
        except ExchangeError:
            return None

    def _usd_prices(self, assets) -> dict[str, float]:
        s = self.svc.settings
        out = {a: 1.0 for a in assets if a in s.stables or a == s.quote}
        out.update(self.svc.asset_prices([a for a in assets if a not in out]))
        return out

    def _budget_alert(self) -> None:
        if self._kv("auto_budget_alert") != self.svc.today():
            self._set("auto_budget_alert", self.svc.today())
            self.svc.events.emit("alert", {"text": f"今天的 {self.sched.max_calls_per_day} 次模型调用额度已用完，"
                                                   "明天之前不再自主决策（可在设置里调高，手动决策不受影响）"})

    # ------------------------------------------------------------------ round bookkeeping
    def started(self, trigger: str) -> None:
        if trigger != "manual":
            self._set("auto_last_start", time.time())
        self._set("auto_pending", [])

    def context(self, trigger: str, why: str) -> dict:
        s = self.sched
        lo, hi = self.check_range()
        return {
            "decision_mode": "auto",
            "this_round": {"trigger": TRIGGER_LABELS.get(trigger, trigger), "why": why},
            "min_gap_minutes": lo, "max_gap_minutes": hi,
            "default_next_check_minutes": self.default_check(), "default_wake_move_pct": s.move_trigger_pct,
            "model_calls_left_today": self.calls_left(),
            "note": "由你决定节奏：next_check_minutes 是下次复查时间，wake_move_pct 是多大波动提前唤醒你，"
                    "alerts 的价位被触及也会唤醒你。行情平静、没有待验证的判断就把间隔拉长、省下调用；"
                    "波动大、刚下单或判断临近验证时就缩短。",
        }

    def finished(self, plan: dict | None) -> None:
        """After any round in auto mode: book the AI's next check-in and reset the move/balance baselines."""
        if not self.auto:
            return
        plan = plan or {}
        asked = plan.get("next_check_minutes")
        lo, hi = self.check_range()
        minutes = min(max(asked or self.default_check(), lo), hi)
        self.svc.db.kv_set("next_cycle_at", time.time() + minutes * 60)
        lo, hi = HARD_BOUNDS["move_trigger_pct"]
        mv = plan.get("wake_move_pct")
        self._set("auto_wake_move", min(max(mv, lo), hi) if mv else None)
        st = self.svc.settings
        tokens = st.tradable_tokens() + ([] if "BTC" in st.tradable_tokens() else ["BTC"])
        self._set("auto_ref_prices", self.svc.live_prices(tokens))
        bal = self._balances()
        if bal is not None:
            self._set("auto_ref_balances", bal)
        self._set("auto_next", {"minutes": minutes, "asked": asked})

    def status(self) -> dict:
        s = self.sched
        out = {"decision_mode": s.decision_mode, "next_cycle_at": self.svc.next_cycle_at()}
        if self.auto:
            nxt = self._kv("auto_next") or {}
            out.update(calls_today=self.calls_today(), max_calls_per_day=s.max_calls_per_day,
                       min_gap_minutes=s.min_gap_minutes, max_gap_minutes=s.max_gap_minutes,
                       check_range=list(self.check_range()),
                       wake_move_pct=self.wake_move(), next_check_minutes=nxt.get("minutes"),
                       ai_asked_minutes=nxt.get("asked"), streaming=self.svc.stream.connected)
        return out
