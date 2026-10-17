import { animate, motion, useMotionValue, useTransform } from "framer-motion";
import { useEffect, type ReactNode } from "react";
import type { RiskRow } from "../api";

export function Card({ children, className = "", strong = false }: { children: ReactNode; className?: string; strong?: boolean }) {
  return <section className={`${strong ? "glass-strong" : "glass"} ${className}`}>{children}</section>;
}

export function PageTitle({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="font-display text-[34px] leading-[1.15] text-ink md:text-[40px]">{title}</h1>
        {sub && <p className="mt-1.5 text-[14px] text-ink-mute">{sub}</p>}
      </div>
      {right}
    </header>
  );
}

export function ModeBadge({ mode }: { mode: "paper" | "live" | string }) {
  return mode === "live" ? (
    <span className="chip border border-live/50 bg-live/15 text-[#FF8A8A]">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-live" />实盘
    </span>
  ) : (
    <span className="chip border border-white/10 bg-white/[0.05] text-ink-soft">
      <span className="h-1.5 w-1.5 rounded-full bg-ink-mute" />模拟盘
    </span>
  );
}

export function RunState({ paused, reason }: { paused: boolean; reason?: string }) {
  return paused ? (
    <span className="chip border border-amber/40 bg-amber/10 text-amber" title={reason}>
      <span className="h-1.5 w-1.5 rounded-full bg-amber" />已暂停
    </span>
  ) : (
    <span className="chip border border-mint/30 bg-mint/10 text-mint">
      <span className="relative flex h-1.5 w-1.5">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-mint opacity-60" />
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-mint" />
      </span>
      运行中
    </span>
  );
}

const DECISION: Record<string, { label: string; cls: string }> = {
  execute: { label: "已自动执行", cls: "border-mint/30 bg-mint/10 text-mint" },
  approve: { label: "待你批准", cls: "border-amber/40 bg-amber/10 text-amber" },
  reject: { label: "风控拦截", cls: "border-rose/35 bg-rose/10 text-rose" },
  failed: { label: "执行失败", cls: "border-rose/35 bg-rose/10 text-rose" },
};

export function DecisionChip({ d }: { d: string }) {
  const x = DECISION[d] ?? { label: d, cls: "border-white/10 text-ink-soft" };
  return <span className={`chip border ${x.cls}`}>{x.label}</span>;
}

export function SideTag({ side }: { side: string }) {
  return (
    <span className={`rounded-md px-1.5 py-0.5 text-[12px] font-semibold tracking-wider ${side === "BUY" ? "bg-mint/15 text-mint" : "bg-rose/15 text-rose"}`}>
      {side === "BUY" ? "买入" : "卖出"}
    </span>
  );
}

export function RiskChecks({ row }: { row: RiskRow }) {
  return (
    <ul className="flex flex-wrap gap-1.5">
      {row.checks.map((c, i) => (
        <li key={i} title={c.detail}
          className={`chip border text-[12px] ${c.ok ? "border-white/10 bg-white/[0.04] text-ink-soft" : "border-rose/35 bg-rose/10 text-rose"}`}>
          <span aria-hidden>{c.ok ? "✓" : "✗"}</span>
          {c.name}
        </li>
      ))}
    </ul>
  );
}

/** Number that rolls to its new value (primary feedback for balances). */
export function Rolling({ value, format }: { value: number; format: (v: number) => string }) {
  const mv = useMotionValue(value);
  const text = useTransform(mv, (v) => format(v));
  useEffect(() => {
    const c = animate(mv, value, { duration: 0.9, ease: [0.22, 1, 0.36, 1] });
    return c.stop;
  }, [value, mv]);
  return <motion.span className="num">{text}</motion.span>;
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-14 text-center">
      <svg width="44" height="44" viewBox="0 0 44 44" aria-hidden className="text-ink-faint">
        <path d="M11 33c3-9 7-16 10-19M19 34c2-8 6-14 9-17M27 34c1.5-6 4-10 6.5-12.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" fill="none" />
      </svg>
      <p className="text-[15px] text-ink-soft">{title}</p>
      {hint && <p className="max-w-sm text-[13px] text-ink-mute">{hint}</p>}
    </div>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return <span className={`inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/20 border-t-mint ${className}`} />;
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-xl bg-white/[0.04] ${className}`} />;
}
