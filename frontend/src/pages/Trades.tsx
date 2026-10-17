import { motion } from "framer-motion";
import { ColorType, createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import type { Kline, Order, Overview } from "../api";
import { Card, Empty, PageTitle, SideTag } from "../components/ui";
import { clock, signedPct, token, usd, useApi } from "../lib";

const INTERVALS = [
  { k: "1h", label: "1 小时", limit: 240 },
  { k: "4h", label: "4 小时", limit: 240 },
  { k: "1d", label: "日线", limit: 200 },
];

function Chart({ symbol, interval, limit, orders }: { symbol: string; interval: string; limit: number; orders: Order[] }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>();
  const { data } = useApi<Kline[]>(`/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`, { poll: 60000 });
  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#7D889E", fontFamily: "Inter Variable, system-ui" },
      grid: { vertLines: { color: "rgba(255,255,255,0.03)" }, horzLines: { color: "rgba(255,255,255,0.03)" } },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.06)" },
      timeScale: { borderColor: "rgba(255,255,255,0.06)", timeVisible: interval !== "1d" },
      crosshair: { vertLine: { color: "rgba(94,242,196,0.3)" }, horzLine: { color: "rgba(94,242,196,0.3)" } },
    });
    chart.current = c;
    return () => c.remove();
  }, [interval]);
  useEffect(() => {
    const c = chart.current;
    if (!c || !data?.length) return;
    const s = c.addCandlestickSeries({
      upColor: "#5EF2C4", downColor: "#FF5C7A", borderVisible: false, wickUpColor: "#5EF2C4", wickDownColor: "#FF5C7A",
    });
    s.setData(data.map((k) => ({ time: Math.floor(k.t) as UTCTimestamp, open: k.o, high: k.h, low: k.l, close: k.c })));
    const step = data.length > 1 ? data[1].t - data[0].t : 3600;
    const start = data[0].t;
    const mine = orders.filter((o) => o.symbol === symbol && (o.status === "filled" || o.status === "submitted") && o.ts >= start)
      .map((o) => ({
        time: Math.floor(start + Math.floor((o.ts - start) / step) * step) as UTCTimestamp,
        position: o.side === "BUY" ? ("belowBar" as const) : ("aboveBar" as const),
        color: o.side === "BUY" ? "#5EF2C4" : "#FF5C7A",
        shape: o.side === "BUY" ? ("arrowUp" as const) : ("arrowDown" as const),
        text: `${o.side === "BUY" ? "买" : "卖"} ${usd(o.quote_usd, 0)}${o.mode === "paper" ? " 模拟" : ""}`,
      })).sort((a, b) => a.time - b.time);
    s.setMarkers(mine);
    c.timeScale().fitContent();
    return () => {
      try { c.removeSeries(s); } catch { /* chart already gone */ }
    };
  }, [data, orders, symbol]);
  return <div ref={el} className="h-[380px] w-full" />;
}

export default function Trades() {
  const ov = useApi<Overview>("/overview", { poll: 30000 });
  const orders = useApi<Order[]>("/orders?limit=300", { kinds: ["order"] });
  const symbols = Object.keys(ov.data?.market ?? { BTC: 1 }).map((t) => `${t}USDT`);
  const [sym, setSym] = useState("BTCUSDT");
  const [iv, setIv] = useState(INTERVALS[1]);
  const m = ov.data?.market?.[token(sym)];
  return (
    <div className="flex flex-col gap-5">
      <PageTitle title="交易" sub="K 线上的箭头是 BinanceClaw 的每一笔成交。" />
      <Card className="p-4 md:p-6">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <div className="relative inline-flex rounded-xl border border-white/[0.07] bg-white/[0.03] p-1">
            {symbols.map((s) => (
              <button key={s} onClick={() => setSym(s)} className="relative rounded-lg px-3 py-1.5 text-[13.5px] font-medium text-ink-soft">
                {sym === s && <motion.span layoutId="sym" className="absolute inset-0 rounded-lg bg-white/[0.08]" transition={{ type: "spring", stiffness: 380, damping: 32 }} />}
                <span className={`relative ${sym === s ? "text-ink" : ""}`}>{token(s)}</span>
              </button>
            ))}
          </div>
          <div className="relative inline-flex rounded-xl border border-white/[0.07] bg-white/[0.03] p-1">
            {INTERVALS.map((x) => (
              <button key={x.k} onClick={() => setIv(x)} className="relative rounded-lg px-3 py-1.5 text-[13px] text-ink-soft">
                {iv.k === x.k && <motion.span layoutId="iv" className="absolute inset-0 rounded-lg bg-white/[0.08]" />}
                <span className={`relative ${iv.k === x.k ? "text-ink" : ""}`}>{x.label}</span>
              </button>
            ))}
          </div>
          {m && (
            <span className="num ml-auto text-[15px]">
              {usd(m.price, m.price < 10 ? 4 : 2)}
              <span className={`ml-2 text-[13.5px] ${(m.change_7d ?? 0) >= 0 ? "text-mint" : "text-rose"}`}>7 日 {signedPct(m.change_7d)}</span>
            </span>
          )}
        </div>
        <Chart symbol={sym} interval={iv.k} limit={iv.limit} orders={orders.data ?? []} />
      </Card>

      <h2 className="mt-4 font-display text-[26px]">成交记录</h2>
      <Card className="overflow-x-auto">
        {orders.data?.length ? (
          <table className="w-full min-w-[640px] text-left text-[14px]">
            <thead className="text-[12.5px] text-ink-mute">
              <tr className="border-b border-white/[0.06]">
                <th className="px-5 py-3 font-medium">时间</th><th className="font-medium">方向</th><th className="font-medium">币种</th>
                <th className="text-right font-medium">金额</th><th className="text-right font-medium">数量</th><th className="text-right font-medium">均价</th>
                <th className="px-5 font-medium">方式</th><th className="pr-5 font-medium">状态</th>
              </tr>
            </thead>
            <tbody className="num">
              {orders.data.map((o) => (
                <tr key={o.id} className="border-b border-white/[0.04] last:border-none">
                  <td className="px-5 py-3 text-ink-soft">{clock(o.ts)}</td>
                  <td><SideTag side={o.side} /></td>
                  <td className="font-semibold">{token(o.symbol)}</td>
                  <td className="text-right">{usd(o.quote_usd)}</td>
                  <td className="text-right text-ink-soft">{o.qty ? o.qty.toPrecision(6) : "—"}</td>
                  <td className="text-right text-ink-soft">{o.price ? usd(o.price) : "—"}</td>
                  <td className="px-5 text-ink-soft">{o.via === "auto" ? "自动" : "已批准"}{o.mode === "paper" ? " · 模拟" : ""}</td>
                  <td className={`pr-5 ${o.status === "failed" ? "text-rose" : "text-ink-soft"}`}>{{ filled: "已成交", submitted: "已提交", failed: "失败" }[o.status] ?? o.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <Empty title="还没有成交" />}
      </Card>
    </div>
  );
}
