import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import type { CycleFull, CycleRow, DebateLog, DebateTurn, NewsCtx, TrackRecord, WinStat } from "../api";
import { Decision, DocLink, Empty, Page, RiskChecks, SectionHead, Side, Skeleton, Tabs } from "../components/ui";
import { clock, pct, token, usd, useApi, fmtPrice } from "../lib";

type F = "all" | "trade" | "none" | "issue";

function Win({ label, s }: { label: string; s: WinStat | undefined }) {
  return (
    <div>
      <dt className="text-[12px] text-fg-3">{label}</dt>
      <dd className="num mt-0.5 text-[15px]">
        {s?.win_rate != null ? pct(s.win_rate, 0) : "—"}
        {s?.evaluated ? <span className="ml-1.5 text-[12px] text-fg-3">{s.wins}/{s.evaluated} 笔 · 平均 {pct(s.avg_return)}</span> : null}
      </dd>
    </div>
  );
}

export function TrackStrip() {
  const { data: t } = useApi<TrackRecord>("/track-record", { kinds: ["order", "cycle"], poll: 300000 });
  if (!t || (!t.trades && !t.futures.trades)) return null;
  return (
    <section className="mb-4">
      <dl className="flex flex-wrap gap-x-8 gap-y-2 rounded-lg bg-raise px-4 py-3" title={t.rule}>
        <Win label="胜率 · 24 小时" s={t["24h"]} />
        <Win label="胜率 · 7 天" s={t["7d"]} />
        <Win label="买入 · 24 小时" s={t.by_side_24h.BUY} />
        <Win label="卖出 · 24 小时" s={t.by_side_24h.SELL} />
        {t.futures.trades > 0 && <Win label="合约" s={{ evaluated: t.futures.trades, wins: t.futures.wins, win_rate: t.futures.win_rate, avg_return: null }} />}
        {t.pending_24h > 0 && <div><dt className="text-[12px] text-fg-3">待评估</dt><dd className="num mt-0.5 text-[15px]">{t.pending_24h} 笔</dd></div>}
      </dl>
    </section>
  );
}

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
      <TrackStrip />
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

const STAGES = [["market_ms", "行情"], ["account_ms", "账户"], ["news_ms", "新闻"], ["debate_ms", "辩论"], ["model_ms", "模型"], ["execute_ms", "执行"]] as const;

const ROLE = { bull: "多头", bear: "空头", risk: "风控" } as const;
const STANCE = { bullish: ["看多", "text-up"], bearish: ["看空", "text-down"], neutral: ["中性", "text-fg-2"] } as const;
const PSIDE: Record<string, [string, string]> = { BUY: ["买", "text-up"], SELL: ["卖", "text-down"], HOLD: ["持有", "text-fg-2"], AVOID: ["回避", "text-warn"] };

