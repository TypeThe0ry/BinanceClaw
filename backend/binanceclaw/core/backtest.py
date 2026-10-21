"""Backtest lab: replay historical Binance candles through the same decision prompt, the same model and
the same risk engine as a live round, on a simulated spot account.

Each round at time T sees only candles that had closed by T (plus today's still-forming daily candle,
rebuilt from the closed 4h candles), so there is no look-ahead. The prompt carries no calendar dates,
so the model cannot simply recall what happened next. Orders fill at the price of round T with fees
and slippage. The result is compared with holding BTC and with holding the chosen assets equal-weighted."""
from __future__ import annotations

import bisect
import datetime as dt
import logging
import threading
import time
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlmodel import desc, select

from ..brain.prompt import SYSTEM, build_context, build_user_prompt
from ..brain.providers import ProviderError
from ..brain.schema import PlanError, parse_plan
from ..db import Backtest
from ..exchange.binance_spot import ExchangeError
from ..risk.engine import Portfolio, RiskState, evaluate, pause_trigger
from . import metrics as m

log = logging.getLogger("binanceclaw")

HOUR, DAY = 3600, 86400
STEPS = {"4h": 4 * HOUR, "12h": 12 * HOUR, "1d": DAY}
MAX_ROUNDS = 400             # every round is one model call
MAX_TOKENS = 8
WARMUP_DAYS = 60             # daily history before the window: 50-day averages from the first round
WARMUP_H4 = 60
MAX_FAILURES = 3             # consecutive failed rounds (model error or invalid plan) stop the run
RECENT = 5


