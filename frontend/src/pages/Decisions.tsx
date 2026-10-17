import { motion } from "framer-motion";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { CycleFull, CycleRow } from "../api";
import { CycleCard } from "../components/CycleCard";
import { Card, DecisionChip, Empty, PageTitle, RiskChecks, SideTag, Skeleton } from "../components/ui";
import { clock, pct, token, usd, useApi } from "../lib";

const FILTERS = [
  { k: "all", label: "全部" },
  { k: "trade", label: "有动作" },
  { k: "none", label: "不操作" },
  { k: "issue", label: "失败 / 跳过" },
];

export function Decisions() {
  const { data, loading } = useApi<CycleRow[]>("/cycles?limit=60", { kinds: ["cycle", "cycle_started", "approval"] });
  const [f, setF] = useState("all");
  const rows = (data ?? []).filter((c) =>
    f === "all" ? true : f === "trade" ? c.risk?.length > 0 : f === "none" ? c.status === "done" && !c.risk?.length : c.status !== "done");
  return (
    <div>
      <PageTitle title="AI 决策" sub="每一轮：模型看到了什么、想做什么、风控放行了什么。" />
      <div className="relative mb-5 inline-flex rounded-xl border border-white/[0.07] bg-white/[0.03] p-1">
        {FILTERS.map((x) => (
          <button key={x.k} onClick={() => setF(x.k)} className="relative rounded-lg px-3.5 py-1.5 text-[13.5px] text-ink-soft">
            {f === x.k && <motion.span layoutId="dec-filter" className="absolute inset-0 rounded-lg bg-white/[0.08]" transition={{ type: "spring", stiffness: 380, damping: 32 }} />}
            <span className={`relative ${f === x.k ? "text-ink" : ""}`}>{x.label}</span>
          </button>
        ))}
      </div>
      <div className="grid gap-4">
        {loading ? <Skeleton className="h-48" /> : rows.length ? rows.map((c) => <CycleCard key={c.id} c={c} />) :
          <Card><Empty title="没有符合条件的记录" /></Card>}
      </div>
    </div>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card className="p-5">
      <h3 className="label mb-3">{title}</h3>
      {children}
    </Card>
  );
}

function Raw({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button className="text-[13px] text-mint" onClick={() => setOpen(!open)}>{open ? "收起" : "展开原文"}</button>
      {open && (
        <pre className="mt-3 max-h-[480px] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-black/30 p-4 text-[12.5px] leading-relaxed text-ink-soft">{text}</pre>
      )}
    </div>
  );
}

export function DecisionDetail() {
  const { id } = useParams();
  const { data: c, error } = useApi<CycleFull>(`/cycles/${id}`, { kinds: ["cycle"] });
  if (error) return <Empty title="找不到这轮决策" hint={error} />;
  if (!c) return <Skeleton className="h-96" />;
  const ctx = c.context as { portfolio?: { total_usd: number; category_weights: Record<string, number> } };
  return (
    <div className="flex flex-col gap-4">
      <Link to="/decisions" className="text-[13.5px] text-ink-mute hover:text-mint">← 返回 AI 决策</Link>
      <PageTitle title={`第 ${clock(c.ts)} 轮`} sub={`${c.model || c.provider} · ${(c.duration_ms / 1000).toFixed(1)} 秒 · ${c.mode === "live" ? "实盘" : "模拟盘"}`} />
      <Block title="① 模型看到的账户">
        <p className="num text-[15px]">总资产 {usd(ctx.portfolio?.total_usd)}</p>
        <p className="mt-1 text-[14px] text-ink-soft">
          {Object.entries(ctx.portfolio?.category_weights ?? {}).map(([k, v]) => `${k} ${pct(v)}`).join("　")}
        </p>
        <div className="mt-3"><Raw text={JSON.stringify(c.context, null, 2)} /></div>
      </Block>
      <Block title="② 模型的判断">
        {c.plan ? (
          <>
            <p className="text-[15px] leading-relaxed">{c.plan.summary}</p>
            {c.plan.market_view && <p className="mt-2 text-[14px] leading-relaxed text-ink-soft">{c.plan.market_view}</p>}
            <p className="mt-2 text-[13px] text-ink-mute">信心 {pct(c.plan.confidence, 0)}</p>
          </>
        ) : <p className="text-rose">{c.error || c.summary}</p>}
        <div className="mt-3"><Raw text={c.raw_output || "（无输出）"} /></div>
      </Block>
      <Block title="③ 风控逐条检查">
        {c.risk?.length ? (
          <ul className="flex flex-col gap-4">
            {c.risk.map((r) => (
              <li key={r.index} className="rounded-xl border border-white/[0.06] bg-black/15 p-4">
                <div className="flex flex-wrap items-center gap-2.5">
                  <SideTag side={r.side} /><b>{token(r.symbol)}</b>
                  <span className="num text-ink-soft">提议 {usd(r.requested_usd, 0)} → 放行 {usd(r.approved_usd, 0)}</span>
                  <span className="ml-auto"><DecisionChip d={r.decision} /></span>
                </div>
                <p className="mt-3 text-[14px] leading-relaxed text-ink-soft"><span className="text-ink-mute">理由　</span>{r.reason_model}</p>
                <p className="mt-1.5 text-[14px] leading-relaxed text-ink-soft"><span className="text-ink-mute">判断错了的信号　</span>{r.invalidation}</p>
                <div className="mt-3"><RiskChecks row={r} /></div>
                <ul className="mt-2 space-y-1 text-[13px] text-ink-mute">
                  {r.checks.map((x, i) => <li key={i}>{x.ok ? "✓" : "✗"} {x.name}：{x.detail}</li>)}
                </ul>
                {r.result && <p className="mt-2 text-[13.5px] text-ink">{r.result}</p>}
              </li>
            ))}
          </ul>
        ) : <p className="text-[14px] text-ink-soft">本轮没有交易动作。</p>}
      </Block>
      <Block title="④ 提示词原文">
        <Raw text={c.prompt || "（本轮未调用模型）"} />
      </Block>
    </div>
  );
}
