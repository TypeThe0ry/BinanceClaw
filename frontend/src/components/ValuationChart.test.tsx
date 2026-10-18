import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { mockApi, renderPage } from "../test/utils";
import { ValuationChart, type ValHistory, type ValRange } from "./ValuationChart";

// jsdom has no ResizeObserver, which recharts' ResponsiveContainer needs
globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

const now = Math.floor(Date.now() / 1000);

function hist(range: ValRange, bases: Partial<ValHistory["bases"]>): ValHistory {
  const all = { "1d": null, "7d": null, "30d": null, "90d": null, "1y": null, ...bases };
  return { range, start: now - 86400, now, points: [{ t: now - 86400, total: 1000 }, { t: now - 3600, total: 1050 }], bases: all };
}

const bases = {
  "1d": { t: now - 86400, total: 1000, full: true },
  "7d": { t: now - 7 * 86400, total: 1250, full: true },
  "30d": { t: now - 10 * 86400, total: 1100, full: false },
};

describe("ValuationChart", () => {
  beforeEach(() => localStorage.clear());

  it("shows the change for every range and marks ranges without history", async () => {
    mockApi({ "GET /equity/history?range=7d": hist("7d", bases), "GET /equity/history?range=1d": hist("1d", bases) });
    renderPage(<ValuationChart current={1100} />);
    expect(await screen.findByText("−150.00")).toBeInTheDocument();            // 7 日：1250 → 1100
    expect(screen.getByRole("tab", { name: /7日/ })).toHaveTextContent("−12.00%");
    expect(screen.getByRole("tab", { name: /1日/ })).toHaveTextContent("+10.00%");
    expect(screen.getByRole("tab", { name: /30天/ })).toHaveTextContent("+0.00%*");   // partly covered
    expect(screen.getByRole("tab", { name: /90天/ })).toHaveTextContent("—");
    await userEvent.click(screen.getByRole("tab", { name: /1日/ }));
    expect(await screen.findByText("+100.00")).toBeInTheDocument();
  });

  it("says insufficient data when a range has no history", async () => {
    mockApi({ "GET /equity/history?range=7d": { ...hist("7d", {}), points: [] } });
    renderPage(<ValuationChart current={1100} />);
    expect(await screen.findByText("数据不足")).toBeInTheDocument();
    expect(screen.getByText(/这个区间还没有估值记录/)).toBeInTheDocument();
  });
});
