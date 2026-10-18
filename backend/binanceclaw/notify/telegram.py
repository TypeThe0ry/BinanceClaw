"""Telegram bot: push notifications, approval buttons, commands and chat with the model.
Only the bound chat id (the owner) is ever served."""
from __future__ import annotations

import datetime as dt
import json
import logging
import queue
import secrets
import threading
import time

import httpx
from sqlmodel import desc, select

from ..config import ConfirmationRequired
from ..core.autopilot import TRIGGER_LABELS
from ..db import AiQuestion, Approval, Cycle, Order
from . import charts

log = logging.getLogger("binanceclaw.telegram")
MAX = 4000

HELP = """BinanceClaw 指令
/status  资产、配置与状态
/run     立即跑一轮 AI 决策
/pause   暂停自动执行
/resume  恢复自动执行
/kill    紧急停止（暂停并作废所有待审批）
/pending 待审批和 AI 提问
/log     最近 5 轮决策
/report  立即生成日报
/chart   K 线图，例：/chart ETH
/alert   价格提醒：/alert BTC < 76000 备注
/alerts  提醒列表 · /unalert 编号 删除
/pnl     成本与盈亏
/preset  风险预设：/preset conservative|balanced|aggressive
/budget  AI 投入预算：/budget 3000，/budget off
/lock    锁定资产：/lock BTC 0.5，/lock BTC 0 解除
/assets  分配：/assets ai（AI 自主）或 /assets manual（手动）；/assets all|whitelist|blacklist 选 AI 范围；/assets BTC,ETH 设白名单；/assets block DOGE / unblock DOGE 改黑名单
/alloc   让 AI 调整目标配置：/alloc 加 10% TON，从 BTC 里减；只发 /alloc 则让 AI 生成一份
/positions 合约持仓
/flat yes 平掉 BinanceClaw 的全部合约仓位
/new     开始新的对话
其他文字：直接和当前模型对话（只读，不能下单）"""


def fmt_setting(v) -> str:
    if v is None:
        return "不限"
    if isinstance(v, bool):
        return "开启" if v else "关闭"
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, list):
        return "、".join(map(str, v)) or "无"
    if isinstance(v, dict):
        return "，".join(f"{k} {fmt_setting(x) if not isinstance(x, dict) else '…'}" for k, x in v.items()) or "无"
    return ENUM_LABEL.get(v, str(v))


UNIVERSE_LABEL = {"all": "全部资产", "whitelist": "白名单", "blacklist": "黑名单"}
ENUM_LABEL = {**UNIVERSE_LABEL, "ai": "AI 自主分配", "targets": "手动分配"}


def _token_args(args: list[str]) -> list[str]:
    return [t for t in " ".join(args).replace("，", ",").replace(" ", ",").split(",") if t]


