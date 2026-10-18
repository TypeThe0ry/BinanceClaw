import {
  BookOpen, ChartLineUp, ChatCircle, DotsThreeOutline, GearSix, Lightning, Newspaper, Sliders, Sparkle, Stamp, Stop, Wallet,
} from "@phosphor-icons/react";
import { motion } from "framer-motion";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { post, type Overview } from "../api";
import { fmtPrice, token, useApi, useLive, useTicker, useToast } from "../lib";
import { Change, FlashNum, ModePill, RunPill } from "./ui";

const MAIN = [
  { to: "/", label: "资产", icon: Wallet },
  { to: "/trade", label: "现货", icon: ChartLineUp },
  { to: "/futures", label: "合约", icon: Lightning },
  { to: "/ai", label: "AI", icon: Sparkle },
  { to: "/approvals", label: "审批", icon: Stamp },
  { to: "/chat", label: "对话", icon: ChatCircle },
  { to: "/reports", label: "日报", icon: Newspaper },
];
const MOBILE = [
  { to: "/", label: "资产", icon: Wallet },
  { to: "/trade", label: "现货", icon: ChartLineUp },
  { to: "/futures", label: "合约", icon: Lightning },
  { to: "/approvals", label: "审批", icon: Stamp },
  { to: "/more", label: "更多", icon: DotsThreeOutline },
];

function Logo() {
  return (
    <Link to="/" className="flex items-center gap-2">
      <svg width="22" height="22" viewBox="0 0 64 64" aria-hidden>
        <path d="M18 46c4-14 10-24 14-28M28 48c3-12 8-21 12-25M38 48c2-9 6-16 9-19" stroke="currentColor" strokeWidth="5" strokeLinecap="round" fill="none" />
      </svg>
      <span className="text-[16px] font-semibold tracking-tight">BinanceClaw</span>
    </Link>
  );
}

function TickerBar() {
  const t = useTicker();
  const nav = useNavigate();
  return (
    <div className="no-scrollbar flex h-9 items-center gap-6 overflow-x-auto border-b border-line px-4 text-[13px] md:px-6">
      {Object.entries(t).map(([sym, x]) => (
        <button key={sym} onClick={() => nav(`/trade?s=${sym}`)} className="flex shrink-0 items-center gap-2 hover:opacity-80">
          <span className="text-fg-2">{token(sym)}</span>
          <FlashNum value={x.price}>{fmtPrice(x.price)}</FlashNum>
          <Change v={x.change_pct} />
        </button>
      ))}
    </div>
  );
}

export function Layout() {
  const { data: ov, reload } = useApi<Overview>("/overview", { poll: 30000, kinds: ["paused", "resumed", "mode", "approval", "approval_created", "order", "cycle", "settings", "question", "question_created"] });
  const waiting = (ov?.pending_approvals ?? 0) + (ov?.pending_questions ?? 0);
  const { connected } = useLive();
  const toast = useToast();
  const loc = useLocation();
  const active = (to: string) => (to === "/" ? loc.pathname === "/" : loc.pathname.startsWith(to));

  async function kill() {
    if (!window.confirm("紧急停止：暂停自动执行并作废所有待审批？")) return;
    await post("/control/kill");
    toast("已紧急停止");
    reload();
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 bg-bg/95 backdrop-blur">
        <div className="flex h-14 items-center gap-8 border-b border-line px-4 md:px-6">
          <Logo />
          <nav className="hidden h-full items-center gap-6 md:flex">
            {MAIN.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === "/"}
                className={`relative flex h-full items-center text-[14px] font-medium transition-colors ${active(n.to) ? "text-fg" : "text-fg-2 hover:text-fg"}`}>
                {n.label}
                {n.to === "/approvals" && !!waiting && (
                  <span className="num ml-1 rounded bg-warn px-1 text-[11px] font-semibold leading-4 text-bg">{waiting}</span>
                )}
                {active(n.to) && <motion.span layoutId="nav-underline" className="absolute inset-x-0 bottom-0 h-[2px] bg-fg" />}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2.5">
            <span className={`hidden h-1.5 w-1.5 rounded-full md:block ${connected ? "bg-up" : "bg-fg-3"}`} title={connected ? "实时" : "重连中"} />
            {ov && <ModePill mode={ov.mode} />}
            {ov && <RunPill paused={ov.paused} />}
            {ov?.approval_free?.active && (
              <Link to="/config" className="tag bg-warn/15 text-warn" title="本来要你批准的交易会在风控限额内直接执行">免审批中</Link>
            )}
            <Link to="/docs" aria-label="文档" className={`hidden rounded-md p-1.5 md:block ${active("/docs") ? "text-fg" : "text-fg-2 hover:text-fg"}`}><BookOpen size={18} /></Link>
            <Link to="/config" aria-label="配置" className={`hidden rounded-md p-1.5 md:block ${active("/config") ? "text-fg" : "text-fg-2 hover:text-fg"}`}><Sliders size={18} /></Link>
            <Link to="/settings" aria-label="设置" className={`hidden rounded-md p-1.5 md:block ${active("/settings") ? "text-fg" : "text-fg-2 hover:text-fg"}`}><GearSix size={18} /></Link>
            <button onClick={kill} aria-label="紧急停止" title="紧急停止"
              className="flex h-7 w-7 items-center justify-center rounded-md bg-down/15 text-down transition-colors hover:bg-down/25 active:scale-95">
              <Stop size={14} weight="fill" />
            </button>
          </div>
        </div>
        <TickerBar />
      </header>

      <main className="mx-auto w-full max-w-[1600px] flex-1 px-4 pb-24 pt-6 md:px-6 md:pb-12">
        <Outlet />
      </main>

      <nav className="fixed inset-x-0 bottom-0 z-40 flex border-t border-line bg-bg pb-[max(env(safe-area-inset-bottom),6px)] pt-1.5 md:hidden">
        {MOBILE.map((n) => {
          const on = n.to === "/more" ? ["/more", "/ai", "/chat", "/reports", "/config", "/settings", "/docs"].some((p) => loc.pathname.startsWith(p)) : active(n.to);
          return (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} className={`relative flex flex-1 flex-col items-center gap-0.5 py-1 text-[11px] ${on ? "text-fg" : "text-fg-3"}`}>
              <n.icon size={22} weight={on ? "fill" : "regular"} />
              {n.label}
              {n.to === "/approvals" && !!waiting && <span className="absolute right-[26%] top-0.5 h-2 w-2 rounded-full bg-warn" />}
            </NavLink>
          );
        })}
      </nav>
    </div>
  );
}

export function More() {
  const items = [
    { to: "/ai", label: "AI 决策", icon: Sparkle },
    { to: "/chat", label: "对话", icon: ChatCircle },
    { to: "/reports", label: "日报", icon: Newspaper },
    { to: "/config", label: "配置", icon: Sliders },
    { to: "/settings", label: "设置", icon: GearSix },
    { to: "/docs", label: "文档", icon: BookOpen },
  ];
  return (
    <div className="divide-y divide-line">
      {items.map((x) => (
        <Link key={x.to} to={x.to} className="flex items-center gap-3 py-4 text-[15px] active:bg-hover">
          <x.icon size={20} className="text-fg-2" />{x.label}<span className="ml-auto text-fg-3">›</span>
        </Link>
      ))}
    </div>
  );
}
