import { useState } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApi } from "../lib";
import { Skeleton } from "./ui";

export type ValRange = "1d" | "7d" | "30d" | "90d" | "1y";
type Base = { t: number; total: number; full: boolean } | null;
export type ValHistory = { range: ValRange; start: number; now: number; points: { t: number; total: number }[]; bases: Record<ValRange, Base> };

export const RANGES: { k: ValRange; label: string }[] = [
  { k: "1d", label: "1日" }, { k: "7d", label: "7日" }, { k: "30d", label: "30天" }, { k: "90d", label: "90天" }, { k: "1y", label: "一年" },
];

const money = (v: number) => v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (v: number) => `${v >= 0 ? "+" : "−"}${money(Math.abs(v))}`;
const pct = (v: number) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(2)}%`;

function stamp(t: number, rng: ValRange, withTime = false) {
  const d = new Date(t * 1000);
  const md = `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, "0")}`;
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (rng === "1d") return hm;
  if (rng === "1y" && !withTime) return `${d.getFullYear() % 100}-${md}`;
  return withTime ? `${md} ${hm}` : md;
}

/** Axis ticks on round boundaries: every 4 hours for 1D, every 1 / 5 / 15 / 60 days otherwise. */
function ticksFor(rng: ValRange, from: number, to: number) {
  const step = rng === "1d" ? 4 * 3600 : { "7d": 1, "30d": 5, "90d": 15, "1y": 60 }[rng] * 86400;
  const d = new Date(from * 1000);
  if (rng === "1d") d.setHours(Math.ceil(d.getHours() / 4) * 4, 0, 0, 0); else { d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + 1); }
  const out: number[] = [];
  for (let t = d.getTime() / 1000; t <= to; t += step) out.push(t);
  return out;
}

/** Change of the account valuation since the start of a range ("insufficient data" without history). */
export function rangeChange(base: Base | undefined, current: number | null | undefined) {
  if (!base || current == null || !base.total) return null;
  const diff = current - base.total;
  return { diff, pct: diff / base.total, full: base.full, since: base.t };
}

/** Home page: valuation line chart with 1D / 7D / 30D / 90D / 1Y tabs, change amount and percent. */
export function ValuationChart({ current }: { current: number | null | undefined }) {
  const [rng, setRng] = useState<ValRange>(() => {
    try { const v = localStorage.getItem("bclaw:val-range"); return RANGES.some((r) => r.k === v) ? (v as ValRange) : "7d"; } catch { return "7d"; }
  });
  const h = useApi<ValHistory>(`/equity/history?range=${rng}`, { kinds: ["cycle"], poll: 300000 });
  function pick(k: ValRange) {
    setRng(k);
    try { localStorage.setItem("bclaw:val-range", k); } catch { /* private mode */ }
  }

  const data = h.data?.range === rng ? h.data : null;
  const pts = data ? [...data.points] : [];
  if (pts.length && current != null) pts.push({ t: Date.now() / 1000, total: current });
  const ch = rangeChange(data?.bases[rng], current);
  const color = (ch?.diff ?? 0) >= 0 ? "#20B26C" : "#EF454A";

  return (
    <div className="w-full">
      <div role="tablist" aria-label="估值区间" className="grid grid-cols-5 gap-1 rounded-lg bg-field p-0.5">
        {RANGES.map((r) => {
          const c = rangeChange(h.data?.bases[r.k], current);
          return (
            <button key={r.k} role="tab" aria-selected={rng === r.k} onClick={() => pick(r.k)}
              title={!c ? "数据不足" : c.full ? undefined : `数据不足：按最早的记录（${stamp(c.since, "7d", true)}）计算`}
              className={`rounded-md px-1 py-1.5 text-center transition-colors ${rng === r.k ? "bg-line-strong" : "hover:bg-hover"}`}>
              <span className={`block text-[13px] font-medium ${rng === r.k ? "text-fg" : "text-fg-2"}`}>{r.label}</span>
              <span className={`num block text-[12px] ${!c ? "text-fg-3" : c.diff >= 0 ? "text-up" : "text-down"}`}>
                {c ? pct(c.pct) : "—"}{c && !c.full ? "*" : ""}
              </span>
            </button>
          );
        })}
      </div>

      <div className="mt-3 flex flex-wrap items-baseline gap-x-3 text-[13px]">
        {!data ? <Skeleton className="h-4 w-40" /> : ch ? (
          <>
            <span className={`num text-[15px] font-medium ${ch.diff >= 0 ? "text-up" : "text-down"}`}>{signed(ch.diff)}</span>
            <span className={`num ${ch.diff >= 0 ? "text-up" : "text-down"}`}>{pct(ch.pct)}</span>
            {!ch.full && <span className="text-fg-3">数据不足，自 {stamp(ch.since, rng === "1d" ? "7d" : rng, true)} 起</span>}
          </>
        ) : <span className="text-fg-3">数据不足</span>}
      </div>

      <div className="mt-2 h-44">
        {!data ? <Skeleton className="h-full w-full" /> : pts.length > 1 ? (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={pts} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="val-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={color} stopOpacity={0.25} />
                  <stop offset="100%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} ticks={ticksFor(rng, pts[0].t, pts[pts.length - 1].t)} tickFormatter={(t) => stamp(t, rng)}
                tick={{ fill: "#5C5C5C", fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={40} />
              <YAxis domain={["dataMin", "dataMax"]} hide />
              <Tooltip cursor={{ stroke: "#2E2E2E" }} isAnimationActive={false}
                contentStyle={{ background: "#0D0D0D", border: "1px solid #2E2E2E", borderRadius: 8, fontSize: 12 }}
                labelFormatter={(t) => stamp(Number(t), rng, true)} formatter={(v) => [money(Number(v)), "估值"]} />
              <Area type="monotone" dataKey="total" stroke={color} strokeWidth={1.5} fill="url(#val-fill)" isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-line text-[13px] text-fg-3">
            数据不足，这个区间还没有估值记录
          </div>
        )}
      </div>
    </div>
  );
}
