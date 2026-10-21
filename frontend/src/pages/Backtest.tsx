import { Trash, X } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, post } from "../api";
import type { BacktestFull, BacktestParams, BacktestResult, BacktestRow, BacktestStep, EquityStats } from "../api";
import { AnalyticsNav } from "../components/AnalyticsNav";
import { DrawdownChart, EquityGrid, TradeGrid } from "../components/Metrics";
import { Num, Row } from "../components/settingsKit";
import TokenPicker from "../components/TokenPicker";
import { Change, Decision, Empty, Page, SectionHead, Seg, Side, Skeleton, Spinner } from "../components/ui";
import { fmtPrice, useApi, useToast } from "../lib";
import { useColors } from "../theme";

const STEP_H: Record<BacktestStep, number> = { "4h": 4, "12h": 12, "1d": 24 };
const STEPS: { k: BacktestStep; label: string }[] = [{ k: "4h", label: "4 小时" }, { k: "12h", label: "12 小时" }, { k: "1d", label: "1 天" }];
const MAX_ROUNDS = 400;

const iso = (d: Date) => d.toISOString().slice(0, 10);
const daysAgo = (n: number) => iso(new Date(Date.now() - n * 86400000));
const stamp = (t: number, time = true) => {
  const d = new Date(t * 1000);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}${time ? ` ${p(d.getHours())}:${p(d.getMinutes())}` : ""}`;
};
const pctv = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${(v * 100).toFixed(d)}%`);
const ratio = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));

export function rounds(p: Pick<BacktestParams, "start" | "end" | "step">) {
  const s = Date.parse(`${p.start}T00:00:00Z`), e = Date.parse(`${p.end}T00:00:00Z`) + 86400000;
  if (!Number.isFinite(s) || !Number.isFinite(e) || e <= s) return 0;
  return Math.floor((e - s) / 3600000 / STEP_H[p.step]);
}

const STATUS: Record<BacktestRow["status"], [string, string]> = {
  running: ["运行中", "text-warn"], done: ["完成", "text-up"], error: ["失败", "text-down"], cancelled: ["已停止", "text-fg-3"],
};

function NewRun({ busy, onStarted }: { busy: boolean; onStarted: (id: string) => void }) {
  const toast = useToast();
  const [p, setP] = useState<BacktestParams>({
    tokens: ["BTC", "ETH"], start: daysAgo(31), end: daysAgo(1), step: "1d", capital: 10000, fee: 0.001, slippage: 0.0005,
    approvals: "execute", model: "", allow_sell: null,
  });
  const [confirm, setConfirm] = useState(false);
  const [sending, setSending] = useState(false);
  const set = (x: Partial<BacktestParams>) => { setP((o) => ({ ...o, ...x })); setConfirm(false); };
  const n = rounds(p);
  const bad = !p.tokens.length ? "至少选一个币种" : n < 1 ? "区间无效" : n > MAX_ROUNDS ? `${n} 轮，超过上限 ${MAX_ROUNDS}` : p.end >= daysAgo(0) ? "结束日期最晚为昨天" : "";
  async function start() {
    setSending(true);
    try {
      const row = await post<BacktestRow>("/backtests", p);
      setConfirm(false);
      onStarted(row.id);
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setSending(false);
    }
  }
  const toggle = (t: string) => set({ tokens: p.tokens.includes(t) ? p.tokens.filter((x) => x !== t) : [...p.tokens, t].slice(0, 8) });
  return (
    <section>
      <SectionHead title="新建回测" />
      <Row label="币种">
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {p.tokens.map((t) => (
            <button key={t} onClick={() => toggle(t)} className="tag gap-1 bg-hover text-fg hover:bg-line" aria-label={`移除 ${t}`}>
              {t}<X size={11} />
            </button>
          ))}
        </div>
      </Row>
      <div className="border-b border-line py-2.5"><TokenPicker selected={p.tokens} onToggle={toggle} /></div>
      <Row label="区间">
        <input type="date" aria-label="开始日期" className="input num h-9 w-40" value={p.start} max={p.end} onChange={(e) => set({ start: e.target.value })} />
        <span className="text-fg-3">—</span>
        <input type="date" aria-label="结束日期" className="input num h-9 w-40" value={p.end} max={daysAgo(1)} onChange={(e) => set({ end: e.target.value })} />
      </Row>
      <Row label="决策间隔"><Seg id="bt-step" value={p.step} onChange={(step) => set({ step })} items={STEPS} /></Row>
      <Row label="初始资金"><Num value={p.capital} onChange={(capital) => set({ capital })} unit="USDT" min={100} step={100} /></Row>
      <Row label="手续费"><Num value={+(p.fee * 100).toFixed(4)} onChange={(v) => set({ fee: v / 100 })} unit="%" min={0} max={1} step={0.01} /></Row>
      <Row label="滑点"><Num value={+(p.slippage * 100).toFixed(4)} onChange={(v) => set({ slippage: v / 100 })} unit="%" min={0} max={2} step={0.01} /></Row>
      <Row label="需审批的交易">
        <Seg id="bt-appr" value={p.approvals} onChange={(approvals) => set({ approvals })}
          items={[{ k: "execute" as const, label: "执行" }, { k: "skip" as const, label: "跳过" }]} />
      </Row>
      <Row label="卖出">
        <Seg id="bt-sell" value={p.allow_sell == null ? "follow" : p.allow_sell ? "yes" : "no"}
          onChange={(v) => set({ allow_sell: v === "follow" ? null : v === "yes" })}
          items={[{ k: "follow" as const, label: "按当前设置" }, { k: "yes" as const, label: "允许" }, { k: "no" as const, label: "禁止" }]} />
      </Row>
      <Row label="模型">
        <input className="input h-9 w-56" placeholder="与实盘决策相同" value={p.model} maxLength={100} onChange={(e) => set({ model: e.target.value.trim() })} />
      </Row>
      <div className="flex flex-wrap items-center justify-end gap-3 pt-4">
        <span className={`text-[13px] ${bad ? "text-down" : "text-fg-2"}`}>{bad || `${n} 轮 · ${n} 次模型调用`}</span>
        {confirm ? (
          <>
            <button className="btn-secondary" onClick={() => setConfirm(false)}>取消</button>
            <button className="btn-primary" disabled={sending} onClick={start}>{sending ? <Spinner /> : `确认调用模型 ${n} 次`}</button>
          </>
        ) : (
          <button className="btn-primary" disabled={!!bad || busy} onClick={() => setConfirm(true)}>{busy ? "已有回测在运行" : "开始回测"}</button>
        )}
      </div>
    </section>
  );
}

