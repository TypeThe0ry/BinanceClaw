import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Approval, LimitOrder } from "../api";
import { mockApi, renderPage } from "../test/utils";
import Trade from "./Trade";

vi.mock("lightweight-charts", () => {
  const series = { setData: () => {}, setMarkers: () => {}, createPriceLine: () => {} };
  const chart = { addCandlestickSeries: () => series, addHistogramSeries: () => series, priceScale: () => ({ applyOptions: () => {} }),
    timeScale: () => ({ setVisibleLogicalRange: () => {} }), applyOptions: () => {}, removeSeries: () => {}, remove: () => {} };
  return { CrosshairMode: { Normal: 0 }, ColorType: { Solid: "solid" }, LineStyle: {}, createChart: () => chart };
});

const now = () => Math.floor(Date.now() / 1000);

const plan: Approval = {
  id: "a1", created: now() - 60, expires: now() + 900, cycle_id: "", symbol: "ETHUSDT", side: "BUY", quote_usd: 60,
  reason: "", invalidation: "", status: "pending", resolved_by: "", result: "", kind: "spot",
  payload: { source: "chat", order: "limit", price: 2400, hours: 24 },
};
const resting: LimitOrder = {
  id: "l1", created: now() - 120, expires: now() + 3600, approval_id: "a0", symbol: "BTCUSDT", side: "SELL", quote_usd: 100,
  price: 90000, status: "open", result: "", order_id: "", done_at: null,
};

describe("Spot page limit orders", () => {
  it("lists pending and open limit orders with their actions", async () => {
    const api = mockApi({
      "GET /overview": null, "GET /orders?limit=500": [], "GET /alerts": [], "GET /klines?symbol=BTCUSDT&interval=4h&limit=300": [],
      "GET /approvals?limit=200": [plan], "GET /limit-orders?status=open": [resting],
      "POST /limit-orders/l1/cancel": { ok: true, message: "已撤单" },
      "POST /approvals/a1/approve": { ok: true, message: "已挂单" },
    });
    renderPage(<Trade />);
    await waitFor(() => expect(screen.getByText("挂单中")).toBeInTheDocument());
    expect(screen.getByText("待批准")).toBeInTheDocument();
    expect(screen.getByText("≤ 2,400.00")).toBeInTheDocument();
    expect(screen.getByText("≥ 90,000.00")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "撤单" }));
    await waitFor(() => expect(api.find("POST", "/limit-orders/l1/cancel")).toHaveLength(1));
    const row = screen.getByText("待批准").closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "批准挂单" }));
    await waitFor(() => expect(api.find("POST", "/approvals/a1/approve")).toHaveLength(1));
  });
});
