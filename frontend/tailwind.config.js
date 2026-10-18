/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Values live in CSS variables (index.css) so light/dark is one attribute flip on <html>.
        bg: "rgb(var(--c-bg) / <alpha-value>)",
        raise: "rgb(var(--c-raise) / <alpha-value>)",
        hover: "rgb(var(--c-hover) / <alpha-value>)",
        field: "rgb(var(--c-field) / <alpha-value>)",
        line: "rgb(var(--c-line) / <alpha-value>)",
        "line-strong": "rgb(var(--c-line-strong) / <alpha-value>)",
        fg: { DEFAULT: "rgb(var(--c-fg) / <alpha-value>)", 2: "rgb(var(--c-fg-2) / <alpha-value>)", 3: "rgb(var(--c-fg-3) / <alpha-value>)" },
        up: "rgb(var(--c-up) / <alpha-value>)",
        down: "rgb(var(--c-down) / <alpha-value>)",
        warn: "rgb(var(--c-warn) / <alpha-value>)",
      },
      fontFamily: {
        sans: ['"Inter Variable"', '"PingFang SC"', '"Hiragino Sans GB"', '"Noto Sans SC"', '"Microsoft YaHei"', "system-ui", "sans-serif"],
      },
      keyframes: {
        flashUp: { "0%": { backgroundColor: "rgba(32,178,108,0.28)" }, "100%": { backgroundColor: "transparent" } },
        flashDown: { "0%": { backgroundColor: "rgba(239,69,74,0.28)" }, "100%": { backgroundColor: "transparent" } },
        pageIn: { "0%": { opacity: "0", transform: "translateY(4px)" }, "100%": { opacity: "1", transform: "none" } },
      },
      animation: { flashUp: "flashUp 700ms ease-out", flashDown: "flashDown 700ms ease-out", pageIn: "pageIn 180ms ease-out both" },
    },
  },
  plugins: [],
};
