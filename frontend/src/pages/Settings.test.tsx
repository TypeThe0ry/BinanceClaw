import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi, renderPage } from "../test/utils";
import Settings from "./Settings";

const PHRASE = "我确认开启实盘";

function settingsResp(over: Record<string, unknown> = {}) {
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
      allocation: { mode: "targets", enabled: ["BTC", "ETH"], max_asset_weight: 0.4 },
      provider_fallback: null,
      stables: ["USDT", "USDC"],
      futures: { enabled: false, symbols: ["BTCUSDT"], max_leverage: 3, max_position_margin_usd: 200, max_total_margin_usd: 500,
        max_loss_per_trade_usd: 50, max_stop_distance: 0.05, allow_short: false, auto_open: false,
        auto_open_max_margin_usd: 20, max_opens_per_day: 3 },
      earn: { enabled: false, assets: ["USDT"], keep_liquid_usd: 100, min_move_usd: 10 },
      ...over,
    },
    presets: {},
    candidates: ["BTC", "ETH", "SOL"],
    deployed_usd: 1234,
    secrets: { binance_api_key: true, telegram_bot_token: false },
    providers: { claude_code: { ok: true, message: "", default_model: "claude" }, openai: { ok: false, message: "缺少 API key", default_model: "gpt" } },
    bounds: { stable_floor: [0.05, 0.9], max_leverage: [1, 10] },
    confirm_phrase: PHRASE,
  };
}

const liveCheck = (ok: boolean) => ({
  ok, blockers: ok ? [] : ["API key 允许提现"],
  rows: [{ label: "提现权限", field: "withdraw", expected: false, actual: !ok, ok, waived: false, waivable: false }],
});

function mockSettings(opts: { ok?: boolean; settings?: Record<string, unknown>; extra?: Record<string, unknown> } = {}) {
  return mockApi({
    "GET /settings": settingsResp(opts.settings),
    "GET /live/check": liveCheck(opts.ok ?? true),
    "GET /devices": [],
    "GET /earn": { enabled: false, positions: {}, products: {} },
    "PUT /settings": { ok: true },
    ...opts.extra,
  });
}

const section = (title: string) => screen.getByText(title, { selector: "h2" }).closest("section") as HTMLElement;
const rowOf = (label: string) => screen.getByText(label).parentElement as HTMLElement;

