import { motion } from "framer-motion";
import { forwardRef, useState } from "react";
import { post, type AiQuestion } from "../api";
import { clock, useToast } from "../lib";
import { Countdown, useNow } from "./ApprovalRow";

/** A question the AI put to the owner: one button per option; the answer wakes a decision round. */
export const QuestionCard = forwardRef<HTMLDivElement, { q: AiQuestion; onTimeout: "skip" | "auto"; onDone?: () => void }>(
  function QuestionCard({ q, onTimeout, onDone }, ref) {
    const toast = useToast();
    const [busy, setBusy] = useState<number | null>(null);
    const left = Math.max(0, q.expires - useNow());
    const expired = left <= 0;

    async function answer(i: number) {
      setBusy(i);
      try {
        const r = await post<{ ok: boolean; message: string }>(`/questions/${q.id}/answer`, { option: i });
        toast(r.message, r.ok ? "ok" : "err");
        onDone?.();
      } catch (e) {
        toast((e as Error).message, "err");
      } finally {
        setBusy(null);
      }
    }

    return (
      <motion.div ref={ref} layout initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, height: 0 }}
        transition={{ duration: 0.2 }} className="border-b border-line py-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-fg-3">
          <span className="tag bg-warn/15 text-warn">AI 提问</span>
          <span className="font-semibold text-fg">{q.topic}</span>
          <span className="num">{clock(q.created)}</span>
          <span className="ml-auto">{expired ? "已过期" : <>剩余 <Countdown left={left} /></>}</span>
        </div>
        <p className="mt-2 whitespace-pre-line text-[14px] leading-relaxed">{q.text}</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {q.options.map((o, i) => (
            <button key={i} disabled={busy !== null || expired} onClick={() => answer(i)}
              className="rounded-lg border border-line-strong px-3 py-2 text-left transition-colors hover:border-fg disabled:opacity-50">
              <span className="text-[14px] font-medium">{busy === i ? "提交中…" : o.label}</span>
              {i === q.recommended && <span className="tag ml-2 bg-hover text-fg-2">AI 推荐</span>}
              {o.detail && <span className="mt-0.5 block text-[13px] text-fg-3">{o.detail}</span>}
            </button>
          ))}
        </div>
        <p className="mt-2 text-[12px] text-fg-3">
          回答后 AI 立即按你的选择重新决策，操作仍经过风控和审批；超时{onTimeout === "auto" ? "采用 AI 推荐的选项" : "这件事不操作"}。
        </p>
      </motion.div>
    );
  });

export function questionOutcome(q: AiQuestion): string {
  if (q.status === "answered" && q.answer !== null) return `你选了「${q.options[q.answer]?.label}」`;
  if (q.status === "expired" && q.answer !== null) return `超时，采用推荐「${q.options[q.answer]?.label}」`;
  if (q.status === "expired") return "超时未回答，不操作";
  return "已作废";
}
