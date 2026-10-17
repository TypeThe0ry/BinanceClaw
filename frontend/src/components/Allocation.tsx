import { motion } from "framer-motion";
import type { Category } from "../api";
import { CATEGORY_COLOR, CATEGORY_LABEL, pct } from "../lib";

const R = 78;
const R_IN = 60;
const C = 2 * Math.PI * R;
const C_IN = 2 * Math.PI * R_IN;

function arcs(weights: [string, number][], circ: number) {
  let acc = 0;
  return weights.map(([k, w]) => {
    const len = Math.max(0, w) * circ;
    const seg = { k, len, offset: -acc * circ };
    acc += w;
    return seg;
  });
}

/** Outer ring = actual allocation. Inner hairline ring = target allocation. */
export function AllocationRing({ weights, targets }: { weights: Record<string, number>; targets: Record<string, Category> }) {
  const order = Object.keys(targets);
  const actual = order.map((k) => [k, weights[k] ?? 0] as [string, number]);
  const target = order.map((k) => [k, Object.values(targets[k].tokens).reduce((a, b) => a + b, 0)] as [string, number]);
  const gap = 0.006;
  return (
    <svg viewBox="0 0 200 200" className="h-[184px] w-[184px] shrink-0 -rotate-90" role="img" aria-label="资产配置环：外圈为实际，内圈为目标">
      <circle cx="100" cy="100" r={R} fill="none" stroke="rgba(255,255,255,0.05)" strokeWidth="14" />
      {arcs(actual, C).map((a, i) => (
        <motion.circle key={a.k} cx="100" cy="100" r={R} fill="none" stroke={CATEGORY_COLOR[a.k]} strokeWidth="14" strokeLinecap="butt"
          initial={{ strokeDasharray: `0 ${C}` }}
          animate={{ strokeDasharray: `${Math.max(0, a.len - gap * C)} ${C}` }}
          transition={{ duration: 0.9, delay: 0.1 + i * 0.06, ease: [0.22, 1, 0.36, 1] }}
          style={{ strokeDashoffset: a.offset }} />
      ))}
      {arcs(target, C_IN).map((a) => (
        <circle key={a.k} cx="100" cy="100" r={R_IN} fill="none" stroke={CATEGORY_COLOR[a.k]} strokeOpacity="0.8" strokeWidth="2.5"
          strokeDasharray={`${Math.max(0, a.len - gap * C_IN)} ${C_IN}`} style={{ strokeDashoffset: a.offset }} />
      ))}
    </svg>
  );
}

export function AllocationLegend({ weights, targets }: { weights: Record<string, number>; targets: Record<string, Category> }) {
  return (
    <ul className="flex w-full flex-col gap-3.5">
      {Object.entries(targets).map(([k, c]) => {
        const w = weights[k] ?? 0;
        const tgt = Object.values(c.tokens).reduce((a, b) => a + b, 0);
        const [lo, hi] = c.band;
        const inBand = w >= lo - 1e-9 && w <= hi + 1e-9;
        return (
          <li key={k}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="flex items-center gap-2 text-[14px] text-ink">
                <span className="h-2 w-2 rounded-full" style={{ background: CATEGORY_COLOR[k] }} />
                {CATEGORY_LABEL[k] ?? k}
              </span>
              <span className="num text-[14px]">
                <span className={inBand ? "text-ink" : "text-amber"}>{pct(w)}</span>
                <span className="text-ink-mute"> / 目标 {pct(tgt, 0)}</span>
              </span>
            </div>
            <div className="relative mt-1.5 h-1.5 rounded-full bg-white/[0.06]" title={`允许区间 ${pct(lo, 0)}–${pct(hi, 0)}`}>
              <div className="absolute inset-y-0 rounded-full bg-white/[0.12]" style={{ left: `${lo * 100}%`, width: `${(hi - lo) * 100}%` }} />
              <div className="absolute top-1/2 h-3 w-[3px] -translate-y-1/2 rounded-full transition-[left] duration-700 ease-out"
                style={{ background: CATEGORY_COLOR[k], left: `calc(${Math.min(Math.max(w, 0), 1) * 100}% - 1.5px)` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
