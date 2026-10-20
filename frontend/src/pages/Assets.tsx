import { AnimatePresence } from "framer-motion";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { post, type Approval, type CycleRow, type FuturesResp, type Overview, type Performance } from "../api";
import { ApprovalRow } from "../components/ApprovalRow";
import { ValuationChart } from "../components/ValuationChart";
import { Band, Change, Decision, Empty, FlashNum, Page, SectionHead, Seg, Side, Skeleton } from "../components/ui";
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

const PICKED = "__picked";
const isStable = (t: string) => t.startsWith("USD") || t === "FDUSD";

/** 自选 first (by `weight`), then one section per Binance category, biggest first; empty sections are left out. */
function sectionsOf<R>(rows: R[], picked: (r: R) => boolean, cat: (r: R) => string, weight: (r: R) => number) {
  const mine = rows.filter(picked);
  const rest = rows.filter((r) => !picked(r));
  const groups = Object.entries(rest.reduce<Record<string, R[]>>((g, r) => ((g[cat(r)] ??= []).push(r), g), {}))
    .map(([name, rs]) => ({ key: name, name, rows: rs, value: rs.reduce((x, r) => x + weight(r), 0) }))
    // stablecoins and the uncategorised go last, like on binance.com
    .sort((a, b) => Number(a.name === "稳定币" || a.name === "未分类") - Number(b.name === "稳定币" || b.name === "未分类")
      || b.value - a.value || a.name.localeCompare(b.name));
  return [...(mine.length ? [{ key: PICKED, name: "自选", rows: mine, value: mine.reduce((x, r) => x + weight(r), 0) }] : []), ...groups];
}

/** A section's header row; click to fold it. */
function GroupRow({ name, n, open, onToggle, cells, span }:
  { name: string; n: number; open: boolean; onToggle: () => void; cells: ReactNode; span: number }) {
  return (
    <tr className="cursor-pointer border-b border-line bg-raise hover:bg-hover" onClick={onToggle} aria-expanded={open}>
      <td className="td font-semibold" colSpan={span}>
        <span className={`mr-1.5 inline-block text-fg-3 transition-transform ${open ? "rotate-90" : ""}`}>›</span>
        {name} <span className="text-[12px] font-normal text-fg-3">{n}</span>
      </td>
      {cells}
    </tr>
  );
}

interface FMarket { token: string; symbol: string; price: number | null; change_pct: number | null; quote_volume: number; enabled: boolean }

