import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ToastProvider } from "../lib";
import { mockApi } from "../test/utils";
import { AIDetail } from "./AI";

const cycle = (over: Record<string, unknown> = {}) => ({
  id: "c1", ts: 1_760_000_000, mode: "paper", trigger: "manual", status: "done", provider: "claude_code", model: "opus",
  summary: "采纳风控上限，小额买入 BTC", error: "", duration_ms: 9000, risk: [], context: {}, prompt: "", raw_output: "{}", usage: {},
  plan: { summary: "采纳风控上限，小额买入 BTC", market_view: "", confidence: 0.6, actions: [], futures: [], alerts: [], questions: [] },
  timing: { debate_ms: 4200, model_ms: 3000 },
  ...over,
});

function open(c: Record<string, unknown>) {
  mockApi({ "GET /cycles/c1": c });
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={["/ai/c1"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes><Route path="/ai/:id" element={<AIDetail />} /></Routes>
      </MemoryRouter>
    </ToastProvider>,
  );
}

describe("AI decision detail", () => {
  it("shows each seat of the debate, the consensus and the debate time", async () => {
    open(cycle({ debate: { rounds: 2, consensus: { score: 0.23, lean: "bullish", votes: { BTC: { BUY: 2, HOLD: 1 } } }, turns: [
      { round: 1, role: "bull", model: "m-bull", ms: 2100, stance: "bullish", conviction: 0.8, thesis: "日线回调到位", points: ["RSI 45"],
        proposals: [{ symbol: "BTC", side: "BUY", note: "≤$200" }], rebuttal: "" },
      { round: 1, role: "bear", model: "m-bear", error: "bear down" },
      { round: 1, role: "risk", model: "m-risk", stance: "neutral", conviction: 0.5, thesis: "单笔不超过 $200", points: [], proposals: [], rebuttal: "" },
      { round: 2, role: "bull", model: "m-bull", stance: "bullish", conviction: 0.7, thesis: "维持", points: [], proposals: [], rebuttal: "空头没有给出数据" },
    ] } }));
    expect(await screen.findByText("多空辩论", { selector: "h2" })).toBeInTheDocument();
    expect(screen.getByText("看多 +0.23")).toBeInTheDocument();
    expect(screen.getByText("买 2 · 持有 1")).toBeInTheDocument();
    expect(screen.getByText("第 2 轮")).toBeInTheDocument();
    const bull = screen.getByTestId("turn-1-bull");
    expect(within(bull).getByText("看多 · 80%")).toBeInTheDocument();
    expect(within(bull).getByText("日线回调到位")).toBeInTheDocument();
    expect(within(screen.getByTestId("turn-1-bear")).getByText("bear down")).toBeInTheDocument();
    expect(within(screen.getByTestId("turn-2-bull")).getByText("空头没有给出数据")).toBeInTheDocument();
    expect(screen.getByText(/辩论 4\.2s/)).toBeInTheDocument();
  });

  it("has no debate section when the debate was off", async () => {
    open(cycle({ debate: {} }));
    expect(await screen.findByText("判断", { selector: "h2" })).toBeInTheDocument();
    expect(screen.queryByText("多空辩论", { selector: "h2" })).toBeNull();
  });

  it("lists the news the round was given", async () => {
    open(cycle({ timing: { news_ms: 1500 }, context: { news: { source: "model", fetched_at: "2026-10-30 08:00 UTC", overview: "监管偏多",
      items: [{ title: "SEC 批准 ETF", url: "https://x.com/1", source: "Reuters", time: "2026-10-30 07:00", assets: ["BTC"], impact: "bullish", summary: "资金流入" },
        { title: "交易所被盗", assets: ["ETH"], impact: "bearish" }] } } }));
    expect(await screen.findByText("新闻", { selector: "h2" })).toBeInTheDocument();
    expect(screen.getByText("监管偏多")).toBeInTheDocument();
    const items = screen.getAllByTestId("news-item");
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByRole("link", { name: "SEC 批准 ETF" })).toHaveAttribute("href", "https://x.com/1");
    expect(within(items[0]).getByText("利好")).toBeInTheDocument();
    expect(within(items[1]).getByText("利空")).toBeInTheDocument();
    expect(screen.getByText(/新闻 1\.5s/)).toBeInTheDocument();
  });
});
