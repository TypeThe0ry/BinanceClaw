import { Trash } from "@phosphor-icons/react";
import { Fragment, useEffect, useState } from "react";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  api, post, type ArenaBatch, type ArenaBoard, type ArenaConfig, type ArenaContestant, type ArenaCurves, type ArenaEntry,
} from "../api";
import { Num, Row } from "../components/settingsKit";
import { Change, DocLink, Empty, Page, SectionHead, Side, Skeleton, Spinner, Tabs } from "../components/ui";
import { clock, pct, usd, useApi, useToast } from "../lib";
import { useColors } from "../theme";

const PROVIDER: Record<string, string> = { claude_code: "Claude 订阅", openai: "OpenAI", openai_compat: "OpenAI 兼容", anthropic_api: "Anthropic API" };
const BENCH = "btc_hold";
type Tab = "board" | "rounds" | "setup";

const label = (c: Pick<ArenaContestant, "provider" | "model">) => c.model || PROVIDER[c.provider] || c.provider;
const ratio = (v: number | null) => (v == null ? "—" : v.toFixed(2));
const tokensK = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K` : String(n));

export default function Arena() {
  const { data: b, reload } = useApi<ArenaBoard>("/arena", { kinds: ["arena"], poll: 60000 });
  const [tab, setTab] = useState<Tab>("board");
  const toast = useToast();

  async function run() {
    try { await post("/arena/run"); toast("比赛开始"); reload(); } catch (e) { toast((e as Error).message, "err"); }
  }
  async function reset() {
    if (!window.confirm("重置比赛：所有模型回到初始资金，清空成绩和回合记录？")) return;
    try { await post("/arena/reset"); toast("已重置"); reload(); } catch (e) { toast((e as Error).message, "err"); }
  }

  return (
    <Page>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <h1 className="flex items-center gap-1.5 text-[20px] font-semibold">竞技场<DocLink to="arena" /></h1>
        <div className="flex items-center gap-2">
          {b && <Status b={b} />}
          <button className="btn-secondary btn-sm" onClick={reset} disabled={!b || b.running}>重置</button>
          <button className="btn-primary btn-sm" onClick={run} disabled={!b || b.running || !b.contestants.some((c) => c.enabled)}>
            {b?.running ? <Spinner /> : "立即比赛"}
          </button>
        </div>
      </div>
      <div className="pt-4">
        <Tabs id="arena" value={tab} onChange={setTab}
          items={[{ k: "board", label: "排行" }, { k: "rounds", label: "回合" }, { k: "setup", label: "参赛与规则" }]} />
      </div>
      <div className="pt-6">
        {!b ? <Skeleton className="h-64 w-full" />
          : tab === "board" ? <Board b={b} onSetup={() => setTab("setup")} />
          : tab === "rounds" ? <Rounds />
          : <Setup b={b} reload={reload} />}
      </div>
    </Page>
  );
}

function Status({ b }: { b: ArenaBoard }) {
  if (b.running) return <span className="tag gap-1 bg-up/10 text-up"><Spinner className="h-2.5 w-2.5" />比赛中</span>;
  if (!b.config.enabled) return <span className="tag bg-hover text-fg-2">自动比赛已关闭</span>;
  return (
    <span className="tag num bg-hover text-fg-2">
      下一轮 {b.next_round && b.next_round > Date.now() / 1000 ? clock(b.next_round) : "即将开始"} · 今日 {b.rounds_today}/{b.config.max_rounds_per_day}
    </span>
  );
}

// ---------------------------------------------------------------- leaderboard
function Board({ b, onSetup }: { b: ArenaBoard; onSetup: () => void }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!b.contestants.length) {
    return (
      <div className="py-10 text-center">
        <p className="text-[13px] text-fg-3">还没有参赛模型</p>
        <button className="btn-secondary btn-sm mt-3" onClick={onSetup}>添加模型</button>
      </div>
    );
  }
  return (
    <div className="space-y-8">
      <Curves b={b} />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[820px] text-[14px]">
          <thead>
            <tr className="border-b border-line text-left text-[12px] text-fg-3">
              <th className="py-2 pr-3 font-normal">#</th>
              <th className="py-2 pr-3 font-normal">模型</th>
              <th className="py-2 pr-3 text-right font-normal">总资产</th>
              <th className="py-2 pr-3 text-right font-normal">收益率</th>
              <th className="py-2 pr-3 text-right font-normal">超额 BTC</th>
              <th className="py-2 pr-3 text-right font-normal">最大回撤</th>
              <th className="py-2 pr-3 text-right font-normal">夏普</th>
              <th className="py-2 pr-3 text-right font-normal">胜率</th>
              <th className="py-2 pr-3 text-right font-normal">交易</th>
              <th className="py-2 text-right font-normal">回合</th>
            </tr>
          </thead>
          <tbody>
            {b.contestants.map((c, i) => (
              <Fragment key={c.id}>
                <tr onClick={() => setOpen(open === c.id ? null : c.id)} aria-expanded={open === c.id}
                  className={`cursor-pointer border-b border-line hover:bg-hover ${c.enabled ? "" : "opacity-50"}`}>
                  <td className="num py-3 pr-3 text-fg-2">{c.rank}</td>
                  <td className="py-3 pr-3">
                    <span className="flex items-center gap-2">
                      <Dot i={i} />
                      <span className="font-medium">{c.name}</span>
                      <span className="text-[12px] text-fg-3">{label(c)}</span>
                      {!c.available && <span className="tag bg-warn/15 text-warn" title={c.unavailable_reason}>不可用</span>}
                    </span>
                  </td>
                  <td className="num py-3 pr-3 text-right">{usd(c.equity)}</td>
                  <td className="py-3 pr-3 text-right"><Change v={c.return} /></td>
                  <td className="py-3 pr-3 text-right"><Change v={c.vs_btc} /></td>
                  <td className="num py-3 pr-3 text-right text-fg-2">{pct(c.max_drawdown, 2)}</td>
                  <td className="num py-3 pr-3 text-right text-fg-2">{ratio(c.sharpe)}</td>
                  <td className="num py-3 pr-3 text-right text-fg-2">{c.win_rate == null ? "—" : `${pct(c.win_rate, 0)} (${c.closed_trades})`}</td>
                  <td className="num py-3 pr-3 text-right text-fg-2">{c.trades}</td>
                  <td className="num py-3 text-right text-fg-2">{c.rounds}{c.errors ? <span className="text-down"> · {c.errors}✗</span> : null}</td>
                </tr>
                {open === c.id && <tr><td colSpan={10} className="border-b border-line bg-hover/40 px-3 py-4"><Detail c={c} /></td></tr>}
              </Fragment>
            ))}
            {b.benchmark && (
              <tr className="text-fg-2">
                <td className="py-3 pr-3" />
                <td className="py-3 pr-3"><span className="flex items-center gap-2"><Dot bench />BTC 持有</span></td>
                <td className="num py-3 pr-3 text-right">{usd(b.benchmark.equity)}</td>
                <td className="py-3 pr-3 text-right"><Change v={b.benchmark.return} /></td>
                <td className="py-3 pr-3 text-right text-fg-3">—</td>
                <td className="num py-3 pr-3 text-right">{pct(b.benchmark.max_drawdown, 2)}</td>
                <td className="num py-3 pr-3 text-right">{ratio(b.benchmark.sharpe)}</td>
                <td colSpan={3} />
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Dot({ i = 0, bench = false }: { i?: number; bench?: boolean }) {
  const col = useColors();
  return <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ background: bench ? col.fg3 : col.series[i % col.series.length] }} />;
}

function Detail({ c }: { c: ArenaContestant }) {
  const { data } = useApi<ArenaEntry[]>(`/arena/rounds?contestant=${c.id}&limit=8`, { kinds: ["arena"] });
  const pos = Object.entries(c.positions).sort((a, b) => b[1].value_usd - a[1].value_usd);
  return (
    <div className="grid gap-6 md:grid-cols-[280px_1fr]">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-[13px]">
        <dt className="text-fg-3">现金</dt><dd className="num text-right">{usd(c.cash)}</dd>
        {pos.map(([t, p]) => (
          <Fragment key={t}>
            <dt className="text-fg-2">{t} <span className="num text-fg-3">{pct(p.weight, 0)}</span></dt>
            <dd className="num text-right">{usd(p.value_usd)} <Change v={p.unrealized_pct} /></dd>
          </Fragment>
        ))}
        <dt className="text-fg-3">已实现盈亏</dt><dd className="num text-right">{usd(c.realized)}</dd>
        <dt className="text-fg-3">手续费</dt><dd className="num text-right">{usd(c.fees)}</dd>
        <dt className="text-fg-3">Token</dt><dd className="num text-right">{tokensK(c.tokens_in)} / {tokensK(c.tokens_out)}</dd>
        {c.cost_usd > 0 && <><dt className="text-fg-3">模型费用</dt><dd className="num text-right">{usd(c.cost_usd, 4)}</dd></>}
        <dt className="text-fg-3">开始</dt><dd className="num text-right">{clock(c.started)}</dd>
      </dl>
      <div className="space-y-4">
        {data == null ? <Skeleton className="h-20 w-full" /> : data.length ? data.map((r) => <Entry key={r.id} r={r} time />) : <Empty text="还没有回合" />}
      </div>
    </div>
  );
}

function Entry({ r, time = false }: { r: ArenaEntry; time?: boolean }) {
  return (
    <div className="text-[13px]">
      <div className="flex items-center gap-2 text-fg-3">
        {time ? <span className="num">{clock(r.ts)}</span> : <span className="font-medium text-fg">{r.name}</span>}
        {r.confidence != null && <span className="num">把握 {pct(r.confidence, 0)}</span>}
        <span className="num ml-auto">{usd(r.equity)}</span>
      </div>
      {r.status === "error" ? <p className="mt-1 text-down">{r.error}</p> : <p className="mt-1 text-fg">{r.summary}</p>}
      {r.actions.length > 0 && (
        <ul className="mt-1.5 space-y-1">
          {r.actions.map((a, i) => (
            <li key={i} className={`flex flex-wrap gap-x-2 ${a.result === "filled" ? "" : "text-fg-3 line-through decoration-fg-3/50"}`}>
              <Side side={a.side} /><span>{a.symbol}</span>
              <span className="num">{usd(a.usd ?? a.quote_usd)}</span>
              {a.pnl != null && <span className={`num ${a.pnl >= 0 ? "text-up" : "text-down"}`}>{a.pnl >= 0 ? "+" : ""}{usd(a.pnl)}</span>}
              {a.note && <span>{a.note}</span>}
              {a.reason && <span className="text-fg-3">{a.reason}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Curves({ b }: { b: ArenaBoard }) {
  const col = useColors();
  const { data } = useApi<ArenaCurves>("/arena/curves", { kinds: ["arena"], poll: 300000 });
  if (!data) return <Skeleton className="h-56 w-full" />;
  const base: Record<string, number> = { [BENCH]: b.config.initial_usd };
  b.contestants.forEach((c) => { base[c.id] = c.initial_usd; });
  const rows = new Map<number, Record<string, number>>();
  for (const [id, pts] of Object.entries(data)) {
    if (!base[id]) continue;
    for (const [t, e] of pts) {
      const r = rows.get(t) ?? { t };
      r[id] = e / base[id] - 1;
      rows.set(t, r);
    }
  }
  const pts = [...rows.values()].sort((a, z) => a.t - z.t);
  if (pts.length < 2) {
    return <div className="flex h-56 items-center justify-center rounded-lg border border-dashed border-line text-[13px] text-fg-3">数据不足</div>;
  }
  const names: Record<string, string> = { [BENCH]: "BTC 持有" };
  b.contestants.forEach((c) => { names[c.id] = c.name; });
  return (
    <div className="h-56 lg:h-72">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={pts} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={(t) => clock(Number(t))}
            tick={{ fill: col.fg3, fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={60} />
          <YAxis tickFormatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} tick={{ fill: col.fg3, fontSize: 11 }} axisLine={false}
            tickLine={false} width={52} />
          <Tooltip isAnimationActive={false} cursor={{ stroke: col.lineStrong }}
            contentStyle={{ background: col.raise, border: `1px solid ${col.lineStrong}`, color: col.fg, borderRadius: 8, fontSize: 12 }}
            labelFormatter={(t) => clock(Number(t))}
            formatter={(v, k) => [`${Number(v) >= 0 ? "+" : ""}${(Number(v) * 100).toFixed(2)}%`, names[String(k)] ?? k]} />
          <Line dataKey={BENCH} stroke={col.fg3} strokeDasharray="4 4" dot={false} strokeWidth={1.25} connectNulls isAnimationActive={false} />
          {b.contestants.map((c, i) => (
            <Line key={c.id} dataKey={c.id} stroke={col.series[i % col.series.length]} dot={false} strokeWidth={1.75} connectNulls
              isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------- rounds: every model's answer to the same snapshot
function Rounds() {
  const { data } = useApi<ArenaBatch[]>("/arena/rounds?limit=20", { kinds: ["arena"] });
  if (!data) return <Skeleton className="h-40 w-full" />;
  if (!data.length) return <Empty text="还没有回合" />;
  return (
    <div className="space-y-8">
      {data.map((g) => (
        <section key={g.batch}>
          <SectionHead title={<span className="num">{clock(g.ts)}</span>} />
          <div className="grid gap-6 pt-4 md:grid-cols-2 xl:grid-cols-3">
            {g.entries.map((r) => <Entry key={r.id} r={r} />)}
          </div>
        </section>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- contestants & rules
function Setup({ b, reload }: { b: ArenaBoard; reload: () => void }) {
  const toast = useToast();
  const [cfg, setCfg] = useState<ArenaConfig>(b.config);
  const [tokens, setTokens] = useState(b.config.tokens.join(", "));
  useEffect(() => { setCfg(b.config); setTokens(b.config.tokens.join(", ")); }, [b.config]);
  const [nw, setNw] = useState({ name: "", provider: "claude_code", model: "", base_url: "" });

  async function saveCfg(patch: Partial<ArenaConfig>) {
    try { await api("/arena/config", { method: "PUT", json: patch }); toast("已保存"); reload(); } catch (e) { toast((e as Error).message, "err"); }
  }
  async function add() {
    try {
      await post("/arena/contestants", nw);
      setNw({ ...nw, name: "", model: "" });
      toast("已添加");
      reload();
    } catch (e) { toast((e as Error).message, "err"); }
  }
  async function edit(c: ArenaContestant, patch: Record<string, unknown>) {
    try { await api(`/arena/contestants/${c.id}`, { method: "PATCH", json: patch }); reload(); } catch (e) { toast((e as Error).message, "err"); }
  }
  async function remove(c: ArenaContestant) {
    if (!window.confirm(`移除 ${c.name}？它的成绩和回合记录会一起删除。`)) return;
    try { await api(`/arena/contestants/${c.id}`, { method: "DELETE" }); reload(); } catch (e) { toast((e as Error).message, "err"); }
  }

  return (
    <div className="grid gap-10 lg:grid-cols-2">
      <section>
        <SectionHead title="参赛模型" right={<span className="num text-[12px] text-fg-3">{b.contestants.length}/{b.max_contestants}</span>} />
        <ul>
          {b.contestants.map((c, i) => (
            <li key={c.id} className="flex min-h-[52px] items-center gap-3 border-b border-line py-2">
              <input type="checkbox" className="h-4 w-4 accent-white" checked={c.enabled} aria-label={`${c.name} 参赛`}
                onChange={(e) => edit(c, { enabled: e.target.checked })} />
              <Dot i={i} />
              <span className="font-medium">{c.name}</span>
              <span className="truncate text-[12px] text-fg-3">{PROVIDER[c.provider] ?? c.provider}{c.model ? ` · ${c.model}` : ""}</span>
              {!c.available && <span className="tag bg-warn/15 text-warn" title={c.unavailable_reason}>不可用</span>}
              <button className="ml-auto rounded-md p-1.5 text-fg-3 hover:text-down" aria-label={`移除 ${c.name}`} onClick={() => remove(c)}>
                <Trash size={16} />
              </button>
            </li>
          ))}
        </ul>
        {b.contestants.length < b.max_contestants && (
          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            <input className="input h-9" placeholder="名称" aria-label="名称" value={nw.name} maxLength={30}
              onChange={(e) => setNw({ ...nw, name: e.target.value })} />
            <select className="input h-9" aria-label="提供方" value={nw.provider} onChange={(e) => setNw({ ...nw, provider: e.target.value })}>
              {b.providers.map((p) => <option key={p} value={p}>{PROVIDER[p] ?? p}</option>)}
            </select>
            <input className="input h-9" placeholder="模型（留空用默认）" aria-label="模型" value={nw.model}
              onChange={(e) => setNw({ ...nw, model: e.target.value })} />
            {nw.provider === "openai_compat" && (
              <input className="input h-9" placeholder="base_url" aria-label="base_url" value={nw.base_url}
                onChange={(e) => setNw({ ...nw, base_url: e.target.value })} />
            )}
            <button className="btn-primary btn-sm h-9 sm:col-span-2" onClick={add} disabled={!nw.name.trim()}>添加</button>
          </div>
        )}
      </section>

      <section>
        <SectionHead title="规则" />
        <Row label="自动比赛">
          <input type="checkbox" className="h-4 w-4 accent-white" checked={cfg.enabled} aria-label="自动比赛"
            onChange={(e) => saveCfg({ enabled: e.target.checked })} />
        </Row>
        <Row label="间隔"><Num value={cfg.interval_minutes} unit="分钟" min={5} max={1440} onChange={(v) => setCfg({ ...cfg, interval_minutes: v })} /></Row>
        <Row label="每日最多"><Num value={cfg.max_rounds_per_day} unit="轮" min={1} max={288} onChange={(v) => setCfg({ ...cfg, max_rounds_per_day: v })} /></Row>
        <Row label="单币上限"><Num value={Math.round(cfg.max_position_pct * 100)} unit="%" min={1} max={100}
          onChange={(v) => setCfg({ ...cfg, max_position_pct: v / 100 })} /></Row>
        <Row label="初始资金（重置后生效）"><Num value={cfg.initial_usd} unit="USDT" min={100} step={100} onChange={(v) => setCfg({ ...cfg, initial_usd: v })} /></Row>
        <Row label="比赛币种">
          <input className="input h-9 w-64" aria-label="比赛币种" value={tokens} onChange={(e) => setTokens(e.target.value)} />
        </Row>
        <div className="mt-4 flex justify-end">
          <button className="btn-primary btn-sm" onClick={() => saveCfg({
            interval_minutes: cfg.interval_minutes, max_rounds_per_day: cfg.max_rounds_per_day, max_position_pct: cfg.max_position_pct,
            initial_usd: cfg.initial_usd, tokens: tokens.split(/[\s,，]+/).filter(Boolean),
          })}>保存</button>
        </div>
      </section>
    </div>
  );
}

