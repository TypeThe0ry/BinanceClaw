import { type ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Category } from "../api";
import { useApi, useToast } from "../lib";
import { SettingsJump } from "./SettingsJump";

/**
 * Shared plumbing for the two settings pages: 设置 (system: connections, models, pacing, earn, devices) and
 * 配置 (how the AI invests: allocation, targets, capital, risk, futures). Both edit the same /settings document,
 * so each section saves only its own slice and new fields slot into whichever page they belong to.
 */
export interface SettingsResp {
  settings: {
    mode: "paper" | "live";
    categories: Record<string, Category>;
    limits: Record<string, number | boolean>;
    schedule: { decision_mode?: "auto" | "fixed"; cycle_minutes: number; min_gap_minutes?: number; max_gap_minutes?: number;
      max_calls_per_day?: number; move_trigger_pct?: number; approval_timeout_minutes: number; daily_report_time: string; timezone: string };
    provider: { name: string; model: string; base_url: string; timeout_seconds: number; temperature: number };
    investor_notes: string;
    live_gate_allow: string[];
    risk_preset: string;
    capital: { locked: Record<string, number>; budget_usd: number | null };
    allocation: Alloc;
    provider_fallback: { name: string; model: string; base_url: string; timeout_seconds: number; temperature: number } | null;
    model_routing?: Routing;
    stables: string[];
    futures: FuturesCfg;
    earn: { enabled: boolean; auto_all: boolean; assets: string[]; keep_liquid_usd: number; min_move_usd: number };
    notify?: NotifyCfg;
    approval_free: ApprovalFree;
    strategy: { style: Style };
    outlook: Outlook;
    questions?: Questions;
  };
  presets: Record<string, unknown>;
  candidates: string[];
  deployed_usd: number;
  secrets: Record<string, boolean>;
  providers: Record<string, { ok: boolean; message: string; default_model: string }>;
  bounds: Record<string, [number, number]>;
  confirm_phrase: string;
  model_tasks?: Record<string, string>;
  tier_models?: Record<string, Partial<Record<"fast" | "strong", string>>>;
  telegram?: boolean;
  notify_types?: Record<string, { label: string; always: boolean }>;
}
export type Tier = "fast" | "strong" | "main";
export interface Routing { enabled: boolean; tasks: Record<string, Tier>; models: Record<string, Partial<Record<"fast" | "strong", string>>> }
export interface Questions { enabled: boolean; question_timeout_minutes: number; on_timeout: "skip" | "auto" }
export const QUESTIONS_DEFAULT: Questions = { enabled: true, question_timeout_minutes: 120, on_timeout: "skip" };
export interface NotifyCfg {
  types: Record<string, boolean>; mode: "instant" | "digest"; digest_minutes: number;
  quiet: { enabled: boolean; start: string; end: string };
}
export interface ApprovalFree { mode: "off" | "always" | "window"; start: string; end: string; include_sell: boolean; include_futures: boolean }
export type Style = "short" | "medium" | "long";
export interface Outlook { market: number; assets: Record<string, number>; note: string }
export interface Alloc {
  mode: "targets" | "ai"; universe: "all" | "whitelist" | "blacklist"; enabled: string[]; blocked: string[];
  max_asset_weight: number; ai_candidates: number; auto_rebalance: boolean; rebalance_drift: number;
}

export interface FuturesCfg {
  enabled: boolean; symbols: string[]; max_leverage: number; max_position_margin_usd: number; max_total_margin_usd: number;
  max_loss_per_trade_usd: number; max_stop_distance: number; allow_short: boolean; auto_open: boolean;
  auto_open_max_margin_usd: number; max_opens_per_day: number;
}
export type SettingsDoc = SettingsResp["settings"];

export function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div data-setting className="flex min-h-[52px] flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-line py-2.5">
      <span className="text-[14px] text-fg-2">{label}</span>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

export function Num({ value, onChange, unit, min, max, step = 1 }: { value: number; onChange: (v: number) => void; unit: string; min?: number; max?: number; step?: number }) {
  return (
    <label className="flex h-9 items-center rounded-lg bg-field pr-3">
      <input type="number" className="num h-9 w-24 bg-transparent px-3 text-right text-[14px] focus:outline-none" value={Number.isFinite(value) ? value : ""}
        min={min} max={max} step={step} onChange={(e) => onChange(parseFloat(e.target.value))} />
      <span className="w-8 text-[12px] text-fg-3">{unit}</span>
    </label>
  );
}

/** Loads /settings into an editable draft; `save` PUTs one slice (the api layer handles confirm/rerun dialogs). */
export function useSettingsDraft() {
  const { data, reload } = useApi<SettingsResp>("/settings", { kinds: ["settings", "mode"] });
  const toast = useToast();
  const [d, setD] = useState<SettingsDoc | null>(null);
  useEffect(() => { if (data) setD(structuredClone(data.settings)); }, [data]);
  async function save(patch: Record<string, unknown>, msg = "已保存") {
    try { await api("/settings", { method: "PUT", json: patch }); toast(msg); reload(); } catch (e) { toast((e as Error).message, "err"); }
  }
  return { data, reload, d, setD, save, toast };
}

const PAGES = [
  { to: "/settings", label: "设置" },
  { to: "/config", label: "配置" },
];

/** Page title that doubles as the switch between 设置 and 配置, followed by the search / category bar. */
export function SettingsNav({ current }: { current: "/settings" | "/config" }) {
  return (
    <>
      <nav aria-label="设置与配置" className="flex items-baseline gap-5">
        {PAGES.map((p) => (
          <Link key={p.to} to={p.to} aria-current={p.to === current ? "page" : undefined}
            className={`text-[20px] font-semibold transition-colors ${p.to === current ? "text-fg" : "text-fg-3 hover:text-fg-2"}`}>
            {p.label}
          </Link>
        ))}
      </nav>
      <SettingsJump current={current} />
    </>
  );
}
