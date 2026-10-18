import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi, renderPage, Status } from "../test/utils";
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
    await waitFor(() => expect(api.find("POST", "/mode")).toMatchObject([{ method: "POST", path: "/mode", body: { mode: "live", confirm: PHRASE } }]));
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

  it("defaults to autonomous decisions and saves their bounds, or a fixed interval", async () => {
    const api = mockSettings();
    renderPage(<Settings />);
    await screen.findByText("节奏", { selector: "h2" });
    const pace = section("节奏");
    expect(within(pace).queryByText("决策间隔")).not.toBeInTheDocument();
    const calls = within(rowOf("每日模型调用上限")).getByRole("spinbutton");
    expect(calls).toHaveValue(48);
    await userEvent.clear(calls);
    await userEvent.type(calls, "20");
    const move = within(rowOf("默认波动唤醒阈值")).getByRole("spinbutton");
    await userEvent.clear(move);
    await userEvent.type(move, "1.5");
    await userEvent.click(within(pace).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.find("PUT", "/settings")).toHaveLength(1));
    const { schedule } = api.find("PUT", "/settings")[0].body as { schedule: Record<string, unknown> };
    expect(schedule).toMatchObject({ max_calls_per_day: 20, cycle_minutes: 60 });
    expect(schedule.move_trigger_pct as number).toBeCloseTo(0.015);

    await userEvent.click(within(pace).getByRole("button", { name: "固定间隔" }));
    expect(within(pace).getByText("决策间隔")).toBeInTheDocument();
    expect(within(pace).queryByText("每日模型调用上限")).not.toBeInTheDocument();
    await userEvent.click(within(pace).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.find("PUT", "/settings")).toHaveLength(2));
    expect((api.find("PUT", "/settings")[1].body as { schedule: Record<string, unknown> }).schedule.decision_mode).toBe("fixed");
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

  it("asks for a second confirmation before a dangerous change and resends with the token", async () => {
    const change = { field: "limits.allow_sell", label: "允许卖出", old: true, new: false };
    const api = mockSettings({ extra: {
      "PUT /settings": (_b: unknown, h: Record<string, string>) => h["X-Confirm-Token"] === "tok"
        ? { ok: true } : new Status(409, { detail: { message: "需要确认", changes: [change], confirm_token: "tok" } }),
    } });
    renderPage(<Settings />);
    await screen.findByText("风控", { selector: "h2" });
    await userEvent.click(within(rowOf("允许卖出")).getByRole("checkbox"));
    await userEvent.click(within(section("风控")).getByRole("button", { name: "保存" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("允许卖出")).toBeInTheDocument();
    expect(within(dialog).getByText("开启")).toHaveClass("line-through");
    expect(within(dialog).getByText("关闭")).toBeInTheDocument();
    expect(api.find("PUT", "/settings")).toHaveLength(1);
    await userEvent.click(within(dialog).getByRole("button", { name: "确认修改" }));

    await waitFor(() => expect(api.find("PUT", "/settings")).toHaveLength(2));
    expect(api.find("PUT", "/settings")[1].headers["X-Confirm-Token"]).toBe("tok");
    expect(api.find("PUT", "/settings")[1].body).toEqual(api.find("PUT", "/settings")[0].body);
    expect(await screen.findByRole("status")).toHaveTextContent("已保存");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does nothing when the dangerous change is cancelled", async () => {
    const change = { field: "earn.enabled", label: "理财自动申购", old: true, new: false };
    const api = mockSettings({ extra: {
      "PUT /settings": new Status(409, { detail: { message: "需要确认", changes: [change], confirm_token: "tok" } }),
    } });
    renderPage(<Settings />);
    await screen.findByText("理财", { selector: "h2" });
    await userEvent.click(screen.getByRole("checkbox", { name: "闲置资产自动申购活期" }));
    await userEvent.click(within(section("理财")).getByRole("button", { name: "保存" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "取消" }));
    const toast = await screen.findByRole("status");
    expect(toast).toHaveTextContent("已取消，设置没有改动");
    expect(api.find("PUT", "/settings")).toHaveLength(1);
  });

  it("subscribes everything by default and offers stablecoins only when that is off", async () => {
    const api = mockSettings();
    renderPage(<Settings />);
    await screen.findByText("理财", { selector: "h2" });
    expect(screen.getByRole("checkbox", { name: "闲置资产自动申购活期" })).toBeChecked();
    const all = screen.getByRole("checkbox", { name: "申购全部可申购的产品" });
    expect(all).toBeChecked();
    expect(screen.queryByText("只申购这些稳定币")).not.toBeInTheDocument();
    await userEvent.click(all);
    expect(screen.getByText("只申购这些稳定币")).toBeInTheDocument();
    await userEvent.click(within(section("理财")).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.find("PUT", "/settings")[0]?.body).toMatchObject({ earn: { enabled: true, auto_all: false } }));
  });

  it("manual allocation keeps the portfolio at its targets with a drift threshold", async () => {
    const api = mockSettings();
    renderPage(<Settings />);
    await screen.findByText("资产与分配", { selector: "h2" });
    expect(screen.getByRole("checkbox", { name: "自动再平衡" })).toBeChecked();
    const thr = within(rowOf("偏离阈值")).getByRole("spinbutton");
    expect(thr).toHaveValue(5);
    await userEvent.clear(thr);
    await userEvent.type(thr, "8");
    await userEvent.click(within(section("资产与分配")).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.find("PUT", "/settings")).toHaveLength(1));
    const { allocation } = api.find("PUT", "/settings")[0].body as { allocation: Record<string, unknown> };
    expect(allocation).toMatchObject({ mode: "targets", auto_rebalance: true });
    expect(allocation.rebalance_drift as number).toBeCloseTo(0.08);
  });

  it("AI allocation covers every asset by default and can switch to a blacklist", async () => {
    const api = mockSettings({ extra: { "GET /markets?q=&limit=40": [
      { token: "DOGE", symbol: "DOGEUSDT", price: 0.1, change_pct: 0, quote_volume: 1, held: false, enabled: false },
    ], "GET /categories": [] } });
    renderPage(<Settings />);
    await screen.findByText("资产与分配", { selector: "h2" });
    await userEvent.click(screen.getByRole("button", { name: "AI 自主分配" }));
    expect(screen.queryByRole("checkbox", { name: "自动再平衡" })).not.toBeInTheDocument();
    expect(screen.getByText(/币安全部现货资产都可以交易/)).toBeInTheDocument();
    expect(screen.queryByText("白名单", { selector: "p" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "黑名单" }));
    expect(screen.getByText("还没有，搜索添加")).toBeInTheDocument();
    await userEvent.click(screen.getByPlaceholderText("搜索要排除的资产"));
    await userEvent.click(await screen.findByText("DOGE"));
    await userEvent.click(within(section("资产与分配")).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.find("PUT", "/settings")).toHaveLength(1));
    expect(api.find("PUT", "/settings")[0].body).toMatchObject({ allocation: { mode: "ai", universe: "blacklist", blocked: ["DOGE"] } });
  });
  it("offers a new decision round after a change the AI should adapt to", async () => {
    const api = mockSettings({ extra: {
      "PUT /settings": { rerun: ["风控限额"] },
      "POST /cycles/run": { ok: true },
    } });
    renderPage(<Settings />);
    await screen.findByText("风控", { selector: "h2" });
    await userEvent.click(within(section("风控")).getByRole("button", { name: "保存" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("风控限额");
    await userEvent.click(within(dialog).getByRole("button", { name: "立即重跑" }));
    await waitFor(() => expect(api.find("POST", "/cycles/run")).toHaveLength(1));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not offer a round when nothing decision-relevant changed", async () => {
    mockSettings({ extra: { "PUT /settings": { rerun: [] } } });
    renderPage(<Settings />);
    await screen.findByText("风控", { selector: "h2" });
    await userEvent.click(within(section("风控")).getByRole("button", { name: "保存" }));
    await screen.findByText("已保存");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("can put the new round off", async () => {
    const api = mockSettings({ extra: { "PUT /settings": { rerun: ["投资备注"] } } });
    renderPage(<Settings />);
    await screen.findByText("风控", { selector: "h2" });
    await userEvent.click(within(section("风控")).getByRole("button", { name: "保存" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "稍后" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.find("POST", "/cycles/run")).toHaveLength(0);
  });
  it("maps each task to a model tier and saves the routing with the provider", async () => {
    const api = mockSettings();
    renderPage(<Settings />);
    await screen.findByText("AI 模型", { selector: "h2" });
    const fast = screen.getByLabelText("轻量模型");
    expect(fast).toHaveAttribute("placeholder", "claude-haiku-5-5");
    expect(screen.getByLabelText("决策模型")).toHaveAttribute("placeholder", "claude-opus-5-5");
    await userEvent.type(fast, "claude-haiku-4-5");
    await userEvent.click(within(rowOf("对话")).getByRole("button", { name: "轻量" }));
    await userEvent.click(within(section("AI 模型")).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.find("PUT", "/settings")).toHaveLength(1));
    expect(api.find("PUT", "/settings")[0].body).toMatchObject({
      model_routing: { enabled: true, tasks: { chat: "fast", decision: "strong" }, models: { claude_code: { fast: "claude-haiku-4-5" } } },
    });
  });

  it("hides the per-task choices when routing is off", async () => {
    mockSettings({ settings: { model_routing: { enabled: false, tasks: {}, models: {} } } });
    renderPage(<Settings />);
    await screen.findByText("AI 模型", { selector: "h2" });
    expect(screen.queryByLabelText("轻量模型")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "按任务分配模型" })).not.toBeChecked();
  });
});
