import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import type { CycleFull, CycleRow } from "../api";
import { Decision, DocLink, Empty, Page, RiskChecks, SectionHead, Side, Skeleton, Tabs } from "../components/ui";
import { clock, pct, token, usd, useApi, fmtPrice } from "../lib";

type F = "all" | "trade" | "none" | "issue";

export function AIList() {
  const { data, loading } = useApi<CycleRow[]>("/cycles?limit=100", { kinds: ["cycle", "cycle_started", "approval"] });
  const [f, setF] = useState<F>("all");
  const nav = useNavigate();
  const rows = (data ?? []).filter((c) =>
    f === "all" ? true : f === "trade" ? c.risk?.length > 0 : f === "none" ? c.status === "done" && !c.risk?.length : c.status !== "done");
  return (
    <Page>
      <div className="mb-1 flex items-center gap-1.5"><h1 className="text-[20px] font-semibold">AI 决策</h1><DocLink to="decisions-and-risk" /></div>
      <Tabs id="ai" value={f} onChange={setF} items={[
        { k: "all", label: "全部" }, { k: "trade", label: "有交易" }, { k: "none", label: "不操作" }, { k: "issue", label: "失败 / 跳过" }]} />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px]">
          <thead><tr className="border-b border-line">
            <th className="th w-32">时间</th><th className="th w-40">模型</th><th className="th">结论</th><th className="th">动作</th>
          </tr></thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className="cursor-pointer border-b border-line transition-colors hover:bg-raise" onClick={() => nav(`/ai/${c.id}`)}>
                <td className="td num text-fg-2">{clock(c.ts)}</td>
                <td className="td truncate text-[13px] text-fg-2">{c.model || c.provider}</td>
                <td className="td max-w-[420px]"><p className="line-clamp-2 text-[14px]">{c.summary || <span className="text-down">{c.error}</span>}</p></td>
                <td className="td">
                  {c.risk?.length ? (
                    <div className="flex flex-col gap-1 text-[13px]">
                      {c.risk.map((r) => (
                        <span key={`${r.kind ?? "spot"}-${r.index}`} className="flex items-center gap-1.5 whitespace-nowrap"><Side side={r.side ?? r.op} />{token(r.symbol)}
                          <span className="num">{usd(r.approved_usd || r.requested_usd || r.margin || r.requested_margin, 0)}</span><Decision d={r.decision} /></span>
                      ))}
                    </div>
                  ) : <span className="text-[13px] text-fg-3">{c.status === "error" ? "失败" : c.status === "skipped" ? "跳过" : c.status === "running" ? "决策中…" : "—"}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {loading && <Skeleton className="mt-4 h-40" />}
      {!loading && !rows.length && <Empty text="暂无记录" />}
    </Page>
  );
}

function Raw({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-3">
      <button className="text-[13px] text-fg-2 hover:text-fg" onClick={() => setOpen(!open)}>{open ? "收起原文" : "原文 ›"}</button>
      {open && <pre className="mt-2 max-h-[480px] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-raise p-4 text-[12.5px] leading-relaxed text-fg-2">{text}</pre>}
    </div>
  );
}

export function AIDetail() {
  const { id } = useParams();
  const { data: c, error } = useApi<CycleFull>(`/cycles/${id}`, { kinds: ["cycle"] });
  if (error) return <Empty text={error} />;
  if (!c) return <Skeleton className="h-96" />;
  const ctx = c.context as { portfolio?: { total_usd: number; category_weights: Record<string, number> } };
  return (
    <Page className="flex flex-col gap-8">
      <div>
        <Link to="/ai" className="text-[13px] text-fg-2 hover:text-fg">‹ AI 决策</Link>
        <h1 className="num mt-2 text-[20px] font-semibold">{clock(c.ts)}</h1>
        <p className="mt-1 text-[13px] text-fg-2">{c.model || c.provider} · {(c.duration_ms / 1000).toFixed(1)}s · {c.mode === "live" ? "实盘" : "模拟"}</p>
      </div>

      <section>
        <SectionHead title="输入" />
        <dl className="flex flex-wrap gap-x-8 gap-y-2 pt-3 text-[14px]">
          <div><dt className="text-[12px] text-fg-3">估值</dt><dd className="num mt-0.5">{usd(ctx.portfolio?.total_usd)}</dd></div>
          {Object.entries(ctx.portfolio?.category_weights ?? {}).map(([k, v]) => (
            <div key={k}><dt className="text-[12px] text-fg-3">{k}</dt><dd className="num mt-0.5">{pct(v)}</dd></div>
          ))}
        </dl>
        <Raw text={JSON.stringify(c.context, null, 2)} />
      </section>

      <section>
        <SectionHead title="判断" />
        {c.plan ? (
          <div className="pt-3 text-[14.5px] leading-relaxed">
            <p>{c.plan.summary}</p>
            {c.plan.market_view && <p className="mt-2 text-fg-2">{c.plan.market_view}</p>}
            <p className="num mt-2 text-[13px] text-fg-3">信心 {pct(c.plan.confidence, 0)}</p>
          </div>
        ) : <p className="pt-3 text-down">{c.error || c.summary}</p>}
        <Raw text={c.raw_output || "—"} />
      </section>

      <section>
        <SectionHead title="风控" doc="decisions-and-risk" />
        {c.risk?.length ? c.risk.map((r) => (
          <div key={`${r.kind ?? "spot"}-${r.index}`} className="border-b border-line py-4">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[14px]">
              <Side side={r.side ?? r.op} /><b>{token(r.symbol)}</b>{r.kind === "futures" && <span className="tag bg-hover text-fg-2">永续</span>}
              {r.kind === "futures" ? (
                <span className="num text-fg-2">
                  {r.requested_margin ? <>{usd(r.requested_margin, 0)} → <span className="text-fg">{usd(r.margin, 0)}</span></> : null}
                  {r.leverage ? <> × {r.leverage}x</> : null}
                  {r.stop_loss ? <> · 止损 {fmtPrice(r.stop_loss)}</> : null}
                  {r.take_profit ? <> · 止盈 {fmtPrice(r.take_profit)}</> : null}
                  {r.op === "REDUCE" && r.fraction ? <> · {(r.fraction * 100).toFixed(0)}%</> : null}
                </span>
              ) : (
                <span className="num text-fg-2">{usd(r.requested_usd, 0)} → <span className="text-fg">{usd(r.approved_usd, 0)}</span></span>
              )}
              <span className="ml-auto"><Decision d={r.decision} /></span>
            </div>
            <dl className="mt-3 grid gap-3 text-[14px] leading-relaxed sm:grid-cols-2">
              <div><dt className="text-[12px] text-fg-3">理由</dt><dd className="mt-0.5">{r.reason_model}</dd></div>
              <div><dt className="text-[12px] text-fg-3">判断错了的信号</dt><dd className="mt-0.5">{r.invalidation}</dd></div>
            </dl>
            <div className="mt-3"><RiskChecks row={r} /></div>
            <ul className="mt-2 space-y-0.5 text-[12.5px] text-fg-3">{r.checks.map((x, i) => <li key={i}>{x.name}：{x.detail}</li>)}</ul>
            {r.result && <p className="mt-2 text-[13px]">{r.result}</p>}
          </div>
        )) : <p className="pt-3 text-[14px] text-fg-2">无动作</p>}
      </section>

      <section>
        <SectionHead title="提示词" />
        <Raw text={c.prompt || "—"} />
      </section>
    </Page>
  );
}
