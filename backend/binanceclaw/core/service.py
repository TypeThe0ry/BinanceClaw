"""BinanceClaw service: market data -> AI plan -> risk engine -> execute / approval -> audit."""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
import uuid
from zoneinfo import ZoneInfo

from sqlmodel import desc, select

from ..brain.prompt import CHAT_SYSTEM, SYSTEM, build_context, build_user_prompt
from ..brain.providers import ProviderError, make_provider
from ..brain.schema import PlanError, parse_plan
from ..config import DATA_DIR, Secrets, SettingsStore
from ..db import Approval, ChatMessage, Cycle, Database, Order, Report
from ..exchange.binance_spot import BinanceSpot, ExchangeError
from ..exchange.broker import Broker
from ..risk import gate
from ..risk.engine import Portfolio, RiskState, evaluate, pause_trigger
from .events import EventBus

log = logging.getLogger("binanceclaw")


class Service:
    def __init__(self, data_dir=DATA_DIR, secrets: Secrets | None = None, client: BinanceSpot | None = None):
        self.data_dir = data_dir
        self.settings_store = SettingsStore(data_dir / "settings.json")
        self.secrets = secrets or Secrets.from_env()
        self.db = Database(data_dir / "binanceclaw.db")
        self.events = EventBus()
        self.client = client or BinanceSpot(self.secrets.binance_api_key, self.secrets.binance_api_secret)
        self._cycle_lock = threading.Lock()
        self._exec_lock = threading.RLock()
        self._chat_lock = threading.Lock()
        self._rebuild()

    # ------------------------------------------------------------------ plumbing
    def _rebuild(self):
        s = self.settings_store.get()
        self.broker = Broker(self.client, self.db, s.mode, s.paper)
        self.provider = make_provider(s.provider, self.secrets)

    @property
    def settings(self):
        return self.settings_store.get()

    def tz(self):
        return ZoneInfo(self.settings.schedule.timezone)

    def now(self) -> dt.datetime:
        return dt.datetime.now(self.tz())

    def today(self) -> str:
        return self.now().strftime("%Y-%m-%d")

    def update_settings(self, patch: dict):
        s = self.settings_store.update(patch)
        self._rebuild()
        self.events.emit("settings", {"mode": s.mode})
        return s

    # ------------------------------------------------------------------ state
    def paused(self) -> tuple[bool, str]:
        p = self.db.kv_get("paused", {"on": False, "reason": ""})
        return bool(p["on"]), p.get("reason", "")

    def pause(self, reason: str, by: str = "system"):
        self.db.kv_set("paused", {"on": True, "reason": reason, "ts": time.time(), "by": by})
        self.events.emit("paused", {"reason": reason, "by": by})

    def resume(self, by: str = "user"):
        self.db.kv_set("paused", {"on": False, "reason": "", "ts": time.time(), "by": by})
        self.db.kv_set("anomaly_count", 0)
        self.events.emit("resumed", {"by": by})

    def kill_switch(self, by: str = "user"):
        self.pause("紧急停止（Kill Switch）", by)
        for a in self.db.pending_approvals():
            if self.db.claim_approval(a.id, "rejected", by):
                self.db.update(Approval, a.id, result="紧急停止，已作废")
                self.events.emit("approval", {"id": a.id, "status": "rejected"})

    def next_cycle_at(self) -> float:
        return self.db.kv_get("next_cycle_at", 0)

    # ------------------------------------------------------------------ market
    def market_snapshot(self, max_age: float = 30):
        """Cached for ``max_age`` seconds; execution paths call with max_age=0 for fresh data."""
        cached = getattr(self, "_mkt_cache", None)
        if cached and time.time() - cached[0] < max_age:
            return cached[1]
        result = self._market_snapshot()
        self._mkt_cache = (time.time(), result)
        return result

    def _market_snapshot(self):
        s = self.settings
        tokens = s.tradable_tokens()
        prices_sym = self.client.prices(s.symbols())
        prices = {sym[: -len(s.quote)]: p for sym, p in prices_sym.items()}
        market, anomaly = {}, None
        missing = [t for t in tokens if t not in prices]
        if missing:
            anomaly = f"缺少价格：{', '.join(missing)}"
        btc_change = None
        for tok in tokens:
            if tok not in prices:
                continue
            k = self.client.klines(f"{tok}{s.quote}", "1d", 31)
            closes = [c["c"] for c in k]
            info = {"price": prices[tok]}
            if len(closes) >= 8:
                info["change_7d"] = round(prices[tok] / closes[-8] - 1, 4)
            if len(closes) >= 31:
                info["change_30d"] = round(prices[tok] / closes[-31] - 1, 4)
            info["high_30d"] = max(c["h"] for c in k) if k else None
            info["low_30d"] = min(c["l"] for c in k) if k else None
            info["daily_closes_14d"] = [round(c, 4) for c in closes[-14:]]
            market[tok] = info
            if tok == "BTC":
                btc_change = info.get("change_7d")
                if k and time.time() - k[-1]["t"] > 2 * 86400:
                    anomaly = anomaly or "BTC 日线停止更新"
                elif closes and abs(prices["BTC"] / closes[-1] - 1) > s.limits.price_deviation_anomaly:
                    anomaly = anomaly or "BTC 实时价与日线收盘价偏离过大"
        return prices, market, btc_change, anomaly

    def risk_state(self, btc_change=None, anomaly=None) -> RiskState:
        orders = self.db.orders_for_day(self.today())
        buys = [o for o in orders if o.side == "BUY"]
        paused, reason = self.paused()
        return RiskState(today_orders=len(buys), today_notional=sum(o.quote_usd for o in buys),
                         today_auto_notional=sum(o.quote_usd for o in buys if o.via == "auto"),
                         paused=paused, pause_reason=reason, btc_change_7d=btc_change, anomaly=anomaly)

    def portfolio(self, prices) -> Portfolio:
        return Portfolio(self.broker.balances(), prices, self.settings)

    # ------------------------------------------------------------------ cycle
    def run_cycle(self, trigger: str = "schedule") -> Cycle | None:
        if not self._cycle_lock.acquire(blocking=False):
            return None
        try:
            return self._run_cycle(trigger)
        finally:
            self._cycle_lock.release()

    def _run_cycle(self, trigger: str) -> Cycle:
        s = self.settings
        c = self.db.add(Cycle(mode=s.mode, trigger=trigger, provider=s.provider.name, model=self.provider.model))
        self.events.emit("cycle_started", {"id": c.id})
        t0 = time.time()

        def finish(**kw):
            kw.setdefault("duration_ms", int((time.time() - t0) * 1000))
            row = self.db.update(Cycle, c.id, **kw)
            self.events.emit("cycle", {"id": c.id, "status": row.status, "summary": row.summary, "error": row.error})
            return row

        try:
            prices, market, btc_change, anomaly = self.market_snapshot(max_age=0)
            pf = self.portfolio(prices)
        except ExchangeError as e:
            n =self.db.kv_get("anomaly_count", 0) + 1
            self.db.kv_set("anomaly_count", n)
            if n >= s.limits.anomaly_failures_to_pause and not self.paused()[0]:
                self.pause(f"连续 {n} 次无法获取行情或账户：{e}")
            return finish(status="error", error=f"获取数据失败（第 {n} 次）：{e}")

        st = self.risk_state(btc_change, anomaly)
        if anomaly:
            n = self.db.kv_get("anomaly_count", 0) + 1
            self.db.kv_set("anomaly_count", n)
            if n < s.limits.anomaly_failures_to_pause:
                st.anomaly = None   # tolerate transient glitches; still recorded below
        else:
            self.db.kv_set("anomaly_count", 0)

        recent = self.recent_decisions(5)
        context = build_context(s, pf, st, market, recent)
        c = self.db.update(Cycle, c.id, context=context)

        if st.paused:
            return finish(status="skipped", summary=f"已暂停：{st.pause_reason}。本轮不调用模型、不交易。")
        trig = pause_trigger(s, st)
        if trig:
            self.pause(trig)
            return finish(status="skipped", summary=f"触发暂停条件：{trig}")

        prompt = build_user_prompt(context)
        self.db.update(Cycle, c.id, prompt=prompt)
        try:
            comp = self.provider.complete(SYSTEM, [{"role": "user", "content": prompt}], json_mode=True)
        except ProviderError as e:
            self.events.emit("alert", {"text": f"模型调用失败，本轮不交易：{e}"})
            return finish(status="error", error=str(e))
        self.db.update(Cycle, c.id, raw_output=comp.text, model=comp.model, usage=comp.usage)
        try:
            plan = parse_plan(comp.text)
        except PlanError as e:
            self.events.emit("alert", {"text": f"模型输出不合规，本轮不交易：{e}"})
            return finish(status="error", error=f"输出校验失败：{e}")

        actions = [a.model_dump() for a in plan.actions]
        verdicts = evaluate(actions, pf, st)
        risk = []
        for a, v in zip(actions, verdicts, strict=True):
            row = v.to_dict()
            row["reason_model"] = a["reason"]
            row["invalidation"] = a["invalidation"]
            if v.decision == "execute":
                ok, msg, oid = self.execute(v.side, v.symbol, v.approved_usd, via="auto", cycle_id=c.id)
                row["result"] = msg
                row["order_id"] = oid
                if not ok:
                    row["decision"] = "failed"
            elif v.decision == "approve":
                ap = self.create_approval(c.id, v.side, v.symbol, v.approved_usd, a["reason"], a["invalidation"])
                row["approval_id"] = ap.id
                row["result"] = "等待批准"
            risk.append(row)
        return finish(status="done", plan=plan.model_dump(), risk=risk, summary=plan.summary)

    def recent_decisions(self, n: int) -> list[dict]:
        rows = self.db.all(select(Cycle).where(Cycle.status == "done").order_by(desc(Cycle.ts)).limit(n))
        out = []
        for r in rows:
            out.append({
                "time": dt.datetime.fromtimestamp(r.ts, self.tz()).strftime("%m-%d %H:%M"),
                "summary": r.summary[:300],
                "actions": [{"side": x.get("side"), "symbol": x.get("symbol"), "usd": x.get("approved_usd"),
                             "decision": x.get("decision"), "result": x.get("result")} for x in (r.risk or [])],
            })
        return out

    # ------------------------------------------------------------------ execution
    def execute(self, side: str, symbol: str, usd: float, via: str, cycle_id: str = "") -> tuple[bool, str, str]:
        """Last line of defence: re-run every risk check on fresh data, then trade."""
        with self._exec_lock:
            s = self.settings
            try:
                prices, _m, btc_change, anomaly = self.market_snapshot(max_age=0)
                pf = self.portfolio(prices)
            except ExchangeError as e:
                return False, f"未执行：获取最新数据失败：{e}", ""
            st = self.risk_state(btc_change, anomaly)
            v = evaluate([{"side": side, "symbol": symbol, "quote_usd": usd}], pf, st)[0]
            allowed = v.decision == "execute" or (v.decision == "approve" and via == "approved")
            if not allowed:
                return False, f"未执行：{v.reason}", ""
            amount = min(usd, v.approved_usd)
            cid = "bclaw" + uuid.uuid4().hex[:18]
            try:
                if side == "BUY":
                    fill = self.broker.buy(symbol, amount, cid)
                else:
                    token = symbol[: -len(s.quote)]
                    qty = amount / prices[token]
                    fill = self.broker.sell(symbol, qty, cid)
            except ExchangeError as e:
                o = self.db.add(Order(day=self.today(), mode=s.mode, symbol=symbol, side=side, quote_usd=amount,
                                      status="failed", via=via, client_order_id=cid, cycle_id=cycle_id,
                                      detail={"error": str(e)}))
                self.events.emit("order", {"id": o.id, "status": "failed"})
                return False, f"下单失败：{e}", o.id
            o = self.db.add(Order(day=self.today(), mode=s.mode, symbol=symbol, side=side,
                                  quote_usd=abs(fill.quote_spent), qty=fill.qty, price=fill.avg_price, fee=fill.fee,
                                  status=fill.status, via=via, client_order_id=cid, cycle_id=cycle_id,
                                  detail=fill.raw))
            label = "模拟盘" if s.mode == "paper" else "实盘"
            msg = (f"[{label}] {'买入' if side == 'BUY' else '卖出'} {fill.qty:.8g} {symbol[: -len(s.quote)]} "
                   f"@ ${fill.avg_price:,.2f}，金额 ${abs(fill.quote_spent):,.2f}")
            self.events.emit("order", {"id": o.id, "status": o.status, "text": msg, "via": via})
            return True, msg, o.id

    # ------------------------------------------------------------------ approvals
    def create_approval(self, cycle_id, side, symbol, usd, reason, invalidation) -> Approval:
        ttl = self.settings.schedule.approval_timeout_minutes * 60
        a = self.db.add(Approval(expires=time.time() + ttl, cycle_id=cycle_id, side=side, symbol=symbol,
                                 quote_usd=usd, reason=reason, invalidation=invalidation))
        self.events.emit("approval_created", {"id": a.id})
        return a

    def resolve_approval(self, aid: str, approve: bool, by: str) -> tuple[bool, str]:
        a = self.db.get(Approval, aid)
        if not a or a.status != "pending":
            return False, "这笔已处理或不存在"
        if time.time() > a.expires:
            if self.db.claim_approval(aid, "expired", "system"):
                self.events.emit("approval", {"id": aid, "status": "expired"})
            return False, "已过期"
        if not approve:
            if self.db.claim_approval(aid, "rejected", by):
                self.db.update(Approval, aid, result="已拒绝")
                self.events.emit("approval", {"id": aid, "status": "rejected", "by": by})
                return True, "已拒绝"
            return False, "这笔已处理"
        if not self.db.claim_approval(aid, "approved", by):
            return False, "这笔已处理"
        ok, msg, _oid = self.execute(a.side, a.symbol, a.quote_usd, via="approved", cycle_id=a.cycle_id)
        self.db.update(Approval, aid, result=msg, status="approved" if ok else "failed")
        self.events.emit("approval", {"id": aid, "status": "approved" if ok else "failed", "text": msg, "by": by})
        return ok, msg

    def expire_approvals(self):
        now = time.time()
        for a in self.db.pending_approvals():
            if a.expires < now and self.db.claim_approval(a.id, "expired", "system"):
                self.db.update(Approval, a.id, result="超时作废")
                self.events.emit("approval", {"id": a.id, "status": "expired"})

    # ------------------------------------------------------------------ live gate
    def live_check(self) -> dict:
        if not (self.secrets.binance_api_key and self.secrets.binance_api_secret):
            return {"ok": False, "rows": [], "blockers": ["未配置币安 API Key"]}
        try:
            r = self.client.api_restrictions()
        except ExchangeError as e:
            return {"ok": False, "rows": [], "blockers": [f"无法读取 Key 权限：{e}"]}
        allow = self.settings.live_gate_allow
        b = gate.blockers(r, allow)
        return {"ok": not b, "rows": gate.check_restrictions(r, allow), "blockers": b}

    def set_mode(self, mode: str, confirm: str = "", by: str = "web") -> tuple[bool, str]:
        if mode == "paper":
            self.settings_store.update({"mode": "paper"}, allow_mode_change=True)
            self._rebuild()
            self.events.emit("mode", {"mode": "paper", "by": by})
            return True, "已切换到模拟盘"
        if mode != "live":
            return False, "未知模式"
        if confirm.strip() != gate.CONFIRM_PHRASE:
            return False, f"请输入确认短语：{gate.CONFIRM_PHRASE}"
        chk = self.live_check()
        if not chk["ok"]:
            return False, "无法开启实盘：" + "；".join(chk["blockers"])
        self.settings_store.update({"mode": "live"}, allow_mode_change=True)
        self._rebuild()
        self.events.emit("mode", {"mode": "live", "by": by})
        return True, "已切换到实盘"

    # ------------------------------------------------------------------ overview
    def overview(self) -> dict:
        s = self.settings
        try:
            prices, market, btc_change, anomaly = self.market_snapshot()
            pf = self.portfolio(prices)
            err = None
        except ExchangeError as e:
            prices, market, btc_change, anomaly, pf, err = {}, {}, None, None, None, str(e)
        paused, reason = self.paused()
        st = self.risk_state(btc_change, anomaly)
        out = {
            "mode": s.mode, "paused": paused, "pause_reason": reason, "error": err,
            "provider": {"name": s.provider.name, "model": self.provider.model},
            "next_cycle_at": self.next_cycle_at(), "cycle_minutes": s.schedule.cycle_minutes,
            "today": {"orders": st.today_orders, "notional": st.today_notional,
                      "max_orders": s.limits.max_orders_per_day, "max_notional": s.limits.max_daily_notional_usd},
            "pending_approvals": len(self.db.pending_approvals()),
            "btc_change_7d": btc_change, "anomaly": anomaly, "market": market,
            "targets": {n: {"tokens": c.tokens, "band": list(c.band)} for n, c in s.categories.items()},
            "limits": s.limits.model_dump(),
        }
        if pf:
            out.update(total=pf.total(), values=pf.values(), balances=pf.balances,
                       token_weights=pf.token_weights(), category_weights=pf.category_weights())
        return out

    def equity_series(self, limit: int = 500) -> list[dict]:
        rows = self.db.all(select(Cycle).order_by(desc(Cycle.ts)).limit(limit))
        pts = [{"t": r.ts, "total": (r.context or {}).get("portfolio", {}).get("total_usd"), "mode": r.mode}
               for r in rows if (r.context or {}).get("portfolio")]
        return list(reversed(pts))

    # ------------------------------------------------------------------ chat & reports
    def chat(self, text: str, channel: str = "web") -> str:
        with self._chat_lock:
            self.db.add(ChatMessage(channel=channel, role="user", text=text))
            cut = self.db.kv_get("chat_cut_ts", 0)
            hist = list(reversed(self.db.all(select(ChatMessage).where(ChatMessage.ts > cut)
                                             .order_by(desc(ChatMessage.ts)).limit(20))))
            snapshot = self.overview()
            slim = {k: snapshot.get(k) for k in ("mode", "paused", "pause_reason", "total", "token_weights",
                                                  "category_weights", "targets", "today", "btc_change_7d", "pending_approvals")}
            slim["recent_decisions"] = self.recent_decisions(3)
            import json as _json
            system = CHAT_SYSTEM + "\n<data>\n" + _json.dumps(slim, ensure_ascii=False, default=str) + "\n</data>"
            msgs = [{"role": m.role, "content": m.text} for m in hist]
            try:
                comp = self.provider.complete(system, msgs)
                reply = comp.text.strip() or "（空回复）"
            except ProviderError as e:
                reply = f"模型调用失败：{e}"
            self.db.add(ChatMessage(channel=channel, role="assistant", text=reply, provider=self.provider.name))
            self.events.emit("chat", {"channel": channel})
            return reply

    def daily_report(self) -> Report:
        s = self.settings
        ov = self.overview()
        since = time.time() - 86400
        orders = self.db.all(select(Order).where(Order.ts >= since).order_by(Order.ts))
        lines = [f"📊 BinanceClaw 日报 {self.today()}（{'模拟盘' if s.mode == 'paper' else '实盘'}）",
                 f"总资产 ${ov.get('total', 0):,.2f}" + ("  ⏸ 已暂停" if ov["paused"] else "")]
        for cat, w in (ov.get("category_weights") or {}).items():
            band = ov["targets"].get(cat, {}).get("band", [0, 0])
            lines.append(f"· {cat} {w:.1%}（区间 {band[0]:.0%}–{band[1]:.0%}）")
        if ov.get("btc_change_7d") is not None:
            lines.append(f"BTC 7 日 {ov['btc_change_7d']:+.1%}")
        lines.append(f"过去 24 小时成交 {len([o for o in orders if o.status in ('filled', 'submitted')])} 笔：")
        for o in orders:
            lines.append(f"  · {dt.datetime.fromtimestamp(o.ts, self.tz()):%m-%d %H:%M} {o.side} {o.symbol} ${o.quote_usd:,.2f} {o.status}")
        body = "\n".join(lines)
        try:
            comp = self.provider.complete(CHAT_SYSTEM, [{"role": "user", "content":
                "根据以下数据写今日点评（不超过 300 字）：当前配置偏离、过去 24 小时决策是否合理、"
                "距离暂停条件多远、需要我关注的事（每条写理由和判断错了的信号）。\n<data>\n" + body + "\n</data>"}])
            body += "\n\n— AI 点评 —\n" + comp.text.strip()
        except ProviderError as e:
            body += f"\n\n（AI 点评失败：{e}）"
        rep = Report(day=self.today(), text=body)
        with self.db.write_lock, self.db.session() as ses:
            ses.merge(rep)
            ses.commit()
        self.events.emit("report", {"day": rep.day, "text": body})
        return rep

    # ------------------------------------------------------------------ scheduler
    def start_scheduler(self):
        threading.Thread(target=self._loop, name="scheduler", daemon=True).start()

    def _loop(self):
        if not self.next_cycle_at() or self.next_cycle_at() < time.time():
            self.db.kv_set("next_cycle_at", time.time() + 20)
        while True:
            try:
                self.expire_approvals()
                if time.time() >= self.next_cycle_at():
                    self.db.kv_set("next_cycle_at", time.time() + self.settings.schedule.cycle_minutes * 60)
                    self.run_cycle("schedule")
                hh, mm = map(int, self.settings.schedule.daily_report_time.split(":"))
                now = self.now()
                if (now.hour, now.minute) >= (hh, mm) and self.db.kv_get("last_report_day") != self.today():
                    self.db.kv_set("last_report_day", self.today())
                    self.daily_report()
            except Exception:
                log.exception("scheduler iteration failed")
            time.sleep(5)
