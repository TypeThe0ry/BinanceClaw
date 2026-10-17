import { AnimatePresence, motion } from "framer-motion";
import { forwardRef, useEffect, useState } from "react";
import { post, type Approval } from "../api";
import { token, usd, useToast } from "../lib";
import { SideTag } from "./ui";

function CountdownRing({ created, expires }: { created: number; expires: number }) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, []);
  const total = Math.max(1, expires - created);
  const left = Math.max(0, expires - now);
  const frac = left / total;
  const r = 15;
  const c = 2 * Math.PI * r;
  const mm = Math.floor(left / 60);
  const ss = Math.floor(left % 60);
  return (
    <div className="flex items-center gap-2" aria-label={`剩余 ${mm} 分 ${ss} 秒`}>
      <svg width="36" height="36" viewBox="0 0 36 36" className="-rotate-90">
        <circle cx="18" cy="18" r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="3" />
        <circle cx="18" cy="18" r={r} fill="none" stroke={frac < 0.2 ? "#FF5C7A" : "#F5B544"} strokeWidth="3"
          strokeLinecap="round" strokeDasharray={`${frac * c} ${c}`} style={{ transition: "stroke-dasharray 1s linear" }} />
      </svg>
      <span className="num text-[14px] text-ink-soft">{String(mm).padStart(2, "0")}:{String(ss).padStart(2, "0")}</span>
    </div>
  );
}

export const ApprovalCard = forwardRef<HTMLElement, { a: Approval; onDone?: () => void }>(function ApprovalCard({ a, onDone }, ref) {
  const toast = useToast();
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [done, setDone] = useState<null | "approve" | "reject">(null);

  async function act(kind: "approve" | "reject") {
    setBusy(kind);
    try {
      const r = await post<{ ok: boolean; message: string }>(`/approvals/${a.id}/${kind}`);
      if (r.ok) {
        setDone(kind);
        toast(kind === "approve" ? `已执行：${r.message}` : `已拒绝：${a.side === "BUY" ? "买入" : "卖出"} ${token(a.symbol)} ${usd(a.quote_usd, 0)}`);
        window.setTimeout(() => onDone?.(), 900);
      } else {
        toast(r.message, "err");
      }
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(null);
    }
  }

  return (
    <motion.article ref={ref} layout
      initial={{ y: 40, opacity: 0, scale: 0.98 }} animate={{ y: 0, opacity: 1, scale: 1 }} exit={{ x: done === "reject" ? -40 : 40, opacity: 0, transition: { duration: 0.22 } }}
      transition={{ type: "spring", stiffness: 280, damping: 24 }}
      className="glass-strong relative overflow-hidden border-amber/25 p-5">
      <div className="pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full bg-amber/10 blur-3xl" />
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="label">待你批准</p>
          <div className="mt-2 flex items-center gap-2.5">
            <SideTag side={a.side} />
            <span className="text-[18px] font-semibold text-ink">{token(a.symbol)}</span>
            <span className="num font-display text-[30px] leading-none text-ink">{usd(a.quote_usd, 0)}</span>
          </div>
        </div>
        <CountdownRing created={a.created} expires={a.expires} />
      </div>
      <dl className="mt-4 grid gap-3 text-[14px] leading-relaxed">
        <div>
          <dt className="text-[12.5px] text-ink-mute">AI 的理由</dt>
          <dd className="mt-0.5 text-ink-soft">{a.reason}</dd>
        </div>
        <div>
          <dt className="text-[12.5px] text-ink-mute">什么情况说明判断错了</dt>
          <dd className="mt-0.5 text-ink-soft">{a.invalidation}</dd>
        </div>
      </dl>
      <div className="mt-5 flex items-center justify-end gap-2.5">
        <AnimatePresence mode="wait" initial={false}>
          {done ? (
            <motion.div key="done" initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }}
              className={`flex items-center gap-2 text-[14px] ${done === "approve" ? "text-mint" : "text-ink-soft"}`}>
              <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden>
                <motion.path d={done === "approve" ? "M5 12.5l4.5 4.5L19 7.5" : "M6 6l12 12M18 6L6 18"} fill="none" stroke="currentColor"
                  strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"
                  initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ duration: 0.35, ease: "easeOut" }} />
              </svg>
              {done === "approve" ? "已批准并执行" : "已拒绝"}
            </motion.div>
          ) : (
            <motion.div key="btns" className="flex gap-2.5" exit={{ opacity: 0, transition: { duration: 0.12 } }}>
              <button className="btn-ghost" disabled={!!busy} onClick={() => act("reject")}>
                {busy === "reject" ? "处理中…" : "拒绝"}
              </button>
              <button className="btn-primary" disabled={!!busy} onClick={() => act("approve")}>
                {busy === "approve" ? "重新检查风控并执行…" : `批准${a.side === "BUY" ? "买入" : "卖出"} ${usd(a.quote_usd, 0)}`}
              </button>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.article>
  );
});
