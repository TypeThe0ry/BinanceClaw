import { AnimatePresence } from "framer-motion";
import type { Approval } from "../api";
import { ApprovalCard } from "../components/ApprovalCard";
import { Card, Empty, PageTitle, SideTag, Skeleton } from "../components/ui";
import { ago, token, usd, useApi } from "../lib";

const STATUS: Record<string, { t: string; c: string }> = {
  approved: { t: "已批准", c: "text-mint" },
  rejected: { t: "已拒绝", c: "text-ink-soft" },
  expired: { t: "已过期", c: "text-ink-mute" },
  failed: { t: "批准后未执行", c: "text-rose" },
};
const BY: Record<string, string> = { web: "网页", telegram: "Telegram", system: "系统" };

export default function Approvals() {
  const pending = useApi<Approval[]>("/approvals?status=pending", { kinds: ["approval", "approval_created"] });
  const all = useApi<Approval[]>("/approvals?limit=80", { kinds: ["approval", "approval_created"] });
  const history = (all.data ?? []).filter((a) => a.status !== "pending");
  return (
    <div>
      <PageTitle title="审批" sub="超过自动执行额度的交易会停在这里，Telegram 里同步推送，哪边先处理都可以。" />
      <div className="grid gap-4">
        {pending.loading ? <Skeleton className="h-52" /> : (
          <AnimatePresence mode="popLayout" initial={false}>
            {(pending.data ?? []).map((a) => <ApprovalCard key={a.id} a={a} onDone={() => { pending.reload(); all.reload(); }} />)}
          </AnimatePresence>
        )}
        {!pending.loading && !pending.data?.length && <Card><Empty title="没有待审批的交易" hint="AI 提出超过自动执行额度的买入时，会出现在这里。" /></Card>}
      </div>
      <h2 className="mb-3 mt-10 font-display text-[26px]">历史</h2>
      <Card className="divide-y divide-white/[0.05]">
        {history.length ? history.map((a) => (
          <div key={a.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-3.5 text-[14px]">
            <SideTag side={a.side} />
            <span className="w-16 font-semibold">{token(a.symbol)}</span>
            <span className="num w-20 text-ink-soft">{usd(a.quote_usd, 0)}</span>
            <span className={STATUS[a.status]?.c}>{STATUS[a.status]?.t ?? a.status}</span>
            {a.resolved_by && <span className="text-ink-mute">· {BY[a.resolved_by] ?? a.resolved_by}</span>}
            <span className="ml-auto text-[13px] text-ink-mute">{ago(a.created)}</span>
            {a.result && <p className="w-full text-[13px] text-ink-mute">{a.result}</p>}
          </div>
        )) : <Empty title="还没有审批记录" />}
      </Card>
    </div>
  );
}