/** Futures tab: the AI's open positions first (自选), then the contracts it may trade, by Binance category. */
function FuturesAssets({ o, bcat, collapsed, fold }:
  { o: Overview | null; bcat: (t: string) => string; collapsed: Set<string>; fold: (k: string) => void }) {
  const fut = useApi<FuturesResp>("/futures", { poll: 10000, kinds: ["futures", "settings", "paper_reset"] });
  const syms = o?.futures?.symbols ?? [];
  const mk = useApi<FMarket[]>(syms.length ? `/futures/markets?limit=${Math.max(syms.length, 1)}` : null, { poll: 60000, kinds: ["settings"] });
  const f = fut.data;
  if (!f) return fut.error ? <Empty text={`合约数据读取失败：${fut.error}`} /> : <Skeleton className="mt-3 h-32" />;
  const held = new Set(f.positions.map((p) => token(p.symbol)));
  const markets = (mk.data ?? []).filter((m) => m.enabled && !held.has(m.token));
  const open = !collapsed.has(`futures:${PICKED}`);
  const groups = sectionsOf(markets, () => false, (m) => bcat(m.token), (m) => m.quote_volume || 0);
  const num = (v: number) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (
    <>
      <dl className="mb-3 grid grid-cols-2 gap-y-3 text-[13px] sm:grid-cols-4">
        <div><dt className="text-fg-3">合约权益</dt><dd className="num mt-1 text-[14px]">{num(f.equity)}</dd></div>
        <div><dt className="text-fg-3">保证金占用</dt><dd className="num mt-1 text-[14px]">{num(f.margin_used)}</dd></div>
        <div><dt className="text-fg-3">未实现盈亏</dt><dd className={`num mt-1 text-[14px] ${f.unrealized >= 0 ? "text-up" : "text-down"}`}>{f.unrealized >= 0 ? "+" : ""}{num(f.unrealized)}</dd></div>
        <div><dt className="text-fg-3">已实现盈亏</dt><dd className={`num mt-1 text-[14px] ${f.realized >= 0 ? "text-up" : "text-down"}`}>{f.realized >= 0 ? "+" : ""}{num(f.realized)}</dd></div>
      </dl>
      {!f.enabled && !f.positions.length && <Empty text="合约交易未开启" />}
      {(f.enabled || !!f.positions.length) && (
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px]">
          <thead>
            <tr className="border-b border-line">
              <th className="th">合约</th><th className="th text-right">方向 / 杠杆</th><th className="th text-right">开仓价</th>
              <th className="th text-right">最新价</th><th className="th text-right">保证金 / 成交额</th><th className="th text-right">盈亏 / 24h</th>
            </tr>
          </thead>
          {!!f.positions.length && (
          <tbody>
            <GroupRow name="自选" n={f.positions.length} open={open} onToggle={() => fold(`futures:${PICKED}`)} span={4}
              cells={<><td className="td num text-right font-medium">{num(f.margin_used)}</td>
                <td className={`td num text-right ${f.unrealized >= 0 ? "text-up" : "text-down"}`}>{f.unrealized >= 0 ? "+" : ""}{num(f.unrealized)}</td></>} />
            {open && f.positions.map((p) => (
              <tr key={p.id} className="border-b border-line">
                <td className="td"><span className="flex items-center gap-2"><Link to="/futures" className="font-semibold hover:underline">{token(p.symbol)}</Link>
                  <span className="text-[12px] text-fg-3">{bcat(token(p.symbol))}</span></span></td>
                <td className="td text-right"><span className={p.side === "LONG" ? "text-up" : "text-down"}>{p.side === "LONG" ? "多" : "空"}</span> <span className="num text-fg-2">{p.leverage}x</span></td>
                <td className="td num text-right text-fg-2">{fmtPrice(p.entry)}</td>
                <td className="td text-right"><FlashNum value={p.mark}>{fmtPrice(p.mark)}</FlashNum></td>
                <td className="td num text-right">{num(p.margin)}</td>
                <td className={`td num text-right ${p.upnl >= 0 ? "text-up" : "text-down"}`}>{p.upnl >= 0 ? "+" : ""}{num(p.upnl)}
                  {p.roe != null && <span className="ml-1 text-[12px]">({(p.roe * 100).toFixed(1)}%)</span>}</td>
              </tr>
            ))}
          </tbody>
          )}
          {groups.map((g) => (
          <tbody key={g.key}>
            <GroupRow name={g.name} n={g.rows.length} open={!collapsed.has(`futures:${g.key}`)} onToggle={() => fold(`futures:${g.key}`)} span={6} cells={null} />
            {!collapsed.has(`futures:${g.key}`) && g.rows.map((m) => (
              <tr key={m.symbol} className="border-b border-line">
                <td className="td"><span className="font-semibold">{m.token}</span> <span className="text-[12px] text-fg-3">永续 · 未持仓</span></td>
                <td className="td text-right text-fg-3">—</td>
                <td className="td text-right text-fg-3">—</td>
                <td className="td text-right">{m.price ? fmtPrice(m.price) : "—"}</td>
                <td className="td num text-right text-fg-2">{m.quote_volume ? usd(m.quote_volume, 0) : "—"}</td>
                <td className="td text-right"><Change v={m.change_pct} /></td>
              </tr>
            ))}
          </tbody>
          ))}
        </table>
      </div>
      )}
    </>
  );
}