function Turn({ t }: { t: DebateTurn }) {
  const [label, cls] = STANCE[t.stance ?? "neutral"];
  return (
    <div className="rounded-lg bg-raise p-4" data-testid={`turn-${t.round}-${t.role}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <b className="text-[14px]">{ROLE[t.role]}</b>
        {t.error ? <span className="text-[13px] text-down">失败</span>
          : <span className={`num text-[13px] ${cls}`}>{label} · {pct(t.conviction ?? 0, 0)}</span>}
        <span className="ml-auto truncate text-[12px] text-fg-3">{t.model}{t.ms != null ? ` · ${(t.ms / 1000).toFixed(1)}s` : ""}</span>
      </div>
      {t.error ? <p className="mt-2 text-[13px] text-fg-2">{t.error}</p> : (
        <div className="mt-2 text-[14px] leading-relaxed">
          {t.rebuttal && <p className="mb-2 border-l-2 border-line-strong pl-3 text-fg-2">{t.rebuttal}</p>}
          <p>{t.thesis}</p>
          {!!t.points?.length && <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[13.5px] text-fg-2">{t.points.map((x, i) => <li key={i}>{x}</li>)}</ul>}
          {!!t.proposals?.length && (
            <ul className="mt-2 space-y-0.5 text-[13px]">
              {t.proposals.map((p, i) => (
                <li key={i}><span className={PSIDE[p.side]?.[1]}>{PSIDE[p.side]?.[0] ?? p.side}</span> <b>{token(p.symbol)}</b>
                  {p.note && <span className="text-fg-2"> · {p.note}</span>}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

const IMPACT = { bullish: ["利好", "text-up"], bearish: ["利空", "text-down"], neutral: ["中性", "text-fg-3"] } as const;

function News({ n }: { n: NewsCtx }) {
  return (
    <section>
      <SectionHead title="新闻" doc="news" right={<span className="text-[12px] text-fg-3">{n.source === "model" ? "联网搜索" : "新闻源"} · {n.fetched_at}</span>} />
      {n.overview && <p className="pt-3 text-[14px] leading-relaxed">{n.overview}</p>}
      {n.items.length ? (
        <ul className="pt-1">
          {n.items.map((it, i) => {
            const [label, cls] = IMPACT[it.impact ?? "neutral"] ?? IMPACT.neutral;
            return (
              <li key={i} className="border-b border-line py-3" data-testid="news-item">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[14px]">
                  {it.url ? <a href={it.url} target="_blank" rel="noreferrer noopener" className="font-medium hover:underline">{it.title}</a>
                    : <span className="font-medium">{it.title}</span>}
                  <span className={`text-[12.5px] ${cls}`}>{label}</span>
                  <span className="ml-auto text-[12px] text-fg-3">{[it.assets?.join(" "), it.source, it.time].filter(Boolean).join(" · ")}</span>
                </div>
                {it.summary && <p className="mt-1 text-[13.5px] leading-relaxed text-fg-2">{it.summary}</p>}
              </li>
            );
          })}
        </ul>
      ) : <p className="pt-3 text-[14px] text-fg-2">无</p>}
    </section>
  );
}

function Debate({ d }: { d: DebateLog }) {
  const rounds = [...new Set(d.turns.map((t) => t.round))];
  const c = d.consensus ?? {};
  return (
    <section>
      <SectionHead title="多空辩论" doc="debate" />
      {c.lean && (
        <dl className="flex flex-wrap gap-x-8 gap-y-2 pt-3 text-[14px]">
          <div><dt className="text-[12px] text-fg-3">共识</dt>
            <dd className={`num mt-0.5 ${STANCE[c.lean][1]}`}>{STANCE[c.lean][0]} {(c.score ?? 0) > 0 ? "+" : ""}{(c.score ?? 0).toFixed(2)}</dd></div>
          {Object.entries(c.votes ?? {}).map(([sym, v]) => (
            <div key={sym}><dt className="text-[12px] text-fg-3">{token(sym)}</dt>
              <dd className="mt-0.5 text-[13px]">{Object.entries(v).map(([side, n]) => `${PSIDE[side]?.[0] ?? side} ${n}`).join(" · ")}</dd></div>
          ))}
        </dl>
      )}
      {rounds.map((r) => (
        <div key={r} className="pt-4">
          {rounds.length > 1 && <h3 className="mb-2 text-[13px] text-fg-3">第 {r} 轮</h3>}
          <div className="grid gap-3 md:grid-cols-3">{d.turns.filter((t) => t.round === r).map((t) => <Turn key={t.role} t={t} />)}</div>
        </div>
      ))}
    </section>
  );
}

function timing(c: CycleFull) {
  const parts = STAGES.filter(([k]) => c.timing?.[k] != null).map(([k, label]) => `${label} ${((c.timing?.[k] ?? 0) / 1000).toFixed(1)}s`);
  return parts.length ? `（${parts.join(" · ")}）` : "";
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
        <p className="mt-1 text-[13px] text-fg-2">{c.model || c.provider} · {(c.duration_ms / 1000).toFixed(1)}s{timing(c)} · {c.mode === "live" ? "实盘" : "模拟"}</p>
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

      {!!(c.context as { news?: NewsCtx }).news && <News n={(c.context as { news: NewsCtx }).news} />}

      {!!c.debate?.turns?.length && <Debate d={c.debate as DebateLog} />}

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