class BacktestParams(BaseModel):
    tokens: list[str] = Field(min_length=1, max_length=MAX_TOKENS)
    start: dt.date
    end: dt.date                                   # inclusive
    step: Literal["4h", "12h", "1d"] = "1d"
    capital: float = Field(default=10_000, ge=100, le=10_000_000)
    fee: float = Field(default=0.001, ge=0, le=0.01)
    slippage: float = Field(default=0.0005, ge=0, le=0.02)
    # what happens to a trade that would wait for the owner's approval: executed (as under 免审批) or skipped
    approvals: Literal["execute", "skip"] = "execute"
    model: str = Field(default="", max_length=100)  # empty: the model live decisions use
    allow_sell: bool | None = None                  # None: the owner's limits.allow_sell

    @field_validator("tokens")
    @classmethod
    def _tokens(cls, v):
        out = list(dict.fromkeys(t.strip().upper() for t in v if t.strip()))
        if not out:
            raise ValueError("至少选一个币种")
        return out

    @model_validator(mode="after")
    def _window(self):
        if self.end < self.start:
            raise ValueError("结束日期早于开始日期")
        if self.end >= dt.datetime.now(dt.UTC).date():
            raise ValueError("结束日期最晚为昨天")
        if self.rounds() < 1:
            raise ValueError("区间太短")
        if self.rounds() > MAX_ROUNDS:
            raise ValueError(f"共 {self.rounds()} 轮，超过上限 {MAX_ROUNDS} 轮；缩短区间或加大决策间隔")
        return self

    def window(self) -> tuple[float, float]:
        s = dt.datetime.combine(self.start, dt.time(), dt.UTC).timestamp()
        e = dt.datetime.combine(self.end + dt.timedelta(days=1), dt.time(), dt.UTC).timestamp()
        return s, e

    def rounds(self) -> int:
        s, e = self.window()
        return int((e - s) // STEPS[self.step])


class Busy(Exception):
    pass


# ---------------------------------------------------------------------------------------------- history
def fetch_range(client, symbol: str, interval: str, since: float, until: float) -> list[dict]:
    """All candles of ``interval`` opening in [since, until), paging 1000 at a time."""
    width = STEPS[interval]
    out: list[dict] = []
    t = since
    while t < until:
        rows = client.klines(symbol, interval, 1000, start=t)
        rows = [r for r in rows if r["t"] >= t and r["t"] < until]
        if not rows:
            break
        out.extend(rows)
        t = rows[-1]["t"] + width
    return out


class History:
    """Candles of one asset, sliced to what was known at a given moment."""

    def __init__(self, daily: list[dict], h4: list[dict]):
        self.daily, self.h4 = daily, h4
        self._dt = [c["t"] for c in daily]
        self._ht = [c["t"] for c in h4]

    def at(self, ts: float) -> tuple[float | None, list[dict], list[dict]]:
        """(price, daily candles, 4h candles) as of ``ts``. The last daily candle is today's, still forming."""
        h = self.h4[: bisect.bisect_right(self._ht, ts - STEPS["4h"])]       # 4h candles closed by ts
        if not h:
            return None, [], []
        price = h[-1]["c"]
        day0 = ts - ts % DAY
        daily = self.daily[: bisect.bisect_right(self._dt, day0 - DAY)]       # days closed before today
        today = [c for c in h[-6:] if c["t"] >= day0]
        if today:
            daily = [*daily, {"t": day0, "o": today[0]["o"], "h": max(c["h"] for c in today),
                              "l": min(c["l"] for c in today), "c": price, "v": sum(c.get("v", 0) for c in today)}]
        return price, daily, h


def load_history(client, tokens: list[str], quote: str, start: float, end: float) -> dict[str, History]:
    out = {}
    for tok in tokens:
        sym = f"{tok}{quote}"
        daily = fetch_range(client, sym, "1d", start - WARMUP_DAYS * DAY, end)
        h4 = fetch_range(client, sym, "4h", start - WARMUP_H4 * STEPS["4h"], end)
        out[tok] = History(daily, h4)
    return out


# ---------------------------------------------------------------------------------------------- simulation
class SimAccount:
    """A spot account in the quote currency: market orders at the round's price, minus fee and slippage."""

    def __init__(self, quote: str, capital: float, fee: float, slippage: float):
        self.quote, self.fee, self.slip = quote, fee, slippage
        self.bal: dict[str, float] = {quote: capital}
        self.trades: list[dict] = []

    def value(self, prices: dict[str, float]) -> float:
        return sum(u if a == self.quote else u * prices.get(a, 0.0) for a, u in self.bal.items())

    def buy(self, tok: str, usd: float, price: float, ts: float, rnd: int) -> dict:
        usd = min(usd, self.bal[self.quote])
        px = price * (1 + self.slip)
        qty = usd * (1 - self.fee) / px
        self.bal[self.quote] -= usd
        self.bal[tok] = self.bal.get(tok, 0.0) + qty
        return self._log(ts, rnd, tok, "BUY", qty, px, usd, usd * self.fee)

    def sell(self, tok: str, usd: float, price: float, ts: float, rnd: int) -> dict:
        qty = min(usd / price, self.bal.get(tok, 0.0))
        px = price * (1 - self.slip)
        gross = qty * px
        self.bal[tok] -= qty
        self.bal[self.quote] += gross * (1 - self.fee)
        return self._log(ts, rnd, tok, "SELL", qty, px, gross * (1 - self.fee), gross * self.fee)

    def _log(self, ts, rnd, tok, side, qty, px, usd, fee) -> dict:
        t = {"ts": ts, "round": rnd, "symbol": f"{tok}{self.quote}", "token": tok, "side": side, "qty": qty,
             "price": px, "quote_usd": round(usd, 6), "fee": round(fee, 6)}
        self.trades.append(t)
        return t


def _ago(seconds: float) -> str:
    h = seconds / HOUR
    return f"{h:.0f} 小时前" if h < 48 else f"{h / 24:.0f} 天前"


def _thin(points: list, n: int) -> list:
    if len(points) <= n:
        return points
    stride = len(points) / n
    return [points[int(i * stride)] for i in range(n)] + [points[-1]]


class BacktestLab:
    def __init__(self, svc):
        self.svc = svc
        self.db = svc.db
        self._cancel: set[str] = set()
        self._lock = threading.Lock()
        for r in self.db.all(select(Backtest).where(Backtest.status == "running")):
            self.db.update(Backtest, r.id, status="error", error="服务重启，回测中断", finished=time.time())

    # ------------------------------------------------------------------ control
    def running(self) -> Backtest | None:
        rows = self.db.all(select(Backtest).where(Backtest.status == "running").limit(1))
        return rows[0] if rows else None

    def start(self, raw: dict, decide: Callable[[str], object] | None = None, wait: bool = False) -> Backtest:
        p = BacktestParams.model_validate(raw)
        quote = self.svc.settings.quote
        stables = set(self.svc.settings.stables)
        bad = [t for t in p.tokens if t in stables or t == quote]
        if bad:
            raise ValueError(f"稳定币不能作为回测币种：{', '.join(bad)}")
        try:
            listed = {x["symbol"] for x in self.svc._spot_markets()}
        except ExchangeError as e:
            raise ValueError(f"读取币安交易对失败：{e}") from e
        missing = [t for t in p.tokens if f"{t}{quote}" not in listed]
        if missing:
            raise ValueError(f"币安没有这些 {quote} 交易对：{', '.join(missing)}")
        with self._lock:
            if self.running():
                raise Busy("已有回测在运行")
            row = self.db.add(Backtest(params=p.model_dump(mode="json"), total=p.rounds()))
        th = threading.Thread(target=self._run_safe, args=(row.id, p, decide), daemon=True, name=f"backtest-{row.id}")
        th.start()
        if wait:
            th.join()
        return self.db.get(Backtest, row.id)

    def cancel(self, bid: str) -> bool:
        row = self.db.get(Backtest, bid)
        if not row or row.status != "running":
            return False
        self._cancel.add(bid)
        return True

    def delete(self, bid: str) -> bool:
        row = self.db.get(Backtest, bid)
        if not row or row.status == "running":
            return False
        with self.db.write_lock, self.db.session() as s:
            s.delete(s.get(Backtest, bid))
            s.commit()
        return True

    def list(self, limit: int = 30) -> list[dict]:
        rows = self.db.all(select(Backtest).order_by(desc(Backtest.ts)).limit(limit))
        return [{k: getattr(r, k) for k in ("id", "ts", "status", "params", "progress", "total", "summary", "error", "finished")}
                for r in rows]

    # ------------------------------------------------------------------ run
    def _run_safe(self, bid: str, p: BacktestParams, decide):
        try:
            self.run(bid, p, decide)
        except Exception as e:      # the row must never stay "running"
            log.exception("backtest %s failed", bid)
            self.db.update(Backtest, bid, status="error", error=str(e)[:500], finished=time.time())
        finally:
            self._cancel.discard(bid)

    def _settings(self, p: BacktestParams):
        """The owner's current settings (limits, strategy, views, memory), trading only the chosen assets
        with AI-decided weights. Futures, earn and questions do not exist in the simulation."""
        s = self.svc.settings_store.get().model_copy(deep=True)
        s.mode = "paper"
        s.allocation.mode, s.allocation.universe = "ai", "whitelist"
        s.allocation.enabled, s.allocation.blocked = list(p.tokens), []
        s.capital.budget_usd = None
        s.capital.locked = {}
        s.futures.enabled = False
        s.earn.enabled = False
        s.questions.enabled = False
        s.approval_free.include_sell = p.approvals == "execute"
        if p.allow_sell is not None:
            s.limits.allow_sell = p.allow_sell
        return s

    def _decider(self, p: BacktestParams):
        def call(prompt: str):
            msgs = [{"role": "user", "content": prompt}]
            if p.model:
                return self.svc.provider.with_model(p.model).complete(SYSTEM, msgs, json_mode=True)
            return self.svc.complete(SYSTEM, msgs, json_mode=True, task="decision")
        return call

    def run(self, bid: str, p: BacktestParams, decide=None):
        from .service import asset_snapshot  # service imports this module
        decide = decide or self._decider(p)
        s = self._settings(p)
        q = s.quote
        start, end = p.window()
        step = STEPS[p.step]
        tz = self.svc.tz()
        watch = [*p.tokens, *([] if "BTC" in p.tokens else ["BTC"])]
        hist = load_history(self.svc.client, watch, q, start, end)
        p0 = {t: hist[t].at(start)[0] for t in watch}
        if not p0.get("BTC") or any(not p0[t] for t in p.tokens):
            gone = [t for t in watch if not p0.get(t)]
            raise ValueError(f"这些币种在开始日期还没有行情：{', '.join(gone)}")

        acct = SimAccount(q, p.capital, p.fee, p.slippage)
        btc_units = p.capital * (1 - p.fee) / p0["BTC"]
        basket = {t: p.capital / len(p.tokens) * (1 - p.fee) / p0[t] for t in p.tokens}
        equity = [{"t": start, "v": p.capital, "btc": p.capital, "basket": p.capital}]
        trail: list[dict] = []
        usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "model_ms": 0}
        failures, model = 0, ""
        total = p.rounds()
        status = "done"

        for i in range(total):
            if bid in self._cancel:
                status = "cancelled"
                break
            ts = start + i * step
            prices, market = {}, {}
            for tok in watch:
                px, daily, h4 = hist[tok].at(ts)
                if px:
                    prices[tok] = px
                    market[tok] = asset_snapshot(px, daily, h4)
            rnd = {"i": i, "t": ts, "status": "done", "summary": "", "confidence": None, "actions": []}
            pf = Portfolio(dict(acct.bal), prices, s)
            st = self._risk_state(acct, ts, tz, market, p)
            trig = pause_trigger(s, st)
            if trig:
                rnd.update(status="skipped", summary=f"触发暂停条件：{trig}")
            else:
                context = build_context(s, pf, st, market, self._recent(trail, ts))
                context["futures"] = {"enabled": False}
                context["questions"] = {"enabled": False}
                perf = self._perf(equity, acct.trades, ts)
                if perf:
                    context["performance_30d"] = perf
                t0 = time.time()
                try:
                    comp = decide(build_user_prompt(context))
                    usage["calls"] += 1
                    usage["model_ms"] += int((time.time() - t0) * 1000)
                    u = getattr(comp, "usage", None) or {}
                    usage["input_tokens"] += int(u.get("input_tokens") or 0)
                    usage["output_tokens"] += int(u.get("output_tokens") or 0)
                    model = getattr(comp, "model", "") or model
                    plan = parse_plan(comp.text)
                except (ProviderError, PlanError) as e:
                    failures += 1
                    rnd.update(status="error", summary=str(e)[:300])
                    if failures >= MAX_FAILURES:
                        trail.append(rnd)
                        raise RuntimeError(f"连续 {failures} 轮失败：{e}") from e
                else:
                    failures = 0
                    rnd.update(summary=plan.summary, market_view=plan.market_view, confidence=plan.confidence)
                    rnd["actions"] = self._apply(plan, pf, st, acct, prices, ts, i, p)
            equity.append({"t": ts + step, **self._marks(acct, hist, watch, ts + step, prices, btc_units, basket)})
            trail.append(rnd)
            self._save(bid, p, i + 1, equity, acct, trail, usage, model, final=False)

        self._save(bid, p, len(trail), equity, acct, trail, usage, model, final=True, status=status)

    def _risk_state(self, acct: SimAccount, ts: float, tz, market: dict, p: BacktestParams) -> RiskState:
        day = dt.datetime.fromtimestamp(ts, tz).date()
        today = [t for t in acct.trades if dt.datetime.fromtimestamp(t["ts"], tz).date() == day]
        buys = [t for t in today if t["side"] == "BUY"]
        sells = [t for t in today if t["side"] == "SELL"]
        return RiskState(today_orders=len(buys), today_notional=sum(t["quote_usd"] for t in buys),
                         today_auto_notional=sum(t["quote_usd"] for t in buys),
                         today_auto_sells=len(sells), today_auto_sell_usd=sum(t["quote_usd"] for t in sells),
                         btc_change_7d=(market.get("BTC") or {}).get("change_7d"),
                         unattended=p.approvals == "execute")

    def _apply(self, plan, pf, st, acct: SimAccount, prices, ts, i, p) -> list[dict]:
        actions = [a.model_dump() for a in plan.actions]
        out = []
        for a, v in zip(actions, evaluate(actions, pf, st), strict=True):
            row = {"side": v.side, "symbol": v.symbol, "requested_usd": v.requested_usd, "approved_usd": v.approved_usd,
                   "decision": v.decision, "reason": v.reason, "reason_model": a["reason"][:300],
                   "checks": [c.__dict__ for c in v.checks]}
            tok = v.symbol[: -len(acct.quote)]
            if v.decision == "execute" and prices.get(tok):
                fill = (acct.buy if v.side == "BUY" else acct.sell)(tok, v.approved_usd, prices[tok], ts, i)
                row["fill"] = {k: fill[k] for k in ("qty", "price", "quote_usd", "fee")}
            elif v.decision == "approve":
                row["decision"] = "skipped"
                row["reason"] = "需要批准，回测设为跳过"
            out.append(row)
        return out

    @staticmethod
    def _recent(trail: list[dict], now: float) -> list[dict]:
        """recent_decisions as the live prompt has them, with relative times instead of dates."""
        rows = [r for r in trail if r["status"] == "done"][-RECENT:]
        return [{"time": _ago(now - r["t"]), "trigger": "schedule", "summary": r["summary"][:300],
                 "actions": [{"side": x["side"], "symbol": x["symbol"], "usd": x["approved_usd"], "decision": x["decision"],
                              "result": "已成交" if x.get("fill") else x["reason"]} for x in r["actions"]]}
                for r in reversed(rows)]

    @staticmethod
    def _perf(equity: list[dict], trades: list[dict], now: float) -> dict | None:
        """performance_30d as the live prompt has it, over the simulated account's last 30 days."""
        pts = [(e["t"], e["v"]) for e in equity if e["t"] >= now - 30 * DAY]
        e = m.equity_stats(pts)
        closed = [t for t in m.spot_round_trips(trades) if t["ts"] >= now - 30 * DAY]
        t = m.trade_stats([x["pnl"] for x in closed])
        if e["points"] < 2 and not t["trades"]:
            return None
        out = {k: e[k] for k in ("span_days", "total_return", "max_drawdown", "sharpe", "volatility") if e.get(k) is not None}
        b = [x for x in equity if x["t"] >= now - 30 * DAY]
        if len(b) >= 2:
            out["btc_return"] = round(b[-1]["btc"] / b[0]["btc"] - 1, 5)
        if t["trades"]:
            out["closed_trades"] = {k: t[k] for k in ("trades", "win_rate", "profit_factor", "payoff", "max_loss_streak")}
        return out

    @staticmethod
    def _marks(acct, hist, watch, ts, fallback, btc_units, basket) -> dict:
        px = {t: hist[t].at(ts)[0] or fallback.get(t, 0.0) for t in watch}
        return {"v": round(acct.value(px), 4), "btc": round(btc_units * px["BTC"], 4),
                "basket": round(sum(u * px[t] for t, u in basket.items()), 4)}

    def _save(self, bid, p, done, equity, acct: SimAccount, trail, usage, model, final: bool, status: str = "running"):
        pts = [(e["t"], e["v"]) for e in equity]
        stats = m.equity_stats(pts)
        dd = stats.pop("drawdown")
        btc = m.equity_stats([(e["t"], e["btc"]) for e in equity])
        basket = m.equity_stats([(e["t"], e["basket"]) for e in equity])
        btc.pop("drawdown")
        basket.pop("drawdown")
        closed = m.spot_round_trips(acct.trades)
        trades = m.trade_stats([t["pnl"] for t in closed], [t["ret"] for t in closed])
        decisions = [x for r in trail for x in r["actions"]]
        count = {k: sum(1 for x in decisions if x["decision"] == k) for k in ("execute", "skipped", "reject")}
        summary = {"total_return": stats["total_return"], "btc_return": btc["total_return"],
                   "basket_return": basket["total_return"], "max_drawdown": stats["max_drawdown"], "sharpe": stats["sharpe"],
                   "win_rate": trades["win_rate"], "trades": len(acct.trades), "end_value": equity[-1]["v"],
                   "excess_btc": (round(stats["total_return"] - btc["total_return"], 5)
                                  if stats["total_return"] is not None and btc["total_return"] is not None else None)}
        result = {"equity": _thin(equity, 600), "drawdown": _thin(dd, 300), "stats": stats, "btc": btc, "basket": basket,
                  "trades": trades, "closed": closed, "log": acct.trades, "rounds": trail, "usage": usage, "model": model,
                  "decisions": {"proposed": len(decisions), **count},
                  "holdings": {a: round(u, 10) for a, u in acct.bal.items() if u > 1e-12}}
        fields = {"progress": done, "summary": summary}
        if final or done <= 3 or done % 5 == 0:        # the full result is large: refresh it every few rounds
            fields["result"] = result
        if final:
            fields.update(status=status, finished=time.time())
        self.db.update(Backtest, bid, **fields)
