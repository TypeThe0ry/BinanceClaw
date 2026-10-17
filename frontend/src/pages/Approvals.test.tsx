import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Approval } from "../api";
import { mockApi, renderPage } from "../test/utils";
import Approvals from "./Approvals";

const now = () => Math.floor(Date.now() / 1000);

function approval(over: Partial<Approval> = {}): Approval {
  return {
    id: "a1", created: now() - 60, expires: now() + 900, cycle_id: "c1", symbol: "ETHUSDT", side: "BUY", quote_usd: 250,
    reason: "回调到支撑位", invalidation: "跌破 2000", status: "pending", resolved_by: "", result: "", kind: "spot", ...over,
  };
}

const PENDING = "GET /approvals?status=pending";
const ALL = "GET /approvals?limit=200";

describe("Approvals page", () => {
  it("lists pending approvals with a count badge", async () => {
    const a = approval();
    mockApi({ [PENDING]: [a, approval({ id: "a2", symbol: "BTCUSDT", side: "SELL", quote_usd: 1200 })], [ALL]: [a] });
    renderPage(<Approvals />);
    expect(await screen.findByText("ETH")).toBeInTheDocument();
    expect(screen.getByText("BTC")).toBeInTheDocument();
    expect(screen.getByText("$1,200")).toBeInTheDocument();
    expect(within(screen.getByRole("tab", { name: /待处理/ })).getByText("2")).toBeInTheDocument();
  });

  it("shows an empty state when nothing is pending", async () => {
    mockApi({ [PENDING]: [], [ALL]: [] });
    renderPage(<Approvals />);
    expect(await screen.findByText("没有待处理的审批")).toBeInTheDocument();
  });

  it("approves an order, toasts the result and reloads both lists", async () => {
    const api = mockApi({
      [PENDING]: [approval()], [ALL]: [],
      "POST /approvals/a1/approve": { ok: true, message: "已买入 ETH $250" },
    });
    renderPage(<Approvals />);
    await userEvent.click(await screen.findByRole("button", { name: "批准" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已买入 ETH $250");
    expect(api.find("POST", "/approvals/a1/approve")).toHaveLength(1);
    await waitFor(() => expect(api.find("GET", "/approvals?status=pending")).toHaveLength(2));
    expect(api.find("GET", "/approvals?limit=200")).toHaveLength(2);
  });

  it("rejects an order", async () => {
    const api = mockApi({ [PENDING]: [approval()], [ALL]: [], "POST /approvals/a1/reject": { ok: true, message: "" } });
    renderPage(<Approvals />);
    await userEvent.click(await screen.findByRole("button", { name: "拒绝" }));
    expect(await screen.findByRole("status")).toHaveTextContent("已拒绝 ETH $250");
    expect(api.find("POST", "/approvals/a1/reject")).toHaveLength(1);
  });

  it("surfaces a failed approval as an error toast", async () => {
    mockApi({ [PENDING]: [approval()], [ALL]: [], "POST /approvals/a1/approve": { ok: false, message: "余额不足" } });
    renderPage(<Approvals />);
    await userEvent.click(await screen.findByRole("button", { name: "批准" }));
    const toast = await screen.findByRole("status");
    expect(toast).toHaveTextContent("余额不足");
    expect(toast).toHaveClass("text-down");
  });

  it("disables approving an expired request", async () => {
    mockApi({ [PENDING]: [approval({ expires: now() - 5 })], [ALL]: [] });
    renderPage(<Approvals />);
    expect(await screen.findByText("已过期")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批准" })).toBeDisabled();
  });

  it("shows reason, invalidation and futures details when expanded", async () => {
    mockApi({
      [PENDING]: [approval({ kind: "futures", side: "OPEN_LONG", payload: { leverage: 3, stop_loss: 1900, take_profit: 2600 } })],
      [ALL]: [],
    });
    renderPage(<Approvals />);
    const toggle = await screen.findByRole("button", { expanded: false });
    expect(within(toggle).getByText("永续")).toBeInTheDocument();
    await userEvent.click(toggle);
    expect(screen.getByText("回调到支撑位")).toBeInTheDocument();
    expect(screen.getByText("跌破 2000")).toBeInTheDocument();
    expect(screen.getByText(/逐仓 3x · 止损 1,900.00 · 止盈 2,600.00/)).toBeInTheDocument();
  });

  it("lists resolved approvals with their outcome on the history tab", async () => {
    mockApi({
      [PENDING]: [],
      [ALL]: [
        approval({ id: "h1", status: "approved", result: "成交 0.1 ETH" }),
        approval({ id: "h2", status: "failed", symbol: "SOLUSDT", result: "下单失败" }),
        approval({ id: "h3", status: "pending" }),
      ],
    });
    renderPage(<Approvals />);
    await userEvent.click(await screen.findByRole("tab", { name: "历史" }));
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("已批准")).toHaveClass("text-up");
    expect(within(rows[0]).getByText("成交 0.1 ETH")).toBeInTheDocument();
    expect(within(rows[1]).getByText("SOL/USDT")).toBeInTheDocument();
    expect(within(rows[1]).getByText("未执行")).toBeInTheDocument();
  });
});
