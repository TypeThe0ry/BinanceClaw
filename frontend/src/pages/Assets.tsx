import { AnimatePresence } from "framer-motion";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";
import { post, type Approval, type CycleRow, type Overview, type Performance } from "../api";
import { ApprovalRow } from "../components/ApprovalRow";
import { Band, Change, Decision, Empty, FlashNum, Page, SectionHead, Side, Skeleton } from "../components/ui";
import { CATEGORY_COLOR, ago, fmtPrice, token, usd, useApi, useTicker, useToast } from "../lib";

function NextIn({ at }: { at: number }) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, []);
  if (!at) return <>—</>;
  const s = Math.max(0, at - now);
  return <span className="num">{s < 1 ? "进行中" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`}</span>;
}

export default function Assets() {
  const ov = useApi<Overview>("/overview", { poll: 30000 });
  const eq = useApi<{ t: number; total: number }[]>("/equity", { kinds: ["cycle"] });
  const cycles = useApi<CycleRow[]>("/cycles?limit=6", { kinds: ["cycle", "cycle_started", "approval"] });
  const pending = useApi<Approval[]>("/approvals?status=pending", { kinds: ["approval", "approval_created"] });
  const perf = useApi<Performance>("/performance", { kinds: ["order", "cycle"], poll: 60000 });
  const tick = useTicker();
  const toast = useToast();
  const [running, setRunning] = useState(false);
  const o = ov.data;

  const series = (eq.data ?? []).filter((p) => p.total != null);
  const startOfDay = new Date(); startOfDay.setHours(0, 0, 0, 0);
  const dayBase = series.find((p) => p.t * 1000 >= startOfDay.getTime())?.total;
  const shown = o?.account_total ?? o?.total;
  const dayPnl = shown != null && dayBase ? shown - dayBase : null;

  // rows: everything held plus every target token
  const s = o?.targets ?? {};
  const acct = o?.account_values ?? o?.values ?? {};
  const tokens = Array.from(new Set([...Object.keys(acct), ...Object.keys(o?.values ?? {}), ...Object.values(s).flatMap((c) => Object.keys(c.tokens))]));
  const catOf = (t: string) => Object.entries(s).find(([, c]) => t in c.tokens)?.[0] ?? (t.startsWith("USD") || t === "FDUSD" ? "STABLE" : "OTHER");
  const rows = tokens.map((t) => {
    const cat = catOf(t);
    const value = acct[t] ?? o?.values?.[t] ?? 0;
    const price = t.startsWith("USD") || t === "FDUSD" ? 1 : tick[`${t}USDT`]?.price ?? o?.market?.[t]?.price;
    const target = s[cat]?.tokens?.[t];
    return { t, cat, value, qty: o?.account_balances?.[t] ?? o?.balances?.[t] ?? 0, price, w: o?.token_weights?.[t] ?? 0,
      target: target === undefined ? 0 : target, locked: o?.capital?.locked?.[t] ?? 0, pf: perf.data?.assets?.[t] };
  }).sort((a, b) => b.value - a.value || (b.target ?? 0) - (a.target ?? 0));

  async function runNow() {
    setRunning(true);
    await post("/cycles/run");
    toast("已开始新一轮决策");
    window.setTimeout(() => setRunning(false), 5000);
  }
  async function toggle() {
    await post(o?.paused ? "/control/resume" : "/control/pause");
    ov.reload();
  }

  return (
    <Page className="flex flex-col gap-10">
      {/* valuation */}
      <section className="flex flex-wrap items-end justify-between gap-6">
        <div>
          <p className="text-[13px] text-fg-2">估值（USDT）</p>
          {shown != null ? (
            <p className="num mt-1 text-[36px] font-semibold leading-none tracking-tight md:text-[44px]">
              <FlashNum value={Math.round(shown * 100)}>{shown.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</FlashNum>
            </p>
          ) : <Skeleton className="mt-2 h-11 w-64" />}
          {perf.data && perf.data.invested > 0 && (
            <p className="mt-2 flex flex-wrap gap-x-4 text-[13px] text-fg-2">
              <span>累计盈亏 <span className={`num ${perf.data.pnl >= 0 ? "text-up" : "text-down"}`}>{perf.data.pnl >= 0 ? "+" : ""}{perf.data.pnl.toFixed(2)}</span>
                {perf.data.roi != null && <> <Change v={perf.data.roi} /></>}</span>
              {perf.data.benchmark.roi != null && <span>全买 BTC <Change v={perf.data.benchmark.roi} /></span>}
            </p>
          )}
          {o?.account_total != null && o.total != null && Math.abs(o.account_total - o.total) > 0.01 && (
            <p className="mt-1 text-[13px] text-fg-3">其中 AI 管理 <span className="num">{o.total.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></p>
          )}
          <p className="mt-2 text-[13px] text-fg-2">
            今日盈亏 {dayPnl != null ? <span className={`num ${dayPnl >= 0 ? "text-up" : "text-down"}`}>{dayPnl >= 0 ? "+" : ""}{dayPnl.toFixed(2)}</span> : <span className="text-fg-3">—</span>}
          </p>
        </div>
        {series.length > 1 && (
          <div className="h-14 w-48">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={series}>
                <YAxis domain={["dataMin", "dataMax"]} hide />
                <Line type="monotone" dataKey="total" dot={false} strokeWidth={1.5}
                  stroke={series[series.length - 1].total >= series[0].total ? "#20B26C" : "#EF454A"} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        <div className="flex w-full gap-2 sm:w-auto">
          <button className="btn-primary flex-1 sm:flex-none" onClick={runNow} disabled={running || o?.paused}>{running ? "决策中…" : "立即决策"}</button>
          <button className="btn-secondary flex-1 sm:flex-none" onClick={toggle}>{o?.paused ? "恢复" : "暂停"}</button>
        </div>
        <dl className="grid w-full grid-cols-2 gap-y-3 border-t border-line pt-4 text-[13px] sm:grid-cols-4">
          <div><dt className="text-fg-3">今日笔数</dt><dd className="num mt-1 text-[14px]">{o ? `${o.today.orders} / ${o.today.max_orders}` : "—"}</dd></div>
          <div><dt className="text-fg-3">今日金额</dt><dd className="num mt-1 text-[14px]">{o ? `${usd(o.today.notional, 0)} / ${usd(o.today.max_notional, 0)}` : "—"}</dd></div>
          <div><dt className="text-fg-3">BTC 7 日</dt><dd className="mt-1 text-[14px]"><Change v={o?.btc_change_7d} digits={1} /></dd></div>
          <div><dt className="text-fg-3">下一轮</dt><dd className="mt-1 text-[14px]">{o ? <NextIn at={o.next_cycle_at} /> : "—"}</dd></div>
        </dl>
        {o?.paused && <p className="w-full text-[13px] text-warn">已暂停 · {o.pause_reason}</p>}
      </section>

      {/* approvals */}
      {!!pending.data?.length && (
        <section>
          <SectionHead title={<>待审批 <span className="num text-warn">{pending.data.length}</span></>} doc="approvals" />
          <AnimatePresence mode="popLayout" initial={false}>
            {pending.data.map((a) => <ApprovalRow key={a.id} a={a} onDone={pending.reload} />)}
          </AnimatePresence>
        </section>
      )}

      {/* assets */}
      <section>
        <SectionHead title="资产" doc="dashboard" />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px]">
            <thead>
              <tr className="border-b border-line">
                <th className="th">资产</th><th className="th text-right">数量</th><th className="th text-right">价格</th>
                <th className="th text-right">估值</th><th className="th text-right">成本价</th><th className="th text-right">盈亏</th>
                <th className="th text-right">占比 / 目标</th><th className="th w-[18%] pl-8">区间</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const band = s[r.cat]?.band ?? [0, 1];
                const catW = o?.category_weights?.[r.cat] ?? 0;
                const tv = Object.values(s[r.cat]?.tokens ?? {}).filter((x): x is number => x != null);
                const catT = tv.length ? tv.reduce((a, b) => a + b, 0) : null;
                return (
                  <tr key={r.t} className="border-b border-line">
                    <td className="td"><span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: CATEGORY_COLOR[r.cat] ?? "#8C8C8C" }} /><b className="font-semibold">{r.t}</b>
                      {r.locked > 0 && <span className="tag bg-hover text-fg-3" title={`锁定 ${r.locked}`}>锁定 {r.locked}</span>}</span></td>
                    <td className="td num text-right text-fg-2">{r.qty ? r.qty.toLocaleString("en-US", { maximumFractionDigits: 8 }) : "0"}</td>
                    <td className="td text-right"><FlashNum value={r.price}>{r.price ? fmtPrice(r.price) : "—"}</FlashNum></td>
                    <td className="td num text-right">{r.value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                    <td className="td num text-right text-fg-2">{r.pf?.avg_cost ? fmtPrice(r.pf.avg_cost) : "—"}</td>
                    <td className="td num text-right">{r.pf?.unrealized != null && r.pf.qty > 1e-12
                      ? <span className={r.pf.unrealized >= 0 ? "text-up" : "text-down"}>{r.pf.unrealized >= 0 ? "+" : ""}{r.pf.unrealized.toFixed(2)}</span>
                      : <span className="text-fg-3">—</span>}</td>
                    <td className="td num text-right"><span>{(r.w * 100).toFixed(1)}%</span><span className="text-fg-3"> / {r.target == null ? "AI" : `${(r.target * 100).toFixed(0)}%`}</span></td>
                    <td className="td pl-8">{s[r.cat] ? <Band w={catW} lo={band[0]} hi={band[1]} target={catT ?? -1} color={CATEGORY_COLOR[r.cat] ?? "#8C8C8C"} /> : <span className="text-[12px] text-fg-3">不在管理范围</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {!o && <Skeleton className="mt-3 h-40" />}
      </section>

      {/* AI feed */}
      <section>
        <SectionHead title="AI 动态" doc="decisions-and-risk" right={<Link to="/ai" className="text-[13px] text-fg-2 hover:text-fg">全部 ›</Link>} />
        {cycles.data?.length ? cycles.data.map((c) => (
          <Link key={c.id} to={`/ai/${c.id}`} className="flex flex-col gap-1.5 border-b border-line py-3.5 transition-colors hover:bg-raise sm:flex-row sm:items-center sm:gap-5">
            <span className="num w-28 shrink-0 text-[13px] text-fg-3">{ago(c.ts)}</span>
            <span className="min-w-0 flex-1 truncate text-[14px]">{c.summary || c.error || "…"}</span>
            <span className="flex shrink-0 flex-wrap gap-x-4 text-[13px]">
              {c.risk?.length ? c.risk.map((r) => (
                <span key={r.index} className="flex items-center gap-1.5"><Side side={r.side} />{token(r.symbol)} <span className="num">{usd(r.approved_usd || r.requested_usd, 0)}</span> <Decision d={r.decision} /></span>
              )) : <span className={c.status === "error" ? "text-down" : "text-fg-3"}>{c.status === "error" ? "失败" : c.status === "skipped" ? "跳过" : "不操作"}</span>}
            </span>
          </Link>
        )) : cycles.loading ? <Skeleton className="mt-3 h-24" /> : <Empty text="暂无记录" />}
      </section>
    </Page>
  );
}