class TelegramBot:
    def __init__(self, svc, token: str, chat_id: str):
        self.svc = svc
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat_id = int(chat_id)
        self.http = httpx.Client(timeout=40)
        self._outbox: queue.Queue = queue.Queue()
        self._confirms: dict[str, tuple] = {}   # id -> (expires, apply(token), done text)
        # events are queued so a slow Telegram API never blocks trading code
        svc.events.on(lambda k, d: self._outbox.put((k, d)))

    # ------------------------------------------------------------------ API
    def _call(self, method, payload, timeout=40, files=None):
        """Telegram call with retries: honours 429 retry_after and retries transient network errors."""
        last = None
        for attempt in range(4):
            try:
                if files:
                    r = self.http.post(f"{self.api}/{method}", data=payload, files=files, timeout=timeout)
                else:
                    r = self.http.post(f"{self.api}/{method}", json=payload, timeout=timeout)
                body = r.json()
            except (httpx.HTTPError, ValueError) as e:
                last = e
                time.sleep(min(2 ** attempt, 8))
                continue
            if body.get("ok"):
                return body["result"]
            if body.get("error_code") == 429 and attempt < 3:
                time.sleep(min(float((body.get("parameters") or {}).get("retry_after", 2)), 30))
                continue
            raise RuntimeError(f"telegram {method}: {body.get('description')}")
        raise RuntimeError(f"telegram {method}: {type(last).__name__}")

    def send(self, text: str, buttons=None) -> int | None:
        text = text.replace("**", "")   # Telegram plain text: drop Markdown bold markers
        chunks = [text[i:i + MAX] for i in range(0, len(text), MAX)] or [""]
        mid = None
        for i, ch in enumerate(chunks):
            p = {"chat_id": self.chat_id, "text": ch, "disable_web_page_preview": True}
            if buttons and i == len(chunks) - 1:
                p["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
            try:
                mid = self._call("sendMessage", p)["message_id"]
            except Exception as e:
                log.warning("send failed: %s", e)
        return mid

    def photo(self, png: bytes, caption: str = "", buttons=None) -> int | None:
        caption = caption.replace("**", "")
        data = {"chat_id": str(self.chat_id), "caption": caption[:1024]}
        if buttons:
            data["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]})
        try:
            return self._call("sendPhoto", data, timeout=60, files={"photo": ("chart.png", png, "image/png")})["message_id"]
        except Exception as e:
            log.warning("sendPhoto failed: %s", e)
        return self.send(caption, buttons)          # fall back to text so nothing is lost

    def edit(self, mid: int | None, text: str):
        if not mid:
            return self.send(text)
        for method, field, limit in (("editMessageText", "text", MAX), ("editMessageCaption", "caption", 1024)):
            try:
                self._call(method, {"chat_id": self.chat_id, "message_id": mid, field: text[:limit]})
                return
            except Exception:
                continue
        self.send(text)

    # ------------------------------------------------------------------ charts
    def chart_png(self, symbol: str, interval: str = "4h", limit: int = 90, pending: tuple[float, float] | None = None,
                  title: str = "") -> bytes | None:
        try:
            kl = self.svc.client.klines(symbol, interval, limit)
        except Exception as e:
            log.warning("klines for chart failed: %s", e)
            return None
        since = kl[0]["t"] if kl else 0
        orders = self.svc.db.all(select(Order).where(Order.symbol == symbol, Order.ts >= since,
                                                     Order.status.in_(("filled", "submitted"))))
        marks = [{"t": o.ts, "side": o.side, "label": f"${o.quote_usd:,.0f}"} for o in orders]
        if pending:
            marks.append({"t": pending[0], "side": "BUY", "label": f"${pending[1]:,.0f}?", "pending": True})
        try:
            return charts.candles(symbol, kl, marks, title=title)
        except Exception as e:
            log.warning("chart render failed: %s", e)
            return None

    def answer(self, cid, text=""):
        try:
            self._call("answerCallbackQuery", {"callback_query_id": cid, "text": text[:190]})
        except Exception:
            pass

    # ------------------------------------------------------------------ events -> messages
    def label(self):
        return "模拟盘" if self.svc.settings.mode == "paper" else "实盘"

    def on_event(self, kind: str, d: dict):
        if kind == "order" and d.get("via") == "auto" and d.get("text"):
            o = self.svc.db.get(Order, d["id"])
            png = self.chart_png(o.symbol, title="AUTO") if o else None
            cap = f"🟢 自动执行\n{d['text']}"
            self.photo(png, cap) if png else self.send(cap)
        elif kind == "order" and d.get("status") == "failed":
            self.send("⚠️ 下单失败，详见网页「交易」页")
        elif kind == "approval_created":
            self._send_approval(d["id"])
        elif kind == "question_created":
            self._send_question(d["id"])
        elif kind == "question":
            q = self.svc.db.get(AiQuestion, d["id"])
            if q:
                self.edit(q.tg_message_id, self._question_outcome(q))
        elif kind == "approval":
            a = self.svc.db.get(Approval, d["id"])
            if a:
                icon = {"approved": "✅", "rejected": "❌", "expired": "⌛", "failed": "⚪"}.get(a.status, "•")
                by = {"web": "（网页）", "telegram": "（Telegram）"}.get(a.resolved_by, "")
                self.edit(a.tg_message_id, f"{icon} {self._ap_title(a)} — {a.status}{by}\n{a.result}")
        elif kind == "futures" and d.get("text"):
            icon = "🟢" if d.get("ok") else "⚠️"
            if d.get("op") in ("STOP_HIT", "LIQUIDATED", "STOP_FAILED"):
                icon = "🔴"
            elif d.get("op") == "TP_HIT":
                icon = "🎯"
            self.send(f"{icon} 合约 {d['text']}")
        elif kind == "paused":
            self.send(f"⏸ 已暂停自动执行\n{d.get('reason')}\n\n确认无误后发送 /resume 恢复。")
        elif kind == "resumed":
            self.send("▶️ 已恢复自动执行")
        elif kind == "mode":
            self.send(f"🔁 已切换到{'实盘 🔴' if d.get('mode') == 'live' else '模拟盘'}")
        elif kind == "alert":
            self.send("⚠️ " + d.get("text", ""))
        elif kind == "price_alert":
            arrow = "跌破" if d.get("op") == "below" else "涨破"
            who = "AI 设置的" if d.get("source") == "ai" else ""
            cap = (f"🔔 {who}价格提醒：{d['symbol']} {arrow} {d['price']:,.2f}\n现价 {d['now']:,.2f}"
                   + (f"\n{d['note']}" if d.get("note") else ""))
            png = None
            try:
                kl = self.svc.client.klines(d["symbol"], "4h", 90)
                png = charts.candles(d["symbol"], kl, [], title="ALERT", level=d["price"])
            except Exception as e:
                log.warning("alert chart failed: %s", e)
            self.photo(png, cap) if png else self.send(cap)
        elif kind == "report":
            self._send_report(d.get("text", ""))
        elif kind == "cycle" and d.get("status") == "done":
            c = self.svc.db.get(Cycle, d["id"])
            # auto mode: the AI's own routine check-ins that change nothing stay quiet; event-woken rounds report
            if c and not c.risk and c.trigger != "ai_scheduled" and not (c.plan or {}).get("questions"):
                why = TRIGGER_LABELS.get(c.trigger)
                self.send(f"🧠 本轮决策{f'（{why}）' if why and c.trigger not in ('schedule', 'manual') else ''}：不操作\n{c.summary[:600]}")

    @staticmethod
    def _ap_title(a) -> str:
        if a.kind != "futures":
            return f"{a.side} {a.symbol}  ${a.quote_usd:,.0f}"
        p = a.payload or {}
        name = {"OPEN_LONG": "合约开多", "OPEN_SHORT": "合约开空"}.get(a.side, a.side)
        out = f"{name} {a.symbol}  保证金 ${a.quote_usd:,.0f} × {p.get('leverage')}x\n止损 {p.get('stop_loss')}"
        return out + (f"  止盈 {p['take_profit']}" if p.get("take_profit") else "")

    def futures_chart(self, symbol: str, level: float | None, title: str = "") -> bytes | None:
        try:
            kl = self.svc.fclient.klines(symbol, "1h", 120)
            return charts.candles(symbol + " PERP", kl, [], title=title, level=level)
        except Exception as e:
            log.warning("futures chart failed: %s", e)
            return None

    def _send_approval(self, aid):
        a = self.svc.db.get(Approval, aid)
        if not a:
            return
        mins = int((a.expires - time.time()) / 60)
        if a.kind == "futures":
            buttons = [[("✅ 批准", f"ap:{aid}"), ("❌ 拒绝", f"rj:{aid}")]]
            cap = (f"🟡 待批准 [{self.label()}]\n{self._ap_title(a)}\n\n理由：{a.reason[:330]}\n\n"
                   f"判断错了的信号：{a.invalidation[:250]}\n\n{mins} 分钟内有效。批准时会用最新价格重新做风控检查。")
            png = self.futures_chart(a.symbol, (a.payload or {}).get("stop_loss"), "PENDING")
            mid = self.photo(png, cap[:1024], buttons) if png else self.send(cap, buttons)
            self.svc.db.update(Approval, aid, tg_message_id=mid)
            return
        text = (f"🟡 待批准 [{self.label()}]\n{a.side} {a.symbol}  ${a.quote_usd:,.0f}\n\n"
                f"理由：{a.reason}\n\n判断错了的信号：{a.invalidation}\n\n{mins} 分钟内有效。批准时会用最新价格重新做风控检查。")
        buttons = [[("✅ 批准", f"ap:{aid}"), ("❌ 拒绝", f"rj:{aid}")]]
        png = self.chart_png(a.symbol, pending=(time.time(), a.quote_usd), title="PENDING")
        if png:
            cap = (f"🟡 待批准 [{self.label()}]\n{a.side} {a.symbol}  ${a.quote_usd:,.0f}\n\n"
                   f"理由：{a.reason[:330]}\n\n判断错了的信号：{a.invalidation[:250]}\n\n{mins} 分钟内有效")
            mid = self.photo(png, cap, buttons)
        else:
            mid = self.send(text, buttons)
        self.svc.db.update(Approval, aid, tg_message_id=mid)

    def _send_question(self, qid):
        q = self.svc.db.get(AiQuestion, qid)
        if not q or q.status != "pending":
            return
        mins = int((q.expires - time.time()) / 60)
        lines = [f"❓ AI 想问你 [{self.label()}]", q.text, ""]
        for i, o in enumerate(q.options):
            star = "（AI 推荐）" if i == q.recommended else ""
            lines.append(f"{i + 1}. {o['label']}{star}" + (f"：{o['detail']}" if o.get("detail") else ""))
        miss = ("采用 AI 推荐的选项" if self.svc.settings.questions.on_timeout == "auto" else "这件事不操作")
        lines.append(f"\n{mins} 分钟内回答；超时{miss}。选中的操作仍会经过风控和审批。")
        buttons = [[(f"{i + 1}. {o['label']}"[:60], f"qa:{q.id}:{i}")] for i, o in enumerate(q.options)]
        mid = self.send("\n".join(lines), buttons)
        self.svc.db.update(AiQuestion, qid, tg_message_id=mid)

    @staticmethod
    def _question_outcome(q) -> str:
        head = f"❓ {q.text[:600]}\n\n"
        if q.status == "answered":
            by = {"web": "（网页）", "telegram": "（Telegram）"}.get(q.resolved_by, "")
            return head + f"✅ 你选了「{q.options[q.answer]['label']}」{by}，AI 正在按你的回答决策"
        if q.status == "expired" and q.answer is not None:
            return head + f"⌛ 超时未回答，按设置采用 AI 推荐的「{q.options[q.answer]['label']}」"
        if q.status == "expired":
            return head + "⌛ 超时未回答，按设置这件事不操作"
        return head + "⚪ 已作废"

    def _send_report(self, text: str):
        try:
            ov = self.svc.overview()
            self.photo(charts.equity(self.svc.equity_series(500)), "📈 资产曲线")
            if ov.get("category_weights"):
                self.photo(charts.allocation(ov["category_weights"], ov["targets"]), "🧭 配置 vs 目标")
        except Exception as e:
            log.warning("report charts failed: %s", e)
        self.send(text)

    # ------------------------------------------------------------------ commands
    def status_text(self) -> str:
        ov = self.svc.overview()
        lines = [f"[{self.label()}] 总资产 ${ov.get('total', 0):,.2f}",
                 f"模型：{ov['provider']['name']} {ov['provider']['model'] or ''}".strip()]
        for cat, w in (ov.get("category_weights") or {}).items():
            spec = ov["targets"].get(cat, {})
            tv = [v for v in spec.get("tokens", {}).values() if v is not None]
            lo, hi = spec.get("band", [0, 0])
            lines.append(f"· {cat} {w:.1%}（目标 {sum(tv):.0%}，区间 {lo:.0%}–{hi:.0%}）" if tv
                         else f"· {cat} {w:.1%}（AI 自主，上限 {hi:.0%}）")
        if ov.get("btc_change_7d") is not None:
            lines.append(f"BTC 7 日 {ov['btc_change_7d']:+.1%}")
        t = ov["today"]
        lines.append(f"今日 {t['orders']}/{t['max_orders']} 笔，${t['notional']:,.0f}/${t['max_notional']:,.0f}")
        lines.append("状态：" + (f"⏸ 暂停（{ov['pause_reason']}）" if ov["paused"] else "▶️ 运行中"))
        ap = ov.get("autopilot") or {}
        if ap.get("decision_mode") == "auto":
            nxt = f"，最晚 {dt.datetime.fromtimestamp(ov['next_cycle_at'], self.svc.tz()):%H:%M} 复查" if ov.get("next_cycle_at") else ""
            lines.append(f"决策：AI 自主实时{nxt}，今日调用 {ap.get('calls_today', 0)}/{ap.get('max_calls_per_day')}")
        elif ov.get("next_cycle_at"):
            lines.append(f"下一轮：{dt.datetime.fromtimestamp(ov['next_cycle_at'], self.svc.tz()):%H:%M}")
        cap = ov.get("capital", {})
        lines.append(self._assets_line(self.svc.settings.allocation) + f" · 风险 {ov.get('risk_preset')}")
        if cap.get("budget_usd") is not None:
            lines.append(f"AI 预算：已投入 ${cap['deployed_usd']:,.0f} / ${cap['budget_usd']:,.0f}")
        if cap.get("locked"):
            lines.append("锁定：" + "，".join(f"{k} {v:g}" for k, v in cap["locked"].items()))
        if ov["pending_approvals"]:
            lines.append(f"待审批：{ov['pending_approvals']} 笔（/pending）")
        if ov.get("pending_questions"):
            lines.append(f"AI 提问待回答：{ov['pending_questions']} 个（/pending）")
        return "\n".join(lines)

    def handle_text(self, text: str):
        cmd = text.split()[0].split("@")[0].lower()
        svc = self.svc
        if cmd in ("/start", "/help"):
            self.send(HELP)
        elif cmd == "/status":
            self.send(self.status_text())
        elif cmd == "/run":
            self.send("🧠 正在运行一轮决策…")
            threading.Thread(target=self._safe, args=(lambda: svc.run_cycle("manual") or self.send("上一轮还在运行"),), daemon=True).start()
        elif cmd == "/pause":
            svc.pause("你在 Telegram 手动暂停", "telegram")
        elif cmd == "/resume":
            svc.resume("telegram")
        elif cmd == "/kill":
            svc.kill_switch("telegram")
        elif cmd == "/pending":
            ps, qs = svc.db.pending_approvals(), svc.db.pending_questions()
            if not ps and not qs:
                self.send("没有待审批的订单，也没有待回答的提问")
            for a in ps:
                self._send_approval(a.id)
            for q in qs:
                self._send_question(q.id)
        elif cmd == "/log":
            rows = svc.db.all(select(Cycle).order_by(desc(Cycle.ts)).limit(5))
            out = []
            for c in rows:
                t = dt.datetime.fromtimestamp(c.ts, svc.tz()).strftime("%m-%d %H:%M")
                acts = "；".join(f"{r.get('side') or r.get('op')} {r.get('symbol')} ${(r.get('approved_usd') or r.get('margin') or 0):.0f}"
                                f"→{r.get('decision')}" for r in (c.risk or [])) or "无动作"
                out.append(f"{t} [{c.status}] {(c.summary or c.error)[:160]}\n   {acts}")
            self.send("\n\n".join(out) or "暂无记录")
        elif cmd == "/positions":
            try:
                ps = svc.fbroker.snapshot() if svc.fbroker.open_rows() else []
            except Exception as e:
                self.send(f"读取合约持仓失败：{e}")
                return
            if not ps:
                self.send("没有合约持仓" + ("" if svc.settings.futures.enabled else "（合约未开启）"))
            for p in ps:
                self.send(f"{p['symbol']} {'多' if p['side'] == 'LONG' else '空'} {p['leverage']}x  {p['qty']:g} @ {p['entry']:,.4g}\n"
                          f"标记 {p['mark']:,.4g}  浮盈 ${p['upnl']:+,.2f}（{(p['roe'] or 0):+.1%}）\n"
                          f"保证金 ${p['margin']:,.2f}  止损 {p['stop']:g}"
                          + (f"  止盈 {p['take_profit']:g}" if p.get("take_profit") else ""))
        elif cmd == "/flat":
            if (text.split() + [""])[1].lower() != "yes":
                self.send("确认平掉 BinanceClaw 开的全部合约仓位？发送 /flat yes")
            else:
                msgs = svc.flatten_futures("telegram")
                self.send("\n".join(msgs) or "没有合约持仓")
        elif cmd == "/report":
            self.send("正在生成日报…")
            threading.Thread(target=self._safe, args=(svc.daily_report,), daemon=True).start()
        elif cmd == "/chart":
            parts = text.split()
            tok = (parts[1] if len(parts) > 1 else "BTC").upper().replace("USDT", "")
            sym = f"{tok}{svc.settings.quote}"
            if sym not in svc.settings.symbols():
                self.send(f"只支持：{', '.join(t for t in svc.settings.tradable_tokens())}")
            else:
                interval = parts[2] if len(parts) > 2 and parts[2] in ("1h", "4h", "1d") else "4h"
                png = self.chart_png(sym, interval)
                self.photo(png, f"{sym} · {interval}") if png else self.send("图表生成失败")
        elif cmd == "/alert":
            parts = text.replace("<", " < ").replace(">", " > ").split()
            try:
                tok, sign, price = parts[1], parts[2], float(parts[3].replace(",", ""))
                note = " ".join(parts[4:])
                a = svc.add_alert(tok, {"<": "below", ">": "above"}[sign], price, note, "telegram")
                self.send(f"✅ 已设置提醒 #{a.id[:6]}：{a.symbol} {'跌破' if a.op == 'below' else '涨破'} {a.price:,.2f}")
            except (IndexError, KeyError, ValueError) as e:
                self.send(f"格式：/alert BTC < 76000 备注（{e}）" if str(e) else "格式：/alert BTC < 76000 备注")
        elif cmd == "/alerts":
            al = svc.active_alerts()
            self.send("\n".join(f"#{a.id[:6]} {a.symbol} {'<' if a.op == 'below' else '>'} {a.price:,.2f}"
                                f"{' · AI' if a.source == 'ai' else ''}{(' · ' + a.note) if a.note else ''}" for a in al)
                      or "没有生效中的提醒")
        elif cmd == "/unalert":
            key = (text.split() + [""])[1]
            hits = [a for a in svc.active_alerts() if a.id.startswith(key)] if key else []
            if len(hits) == 1 and svc.cancel_alert(hits[0].id):
                self.send(f"✅ 已删除提醒 #{key}")
            else:
                self.send("没找到这个提醒，用 /alerts 查看编号")
        elif cmd == "/pnl":
            try:
                pf = svc.performance()
            except Exception as e:
                self.send(f"获取失败：{e}")
                return
            lines = [f"成本与盈亏（{'模拟盘' if pf['mode'] == 'paper' else '实盘'}，只统计 BinanceClaw 的成交）",
                     f"累计买入 ${pf['invested']:,.2f} · 现值 ${pf['value']:,.2f}",
                     f"盈亏 ${pf['pnl']:+,.2f}" + (f"（{pf['roi']:+.2%}）" if pf["roi"] is not None else "")]
            b = pf["benchmark"]
            if b["pnl"] is not None:
                lines.append(f"同样的钱全买 BTC：${b['pnl']:+,.2f}" + (f"（{b['roi']:+.2%}）" if b["roi"] is not None else ""))
            for tok, a in pf["assets"].items():
                if a["qty"] > 1e-12:
                    lines.append(f"· {tok} 成本 {a['avg_cost']:,.2f} · 浮盈 ${a['unrealized'] or 0:+,.2f}"
                                 + (f"（{a['unrealized_pct']:+.1%}）" if a["unrealized_pct"] is not None else ""))
            self.send("\n".join(lines))
        elif cmd in ("/preset", "/budget", "/lock", "/assets"):
            self._intervene(cmd, text.split()[1:])
        elif cmd == "/alloc":
            instruction = text.partition(" ")[2].strip()
            self.send("🧠 正在让 AI " + ("按你的指令调整目标配置…" if instruction else "生成目标配置…"))
            threading.Thread(target=self._safe, args=(lambda: self._send_proposal(svc.propose_allocation(instruction, "telegram")),),
                             daemon=True).start()
        elif cmd == "/new":
            svc.db.kv_set("chat_cut_ts", time.time())
            self.send("已开始新的对话。")
        elif cmd.startswith("/"):
            self.send("未知指令\n\n" + HELP)
        else:
            threading.Thread(target=self._safe, args=(lambda: self.send(svc.chat(text, "telegram")),), daemon=True).start()

    def _intervene(self, cmd: str, args: list[str]):
        svc = self.svc
        try:
            if cmd == "/preset":
                if not args:
                    self.send(f"当前风险预设：{svc.settings.risk_preset}")
                    return
                name = args[0].lower()
                self._guarded(lambda tok: svc.apply_preset(name, guard=True, confirm=tok), f"✅ 已套用风险预设：{name}")
            elif cmd == "/budget":
                if not args:
                    b = svc.settings.capital.budget_usd
                    self.send(f"AI 投入预算：{'不限' if b is None else f'${b:,.0f}'}，已投入 ${svc.deployed_usd():,.0f}")
                    return
                val = None if args[0].lower() in ("off", "none", "不限") else float(args[0].replace(",", ""))
                self._guarded(lambda tok: svc.update_settings({"capital": {"budget_usd": val}}, guard=True, confirm=tok),
                              "✅ 已取消 AI 投入预算" if val is None else f"✅ AI 投入预算设为 ${val:,.0f}")
            elif cmd == "/lock":
                if len(args) < 2:
                    lk = svc.settings.capital.locked
                    self.send("锁定资产：" + ("，".join(f"{k} {v:g}" for k, v in lk.items()) or "无"))
                    return
                asset, amt = args[0].upper(), float(args[1].replace(",", ""))
                locked = dict(svc.settings.capital.locked)
                locked[asset] = amt
                patch = {"capital": {"locked": {k: v for k, v in locked.items() if v > 0}}}
                self._guarded(lambda tok: svc.update_settings(patch, guard=True, confirm=tok),
                              f"✅ 已解除 {asset} 锁定" if amt == 0 else f"✅ 已锁定 {amt:g} {asset}，AI 不会看到也不会使用")
            elif cmd == "/assets":
                if not args:
                    self.send(self._assets_line(svc.settings.allocation))
                    return
                word = args[0].lower()
                if word in ("ai", "targets", "manual"):
                    mode = "targets" if word == "manual" else word
                    self._guarded(lambda tok: svc.update_settings({"allocation": {"mode": mode}}, guard=True, confirm=tok),
                                  f"✅ 分配方式：{'AI 自主分配' if mode == 'ai' else '手动分配'}")
                elif word in UNIVERSE_LABEL:
                    self._guarded(lambda tok: svc.update_settings({"allocation": {"universe": word}}, guard=True, confirm=tok),
                                  f"✅ AI 可交易范围：{UNIVERSE_LABEL[word]}")
                elif word in ("block", "unblock"):
                    toks = _token_args(args[1:])
                    al = svc.settings.allocation
                    blocked = ([*al.blocked, *toks] if word == "block" else [t for t in al.blocked if t not in toks])
                    self._guarded(lambda tok: svc.update_settings({"allocation": {"blocked": blocked}}, guard=True, confirm=tok),
                                  "✅ 黑名单已更新")
                else:
                    toks = _token_args(args)
                    self._guarded(lambda tok: svc.update_settings({"allocation": {"enabled": toks}}, guard=True, confirm=tok),
                                  f"✅ 可交易资产：{', '.join(toks)}")
        except (ValueError, TypeError) as e:
            self.send(f"⚠️ 没有生效：{e}")

    @staticmethod
    def _assets_line(al) -> str:
        if al.mode == "targets":
            return f"分配：手动分配（自动再平衡{'开，阈值 ' + format(al.rebalance_drift, '.0%') if al.auto_rebalance else '关'}）；可交易：{', '.join(al.enabled)}"
        line = f"分配：AI 自主分配 · 范围 {UNIVERSE_LABEL[al.universe]}"
        if al.universe == "whitelist":
            line += f"：{', '.join(al.enabled)}"
        elif al.blocked:
            line += f" · 黑名单 {', '.join(al.blocked)}"
        return line

    def _guarded(self, apply, done: str):
        """Run a settings change; a dangerous one is held until the owner taps 确认修改."""
        before = self.svc.settings_store.get()
        try:
            apply("")
        except ConfirmationRequired as e:
            cid = secrets.token_hex(4)
            now = time.time()
            self._confirms = {k: v for k, v in self._confirms.items() if v[0] > now}
            self._confirms[cid] = (now + 600, apply, done, e.token)
            lines = ["⚠️ 这是危险设置，确认后才会生效："]
            lines += [f"· {c['label']}：{fmt_setting(c['old'])} → {fmt_setting(c['new'])}" for c in e.changes]
            self.send("\n".join(lines), buttons=[[("确认修改", f"cfm:{cid}"), ("取消", f"cfm_drop:{cid}")]])
            return
        self._done(done, before)

    def _resolve_confirm(self, cid: str, ok: bool):
        item = self._confirms.pop(cid, None)
        if not item or item[0] < time.time():
            self.send("这个确认已失效，请重新发送指令。")
            return
        if not ok:
            self.send("已取消，设置没有改动。")
            return
        _, apply, done, token = item
        before = self.svc.settings_store.get()
        try:
            apply(token)
        except ConfirmationRequired:
            self.send("⚠️ 设置在确认前已经变化，没有生效，请重新发送指令。")
            return
        except (ValueError, TypeError) as e:
            self.send(f"⚠️ 没有生效：{e}")
            return
        self._done(done, before)

    RERUN_TTL = 600

    def _done(self, text: str, before):
        """Report a saved change; if it affects decisions, offer a manual round so the AI adapts now."""
        changed = self.svc.rerun_offer(before)
        if not changed:
            self.send(text)
            return
        self.send(f"{text}\n\n这会影响 AI 之后的决策（{'、'.join(changed)}）。要让 AI 现在按新设置重新跑一轮吗？",
                  buttons=[[("🧠 立即重跑", f"rerun:{int(time.time())}"), ("稍后", "rerun_drop:")]])

    def _rerun(self, ts: str):
        if not ts.isdigit() or time.time() - int(ts) > self.RERUN_TTL:
            self.send("这个提示已过期，需要的话发送 /run 运行一轮决策。")
            return
        if self.svc.paused()[0]:
            self.send("机器人已暂停，本轮不会运行。恢复后发送 /run。")
            return
        self.send("🧠 正在按新设置运行一轮决策…")
        threading.Thread(target=self._safe, args=(lambda: self.svc.run_cycle("manual") or self.send("上一轮还在运行"),),
                         daemon=True).start()

    def _send_proposal(self, p: dict):
        lines = ["📐 目标配置提案（应用前不会生效）", p["summary"], ""]
        for tok in dict.fromkeys([*p["proposed"], *p["current"]]):
            a, b = p["current"].get(tok, 0.0), p["proposed"].get(tok, 0.0)
            mark = "" if abs(a - b) < 1e-6 else f"（原 {a:.1%}）"
            lines.append(f"· {tok} {b:.1%}{mark}")
        lines += [f"⚠️ {n}" for n in p["notes"]]
        lines.append("\n应用后切换为「手动分配」，之后的买入照常经过风控和审批。")
        self.send("\n".join(lines), buttons=[[("✅ 应用", f"alloc_apply:{p['id']}"), ("放弃", f"alloc_drop:{p['id']}")]])

    def _safe(self, fn):
        try:
            fn()
        except Exception as e:
            log.exception("telegram task failed")
            self.send(f"⚠️ 出错：{e}")

    # ------------------------------------------------------------------ loop
    def start(self):
        threading.Thread(target=self._loop, name="telegram", daemon=True).start()
        threading.Thread(target=self._drain, name="telegram-out", daemon=True).start()

    def _drain(self):
        while True:
            kind, d = self._outbox.get()
            try:
                self.on_event(kind, d)
            except Exception:
                log.exception("notify failed")

    def _loop(self):
        offset = self.svc.db.kv_get("tg_offset")
        while True:
            try:
                p = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
                if offset:
                    p["offset"] = offset
                updates = self._call("getUpdates", p, timeout=40)
            except Exception as e:
                log.warning("getUpdates failed: %s", e)
                time.sleep(5)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                self.svc.db.kv_set("tg_offset", offset)
                try:
                    if "callback_query" in u:
                        cq = u["callback_query"]
                        if cq["from"]["id"] != self.chat_id or cq["message"]["chat"]["id"] != self.chat_id:
                            continue
                        kind, _, aid = cq.get("data", "").partition(":")
                        self.answer(cq["id"], "处理中…")
                        if kind in ("cfm", "cfm_drop"):
                            self._resolve_confirm(aid, kind == "cfm")
                            continue
                        if kind == "qa":
                            qid, _, idx = aid.partition(":")
                            ok, msg = self.svc.answer_question(qid, int(idx) if idx.isdigit() else -1, "telegram")
                            if not ok:
                                self.send(f"⚠️ {msg}")
                            continue
                        if kind in ("rerun", "rerun_drop"):
                            if kind == "rerun":
                                self._rerun(aid)
                            continue
                        if kind in ("alloc_apply", "alloc_drop"):
                            before = self.svc.settings_store.get()
                            try:
                                self.svc.resolve_allocation(aid, kind == "alloc_apply", "telegram")
                                if kind == "alloc_apply":
                                    self._done("✅ 已应用新的目标配置", before)
                                else:
                                    self.send("已放弃这个配置提案")
                            except ValueError as e:
                                self.send(f"⚠️ {e}")
                            continue
                        ok, msg = self.svc.resolve_approval(aid, kind == "ap", "telegram")
                        if not ok:
                            self.answer(cq["id"], msg)
                    elif "message" in u:
                        m = u["message"]
                        if m["chat"]["id"] == self.chat_id and m.get("from", {}).get("id") == self.chat_id and m.get("text"):
                            self.handle_text(m["text"].strip())
                        else:
                            log.warning("ignored message from chat %s", m["chat"]["id"])
                except Exception:
                    log.exception("update handling failed")
