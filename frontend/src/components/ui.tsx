import { Info } from "@phosphor-icons/react";
import { motion } from "framer-motion";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { RiskRow } from "../api";
import { useFlash } from "../lib";

/** Page fade-in: plays once per mount. */
export function Page({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`animate-pageIn ${className}`}>{children}</div>;
}

export function DocLink({ to }: { to: string }) {
  return (
    <Link to={`/docs/${to}`} aria-label="文档" className="inline-flex text-fg-3 transition-colors hover:text-fg">
      <Info size={15} />
    </Link>
  );
}

export function SectionHead({ title, doc, right, className = "" }: { title: ReactNode; doc?: string; right?: ReactNode; className?: string }) {
  return (
    <div className={`flex items-center justify-between gap-3 border-b border-line pb-3 ${className}`}>
      <h2 className="flex items-center gap-1.5 text-[16px] font-semibold">
        {title}
        {doc && <DocLink to={doc} />}
      </h2>
      {right}
    </div>
  );
}

/** Underlined tab bar with a sliding indicator. */
export function Tabs<T extends string>({ items, value, onChange, id }:
  { items: { k: T; label: ReactNode }[]; value: T; onChange: (k: T) => void; id: string }) {
  return (
    <div className="flex gap-6 border-b border-line" role="tablist">
      {items.map((x) => (
        <button key={x.k} role="tab" aria-selected={value === x.k} onClick={() => onChange(x.k)}
          className={`relative pb-3 text-[14px] font-medium transition-colors ${value === x.k ? "text-fg" : "text-fg-2 hover:text-fg"}`}>
          {x.label}
          {value === x.k && (
            <motion.span layoutId={`tab-${id}`} className="absolute inset-x-0 -bottom-px h-[2px] rounded-full bg-fg"
              transition={{ type: "spring", stiffness: 500, damping: 38 }} />
          )}
        </button>
      ))}
    </div>
  );
}

/** Segmented control (pill). */
export function Seg<T extends string>({ items, value, onChange, id }:
  { items: { k: T; label: ReactNode }[]; value: T; onChange: (k: T) => void; id: string }) {
  return (
    <div className="inline-flex rounded-lg bg-field p-0.5">
      {items.map((x) => (
        <button key={x.k} onClick={() => onChange(x.k)}
          className={`relative rounded-md px-3 py-1 text-[13px] font-medium ${value === x.k ? "text-fg" : "text-fg-2 hover:text-fg"}`}>
          {value === x.k && <motion.span layoutId={`seg-${id}`} className="absolute inset-0 rounded-md bg-line-strong" transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
          <span className="relative">{x.label}</span>
        </button>
      ))}
    </div>
  );
}

export function ModePill({ mode }: { mode: string }) {
  return mode === "live"
    ? <span className="tag bg-down/15 text-down">实盘</span>
    : <span className="tag bg-hover text-fg-2">模拟</span>;
}

export function RunPill({ paused }: { paused: boolean }) {
  return paused
    ? <span className="tag bg-warn/15 text-warn">已暂停</span>
    : <span className="tag gap-1 bg-up/10 text-up"><span className="h-1.5 w-1.5 rounded-full bg-up" />运行中</span>;
}

export function Side({ side }: { side: string }) {
  return <span className={`font-medium ${side === "BUY" ? "text-up" : "text-down"}`}>{side === "BUY" ? "买入" : "卖出"}</span>;
}

const DECISION: Record<string, [string, string]> = {
  execute: ["已执行", "text-up"],
  approve: ["待批准", "text-warn"],
  reject: ["风控拒绝", "text-fg-3"],
  failed: ["失败", "text-down"],
};
export function Decision({ d }: { d: string }) {
  const [t, c] = DECISION[d] ?? [d, "text-fg-2"];
  return <span className={`text-[13px] ${c}`}>{t}</span>;
}

export function RiskChecks({ row }: { row: RiskRow }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
      {row.checks.map((c, i) => (
        <li key={i} title={c.detail} className={c.ok ? "text-fg-2" : "text-down"}>
          <span aria-hidden className="mr-1">{c.ok ? "✓" : "✗"}</span>{c.name}
        </li>
      ))}
    </ul>
  );
}

/** Number cell that flashes green/red when it ticks. */
export function FlashNum({ value, children, className = "" }: { value: number | undefined; children: ReactNode; className?: string }) {
  const { dir, key } = useFlash(value);
  return (
    <span key={key} className={`num -mx-1 rounded px-1 ${dir === "up" ? "animate-flashUp" : dir === "down" ? "animate-flashDown" : ""} ${className}`}>
      {children}
    </span>
  );
}

export function Change({ v, digits = 2 }: { v: number | null | undefined; digits?: number }) {
  if (v == null) return <span className="text-fg-3">—</span>;
  return <span className={`num ${v >= 0 ? "text-up" : "text-down"}`}>{v >= 0 ? "+" : ""}{(v * 100).toFixed(digits)}%</span>;
}

export function Empty({ text }: { text: string }) {
  return <p className="py-10 text-center text-[13px] text-fg-3">{text}</p>;
}

export function Spinner({ className = "" }: { className?: string }) {
  return <span className={`inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-fg-3 border-t-fg ${className}`} />;
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-hover ${className}`} />;
}

/** Allocation band: track = 0..100%, light = allowed band, tick = target, dot = actual. */
export function Band({ w, lo, hi, target, color }: { w: number; lo: number; hi: number; target: number; color: string }) {
  const inBand = w >= lo - 1e-9 && w <= hi + 1e-9;
  return (
    <div className="relative h-1 w-full min-w-[90px] rounded-full bg-line" title={`区间 ${(lo * 100).toFixed(0)}–${(hi * 100).toFixed(0)}%`}>
      <div className="absolute inset-y-0 rounded-full bg-line-strong" style={{ left: `${lo * 100}%`, width: `${(hi - lo) * 100}%` }} />
      <div className="absolute -top-1 h-3 w-px bg-fg-2" style={{ left: `${target * 100}%` }} />
      <div className={`absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-bg transition-[left] duration-700 ease-out ${inBand ? "" : "outline outline-1 outline-warn"}`}
        style={{ left: `${Math.min(Math.max(w, 0), 1) * 100}%`, background: color }} />
    </div>
  );
}
