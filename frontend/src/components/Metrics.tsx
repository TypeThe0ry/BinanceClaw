import type { ReactNode } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { EquityStats, TradeStats } from "../api";
import { useColors } from "../theme";
import { Change } from "./ui";

const ratio = (v: number | null | undefined, d = 2) => (v == null ? "—" : v.toFixed(d));
const money = (v: number | null | undefined) =>
  v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

function Cell({ label, children, tone }: { label: string; children: ReactNode; tone?: "up" | "down" }) {
  return (
    <div>
      <dt className="text-[12px] text-fg-3">{label}</dt>
      <dd className={`num mt-1 text-[15px] ${tone === "up" ? "text-up" : tone === "down" ? "text-down" : ""}`}>{children}</dd>
    </div>
  );
}

const toneOf = (v: number | null | undefined, pivot = 0) => (v == null ? undefined : v >= pivot ? "up" : "down");

/** Return, risk and risk-adjusted ratios of an equity curve, optionally against a benchmark. */
export function EquityGrid({ e, bench, benchName = "BTC" }: { e: EquityStats; bench?: number | null; benchName?: string }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
      <Cell label="收益率"><Change v={e.total_return} /></Cell>
      {bench !== undefined && <Cell label={`同期 ${benchName}`}><Change v={bench} /></Cell>}
      <Cell label="年化收益"><Change v={e.cagr} /></Cell>
      <Cell label="最大回撤" tone={e.max_drawdown ? "down" : undefined}>{e.max_drawdown == null ? "—" : `${(e.max_drawdown * 100).toFixed(2)}%`}</Cell>
      <Cell label="最长回撤期">{e.max_drawdown_days == null ? "—" : `${e.max_drawdown_days.toFixed(1)} 天`}</Cell>
      <Cell label="夏普比率" tone={toneOf(e.sharpe)}>{ratio(e.sharpe)}</Cell>
      <Cell label="索提诺比率" tone={toneOf(e.sortino)}>{ratio(e.sortino)}</Cell>
      <Cell label="卡玛比率" tone={toneOf(e.calmar)}>{ratio(e.calmar)}</Cell>
      <Cell label="年化波动">{e.volatility == null ? "—" : `${(e.volatility * 100).toFixed(1)}%`}</Cell>
      <Cell label={e.period === "1h" ? "最好 / 最差一小时" : "最好 / 最差一天"}>
        <Change v={e.best_period} /> <span className="text-fg-3">/</span> <Change v={e.worst_period} />
      </Cell>
    </dl>
  );
}

/** Closed-trade statistics. */
export function TradeGrid({ t }: { t: TradeStats }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
      <Cell label="平仓笔数">{t.trades}</Cell>
      <Cell label="胜率">{t.win_rate == null ? "—" : `${(t.win_rate * 100).toFixed(1)}%`} <span className="text-[12px] text-fg-3">{t.wins}胜 {t.losses}负</span></Cell>
      <Cell label="盈亏比" tone={toneOf(t.profit_factor, 1)}>{ratio(t.profit_factor)}</Cell>
      <Cell label="净盈亏" tone={toneOf(t.net)}>{money(t.net)}</Cell>
      <Cell label="平均盈利 / 亏损">{t.avg_win == null ? "—" : money(t.avg_win)} <span className="text-fg-3">/</span> {t.avg_loss == null ? "—" : money(-t.avg_loss)}</Cell>
      <Cell label="每笔期望" tone={toneOf(t.expectancy)}>{money(t.expectancy)}</Cell>
      <Cell label="最大单笔盈利 / 亏损">{money(t.best)} <span className="text-fg-3">/</span> {money(t.worst)}</Cell>
      <Cell label="最长连胜 / 连亏">{t.max_win_streak} / {t.max_loss_streak}</Cell>
    </dl>
  );
}

/** Underwater chart: how far below its running peak the equity was over time. */
export function DrawdownChart({ points }: { points: { t: number; dd: number }[] }) {
  const col = useColors();
  if (points.length < 2) return <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-line text-[13px] text-fg-3">数据不足</div>;
  const data = points.map((p) => ({ t: p.t, dd: -p.dd * 100 }));
  const fmt = (t: number) => { const d = new Date(t * 1000); return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, "0")}`; };
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
        <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={fmt}
          tick={{ fill: col.fg3, fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={40} />
        <YAxis domain={["dataMin", 0]} tick={{ fill: col.fg3, fontSize: 11 }} axisLine={false} tickLine={false} width={40}
          tickFormatter={(v) => `${Number(v).toFixed(0)}%`} />
        <Tooltip isAnimationActive={false} cursor={{ stroke: col.lineStrong }}
          contentStyle={{ background: col.raise, border: `1px solid ${col.lineStrong}`, color: col.fg, borderRadius: 8, fontSize: 12 }}
          labelFormatter={(t) => new Date(Number(t) * 1000).toLocaleString()} formatter={(v) => [`${Number(v).toFixed(2)}%`, "回撤"]} />
        <Area type="stepAfter" dataKey="dd" stroke={col.down} strokeWidth={1.5} fill={col.down} fillOpacity={0.15} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
