import {
  ChartLineUp, ChatCircleDots, Gauge, GearSix, Newspaper, Sparkle, Stamp, Stop,
} from "@phosphor-icons/react";
import { motion } from "framer-motion";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { post, type Overview } from "../api";
import { useApi, useLive, useToast } from "../lib";
import { ModeBadge, RunState } from "./ui";

const NAV = [
  { to: "/", label: "总览", icon: Gauge },
  { to: "/decisions", label: "AI 决策", icon: Sparkle },
  { to: "/approvals", label: "审批", icon: Stamp },
  { to: "/trades", label: "交易", icon: ChartLineUp },
  { to: "/chat", label: "对话", icon: ChatCircleDots },
  { to: "/reports", label: "日报", icon: Newspaper },
  { to: "/settings", label: "设置", icon: GearSix },
];

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <svg width="28" height="28" viewBox="0 0 64 64" aria-hidden>
        <rect width="64" height="64" rx="16" fill="#0E1A2E" />
        <path d="M18 46c4-14 10-24 14-28M28 48c3-12 8-21 12-25M38 48c2-9 6-16 9-19" stroke="#5EF2C4" strokeWidth="4" strokeLinecap="round" fill="none" />
      </svg>
      <span className="font-display text-[22px] leading-none tracking-tight">BinanceClaw</span>
    </div>
  );
}

export function Layout() {
  const { data: ov, reload } = useApi<Overview>("/overview", { poll: 30000, kinds: ["paused", "resumed", "mode", "approval", "approval_created", "order", "cycle", "settings"] });
  const { connected } = useLive();
  const toast = useToast();
  const loc = useLocation();
  const current = NAV.find((n) => (n.to === "/" ? loc.pathname === "/" : loc.pathname.startsWith(n.to)))?.to ?? "/";

  async function kill() {
    if (!window.confirm("紧急停止：暂停自动执行，并作废所有待审批订单。确定吗？")) return;
    await post("/control/kill");
    toast("已紧急停止");
    reload();
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-[1440px]">
      {/* desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-[232px] shrink-0 flex-col px-5 py-7 md:flex">
        <Logo />
        <nav className="mt-10 flex flex-col gap-1">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"}
              className="relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-[14.5px] text-ink-soft transition-colors hover:text-ink">
              {current === n.to && (
                <motion.span layoutId="nav-pill" className="absolute inset-0 rounded-xl border border-white/[0.08] bg-white/[0.06]"
                  transition={{ type: "spring", stiffness: 380, damping: 32 }} />
              )}
              <n.icon size={19} weight="light" className="relative" />
              <span className={`relative ${current === n.to ? "text-ink" : ""}`}>{n.label}</span>
              {n.to === "/approvals" && !!ov?.pending_approvals && (
                <span className="relative ml-auto rounded-full bg-amber px-1.5 text-[11.5px] font-semibold leading-5 text-[#2a1c00]">{ov.pending_approvals}</span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto flex flex-col gap-3">
          <div className="flex items-center gap-2 text-[12.5px] text-ink-mute">
            <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-mint" : "bg-ink-faint"}`} />
            {connected ? "实时连接" : "正在重连…"}
          </div>
          <button onClick={kill} className="btn-danger w-full justify-start">
            <Stop size={18} weight="fill" />紧急停止
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center justify-between gap-3 border-b border-white/[0.05] bg-abyss/70 px-4 py-3 backdrop-blur-xl md:border-none md:bg-transparent md:px-8 md:pt-7 md:backdrop-blur-0">
          <div className="md:hidden"><Logo /></div>
          <div className="ml-auto flex items-center gap-2">
            {ov && <ModeBadge mode={ov.mode} />}
            {ov && <RunState paused={ov.paused} reason={ov.pause_reason} />}
            <button onClick={kill} className="btn-danger px-2.5 py-1.5 md:hidden" aria-label="紧急停止"><Stop size={16} weight="fill" /></button>
          </div>
        </header>
        <main className="flex-1 px-4 pb-28 pt-4 md:px-8 md:pb-12">
          <Outlet />
        </main>
      </div>

      {/* mobile tab bar */}
      <nav className="fixed inset-x-0 bottom-0 z-40 flex justify-around border-t border-white/[0.06] bg-[#070d19]/90 px-1 pb-[max(env(safe-area-inset-bottom),8px)] pt-1.5 backdrop-blur-xl md:hidden">
        {NAV.filter((n) => n.to !== "/reports").map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === "/"} className="relative flex flex-1 flex-col items-center gap-0.5 py-1.5 text-[11px] text-ink-mute">
            {current === n.to && <motion.span layoutId="tab-pill" className="absolute inset-x-2 inset-y-0 rounded-xl bg-white/[0.06]" />}
            <n.icon size={21} weight={current === n.to ? "regular" : "light"} className={`relative ${current === n.to ? "text-mint" : ""}`} />
            <span className={`relative ${current === n.to ? "text-ink" : ""}`}>{n.label}</span>
            {n.to === "/approvals" && !!ov?.pending_approvals && <span className="absolute right-[22%] top-1 h-2 w-2 rounded-full bg-amber" />}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