describe("Settings page", () => {
  it("only enables going live once the checks pass and the confirm phrase matches", async () => {
    const api = mockSettings({ extra: { "POST /mode": { ok: true, message: "已切换到实盘" } } });
    renderPage(<Settings />);
    const goLive = await screen.findByRole("button", { name: "开启实盘" });
    expect(screen.getByText("模拟盘")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("提现权限")).toBeInTheDocument());
    expect(goLive).toBeDisabled();

    const input = screen.getByPlaceholderText(PHRASE);
    await userEvent.type(input, "我确认");
    expect(goLive).toBeDisabled();
    await userEvent.type(input, "开启实盘");
    expect(goLive).toBeEnabled();

    await userEvent.click(goLive);
    await waitFor(() => expect(api.find("POST", "/mode")).toEqual([{ method: "POST", path: "/mode", body: { mode: "live", confirm: PHRASE } }]));
    expect(await screen.findByRole("status")).toHaveTextContent("已切换到实盘");
    expect(input).toHaveValue("");
  });

  it("keeps live mode locked while an API-key check fails", async () => {
    mockSettings({ ok: false });
    renderPage(<Settings />);
    await userEvent.type(await screen.findByPlaceholderText(PHRASE), PHRASE);
    expect(screen.getByRole("button", { name: "开启实盘" })).toBeDisabled();
    expect(within(rowOf("提现权限")).getByText("开启")).toHaveClass("text-down");
  });

  it("offers switching back to paper when live", async () => {
    const api = mockSettings({ settings: { mode: "live" }, extra: { "POST /mode": { ok: true, message: "已切回模拟盘" } } });
    renderPage(<Settings />);
    expect(await screen.findByText("实盘")).toHaveClass("text-down");
    expect(screen.queryByRole("button", { name: "开启实盘" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "切回模拟盘" }));
    await waitFor(() => expect(api.find("POST", "/mode")[0]?.body).toEqual({ mode: "paper", confirm: "" }));
  });

  it("saves risk limits, converting percentages back to fractions", async () => {
    const api = mockSettings();
    renderPage(<Settings />);
    await screen.findByText("风控", { selector: "h2" });
    const floor = within(rowOf("稳定币下限")).getByRole("spinbutton");
    expect(floor).toHaveValue(20);
    await userEvent.clear(floor);
    await userEvent.type(floor, "25");
    await userEvent.click(within(rowOf("允许卖出")).getByRole("checkbox"));
    await userEvent.click(within(section("风控")).getByRole("button", { name: "保存" }));

    await waitFor(() => expect(api.find("PUT", "/settings")).toHaveLength(1));
    const { limits } = api.find("PUT", "/settings")[0].body as { limits: Record<string, number | boolean> };
    expect(limits.stable_floor).toBeCloseTo(0.25);
    expect(limits.allow_sell).toBe(false);
    expect(limits.max_order_usd).toBe(500);
    expect(await screen.findByRole("status")).toHaveTextContent("已保存");
  });

  it("saves futures settings", async () => {
    const api = mockSettings();
    renderPage(<Settings />);
    await screen.findByText("合约", { selector: "h2" });
    await userEvent.click(within(rowOf("允许做空")).getByRole("checkbox"));
    const lev = within(rowOf("最大杠杆")).getByRole("spinbutton");
    expect(lev).toHaveAttribute("max", "10");
    await userEvent.clear(lev);
    await userEvent.type(lev, "5");
    await userEvent.click(within(section("合约")).getByRole("button", { name: "保存" }));

    await waitFor(() => expect(api.find("PUT", "/settings")).toHaveLength(1));
    const { futures } = api.find("PUT", "/settings")[0].body as { futures: Record<string, unknown> };
    expect(futures).toMatchObject({ allow_short: true, max_leverage: 5, symbols: ["BTCUSDT"] });
  });

  it("adds a locked asset and saves capital", async () => {
    const api = mockSettings();
    renderPage(<Settings />);
    await screen.findByText("资金管理", { selector: "h2" });
    await userEvent.type(screen.getByPlaceholderText("BTC"), "bnb");
    await userEvent.type(screen.getByPlaceholderText("数量"), "1.5");
    await userEvent.click(screen.getByRole("button", { name: "添加" }));
    expect(screen.getByText("BNB")).toBeInTheDocument();
    await userEvent.click(within(section("资金管理")).getByRole("button", { name: "保存" }));

    await waitFor(() => expect(api.find("PUT", "/settings")[0]?.body).toEqual({ capital: { locked: { BNB: 1.5 }, budget_usd: null } }));
  });

  it("applies a risk preset", async () => {
    const api = mockSettings({ extra: { "POST /risk-preset": { ok: true } } });
    renderPage(<Settings />);
    await userEvent.click(await screen.findByRole("button", { name: "积极" }));
    await waitFor(() => expect(api.find("POST", "/risk-preset")[0]?.body).toEqual({ name: "aggressive" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已套用：积极");
  });

  it("shows a toast when saving fails", async () => {
    mockApi({
      "GET /settings": settingsResp(), "GET /live/check": liveCheck(true), "GET /devices": [],
      "GET /earn": { enabled: false, positions: {}, products: {} },
    });
    renderPage(<Settings />);
    await screen.findByText("节奏", { selector: "h2" });
    await userEvent.click(within(section("节奏")).getByRole("button", { name: "保存" }));
    const toast = await screen.findByRole("status");
    expect(toast).toHaveTextContent("no mock for PUT /settings");
    expect(toast).toHaveClass("text-down");
  });

  it("reports which connections are configured", async () => {
    mockSettings();
    renderPage(<Settings />);
    await screen.findByText("连接", { selector: "h2" });
    expect(within(rowOf("币安 API")).getByText("已配置")).toBeInTheDocument();
    expect(within(rowOf("Telegram")).getByText("未配置")).toBeInTheDocument();
  });
});
