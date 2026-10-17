/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        abyss: "#060A13",
        deep: "#0A1222",
        ink: { DEFAULT: "#E9EEF7", soft: "#A9B4C8", mute: "#7D889E", faint: "#525D73" },
        mint: { DEFAULT: "#5EF2C4", dim: "#2FBF95" },
        amber: { DEFAULT: "#F5B544" },
        rose: { DEFAULT: "#FF5C7A" },
        live: "#FF4D4D",
      },
      fontFamily: {
        display: ['"Instrument Serif"', '"Songti SC"', '"Noto Serif SC"', "serif"],
        sans: ['"Inter Variable"', '"PingFang SC"', '"Hiragino Sans GB"', '"Noto Sans SC"', '"Microsoft YaHei"', "system-ui", "sans-serif"],
      },
      boxShadow: {
        glass: "inset 0 1px 0 0 rgba(255,255,255,0.06), 0 20px 50px -25px rgba(0,0,0,0.6)",
        glow: "0 0 0 1px rgba(94,242,196,0.35), 0 10px 40px -10px rgba(94,242,196,0.35)",
      },
    },
  },
  plugins: [],
};
