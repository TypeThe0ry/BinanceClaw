import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { FuturesResp, Overview } from "../api";
import { mockApi, renderPage } from "../test/utils";
import Assets from "./Assets";

const cat = (primary: string) => ({ primary, tags: [primary] });

function overview(): Partial<Overview> {
  return {
    mode: "paper", paused: false, pause_reason: "", next_cycle_at: 0, today: { orders: 0, notional: 0, max_orders: 10, max_notional: 1000 },
    market: {}, limits: {},
    targets: { BTC: { tokens: { BTC: 0.5 }, band: [0.3, 0.7] }, OTHER: { tokens: { SOL: null }, band: [0, 0.4] } },
    values: { BTC: 600, SOL: 200, USDT: 100 },
    account_values: { BTC: 600, SOL: 200, USDT: 100, DOGE: 50, FUTURES: 30 },
    balances: { BTC: 0.01, SOL: 1, USDT: 100 },
    futures: { enabled: true, open: 1, symbols: ["ETH", "DOGE"] },
    binance_categories: { BTC: cat("支付"), SOL: cat("Layer 1 / Layer 2"), USDT: cat("稳定币"), DOGE: cat("Meme"), ETH: cat("Layer 1 / Layer 2") },
  };
}

const futures: Partial<FuturesResp> = {
  enabled: true, settings: {}, closed: [], trades: [], realized: 5, unrealized: 2, margin_used: 28, equity: 30, error: null,
  positions: [{ id: "p1", symbol: "ETHUSDT", side: "LONG", qty: 0.1, entry: 2000, mark: 2020, margin: 28, leverage: 3, stop: 1900,
    take_profit: null, upnl: 2, roe: 0.07, liq_est: null, opened: 0, status: "open", reason: "", invalidation: "", realized: 0 }],
};

const routes = {
  "GET /overview": overview(), "GET /equity": [], "GET /cycles?limit=6": [], "GET /approvals?status=pending": [],
  "GET /futures": futures,
  "GET /futures/markets?limit=2": [
    { token: "ETH", symbol: "ETHUSDT", price: 2020, change_pct: 0.01, quote_volume: 1e9, enabled: true },
    { token: "DOGE", symbol: "DOGEUSDT", price: 0.2, change_pct: -0.03, quote_volume: 1e8, enabled: true },
  ],
};

const section = (name: string) => screen.getByText(name, { selector: "td" }).closest("tbody")!;

describe("Assets page", () => {
  beforeEach(() => localStorage.clear());

  it("lists the AI's spot assets first, then the rest by Binance category", async () => {
    mockApi(routes);
    renderPage(<Assets />);
    await screen.findByText("自选", { selector: "td" });
    const order = Array.from(document.querySelectorAll("tr[aria-expanded] td:first-child"))
      .map((td) => td.textContent?.replace(/^›/, "").split(" ")[0]);
    expect(order).toEqual(["自选", "Meme", "稳定币"]);
    expect(within(section("自选")).getByText("BTC")).toBeInTheDocument();
    expect(within(section("自选")).getByText("SOL")).toBeInTheDocument();
    expect(within(section("Meme")).getByText("DOGE")).toBeInTheDocument();
    expect(within(section("稳定币")).getByText("USDT")).toBeInTheDocument();
    expect(screen.queryByText("合约权益")).not.toBeInTheDocument();
  });

  it("hides empty assets that are not targets until asked", async () => {
    const ov = overview();
    ov.account_values = { ...ov.account_values!, PEPE: 0 };
    ov.targets = { ...ov.targets!, OTHER: { tokens: { SOL: null, ADA: 0.1 }, band: [0, 0.4] } };
    mockApi({ ...routes, "GET /overview": ov });
    renderPage(<Assets />);
    await screen.findByText("自选", { selector: "td" });
    expect(screen.getByText("ADA")).toBeInTheDocument();
    expect(screen.queryByText("PEPE")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /显示 1 个零余额资产/ }));
    expect(screen.getByText("PEPE")).toBeInTheDocument();
    expect(localStorage.getItem("bclaw:assets-show-empty")).toBe("1");
  });

  it("folds a section and remembers it", async () => {
    mockApi(routes);
    renderPage(<Assets />);
    await userEvent.click(await screen.findByText("Meme", { selector: "td" }));
    expect(screen.queryByText("DOGE")).not.toBeInTheDocument();
    expect(localStorage.getItem("bclaw:assets-collapsed")).toContain("spot:Meme");
  });

  it("shows futures positions first, then tradable contracts by category", async () => {
    mockApi(routes);
    renderPage(<Assets />);
    await userEvent.click(await screen.findByRole("button", { name: /合约/ }));
    await screen.findByText("合约权益");
    expect(within(section("自选")).getByText("ETH")).toBeInTheDocument();
    expect(within(section("Meme")).getByText("DOGE")).toBeInTheDocument();
    expect(screen.queryByText("Layer 1 / Layer 2", { selector: "td" })).not.toBeInTheDocument();
  });
});