export default function Assets() {
  const ov = useApi<Overview>("/overview", { poll: 30000 });
  const eq = useApi<{ t: number; total: number; mode?: string }[]>("/equity", { kinds: ["cycle", "mode"] });
  const cycles = useApi<CycleRow[]>("/cycles?limit=6", { kinds: ["cycle", "cycle_started", "approval"] });
  const pending = useApi<Approval[]>("/approvals?status=pending", { kinds: ["approval", "approval_created"] });
  const perf = useApi<Performance>("/performance", { kinds: ["order", "cycle"], poll: 60000 });
  const tick = useTicker();
  const toast = useToast();
  const [running, setRunning] = useState(false);
  const [view, setView] = useState<"spot" | "futures">(() => {
    try { return localStorage.getItem("bclaw:assets-view") === "futures" ? "futures" : "spot"; } catch { return "spot"; }
  });
  const o = ov.data;

  const series = (eq.data ?? []).filter((p) => p.total != null && (!p.mode || p.mode === o?.mode));
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
    const price = t.startsWith("USD") || t === "FDUSD" || t === "FUTURES" ? 1 : tick[`${t}USDT`]?.price ?? o?.market?.[t]?.price;
    const target = s[cat]?.tokens?.[t];
    return { t, cat, value, qty: o?.account_balances?.[t] ?? o?.balances?.[t] ?? 0, price, w: o?.token_weights?.[t] ?? 0,
      target: target === undefined ? 0 : target, locked: o?.capital?.locked?.[t] ?? 0, pf: perf.data?.assets?.[t] };
  }).sort((a, b) => b.value - a.value || (b.target ?? 0) - (a.target ?? 0));

  // spot | futures as on Binance's own asset page; inside each, 自选 (what the AI holds or targets) first,
  // then everything else grouped by Binance's categories (display only; risk bands follow the target categories)
  const bcat = (t: string) => o?.binance_categories?.[t]?.primary ?? (isStable(t) ? "稳定币" : "未分类");
  const acctTotal = Object.values(acct).reduce((x, y) => x + y, 0);
  // nothing held and not an investment target: hidden unless asked for
  const inTargets = (t: string) => Object.values(s).some((c) => t in c.tokens);
  const empty = (r: (typeof rows)[number]) => r.qty <= 0 && r.value < 0.01 && !r.locked && !o?.earn?.positions?.[r.t]?.amount;
  const [showEmpty, setShowEmpty] = useState(() => {
    try { return localStorage.getItem("bclaw:assets-show-empty") === "1"; } catch { return false; }
  });
  function toggleEmpty() {
    setShowEmpty(!showEmpty);
    try { localStorage.setItem("bclaw:assets-show-empty", showEmpty ? "0" : "1"); } catch { /* private mode */ }
  }
  const allSpot = rows.filter((r) => r.t !== "FUTURES");
  const hiddenN = allSpot.filter((r) => empty(r) && !inTargets(r.t)).length;
  const spotRows = showEmpty ? allSpot : allSpot.filter((r) => !empty(r) || inTargets(r.t));
  const picked = (r: (typeof rows)[number]) => !isStable(r.t) && bcat(r.t) !== "稳定币"
    && ((o?.values?.[r.t] ?? 0) >= 1 || inTargets(r.t));
  const spotSections = sectionsOf(spotRows, picked, (r) => bcat(r.t), (r) => r.value);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem("bclaw:assets-collapsed") ?? "[]")); } catch { return new Set(); }
  });
  function fold(key: string) {
    const next = new Set(collapsed);
    if (!next.delete(key)) next.add(key);
    setCollapsed(next);
    try { localStorage.setItem("bclaw:assets-collapsed", JSON.stringify([...next])); } catch { /* private mode */ }
  }
  function pickView(v: "spot" | "futures") {
    setView(v);
    try { localStorage.setItem("bclaw:assets-view", v); } catch { /* private mode */ }
  }

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
    // wide screens: summary | chart on top; from 1440 px the asset table sits beside approvals and the AI feed
    <Page className="grid grid-flow-row-dense grid-cols-1 gap-10 lg:grid-cols-12 lg:gap-x-8">
      {/* valuation */}
      <section className="flex flex-col gap-6 lg:col-span-5 xl:col-span-4">
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
        <div className="flex w-full gap-2">
          <button className="btn-primary flex-1 sm:flex-none lg:flex-1" onClick={runNow} disabled={running || o?.paused}>{running ? "决策中…" : "立即决策"}</button>
          <button className="btn-secondary flex-1 sm:flex-none lg:flex-1" onClick={toggle}>{o?.paused ? "恢复" : "暂停"}</button>
        </div>
        <dl className="grid w-full grid-cols-2 gap-y-3 border-t border-line pt-4 text-[13px] sm:grid-cols-4 lg:grid-cols-2">
          <div><dt className="text-fg-3">今日笔数</dt><dd className="num mt-1 text-[14px]">{o ? `${o.today.orders} / ${o.today.max_orders}` : "—"}</dd></div>
          <div><dt className="text-fg-3">今日金额</dt><dd className="num mt-1 text-[14px]">{o ? `${usd(o.today.notional, 0)} / ${usd(o.today.max_notional, 0)}` : "—"}</dd></div>
          <div><dt className="text-fg-3">BTC 7 日</dt><dd className="mt-1 text-[14px]"><Change v={o?.btc_change_7d} digits={1} /></dd></div>
          {o?.autopilot?.decision_mode === "auto" ? (
            <div title={`今日模型调用 ${o.autopilot.calls_today ?? 0} / ${o.autopilot.max_calls_per_day ?? "—"} · 波动 ${((o.autopilot.wake_move_pct ?? 0) * 100).toFixed(1)}% 提前唤醒${o.autopilot.streaming ? " · 实时行情已连接" : ""}`}>
              <dt className="text-fg-3">AI 自主 · 最晚复查</dt>
              <dd className="mt-1 text-[14px]"><NextIn at={o.next_cycle_at} /></dd>
            </div>
          ) : (
            <div><dt className="text-fg-3">下一轮</dt><dd className="mt-1 text-[14px]">{o ? <NextIn at={o.next_cycle_at} /> : "—"}</dd></div>
          )}
        </dl>
        {o?.paused && <p className="w-full text-[13px] text-warn">已暂停 · {o.pause_reason}</p>}
      </section>

      {/* valuation over time */}
      <section className="lg:col-span-7 xl:col-span-8">
        <ValuationChart current={shown} />
      </section>

      {/* approvals */}
      {!!pending.data?.length && (
        <section className="self-start lg:col-span-12 min-[1440px]:col-span-4 min-[1440px]:col-start-9">
          <SectionHead title={<>待审批 <span className="num text-warn">{pending.data.length}</span></>} doc="approvals" />
          <AnimatePresence mode="popLayout" initial={false}>
            {pending.data.map((a) => <ApprovalRow key={a.id} a={a} onDone={pending.reload} />)}
          </AnimatePresence>
        </section>
      )}

      {/* assets */}
      <section className="min-w-0 lg:col-span-12 min-[1440px]:col-span-8 min-[1440px]:col-start-1 min-[1440px]:row-span-2">
        <SectionHead title="资产" doc="dashboard" right={
          <Seg id="assets-view" value={view} onChange={pickView}
            items={[{ k: "spot" as const, label: "现货" }, { k: "futures" as const, label: <>合约{!!o?.futures?.open && <span className="num ml-1 text-fg-3">{o.futures.open}</span>}</> }]} />
        } />
        {view === "spot" ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px]">
            <thead>
              <tr className="border-b border-line">
                <th className="th">资产</th><th className="th text-right">数量</th><th className="th text-right">价格</th>
                <th className="th text-right">估值</th><th className="th text-right">成本价</th><th className="th text-right">盈亏</th>
                <th className="th text-right">占比 / 目标</th><th className="th w-[18%] pl-8">区间</th>
              </tr>
            </thead>
            {spotSections.map((sec) => (
            <tbody key={sec.key}>
              <GroupRow name={sec.name} n={sec.rows.length} open={!collapsed.has(`spot:${sec.key}`)} onToggle={() => fold(`spot:${sec.key}`)}
                cells={<>
                  <td className="td num text-right font-medium">{sec.value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                  <td className="td num text-right text-fg-3" colSpan={3}>{acctTotal > 0 ? `${((sec.value / acctTotal) * 100).toFixed(1)}%` : ""}</td>
                  <td className="td" />
                </>} span={3} />
              {!collapsed.has(`spot:${sec.key}`) && sec.rows.map((r) => {
                const band = s[r.cat]?.band ?? [0, 1];
                const catW = o?.category_weights?.[r.cat] ?? 0;
                const tv = Object.values(s[r.cat]?.tokens ?? {}).filter((x): x is number => x != null);
                const catT = tv.length ? tv.reduce((a, b) => a + b, 0) : null;
                return (
                  <tr key={r.t} className="border-b border-line">
                    <td className="td"><span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: CATEGORY_COLOR[r.cat] ?? "#8C8C8C" }} />
                      <b className="font-semibold">{r.t}</b>
                      {sec.key === PICKED && <span className="text-[12px] text-fg-3" title={o?.binance_categories?.[r.t]?.tags.join(" · ")}>{bcat(r.t)}</span>}
                      {!!o?.earn?.positions?.[r.t]?.amount && <span className="tag bg-hover text-fg-2" title="活期理财">理财 {o.earn.positions[r.t].amount.toLocaleString("en-US", { maximumFractionDigits: 2 })}</span>}
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
            ))}
          </table>
          {hiddenN > 0 && (
            <button className="mt-2 text-[13px] text-fg-3 hover:text-fg" onClick={toggleEmpty}>
              {showEmpty ? "隐藏零余额资产" : `显示 ${hiddenN} 个零余额资产（不在投资目标里）`}
            </button>
          )}
        </div>
        ) : <FuturesAssets o={o} bcat={bcat} collapsed={collapsed} fold={fold} />}
        {!o && <Skeleton className="mt-3 h-40" />}
      </section>

      {/* AI feed */}
      <section className="self-start lg:col-span-12 min-[1440px]:col-span-4 min-[1440px]:col-start-9">
        <SectionHead title="AI 动态" doc="decisions-and-risk" right={<Link to="/ai" className="text-[13px] text-fg-2 hover:text-fg">全部 ›</Link>} />
        {cycles.data?.length ? cycles.data.map((c) => (
          <Link key={c.id} to={`/ai/${c.id}`} className="flex flex-col gap-1.5 border-b border-line py-3.5 transition-colors hover:bg-raise sm:flex-row sm:items-center sm:gap-5 min-[1440px]:flex-col min-[1440px]:items-start min-[1440px]:gap-1.5">
            <span className="num w-28 shrink-0 text-[13px] text-fg-3">{ago(c.ts)}</span>
            <span className="min-w-0 flex-1 truncate text-[14px] min-[1440px]:line-clamp-2 min-[1440px]:w-full min-[1440px]:whitespace-normal">{c.summary || c.error || "…"}</span>
            <span className="flex shrink-0 flex-wrap gap-x-4 text-[13px]">
              {c.risk?.length ? c.risk.map((r) => (
                <span key={`${r.kind ?? "spot"}-${r.index}`} className="flex items-center gap-1.5"><Side side={r.side ?? r.op} />{token(r.symbol)} <span className="num">{usd(r.approved_usd || r.requested_usd || r.margin || r.requested_margin, 0)}</span> <Decision d={r.decision} /></span>
              )) : <span className={c.status === "error" ? "text-down" : "text-fg-3"}>{c.status === "error" ? "失败" : c.status === "skipped" ? "跳过" : "不操作"}</span>}
            </span>
          </Link>
        )) : cycles.loading ? <Skeleton className="mt-3 h-24" /> : <Empty text="暂无记录" />}
      </section>
    </Page>
  );
}
