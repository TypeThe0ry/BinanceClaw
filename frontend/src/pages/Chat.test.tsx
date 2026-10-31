import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it } from "vitest";
import type { ChatPlan } from "../api";
import { mockApi, renderPage } from "../test/utils";
import Chat from "./Chat";

const now = () => Math.floor(Date.now() / 1000);

function plan(over: Partial<ChatPlan> = {}): ChatPlan {
  return {
    id: "a1", created: now() - 60, expires: now() + 900, cycle_id: "", symbol: "ETHUSDT", side: "BUY", quote_usd: 60,
    reason: "回调", invalidation: "跌破支撑", status: "pending", resolved_by: "", result: "", kind: "spot",
    payload: { source: "chat", order: "limit", price: 2400, hours: 24 }, limit: null, ...over,
  };
}

function history(p: ChatPlan) {
  return {
    cut: 0, plans: { [p.id]: p },
    messages: [
      { id: 1, ts: now() - 70, channel: "web", role: "user", text: "ETH 跌到 2400 买 60" },
      { id: 2, ts: now() - 60, channel: "web", role: "assistant", text: "📝 方案：买入 ETH $60.00（待批准）", approvals: [p.id] },
    ],
  };
}

describe("Chat trade plans", () => {
  beforeAll(() => { Element.prototype.scrollIntoView = () => {}; });

  it("approves a pending plan from the chat", async () => {
    const api = mockApi({ "GET /chat?limit=200": history(plan()), "POST /approvals/a1/approve": { ok: true, message: "已挂单" } });
    renderPage(<Chat />);
    const card = await screen.findByTestId("plan");
    expect(within(card).getByText("ETH")).toBeInTheDocument();
    expect(within(card).getByText("挂单 ≤ 2,400.00")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "批准挂单" }));
    await waitFor(() => expect(api.find("POST", "/approvals/a1/approve")).toHaveLength(1));
  });

  it("rejects a market plan", async () => {
    const api = mockApi({
      "GET /chat?limit=200": history(plan({ payload: { source: "chat", order: "market" } })),
      "POST /approvals/a1/reject": { ok: true, message: "已拒绝" },
    });
    renderPage(<Chat />);
    const card = await screen.findByTestId("plan");
    expect(within(card).getByText("市价")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "批准下单" })).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "拒绝" }));
    await waitFor(() => expect(api.find("POST", "/approvals/a1/reject")).toHaveLength(1));
  });

  it("cancels an open limit order", async () => {
    const lo = { id: "l1", created: now(), expires: now() + 3600, approval_id: "a1", symbol: "ETHUSDT", side: "BUY" as const,
      quote_usd: 60, price: 2400, status: "open" as const, result: "", order_id: "", done_at: null };
    const api = mockApi({
      "GET /chat?limit=200": history(plan({ status: "approved", result: "已挂单：买入 ETH $60.00，价格 ≤ 2400", limit: lo })),
      "POST /limit-orders/l1/cancel": { ok: true, message: "已撤单" },
    });
    renderPage(<Chat />);
    const card = await screen.findByTestId("plan");
    expect(within(card).getByText("挂单中")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: "批准挂单" })).toBeNull();
    await userEvent.click(within(card).getByRole("button", { name: "撤单" }));
    await waitFor(() => expect(api.find("POST", "/limit-orders/l1/cancel")).toHaveLength(1));
  });
});
