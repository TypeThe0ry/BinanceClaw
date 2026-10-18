import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockSettings, rowOf, section } from "../test/settingsFixture";
import { renderPage, Status } from "../test/utils";
import Config from "./Config";

describe("Config page (配置)", () => {
  it("saves risk limits, converting percentages back to fractions", async () => {
    const api = mockSettings();
    renderPage(<Config />);
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
    renderPage(<Config />);
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
    renderPage(<Config />);
    await screen.findByText("资金管理", { selector: "h2" });
    await userEvent.type(screen.getByPlaceholderText("BTC"), "bnb");
    await userEvent.type(screen.getByPlaceholderText("数量"), "1.5");
    await userEvent.click(within(section("资金管理")).getByRole("button", { name: "添加" }));
    expect(screen.getByText("BNB")).toBeInTheDocument();
    await userEvent.click(within(section("资金管理")).getByRole("button", { name: "保存" }));

    await waitFor(() => expect(api.find("PUT", "/settings")[0]?.body).toEqual({ capital: { locked: { BNB: 1.5 }, budget_usd: null } }));
  });

  it("applies a risk preset", async () => {
    const api = mockSettings({ extra: { "POST /risk-preset": { ok: true } } });
    renderPage(<Config />);
    await userEvent.click(await screen.findByRole("button", { name: "积极" }));
    await waitFor(() => expect(api.find("POST", "/risk-preset")[0]?.body).toEqual({ name: "aggressive" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已套用：积极");
  });

  it("asks for a second confirmation before a dangerous change and resends with the token", async () => {
    const change = { field: "limits.allow_sell", label: "允许卖出", old: true, new: false };
    const api = mockSettings({ extra: {
      "PUT /settings": (_b: unknown, h: Record<string, string>) => h["X-Confirm-Token"] === "tok"
        ? { ok: true } : new Status(409, { detail: { message: "需要确认", changes: [change], confirm_token: "tok" } }),
    } });
    renderPage(<Config />);
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

  it("manual allocation keeps the portfolio at its targets with a drift threshold", async () => {
    const api = mockSettings();
    renderPage(<Config />);
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
    renderPage(<Config />);
    await screen.findByText("资产与分配", { selector: "h2" });
    await userEvent.click(screen.getByRole("button", { name: "AI 自主分配" }));
    expect(screen.queryByRole("checkbox", { name: "自动再平衡" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部资产" })).toBeInTheDocument();
    expect(screen.queryByText("白名单", { selector: "p" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "黑名单" }));
    expect(screen.getByText("黑名单", { selector: "p" })).toBeInTheDocument();
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
    renderPage(<Config />);
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
    renderPage(<Config />);
    await screen.findByText("风控", { selector: "h2" });
    await userEvent.click(within(section("风控")).getByRole("button", { name: "保存" }));
    await screen.findByText("已保存");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("can put the new round off", async () => {
    const api = mockSettings({ extra: { "PUT /settings": { rerun: ["投资备注"] } } });
    renderPage(<Config />);
    await screen.findByText("风控", { selector: "h2" });
    await userEvent.click(within(section("风控")).getByRole("button", { name: "保存" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "稍后" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.find("POST", "/cycles/run")).toHaveLength(0);
  });

  it("keeps system settings off the config page and links back to them", async () => {
    mockSettings();
    renderPage(<Config />);
    await screen.findByText("风控", { selector: "h2" });
    for (const t of ["交易模式", "AI 模型", "节奏", "理财", "连接", "已登录设备"]) expect(screen.queryByText(t, { selector: "h2" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "设置" })).toHaveAttribute("href", "/settings");
    expect(screen.getByRole("link", { name: "配置" })).toHaveAttribute("aria-current", "page");
  });

  it("sets approval-free hours and saves the window", async () => {
    const api = mockSettings();
    renderPage(<Config />);
    await screen.findByText("免审批", { selector: "h2" });
    const sec = section("免审批");
    expect(within(sec).queryByLabelText("免审批开始")).not.toBeInTheDocument();
    await userEvent.click(within(sec).getByRole("button", { name: "按时段" }));
    const start = within(sec).getByLabelText("免审批开始");
    await userEvent.clear(start);
    await userEvent.type(start, "23:30");
    await userEvent.click(within(sec).getByRole("checkbox", { name: "也包含卖出" }));
    await userEvent.click(within(sec).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.find("PUT", "/settings")[0]?.body).toEqual({
      approval_free: { mode: "window", start: "23:30", end: "08:00", include_sell: true, include_futures: false } }));
  });

  it("saves trading style and the owner's views", async () => {
    const api = mockSettings();
    renderPage(<Config />);
    await screen.findByText("交易风格与观点", { selector: "h2" });
    const sec = section("交易风格与观点");
    await userEvent.click(within(sec).getByRole("button", { name: "长期" }));
    await userEvent.click(within(within(sec).getByRole("radiogroup", { name: "整体看法" })).getByRole("radio", { name: "偏空" }));
    await userEvent.type(within(sec).getByPlaceholderText("ETH"), "eth{enter}");
    await userEvent.click(within(within(sec).getByRole("radiogroup", { name: "ETH 看法" })).getByRole("radio", { name: "强烈看多" }));
    await userEvent.type(within(sec).getByLabelText("我的观点"), "等回调");
    await userEvent.click(within(sec).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.find("PUT", "/settings")[0]?.body).toEqual({
      strategy: { style: "long" }, outlook: { market: -1, assets: { ETH: 2 }, note: "等回调" } }));
  });
});
