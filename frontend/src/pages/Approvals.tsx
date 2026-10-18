import { AnimatePresence } from "framer-motion";
import { useState } from "react";
import type { AiQuestion, Approval } from "../api";
import { ApprovalRow } from "../components/ApprovalRow";
import { QuestionCard, questionOutcome } from "../components/QuestionCard";
import { DocLink, Empty, Page, Side, Skeleton, Tabs } from "../components/ui";
import { clock, token, usd, useApi } from "../lib";

const STATUS: Record<string, [string, string]> = {
  approved: ["已批准", "text-up"], rejected: ["已拒绝", "text-fg-2"], expired: ["已过期", "text-fg-3"], failed: ["未执行", "text-down"],
};

export default function Approvals() {
  const pending = useApi<Approval[]>("/approvals?status=pending", { kinds: ["approval", "approval_created"] });
  const all = useApi<Approval[]>("/approvals?limit=200", { kinds: ["approval", "approval_created"] });
  const qKinds = { kinds: ["question", "question_created"] };
  const questions = useApi<AiQuestion[]>("/questions?status=pending", qKinds);
  const pastQ = useApi<AiQuestion[]>("/questions?limit=50", qKinds);
  const cfg = useApi<{ settings: { questions?: { on_timeout: "skip" | "auto" } } }>("/settings", { kinds: ["settings"] });
  const [tab, setTab] = useState<"pending" | "history">("pending");
  const history = (all.data ?? []).filter((a) => a.status !== "pending");
  const asked = questions.data ?? [];
  const answered = (pastQ.data ?? []).filter((q) => q.status !== "pending");
  const waiting = (pending.data?.length ?? 0) + asked.length;
  const reloadQ = () => { questions.reload(); pastQ.reload(); };
  return (
    <Page>
      <div className="mb-1 flex items-center gap-1.5"><h1 className="text-[20px] font-semibold">审批</h1><DocLink to="approvals" /></div>
      <Tabs id="appr" value={tab} onChange={setTab} items={[
        { k: "pending", label: <>待处理{!!waiting && <span className="num ml-1 text-warn">{waiting}</span>}</> },
        { k: "history", label: "历史" }]} />
      {tab === "pending" && !!asked.length && (
        <AnimatePresence mode="popLayout" initial={false}>
          {asked.map((q) => <QuestionCard key={q.id} q={q} onTimeout={cfg.data?.settings.questions?.on_timeout ?? "skip"} onDone={reloadQ} />)}
        </AnimatePresence>
      )}
      {tab === "pending" ? (
        pending.loading ? <Skeleton className="mt-4 h-24" /> : pending.data?.length ? (
          <AnimatePresence mode="popLayout" initial={false}>
            {pending.data.map((a) => <ApprovalRow key={a.id} a={a} onDone={() => { pending.reload(); all.reload(); }} />)}
          </AnimatePresence>
        ) : asked.length ? null : <Empty text="没有待处理的审批" />
      ) : history.length || answered.length ? (
        <>
        {!!answered.length && (
          <div className="mb-6">
            <h2 className="mb-1 mt-4 text-[14px] font-semibold">AI 提问</h2>
            {answered.map((q) => (
              <div key={q.id} className="flex gap-4 border-b border-line py-2.5 text-[14px]">
                <span className="num w-24 shrink-0 text-[13px] text-fg-2">{clock(q.created)}</span>
                <span className="min-w-0 flex-1 truncate" title={q.text}><b className="font-semibold">{q.topic}</b> <span className="text-fg-2">{q.text}</span></span>
                <span className="shrink-0 text-[13px] text-fg-2">{questionOutcome(q)}</span>
              </div>
            ))}
          </div>
        )}
        {!!history.length && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px]">
            <thead><tr className="border-b border-line">
              <th className="th">时间</th><th className="th">交易对</th><th className="th">方向</th><th className="th text-right">金额</th><th className="th pl-6">结果</th><th className="th">备注</th>
            </tr></thead>
            <tbody>
              {history.map((a) => (
                <tr key={a.id} className="border-b border-line">
                  <td className="td num text-fg-2">{clock(a.created)}</td>
                  <td className="td font-semibold">{token(a.symbol)}/USDT</td>
                  <td className="td"><Side side={a.side} /></td>
                  <td className="td num text-right">{usd(a.quote_usd, 0)}</td>
                  <td className={`td pl-6 ${STATUS[a.status]?.[1]}`}>{STATUS[a.status]?.[0] ?? a.status}</td>
                  <td className="td max-w-[320px] truncate text-[13px] text-fg-3" title={a.result}>{a.result}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
        </>
      ) : <Empty text="暂无记录" />}
    </Page>
  );
}
