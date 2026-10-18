import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { mockApi, renderPage } from "../test/utils";
import TokenPicker from "./TokenPicker";

const row = (token: string, tags: string[], category: string) => ({
  token, symbol: `${token}USDT`, price: 1, change_pct: 0.01, quote_volume: 1, held: false, enabled: false,
  status: "TRADING", trading: true, selectable: true, tags, category,
});

describe("TokenPicker", () => {
  it("filters spot coins by Binance category", async () => {
    const api = mockApi({
      "GET /categories": [{ tag: "Layer1_Layer2", label: "Layer 1 / Layer 2", count: 2 }, { tag: "Meme", label: "Meme", count: 1 }],
      "GET /markets?q=&limit=40": [row("ETH", ["Layer1_Layer2"], "Layer 1 / Layer 2"), row("DOGE", ["Meme"], "Meme")],
      "GET /markets?q=&tag=Meme&limit=200": [row("DOGE", ["Meme"], "Meme")],
    });
    const onToggle = vi.fn();
    renderPage(<TokenPicker selected={[]} onToggle={onToggle} />);
    await userEvent.click(screen.getByPlaceholderText("搜索币安现货"));
    expect(await screen.findByText("ETH")).toBeInTheDocument();
    expect(screen.getAllByText("Layer 1 / Layer 2").length).toBeGreaterThan(1);    // chip + row label

    await userEvent.click(screen.getByRole("button", { name: /^Meme/ }));
    await waitFor(() => expect(screen.queryByText("ETH")).not.toBeInTheDocument());
    expect(api.find("GET", "/markets?q=&tag=Meme&limit=200")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /^Meme/ })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByText("DOGE"));
    expect(onToggle).toHaveBeenCalledWith("DOGE");
  });

  it("has no category bar for futures", async () => {
    const api = mockApi({ "GET /futures/markets?q=&limit=40": [] });
    renderPage(<TokenPicker selected={[]} onToggle={() => {}} endpoint="/futures/markets" placeholder="合约" />);
    await userEvent.click(screen.getByPlaceholderText("合约"));
    await screen.findByText("无匹配");
    expect(api.find("GET", "/categories")).toHaveLength(0);
  });
});
