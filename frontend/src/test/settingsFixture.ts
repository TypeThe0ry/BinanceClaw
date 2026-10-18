import { screen } from "@testing-library/react";
import { mockApi } from "./utils";

/** Shared /settings mocks for the 设置 and 配置 page tests. */
export const PHRASE = "我确认开启实盘";

export function settingsResp(over: Record<string, unknown> = {}) {
  return {
    settings: {
      mode: "paper",
      categories: { BTC: { tokens: { BTC: 0.4 }, band: [0.3, 0.5] } },
      limits: { max_order_usd: 500, auto_execute_max_usd: 100, max_auto_daily_usd: 300, max_orders_per_day: 10,
        max_daily_notional_usd: 2000, stable_floor: 0.2, btc_7d_drop_pause: 0.15, allow_sell: true },
      schedule: { cycle_minutes: 60, approval_timeout_minutes: 30, daily_report_time: "21:00", timezone: "Asia/Shanghai" },
      provider: { name: "claude_code", model: "", base_url: "", timeout_seconds: 120, temperature: 0.2 },
      investor_notes: "",
      live_gate_allow: [],
      risk_preset: "balanced",
      capital: { locked: {}, budget_usd: null },
      allocation: { mode: "targets", universe: "all", enabled: ["BTC", "ETH"], blocked: [], max_asset_weight: 0.4,
        ai_candidates: 30, auto_rebalance: true, rebalance_drift: 0.05 },
      provider_fallback: null,
      model_routing: { enabled: true, tasks: { decision: "strong", allocation: "strong", chat: "strong", report: "fast" }, models: {} },
      stables: ["USDT", "USDC"],
      futures: { enabled: false, symbols: ["BTCUSDT"], max_leverage: 3, max_position_margin_usd: 200, max_total_margin_usd: 500,
        max_loss_per_trade_usd: 50, max_stop_distance: 0.05, allow_short: false, auto_open: false,
        auto_open_max_margin_usd: 20, max_opens_per_day: 3 },
      earn: { enabled: true, auto_all: true, assets: ["USDT"], keep_liquid_usd: 100, min_move_usd: 10 },
      ...over,
    },
    presets: {},
    candidates: ["BTC", "ETH", "SOL"],
    deployed_usd: 1234,
    secrets: { binance_api_key: true, telegram_bot_token: false },
    providers: { claude_code: { ok: true, message: "", default_model: "claude" }, openai: { ok: false, message: "缺少 API key", default_model: "gpt" } },
    bounds: { stable_floor: [0.05, 0.9], max_leverage: [1, 10] },
    confirm_phrase: PHRASE,
    model_tasks: { decision: "投资决策", allocation: "配置提案", chat: "对话", report: "日报点评" },
    tier_models: { claude_code: { fast: "claude-haiku-5-5", strong: "claude-opus-5-5" } },
  };
}

export const liveCheck = (ok: boolean) => ({
  ok, blockers: ok ? [] : ["API key 允许提现"],
  rows: [{ label: "提现权限", field: "withdraw", expected: false, actual: !ok, ok, waived: false, waivable: false }],
});

export function mockSettings(opts: { ok?: boolean; settings?: Record<string, unknown>; extra?: Record<string, unknown> } = {}) {
  return mockApi({
    "GET /settings": settingsResp(opts.settings),
    "GET /live/check": liveCheck(opts.ok ?? true),
    "GET /devices": [],
    "GET /earn": { enabled: false, positions: {}, products: {} },
    "PUT /settings": { ok: true },
    ...opts.extra,
  });
}

export const section = (title: string) => screen.getByText(title, { selector: "h2" }).closest("section") as HTMLElement;
export const rowOf = (label: string) => screen.getByText(label).parentElement as HTMLElement;
