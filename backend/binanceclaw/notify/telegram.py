"""Telegram bot: push notifications, approval buttons, commands and chat with the model.
Only the bound chat id (the owner) is ever served."""
from __future__ import annotations

import datetime as dt
import logging
import queue
import threading
import time

import httpx
from sqlmodel import desc, select

from ..db import Approval, Cycle

log = logging.getLogger("binanceclaw.telegram")
MAX = 4000

HELP = """BinanceClaw 指令
/status  资产、配置与状态
/run     立即跑一轮 AI 决策
/pause   暂停自动执行
/resume  恢复自动执行
/kill    紧急停止（暂停并作废所有待审批）
/pending 待审批列表
/log     最近 5 轮决策
/report  立即生成日报
/new     开始新的对话
其他文字：直接和当前模型对话（只读，不能下单）"""


class TelegramBot:
    def __init__(self, svc, token: str, chat_id: str):
        self.svc = svc
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat_id = int(chat_id)
        self.http = httpx.Client(timeout=40)
        self._outbox: queue.Queue = queue.Queue()
        # events are queued so a slow Telegram API never blocks trading code
        svc.events.on(lambda k, d: self._outbox.put((k, d)))

    # ------------------------------------------------------------------ API
    def _call(self, method, payload, timeout=40):
        r = self.http.post(f"{self.api}/{method}", json=payload, timeout=timeout)
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"telegram {method}: {body.get('description')}")
        return body["result"]

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

    def edit(self, mid: int | None, text: str):
        if not mid:
            return self.send(text)
        try:
            self._call("editMessageText", {"chat_id": self.chat_id, "message_id": mid, "text": text[:MAX]})
        except Exception:
            self.send(text)

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
            self.send(f"🟢 自动执行\n{d['text']}")
        elif kind == "order" and d.get("status") == "failed":
            self.send("⚠️ 下单失败，详见网页「交易」页")
        elif kind == "approval_created":
            self._send_approval(d["id"])
        elif kind == "approval":
            a = self.svc.db.get(Approval, d["id"])
            if a:
                icon = {"approved": "✅", "rejected": "❌", "expired": "⌛", "failed": "⚪"}.get(a.status, "•")
                by = {"web": "（网页）", "telegram": "（Telegram）"}.get(a.resolved_by, "")
                self.edit(a.tg_message_id, f"{icon} {a.side} {a.symbol} ${a.quote_usd:,.0f} — {a.status}{by}\n{a.result}")
        elif kind == "paused":
            self.send(f"⏸ 已暂停自动执行\n{d.get('reason')}\n\n确认无误后发送 /resume 恢复。")
        elif kind == "resumed":
            self.send("▶️ 已恢复自动执行")
        elif kind == "mode":
            self.send(f"🔁 已切换到{'实盘 🔴' if d.get('mode') == 'live' else '模拟盘'}")
        elif kind == "alert":
            self.send("⚠️ " + d.get("text", ""))
        elif kind == "report":
            self.send(d.get("text", ""))
        elif kind == "cycle" and d.get("status") == "done":
            c = self.svc.db.get(Cycle, d["id"])
            if c and not c.risk:
                self.send(f"🧠 本轮决策：不操作\n{c.summary[:600]}")

    def _send_approval(self, aid):
        a = self.svc.db.get(Approval, aid)
        if not a:
            return
        mins = int((a.expires - time.time()) / 60)
        text = (f"🟡 待批准 [{self.label()}]\n{a.side} {a.symbol}  ${a.quote_usd:,.0f}\n\n"
                f"理由：{a.reason}\n\n判断错了的信号：{a.invalidation}\n\n{mins} 分钟内有效。批准时会用最新价格重新做风控检查。")
        mid = self.send(text, [[("✅ 批准", f"ap:{aid}"), ("❌ 拒绝", f"rj:{aid}")]])
        self.svc.db.update(Approval, aid, tg_message_id=mid)

    # ------------------------------------------------------------------ commands
    def status_text(self) -> str:
        ov = self.svc.overview()
        lines = [f"[{self.label()}] 总资产 ${ov.get('total', 0):,.2f}",
                 f"模型：{ov['provider']['name']} {ov['provider']['model'] or ''}".strip()]
        for cat, w in (ov.get("category_weights") or {}).items():
            spec = ov["targets"].get(cat, {})
            tgt = sum(spec.get("tokens", {}).values())
            lo, hi = spec.get("band", [0, 0])
            lines.append(f"· {cat} {w:.1%}（目标 {tgt:.0%}，区间 {lo:.0%}–{hi:.0%}）")
        if ov.get("btc_change_7d") is not None:
            lines.append(f"BTC 7 日 {ov['btc_change_7d']:+.1%}")
        t = ov["today"]
        lines.append(f"今日 {t['orders']}/{t['max_orders']} 笔，${t['notional']:,.0f}/${t['max_notional']:,.0f}")
        lines.append("状态：" + (f"⏸ 暂停（{ov['pause_reason']}）" if ov["paused"] else "▶️ 运行中"))
        if ov.get("next_cycle_at"):
            lines.append(f"下一轮：{dt.datetime.fromtimestamp(ov['next_cycle_at'], self.svc.tz()):%H:%M}")
        if ov["pending_approvals"]:
            lines.append(f"待审批：{ov['pending_approvals']} 笔（/pending）")
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
            ps = svc.db.pending_approvals()
            if not ps:
                self.send("没有待审批的订单")
            for a in ps:
                self._send_approval(a.id)
        elif cmd == "/log":
            rows = svc.db.all(select(Cycle).order_by(desc(Cycle.ts)).limit(5))
            out = []
            for c in rows:
                t = dt.datetime.fromtimestamp(c.ts, svc.tz()).strftime("%m-%d %H:%M")
                acts = "；".join(f"{r['side']} {r['symbol']} ${r['approved_usd']:.0f}→{r['decision']}" for r in (c.risk or [])) or "无动作"
                out.append(f"{t} [{c.status}] {(c.summary or c.error)[:160]}\n   {acts}")
            self.send("\n\n".join(out) or "暂无记录")
        elif cmd == "/report":
            self.send("正在生成日报…")
            threading.Thread(target=self._safe, args=(svc.daily_report,), daemon=True).start()
        elif cmd == "/new":
            svc.db.kv_set("chat_cut_ts", time.time())
            self.send("已开始新的对话。")
        elif cmd.startswith("/"):
            self.send("未知指令\n\n" + HELP)
        else:
            threading.Thread(target=self._safe, args=(lambda: self.send(svc.chat(text, "telegram")),), daemon=True).start()

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
