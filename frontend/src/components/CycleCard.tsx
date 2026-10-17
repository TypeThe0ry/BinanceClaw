import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import type { CycleRow } from "../api";
import { ago, token, usd } from "../lib";
import { DecisionChip, RiskChecks, SideTag } from "./ui";

const STATUS: Record<string, string> = { done: "", skipped: "跳过", error: "失败", running: "思考中" };

export function CycleCard({ c, compact = false }: { c: CycleRow; compact?: boolean }) {
  return (
    <motion.article layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="glass p-5">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[13px] text-ink-mute">
          <span className={`h-1.5 w-1.5 rounded-full ${c.status === "error" ? "bg-rose" : c.status === "skipped" ? "bg-amber" : c.status === "running" ? "animate-pulse bg-mint" : "bg-mint/70"}`} />
          <span>{ago(c.ts)}</span>
          <span className="text-ink-faint">·</span>
          <span>{c.model || c.provider}</span>
          {c.trigger === "manual" && <span className="text-ink-faint">· 手动</span>}
          {STATUS[c.status] && <span className={c.status === "error" ? "text-rose" : "text-amber"}>· {STATUS[c.status]}</span>}
        </div>
        <Link to={`/decisions/${c.id}`} className="text-[13px] text-ink-mute transition hover:text-mint">完整思考过程 →</Link>
      </header>
      <p className={`mt-3 text-[15px] leading-relaxed text-ink ${compact ? "line-clamp-3" : ""}`}>
        {c.summary || c.error || "…"}
      </p>
      {c.risk?.length > 0 && (
        <ul className="mt-4 flex flex-col gap-3">
          {c.risk.map((r) => (
            <li key={r.index} className="rounded-xl border border-white/[0.06] bg-black/15 p-3.5">
              <div className="flex flex-wrap items-center gap-2.5">
                <SideTag side={r.side} />
                <span className="font-semibold">{token(r.symbol)}</span>
                <span className="num text-ink-soft">
                  {usd(r.approved_usd || r.requested_usd, 0)}
                  {r.approved_usd > 0 && r.approved_usd < r.requested_usd && (
                    <span className="text-ink-mute">（提议 {usd(r.requested_usd, 0)}，已截断）</span>
                  )}
                </span>
                <span className="ml-auto"><DecisionChip d={r.decision} /></span>
              </div>
              {!compact && (
                <>
                  <p className="mt-2.5 text-[13.5px] leading-relaxed text-ink-soft">{r.reason_model}</p>
                  <div className="mt-2.5"><RiskChecks row={r} /></div>
                  {r.result && <p className="mt-2 text-[13px] text-ink-mute">{r.result}</p>}
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </motion.article>
  );
}
