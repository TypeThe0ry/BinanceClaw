import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it } from "vitest";
import type { ArenaBatch, ArenaBoard, ArenaContestant } from "../api";
import { mockApi, renderPage } from "../test/utils";
import Arena from "./Arena";

beforeAll(() => {
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as unknown as typeof ResizeObserver;
});

const now = () => Math.floor(Date.now() / 1000);

function contestant(over: Partial<ArenaContestant> = {}): ArenaContestant {
  return {
    id: "c1", name: "Opus", provider: "claude_code", model: "claude-opus-5-5", base_url: "", enabled: true, available: true,
    unavailable_reason: "", started: now() - 86400, initial_usd: 10000, equity: 10850, return: 0.085, vs_btc: 0.02,
    max_drawdown: 0.031, sharpe: 1.84, win_rate: 0.75, closed_trades: 4, trades: 9, rounds: 12, errors: 0, realized: 420, fees: 12.5,
    tokens_in: 52000, tokens_out: 4100, cost_usd: 0, cash: 4000,
    positions: { BTC: { qty: 0.08, value_usd: 6850, weight: 0.63, avg_cost: 80000, unrealized_pct: 0.07 } }, rank: 1, ...over,
  };
}

function board(over: Partial<ArenaBoard> = {}): ArenaBoard {
  return {
    config: { enabled: true, interval_minutes: 60, max_rounds_per_day: 24, initial_usd: 10000, tokens: ["BTC", "ETH"], max_position_pct: 0.5 },
    contestants: [contestant(), contestant({ id: "c2", name: "GPT", provider: "openai", model: "gpt-5.5", return: -0.012, equity: 9880, rank: 2 })],
    benchmark: { return: 0.065, equity: 10650, since: now() - 86400, max_drawdown: 0.05, sharpe: 1.2 },
    running: false, last_round: now() - 600, next_round: now() + 3000, rounds_today: 3, season_start: now() - 86400,
    providers: ["claude_code", "openai", "openai_compat", "anthropic_api"], max_contestants: 8, ...over,
  };
}

const batch: ArenaBatch = {
  batch: "b1", ts: now() - 600, entries: [
    { id: "r1", ts: now() - 600, batch: "b1", contestant_id: "c1", name: "Opus", status: "done", model: "m", summary: "回调买 BTC",
      confidence: 0.7, equity: 10850, duration_ms: 9000, error: "",
      actions: [{ side: "BUY", symbol: "BTC", quote_usd: 2000, reason: "趋势向上", result: "filled", usd: 2000, price: 80000 }] },
    { id: "r2", ts: now() - 600, batch: "b1", contestant_id: "c2", name: "GPT", status: "error", model: "m", summary: "",
      confidence: null, equity: 9880, duration_ms: 3000, error: "输出校验失败", actions: [] },
  ],
};

const routes = (b = board()) => ({
  "GET /arena": b,
  "GET /arena/curves": { c1: [[now() - 7200, 10000], [now() - 600, 10850]], btc_hold: [[now() - 7200, 10000], [now() - 600, 10650]] },
  "GET /arena/rounds?limit=20": [batch],
  "GET /arena/rounds?contestant=c1&limit=8": batch.entries.slice(0, 1),
});

describe("Arena page", () => {
  it("ranks contestants with the BTC benchmark", async () => {
    mockApi(routes());
    renderPage(<Arena />);
    expect(await screen.findByText("Opus")).toBeInTheDocument();
    expect(screen.getByText("GPT")).toBeInTheDocument();
    expect(screen.getByText("BTC 持有")).toBeInTheDocument();
    expect(screen.getByText("+8.50%")).toBeInTheDocument();
    expect(screen.getByText("-1.20%")).toBeInTheDocument();
    expect(screen.getAllByText("75% (4)")).toHaveLength(2);
    expect(screen.getAllByText("1.84")).toHaveLength(2);
  });

  it("expands a contestant into holdings and its recent rounds", async () => {
    mockApi(routes());
    renderPage(<Arena />);
    await userEvent.click(await screen.findByText("Opus"));
    expect(await screen.findByText("回调买 BTC")).toBeInTheDocument();
    expect(screen.getByText("$6,850.00", { exact: false })).toBeInTheDocument();
  });

  it("shows every model's answer to the same round", async () => {
    mockApi(routes());
    renderPage(<Arena />);
    await userEvent.click(await screen.findByRole("tab", { name: "回合" }));
    expect(await screen.findByText("回调买 BTC")).toBeInTheDocument();
    expect(screen.getByText("输出校验失败")).toBeInTheDocument();
  });

  it("runs a round now", async () => {
    const api = mockApi({ ...routes(), "POST /arena/run": { ok: true } });
    renderPage(<Arena />);
    await userEvent.click(await screen.findByRole("button", { name: "立即比赛" }));
    await waitFor(() => expect(api.find("POST", "/arena/run")).toHaveLength(1));
  });

  it("adds a contestant and saves the rules", async () => {
    const api = mockApi({ ...routes(), "POST /arena/contestants": { id: "c3" }, "PUT /arena/config": board().config });
    renderPage(<Arena />);
    await userEvent.click(await screen.findByRole("tab", { name: "参赛与规则" }));
    await userEvent.type(screen.getByLabelText("名称"), "DeepSeek");
    await userEvent.selectOptions(screen.getByLabelText("提供方"), "openai_compat");
    await userEvent.type(screen.getByLabelText("base_url"), "https://api.deepseek.com/v1");
    await userEvent.click(screen.getByRole("button", { name: "添加" }));
    await waitFor(() => expect(api.find("POST", "/arena/contestants")).toHaveLength(1));
    expect(api.find("POST", "/arena/contestants")[0].body).toEqual(
      { name: "DeepSeek", provider: "openai_compat", model: "", base_url: "https://api.deepseek.com/v1" });

    const tokens = screen.getByLabelText("比赛币种");
    await userEvent.clear(tokens);
    await userEvent.type(tokens, "BTC, SOL");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(api.find("PUT", "/arena/config")).toHaveLength(1));
    expect((api.find("PUT", "/arena/config")[0].body as { tokens: string[] }).tokens).toEqual(["BTC", "SOL"]);
  });

  it("offers to add a model when nobody is competing", async () => {
    mockApi(routes(board({ contestants: [], benchmark: null })));
    renderPage(<Arena />);
    await userEvent.click(await screen.findByRole("button", { name: "添加模型" }));
    expect(screen.getByLabelText("名称")).toBeInTheDocument();
  });
});
