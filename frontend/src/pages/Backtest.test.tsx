import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { BacktestFull, BacktestRow, EquityStats, TradeStats } from "../api";
import { mockApi, renderPage } from "../test/utils";
import Backtest, { rounds } from "./Backtest";

globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;

const eq = (total_return: number, sharpe: number): EquityStats => ({
  points: 11, start: 10000, end: 10000 * (1 + total_return), span_days: 10, total_return, cagr: null, volatility: 0.3, sharpe, sortino: null,
  max_drawdown: 0.04, max_drawdown_days: 2, calmar: null, best_period: 0.02, worst_period: -0.01, period: "1h",
});
const trades: TradeStats = { trades: 1, wins: 1, losses: 0, win_rate: 1, profit_factor: null, expectancy: 5, avg_win: 5, avg_loss: null,
  payoff: null, best: 5, worst: 5, max_win_streak: 1, max_loss_streak: 0, net: 5, avg_return: 0.05 };
const params = { tokens: ["BTC", "ETH"], start: "2024-10-01", end: "2024-10-10", step: "1d" as const, capital: 10000, fee: 0.001,
  slippage: 0.0005, approvals: "execute" as const, model: "", allow_sell: null };
const row: BacktestRow = { id: "b1", ts: 1, status: "done", params, progress: 10, total: 10, error: "", finished: 2,
  summary: { total_return: 0.0321, btc_return: 0.1, max_drawdown: 0.04, sharpe: 1.23, trades: 2 } };
const full: BacktestFull = { ...row, result: {
  equity: [{ t: 1727740800, v: 10000, btc: 10000, basket: 10000 }, { t: 1727827200, v: 10321, btc: 11000, basket: 9900 }],
  drawdown: [{ t: 1, dd: 0 }, { t: 2, dd: 0.04 }], stats: eq(0.0321, 1.23), btc: eq(0.1, 2.5), basket: eq(-0.01, -0.4), trades,
  log: [{ ts: 1727740800, round: 0, token: "BTC", side: "BUY", qty: 0.002, price: 60000, quote_usd: 120, fee: 0.12 }],
  rounds: [{ i: 0, t: 1727740800, status: "done", summary: "BTC 回调买入", confidence: 0.62, actions: [
    { side: "BUY", symbol: "BTCUSDT", requested_usd: 120, approved_usd: 120, decision: "execute", reason: "", reason_model: "日线向上" }] }],
  usage: { calls: 10, input_tokens: 12000, output_tokens: 900, model_ms: 30000 }, model: "m1",
  decisions: { proposed: 3, execute: 2, skipped: 0, reject: 1 }, holdings: { USDT: 9880 },
} };

describe("Backtest", () => {
  it("counts rounds like the server", () => {
    expect(rounds({ start: "2024-10-01", end: "2024-10-10", step: "1d" })).toBe(10);
    expect(rounds({ start: "2024-10-01", end: "2024-10-01", step: "4h" })).toBe(6);
    expect(rounds({ start: "2024-10-02", end: "2024-10-01", step: "1d" })).toBe(0);
  });

  it("lists runs, shows the selected run against BTC and starts a new one after confirming", async () => {
    const api = mockApi({
      "GET /backtests": [row], "GET /backtests/b1": full, "GET /backtests/b2": { ...full, id: "b2", status: "running", progress: 0 },
      "POST /backtests": { ...row, id: "b2", status: "running", progress: 0 },
    });
    renderPage(<Backtest />);
    expect(await screen.findByText("BTC 回调买入")).toBeInTheDocument();
    const table = screen.getByText("等权持有", { selector: "td" }).closest("table")!;
    expect(within(table).getByText("2.50")).toBeInTheDocument();
    expect(screen.getAllByText("+3.21%").length).toBeGreaterThan(0);
    expect(screen.getByText("12,000")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "开始回测" }));
    const go = screen.getByRole("button", { name: /确认调用模型 \d+ 次/ });
    await userEvent.click(go);
    const sent = api.find("POST", "/backtests");
    expect(sent).toHaveLength(1);
    expect(sent[0].body).toMatchObject({ tokens: ["BTC", "ETH"], step: "1d", approvals: "execute", allow_sell: null });
  });

  it("blocks windows that are too long", async () => {
    mockApi({ "GET /backtests": [] });
    renderPage(<Backtest />);
    await userEvent.click(screen.getByRole("button", { name: "4 小时" }));
    fireEvent.change(screen.getByLabelText("开始日期"), { target: { value: "2024-01-01" } });
    expect(await screen.findByText(/超过上限 400/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始回测" })).toBeDisabled();
  });
});