function EquityChart({ points }: { points: BacktestResult["equity"] }) {
  const col = useColors();
  if (points.length < 2) return <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-line text-[13px] text-fg-3">数据不足</div>;
  const fmt = (t: number) => { const d = new Date(t * 1000); return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, "0")}`; };
  const names: Record<string, string> = { v: "策略", btc: "持有 BTC", basket: "等权持有" };
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={points} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={col.grid} vertical={false} />
        <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={fmt}
          tick={{ fill: col.fg3, fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={40} />
        <YAxis domain={["auto", "auto"]} tick={{ fill: col.fg3, fontSize: 11 }} axisLine={false} tickLine={false} width={56}
          tickFormatter={(v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 })} />
        <Tooltip isAnimationActive={false} cursor={{ stroke: col.lineStrong }}
          contentStyle={{ background: col.raise, border: `1px solid ${col.lineStrong}`, color: col.fg, borderRadius: 8, fontSize: 12 }}
          labelFormatter={(t) => stamp(Number(t))}
          formatter={(v, k) => [Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }), names[String(k)] ?? k]} />
        <Legend formatter={(k) => names[String(k)] ?? k} wrapperStyle={{ fontSize: 12, color: col.fg2 }} />
        <Line type="linear" dataKey="v" stroke={col.fg} strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line type="linear" dataKey="btc" stroke={col.warn} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        <Line type="linear" dataKey="basket" stroke={col.fg3} strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

function Compare({ r }: { r: Partial<BacktestResult> }) {
  const rows: [string, EquityStats | undefined][] = [["策略", r.stats], ["持有 BTC", r.btc], ["等权持有", r.basket]];
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[420px]">
        <thead><tr className="border-b border-line"><th className="th"></th><th className="th text-right">收益率</th><th className="th text-right">最大回撤</th><th className="th text-right">夏普比率</th><th className="th text-right">年化波动</th></tr></thead>
        <tbody>
          {rows.map(([k, e]) => (
            <tr key={k} className="border-b border-line">
              <td className="td font-semibold">{k}</td>
              <td className="td num text-right"><Change v={e?.total_return} /></td>
              <td className="td num text-right">{pctv(e?.max_drawdown)}</td>
              <td className="td num text-right">{ratio(e?.sharpe)}</td>
              <td className="td num text-right">{pctv(e?.volatility, 1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Detail({ id }: { id: string }) {
  const [poll, setPoll] = useState(0);
  const { data, error } = useApi<BacktestFull>(`/backtests/${id}`, { poll: poll || undefined, kinds: [] });
  useEffect(() => setPoll(data?.status === "running" ? 4000 : 0), [data?.status]);
  if (error && !data) return <Empty text={`读取失败：${error}`} />;
  if (!data || data.id !== id) return <Skeleton className="h-64" />;
  const r = data.result ?? {};
  const p = data.params;
  return (
    <div className="flex flex-col gap-10">
      <section>
        <SectionHead title={`${p.tokens.join(" · ")}　${p.start} — ${p.end}`} right={
          <span className={`text-[13px] ${STATUS[data.status][1]}`}>{STATUS[data.status][0]}{data.status === "running" ? ` ${data.progress}/${data.total}` : ""}</span>
        } />
        {data.error && <p className="pt-3 text-[13px] text-down">{data.error}</p>}
        <div className="mt-5 h-64"><EquityChart points={r.equity ?? []} /></div>
        {r.stats && r.stats.points >= 2 && <div className="pt-6"><EquityGrid e={r.stats} bench={r.btc?.total_return ?? null} /></div>}
      </section>
      {r.stats && (
        <section>
          <SectionHead title="对比" />
          <Compare r={r} />
        </section>
      )}
      {!!r.drawdown?.length && (
        <section>
          <SectionHead title="回撤" />
          <div className="mt-5 h-36"><DrawdownChart points={r.drawdown} /></div>
        </section>
      )}
      <section>
        <SectionHead title="已平仓交易" />
        <div className="pt-5">{r.trades?.trades ? <TradeGrid t={r.trades} /> : <Empty text="没有平仓交易" />}</div>
      </section>
      {r.decisions && r.usage && (
        <section>
          <SectionHead title="决策" />
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 pt-5 sm:grid-cols-4">
            {([["提出动作", r.decisions.proposed], ["执行", r.decisions.execute], ["跳过", r.decisions.skipped], ["风控拒绝", r.decisions.reject],
              ["模型调用", r.usage.calls], ["输入 tokens", r.usage.input_tokens.toLocaleString()], ["输出 tokens", r.usage.output_tokens.toLocaleString()],
              ["平均耗时", r.usage.calls ? `${(r.usage.model_ms / r.usage.calls / 1000).toFixed(1)} 秒` : "—"]] as [string, string | number][]).map(([k, v]) => (
              <div key={k}><dt className="text-[12px] text-fg-3">{k}</dt><dd className="num mt-1 text-[15px]">{v}</dd></div>
            ))}
          </dl>
        </section>
      )}
      <section>
        <SectionHead title="交易记录" right={r.model ? <span className="text-[12px] text-fg-3">{r.model}</span> : undefined} />
        {!r.log?.length ? <div className="pt-5"><Empty text="没有成交" /></div> : (
          <div className="max-h-[420px] overflow-auto">
            <table className="w-full min-w-[560px]">
              <thead><tr className="border-b border-line"><th className="th">时间</th><th className="th">方向</th><th className="th">币种</th><th className="th text-right">数量</th><th className="th text-right">价格</th><th className="th text-right">金额</th><th className="th text-right">手续费</th></tr></thead>
              <tbody>
                {r.log.map((t, i) => (
                  <tr key={i} className="border-b border-line">
                    <td className="td num text-fg-2">{stamp(t.ts)}</td>
                    <td className="td"><Side side={t.side} /></td>
                    <td className="td font-semibold">{t.token}</td>
                    <td className="td num text-right">{t.qty.toPrecision(6)}</td>
                    <td className="td num text-right">{fmtPrice(t.price)}</td>
                    <td className="td num text-right">{t.quote_usd.toFixed(2)}</td>
                    <td className="td num text-right text-fg-2">{t.fee.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section>
        <SectionHead title="决策轨迹" />
        {!r.rounds?.length ? <div className="pt-5"><Empty text="还没有决策" /></div> : (
          <ul className="max-h-[560px] overflow-auto">
            {[...r.rounds].reverse().map((x) => (
              <li key={x.i} className="border-b border-line py-3">
                <details>
                  <summary className="flex cursor-pointer list-none items-start gap-3">
                    <span className="num w-[132px] shrink-0 text-[13px] text-fg-2">{stamp(x.t)}</span>
                    <span className={`min-w-0 flex-1 text-[14px] ${x.status === "error" ? "text-down" : x.status === "skipped" ? "text-fg-3" : ""}`}>
                      {x.summary || "—"}
                    </span>
                    <span className="flex shrink-0 gap-2 text-[13px]">
                      {x.actions.map((a, j) => <span key={j}><Side side={a.side} /> {a.symbol.replace(/USDT$/, "")}</span>)}
                      {x.confidence != null && <span className="num text-fg-3">{Math.round(x.confidence * 100)}%</span>}
                    </span>
                  </summary>
                  {!!x.actions.length && (
                    <ul className="mt-2 flex flex-col gap-1.5 pl-[144px] max-sm:pl-0">
                      {x.actions.map((a, j) => (
                        <li key={j} className="text-[13px]">
                          <Side side={a.side} /> <span className="font-semibold">{a.symbol.replace(/USDT$/, "")}</span>{" "}
                          <span className="num">${a.approved_usd.toFixed(0)}</span>
                          {a.approved_usd !== a.requested_usd && <span className="num text-fg-3"> / ${a.requested_usd.toFixed(0)}</span>}{" "}
                          {a.decision === "skipped" ? <span className="text-fg-3">跳过</span> : <Decision d={a.decision} />}
                          <span className="text-fg-2"> · {a.reason_model}</span>
                          {a.decision !== "execute" && <span className="text-fg-3"> · {a.reason}</span>}
                        </li>
                      ))}
                    </ul>
                  )}
                </details>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export default function Backtest() {
  const toast = useToast();
  const [sel, setSel] = useState<string | null>(null);
  const [poll, setPoll] = useState(0);
  const { data: rows, error, reload } = useApi<BacktestRow[]>("/backtests", { poll: poll || undefined, kinds: [] });
  const running = rows?.some((r) => r.status === "running") ?? false;
  useEffect(() => setPoll(running ? 4000 : 0), [running]);
  const current = sel && rows?.some((r) => r.id === sel) ? sel : rows?.[0]?.id ?? null;
  async function act(r: BacktestRow) {
    try {
      if (r.status === "running") await post(`/backtests/${r.id}/cancel`);
      else if (window.confirm("删除这条回测？")) await api(`/backtests/${r.id}`, { method: "DELETE" });
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }
  return (
    <Page className="flex flex-col gap-10">
      <div className="border-b border-line pb-3"><AnalyticsNav /></div>
      <NewRun busy={running} onStarted={(id) => { setSel(id); reload(); }} />
      <section>
        <SectionHead title="回测记录" />
        {error && !rows ? <Empty text={`读取失败：${error}`} /> : !rows ? <Skeleton className="h-24" /> : !rows.length ? <div className="pt-5"><Empty text="还没有回测" /></div> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px]">
              <thead><tr className="border-b border-line">
                <th className="th">币种</th><th className="th">区间</th><th className="th">间隔</th><th className="th">状态</th>
                <th className="th text-right">收益率</th><th className="th text-right">持有 BTC</th><th className="th text-right">最大回撤</th><th className="th text-right">夏普</th><th className="th"></th>
              </tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} onClick={() => setSel(r.id)} aria-selected={r.id === current}
                    className={`cursor-pointer border-b border-line ${r.id === current ? "bg-hover" : "hover:bg-hover"}`}>
                    <td className="td font-semibold">{r.params.tokens.join(" · ")}</td>
                    <td className="td num text-fg-2">{r.params.start} — {r.params.end}</td>
                    <td className="td">{STEPS.find((s) => s.k === r.params.step)?.label}</td>
                    <td className={`td text-[13px] ${STATUS[r.status][1]}`}>{STATUS[r.status][0]}{r.status === "running" ? ` ${r.progress}/${r.total}` : ""}</td>
                    <td className="td num text-right"><Change v={r.summary.total_return} /></td>
                    <td className="td num text-right"><Change v={r.summary.btc_return} /></td>
                    <td className="td num text-right">{pctv(r.summary.max_drawdown)}</td>
                    <td className="td num text-right">{ratio(r.summary.sharpe)}</td>
                    <td className="td text-right">
                      <button aria-label={r.status === "running" ? "停止" : "删除"} onClick={(e) => { e.stopPropagation(); act(r); }}
                        className="rounded-md p-1 text-fg-3 hover:bg-line hover:text-fg">
                        {r.status === "running" ? <X size={15} /> : <Trash size={15} />}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {current && <Detail id={current} />}
    </Page>
  );
}
