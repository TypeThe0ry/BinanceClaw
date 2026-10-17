/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#000000",
        raise: "#0D0D0D",
        hover: "#141414",
        field: "#121212",
        line: "#1F1F1F",
        "line-strong": "#2E2E2E",
        fg: { DEFAULT: "#FFFFFF", 2: "#8C8C8C", 3: "#5C5C5C" },
        up: "#20B26C",
        down: "#EF454A",
        warn: "#F0A23B",
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
