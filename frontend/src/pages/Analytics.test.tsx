import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { Analytics as A, TradeStats } from "../api";
import { mockApi, renderPage } from "../test/utils";
import Analytics from "./Analytics";

globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

const none: TradeStats = { trades: 0, wins: 0, losses: 0, win_rate: null, profit_factor: null, expectancy: null, avg_win: null, avg_loss: null,
  payoff: null, best: null, worst: null, max_win_streak: 0, max_loss_streak: 0, net: 0, avg_return: null };
const spot: TradeStats = { ...none, trades: 4, wins: 3, losses: 1, win_rate: 0.75, profit_factor: 2.5, net: 30, expectancy: 7.5, max_loss_streak: 1 };

function resp(range: A["range"]): A {
  return {
    range, mode: "paper", drawdown: [{ t: 1, dd: 0 }, { t: 2, dd: 0.08 }],
    equity: { points: 30, start: 1000, end: 1100, span_days: 30, total_return: 0.1, cagr: null, volatility: 0.4, sharpe: 1.84, sortino: 2.1,
      max_drawdown: 0.08, max_drawdown_days: 3, calmar: null, best_period: 0.03, worst_period: -0.04, period: "1d" },
    trades: spot, spot, futures: none, by_symbol: [{ symbol: "ETH", trades: 4, wins: 3, pnl: 30 }],
    benchmark: { name: "BTC", return: 0.05, excess: 0.05 },
  };
}

describe("Analytics", () => {
  beforeEach(() => localStorage.clear());

  it("shows ratios, trade stats and switches range", async () => {
    const api = mockApi({ "GET /analytics?range=30d": resp("30d"), "GET /analytics?range=90d": resp("90d") });
    renderPage(<Analytics />);
    expect(await screen.findByText("1.84")).toBeInTheDocument();
    expect(screen.getByText("8.00%")).toBeInTheDocument();
    expect(screen.getByText("2.50", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText("ETH")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "合约" }));
    expect(screen.getByText("这段时间没有平仓交易")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "90天" }));
    expect(api.find("GET", "/analytics?range=90d")).toHaveLength(1);
  });
});
