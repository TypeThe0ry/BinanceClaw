export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** One dangerous setting the server wants confirmed before it saves (409 from PUT /settings etc.). */
export interface DangerChange { field: string; label: string; old: unknown; new: unknown }
type Confirmer = (changes: DangerChange[]) => Promise<boolean>;
let confirmer: Confirmer | null = null;

/** Registered by <ConfirmProvider>; asked whenever the server answers 409 with changes to confirm. */
export function setConfirmer(fn: Confirmer | null) {
  confirmer = fn;
}

type RerunOfferer = (changed: string[]) => void;
let rerunOfferer: RerunOfferer | null = null;

/** Registered by <ConfirmProvider>; called when a save reports changes the AI should adapt to (`rerun`). */
export function setRerunOfferer(fn: RerunOfferer | null) {
  rerunOfferer = fn;
}

export async function api<T = unknown>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const { json, ...rest } = init ?? {};
  const res = await fetch(`/api${path}`, {
    credentials: "same-origin",
    ...rest,
    headers: { ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...(rest.headers ?? {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (res.status === 401 && path !== "/auth/login") {
    window.dispatchEvent(new Event("bclaw:unauthorized"));
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const b = await res.json();
      detail = b.detail ?? b;
    } catch {
      /* not json */
    }
    const d = detail as { confirm_token?: string; changes?: DangerChange[] } | string;
    if (res.status === 409 && typeof d === "object" && d.confirm_token && Array.isArray(d.changes)) {
      // second confirmation for dangerous settings: show the changes, then resend with the token
      if (!confirmer || !(await confirmer(d.changes))) throw new ApiError(409, "已取消，设置没有改动");
      return api<T>(path, { ...init, headers: { ...(rest.headers ?? {}), "X-Confirm-Token": d.confirm_token } });
    }
    throw new ApiError(res.status, typeof d === "string" ? d : JSON.stringify(d));
  }
  const data = await res.json();
  const rerun = (data as { rerun?: unknown } | null)?.rerun;
  if (Array.isArray(rerun) && rerun.length) rerunOfferer?.(rerun as string[]);
  return data as T;
}

export const post = <T = unknown>(path: string, json: unknown = {}) => api<T>(path, { method: "POST", json });

// ---------------------------------------------------------------- types
export interface Category { tokens: Record<string, number | null>; band: [number, number] }

export interface Overview {
  mode: "paper" | "live";
  paused: boolean;
  pause_reason: string;
  error: string | null;
  provider: { name: string; model: string };
  next_cycle_at: number;
  cycle_minutes: number;
  autopilot?: {
    decision_mode: "auto" | "fixed"; next_cycle_at: number; calls_today?: number; max_calls_per_day?: number;
    wake_move_pct?: number; next_check_minutes?: number | null; ai_asked_minutes?: number | null; streaming?: boolean;
  };
  today: { orders: number; notional: number; max_orders: number; max_notional: number };
  pending_approvals: number;
  btc_change_7d: number | null;
  anomaly: string | null;
  market: Record<string, { price: number; change_7d?: number; change_30d?: number; daily_closes_14d?: number[] }>;
  targets: Record<string, Category>;
  limits: Record<string, number | boolean>;
  total?: number;
  values?: Record<string, number>;
  balances?: Record<string, number>;
  token_weights?: Record<string, number>;
  category_weights?: Record<string, number>;
  account_total?: number;
  account_values?: Record<string, number>;
  account_balances?: Record<string, number>;
  allocation?: { mode: "targets" | "ai"; universe: "all" | "whitelist" | "blacklist"; enabled: string[]; blocked: string[];
    max_asset_weight: number; auto_rebalance: boolean; rebalance_drift: number };
  capital?: { locked: Record<string, number>; budget_usd: number | null; deployed_usd: number };
  risk_preset?: string;
  futures?: { enabled: boolean; open: number; symbols?: string[] };
  earn?: { enabled: boolean; positions: Record<string, { amount: number; apr: number | null }> };
  binance_categories?: Record<string, { primary: string; tags: string[] }>;
  approval_free?: { mode: "off" | "always" | "window"; start: string; end: string; active: boolean };
  strategy?: "short" | "medium" | "long";
}

export interface PerfAsset { qty: number; avg_cost: number | null; cost: number; value: number | null; price: number | null;
  realized: number; unrealized: number | null; unrealized_pct: number | null }
export interface Performance {
  mode: string; assets: Record<string, PerfAsset>; invested: number; open_cost: number; value: number;
  unrealized: number; realized: number; pnl: number; roi: number | null;
  benchmark: { name: string; value: number | null; pnl: number | null; roi: number | null };
  futures?: { realized: number; unrealized: number | null; trades: number; wins: number };
}

export interface PriceAlert { id: string; created: number; symbol: string; op: "below" | "above"; price: number; note: string;
  source: string; status: string; triggered_at: number | null; trigger_price: number | null }

export interface RiskCheck { name: string; ok: boolean; detail: string }
export interface RiskRow {
  index: number;
  kind?: "futures";
  side?: "BUY" | "SELL";
  op?: string;
  symbol: string;
  requested_usd?: number;
  approved_usd?: number;
  requested_margin?: number;
  margin?: number;
  leverage?: number;
  stop_loss?: number | null;
  take_profit?: number | null;
  fraction?: number;
  decision: "execute" | "approve" | "reject" | "failed";
  checks: RiskCheck[];
  reason: string;
  reason_model: string;
  invalidation: string;
  result?: string;
  approval_id?: string;
  order_id?: string;
}

export interface Plan {
  summary: string;
  market_view: string;
  confidence: number;
  actions: { side: string; symbol: string; quote_usd: number; reason: string; invalidation: string }[];
}

export interface CycleRow {
  id: string;
  ts: number;
  mode: string;
  trigger: string;
  status: "running" | "done" | "skipped" | "error";
  provider: string;
  model: string;
  summary: string;
  error: string;
  duration_ms: number;
  risk: RiskRow[];
  plan: Plan | null;
  total_usd?: number;
}

export interface CycleFull extends CycleRow {
  context: Record<string, unknown>;
  prompt: string;
  raw_output: string;
  usage: Record<string, unknown>;
  timing?: { market_ms?: number; account_ms?: number; model_ms?: number; execute_ms?: number };
}

export interface WinStat { evaluated: number; wins: number; win_rate: number | null; avg_return: number | null }
export interface TrackRecord {
  rule: string; trades: number; pending_24h: number; "24h": WinStat; "7d": WinStat;
  by_side_24h: { BUY: WinStat; SELL: WinStat };
  futures: { trades: number; wins: number; win_rate: number | null; realized: number };
  recent: { time: string; side: "BUY" | "SELL"; symbol: string; usd: number; entry: number;
    ret_24h?: number; ret_7d?: number; ret_now?: number }[];
}

export interface Approval {
  id: string;
  created: number;
  expires: number;
  cycle_id: string;
  symbol: string;
  side: string;
  quote_usd: number;
  reason: string;
  invalidation: string;
  status: "pending" | "approved" | "rejected" | "expired" | "failed";
  resolved_by: string;
  result: string;
  kind?: "spot" | "futures";
  payload?: { leverage?: number; stop_loss?: number; take_profit?: number | null; margin_usd?: number };
}

export interface FPosition {
  id: string; symbol: string; side: "LONG" | "SHORT"; qty: number; entry: number; mark: number; margin: number; leverage: number;
  stop: number; take_profit: number | null; upnl: number; roe: number | null; liq_est: number | null; opened: number;
  status: string; reason: string; invalidation: string; realized: number;
}
export interface FClosed { id: string; symbol: string; side: string; qty: number; entry: number; close_price: number | null; margin: number;
  leverage: number; realized: number; fees: number; opened: number; closed: number | null; close_reason: string }
export interface FTrade { id: string; ts: number; symbol: string; action: string; qty: number; price: number; margin: number; pnl: number;
  fee: number; status: string; via: string; mode: string }
export interface FuturesResp {
  enabled: boolean; settings: Record<string, unknown>; positions: FPosition[]; closed: FClosed[]; trades: FTrade[];
  realized: number; unrealized: number; margin_used: number; equity: number; error: string | null;
}

export interface Order {
  id: string;
  ts: number;
  mode: string;
  symbol: string;
  side: string;
  quote_usd: number;
  qty: number;
  price: number;
  fee: number;
  status: string;
  via: string;
  cycle_id: string;
}

export interface ChatMsg { id: number; ts: number; channel: string; role: "user" | "assistant"; text: string }
export interface Report { day: string; ts: number; text: string }
export interface Kline { t: number; o: number; h: number; l: number; c: number; v: number }
