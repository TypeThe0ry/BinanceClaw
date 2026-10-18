import { afterEach, describe, expect, it, vi } from "vitest";
import { getPref, initTheme, resolveTheme, setPref } from "./theme";

function stubSystem(dark: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: dark, media: q, addEventListener: () => {}, removeEventListener: () => {} }));
}

describe("theme", () => {
  afterEach(() => localStorage.clear());

  it("follows the system by default", () => {
    stubSystem(false);
    expect(getPref()).toBe("system");
    initTheme();
    expect(document.documentElement.dataset.theme).toBe("light");
    stubSystem(true);
    expect(resolveTheme()).toBe("dark");
  });

  it("manual choice overrides the system and persists", () => {
    stubSystem(true);
    setPref("light");
    expect(localStorage.getItem("bclaw:theme")).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    setPref("system");
    expect(localStorage.getItem("bclaw:theme")).toBeNull();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
