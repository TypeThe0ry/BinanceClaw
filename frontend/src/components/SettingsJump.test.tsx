import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import Config from "../pages/Config";
import Settings from "../pages/Settings";
import { mockSettings } from "../test/settingsFixture";
import { renderPage } from "../test/utils";

function Loc() {
  const l = useLocation();
  return <output aria-label="location">{l.pathname + l.hash}</output>;
}
/** The bar scans the rendered sections after mount; wait for that before searching. */
const ready = (t: string) => within(screen.getByRole("navigation", { name: "分类" })).findByRole("button", { name: t });
const withLoc = (ui: JSX.Element) => <Routes><Route path="*" element={<>{ui}<Loc /></>} /></Routes>;

describe("settings search and category bar", () => {
  it("lists every section of the current page as a jump link", async () => {
    mockSettings();
    renderPage(<Config />);
    await screen.findByText("风控", { selector: "h2" });
    const cats = within(screen.getByRole("navigation", { name: "分类" }));
    await cats.findByRole("button", { name: "风控" });
    for (const t of ["风险预设", "资产与分配", "资金管理", "风控", "合约"]) expect(cats.getByRole("button", { name: t })).toBeInTheDocument();
    expect(cats.queryByRole("button", { name: "节奏" })).not.toBeInTheDocument();
  });

  it("finds a setting row on this page by its label", async () => {
    mockSettings();
    renderPage(<Config />);
    await screen.findByText("风控", { selector: "h2" });
    await ready("风控");
    await userEvent.type(screen.getByRole("textbox", { name: "搜索设置" }), "做空");
    const opt = within(screen.getByRole("listbox", { name: "搜索结果" })).getByRole("option", { name: /允许做空/ });
    expect(opt).toHaveTextContent("合约");
    await userEvent.click(opt);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("finds settings that live on the other page and goes there", async () => {
    mockSettings();
    renderPage(withLoc(<Config />));
    await screen.findByText("风控", { selector: "h2" });
    await ready("风控");
    await userEvent.type(screen.getByRole("textbox", { name: "搜索设置" }), "免打扰");
    await userEvent.click(within(screen.getByRole("listbox")).getByRole("option", { name: /Telegram 推送/ }));
    expect(decodeURIComponent(screen.getByLabelText("location").textContent ?? "")).toBe("/settings#Telegram 推送");
  });

  it("says when nothing matches", async () => {
    mockSettings();
    renderPage(<Settings />);
    await screen.findByText("节奏", { selector: "h2" });
    await ready("节奏");
    await userEvent.type(screen.getByRole("textbox", { name: "搜索设置" }), "完全不存在");
    expect(screen.getByText("没有找到")).toBeInTheDocument();
  });
});
