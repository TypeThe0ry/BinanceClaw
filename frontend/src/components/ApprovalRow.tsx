import { AnimatePresence, motion } from "framer-motion";
import { forwardRef, useEffect, useState } from "react";
import { post, type Approval } from "../api";
import { clock, token, usd, useToast } from "../lib";
import { Side } from "./ui";

function useNow() {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

function Countdown({ left }: { left: number }) {
  const mm = String(Math.floor(left / 60)).padStart(2, "0");
  const ss = String(Math.floor(left % 60)).padStart(2, "0");
  return <span className={`num ${left < 300 ? "text-down" : "text-warn"}`}>{mm}:{ss}</span>;
}

export const ApprovalRow = forwardRef<HTMLDivElement, { a: Approval; onDone?: () => void }>(function ApprovalRow({ a, onDone }, ref) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const left = Math.max(0, a.expires - useNow());
  const expired = left <= 0;

  async function act(kind: "approve" | "reject") {
    setBusy(kind);
    try {
      const r = await post<{ ok: boolean; message: string }>(`/approvals/${a.id}/${kind}`);
      toast(r.ok ? (kind === "approve" ? r.message : `已拒绝 ${token(a.symbol)} ${usd(a.quote_usd, 0)}`) : r.message, r.ok ? "ok" : "err");
      onDone?.();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(null);
    }
  }

  return (
    <motion.div ref={ref} layout initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.2 }} className="border-b border-line">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 py-3.5">
        <button onClick={() => setOpen(!open)} className="flex min-w-0 flex-1 items-center gap-x-4 whitespace-nowrap text-left sm:gap-x-5" aria-expanded={open}>
          <span className="num hidden w-24 text-[13px] text-fg-3 sm:block">{clock(a.created)}</span>
          <span className="flex items-center gap-2 text-[14px]"><Side side={a.side} /><b className="font-semibold">{token(a.symbol)}</b></span>
          <span className="num text-[14px]">{usd(a.quote_usd, 0)}</span>
          <span className="text-[13px] text-fg-3">{expired ? "已过期" : <><span className="hidden sm:inline">剩余 </span><Countdown left={left} /></>}</span>
          <span className={`ml-auto text-fg-3 transition-transform ${open ? "rotate-90" : ""}`}>›</span>
        </button>
        <div className={`flex gap-2 ${expired ? "invisible" : ""}`}>
          <button className="btn-secondary btn-sm" disabled={!!busy} onClick={() => act("reject")}>{busy === "reject" ? "…" : "拒绝"}</button>
          <button className={`${a.side === "BUY" ? "btn-up" : "btn-down"} btn-sm`} disabled={!!busy || expired} onClick={() => act("approve")}>
            {busy === "approve" ? "执行中…" : "批准"}
          </button>
        </div>
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.dl initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            className="grid gap-3 overflow-hidden pb-4 text-[14px] leading-relaxed sm:grid-cols-2">
            <div><dt className="text-[12px] text-fg-3">理由</dt><dd className="mt-1">{a.reason}</dd></div>
            <div><dt className="text-[12px] text-fg-3">判断错了的信号</dt><dd className="mt-1">{a.invalidation}</dd></div>
          </motion.dl>
        )}
      </AnimatePresence>
    </motion.div>
  );
});
