import { ColorType } from "lightweight-charts";
import { useSyncExternalStore } from "react";

/**
 * Light/dark theme. The preference (system | light | dark) is per browser, kept in localStorage; "system" follows
 * prefers-color-scheme live. index.html applies the same logic before first paint so there is no flash.
 */
export type ThemePref = "system" | "light" | "dark";
export type Theme = "light" | "dark";

const KEY = "bclaw:theme";
const listeners = new Set<() => void>();
const mq = () => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null);

export function getPref(): ThemePref {
  try { const v = localStorage.getItem(KEY); return v === "light" || v === "dark" ? v : "system"; } catch { return "system"; }
}

export function resolveTheme(pref = getPref()): Theme {
  if (pref !== "system") return pref;
  const m = mq();
  return m ? (m.matches ? "dark" : "light") : "dark";
}

function apply() {
  const t = resolveTheme();
  const root = document.documentElement;
  root.dataset.theme = t;
  root.style.colorScheme = t;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", t === "dark" ? "#000000" : "#FFFFFF");
  listeners.forEach((f) => f());
}

export function setPref(p: ThemePref) {
  try { if (p === "system") localStorage.removeItem(KEY); else localStorage.setItem(KEY, p); } catch { /* private mode */ }
  apply();
}

export function initTheme() {
  apply();
  mq()?.addEventListener?.("change", () => { if (getPref() === "system") apply(); });
  window.addEventListener("storage", (e) => { if (e.key === KEY) apply(); });
}

function subscribe(f: () => void) { listeners.add(f); return () => { listeners.delete(f); }; }

export const useThemePref = () => useSyncExternalStore(subscribe, getPref, () => "system" as ThemePref);
export const useTheme = () => useSyncExternalStore(subscribe, () => resolveTheme(), () => "dark" as Theme);

/** Concrete colours for canvas/SVG charts, which can't read Tailwind classes. Mirrors the CSS variables in index.css. */
export const PALETTE = {
  dark: {
    bg: "#000000", raise: "#0D0D0D", grid: "#111111", line: "#1F1F1F", lineStrong: "#2E2E2E",
    fg: "#FFFFFF", fg2: "#8C8C8C", fg3: "#5C5C5C", up: "#20B26C", down: "#EF454A", warn: "#F0A23B",
    series: ["#4C8DFF", "#F0A23B", "#20B26C", "#C77DFF", "#EF454A", "#2EC4C4", "#FF7AB6", "#B5B5B5"],
  },
  light: {
    bg: "#FFFFFF", raise: "#FFFFFF", grid: "#F2F2F2", line: "#E6E6E6", lineStrong: "#DDDDDD",
    fg: "#0A0A0A", fg2: "#5C5C5C", fg3: "#8A8A8A", up: "#0E9F5C", down: "#E0383E", warn: "#C77D12",
    series: ["#2563EB", "#C77D12", "#0E9F5C", "#8B3FD9", "#E0383E", "#0E8F8F", "#D63F86", "#6B6B6B"],
  },
} as const;

export const useColors = () => PALETTE[useTheme()];

/** "#RRGGBB" + alpha -> rgba() */
export const alpha = (hex: string, a: number) =>
  `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${a})`;

type Colors = (typeof PALETTE)[Theme];

/** Theme-dependent options for lightweight-charts (createChart / applyOptions). */
export const chartTheme = (c: Colors) => ({
  layout: { background: { type: ColorType.Solid, color: c.bg } as const, textColor: c.fg3 },
  grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
  rightPriceScale: { borderColor: c.line },
  timeScale: { borderColor: c.line },
  crosshair: { vertLine: { color: c.fg3, labelBackgroundColor: c.lineStrong }, horzLine: { color: c.fg3, labelBackgroundColor: c.lineStrong } },
});

export const candleColors = (c: Colors) => ({
  upColor: c.up, downColor: c.down, borderUpColor: c.up, borderDownColor: c.down,
  wickUpColor: c.up, wickDownColor: c.down, priceLineColor: c.fg2,
});
