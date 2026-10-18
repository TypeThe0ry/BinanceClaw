import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockSettings, PHRASE, rowOf, section, liveCheck, settingsResp } from "../test/settingsFixture";
import { mockApi, renderPage, Status } from "../test/utils";
import Settings from "./Settings";

describe("Settings page (设置)", () => {
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

  it("keeps investment config off the system page and links to it", async () => {
    mockSettings();
    renderPage(<Settings />);
    await screen.findByText("节奏", { selector: "h2" });
    for (const t of ["风控", "资产与分配", "资金管理", "合约", "目标配置", "风险预设"]) expect(screen.queryByText(t, { selector: "h2" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "配置" })).toHaveAttribute("href", "/config");
    expect(screen.getByRole("link", { name: "设置" })).toHaveAttribute("aria-current", "page");
  });

  it("edits Telegram push types, digest and quiet hours; approvals cannot be muted", async () => {
    const notify = { types: { approval: true, trade: true, earn: false }, mode: "instant", digest_minutes: 60,
      quiet: { enabled: false, start: "23:00", end: "08:00" } };
    const api = mockApi({
      "GET /settings": { ...settingsResp({ notify }), telegram: true,
        notify_types: { approval: { label: "审批请求", always: true }, trade: { label: "自动成交", always: false },
          earn: { label: "理财申购 / 赎回", always: false } } },
      "GET /live/check": liveCheck(true), "GET /devices": [], "GET /earn": { enabled: false, positions: {}, products: {} },
      "PUT /settings": { ok: true },
    });
    renderPage(<Settings />);
    const sec = await waitFor(() => section("Telegram 推送"));
    expect(within(rowOf("审批请求")).getByText("始终推送")).toBeInTheDocument();
    expect(within(sec).queryByRole("checkbox", { name: "审批请求" })).toBeNull();
    await userEvent.click(within(sec).getByRole("checkbox", { name: "自动成交" }));
    await userEvent.click(within(sec).getByRole("checkbox", { name: "理财申购 / 赎回" }));
    await userEvent.click(within(sec).getByRole("button", { name: "定时汇总" }));
    await userEvent.click(within(sec).getByRole("checkbox", { name: "免打扰" }));
    expect(within(sec).getByLabelText("免打扰开始")).toHaveValue("23:00");
    await userEvent.click(within(sec).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.find("PUT", "/settings")).toHaveLength(1));
    expect(api.find("PUT", "/settings")[0].body).toEqual({ notify: { ...notify, mode: "digest",
      types: { approval: true, trade: false, earn: true }, quiet: { enabled: true, start: "23:00", end: "08:00" } } });
  });
});
