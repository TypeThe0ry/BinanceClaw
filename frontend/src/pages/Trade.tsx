import { ColorType, CrosshairMode, createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { Approval, Kline, Order, Overview } from "../api";
import { Change, Empty, FlashNum, Page, Seg, Side, Tabs } from "../components/ui";
import { clock, fmtPrice, token, usd, useApi, useTicker } from "../lib";

type Iv = "15m" | "1h" | "4h" | "1d";
const IV: { k: Iv; label: string; limit: number }[] = [
  { k: "15m", label: "15分", limit: 300 }, { k: "1h", label: "1时", limit: 300 },
  { k: "4h", label: "4时", limit: 300 }, { k: "1d", label: "1日", limit: 300 },
];

function Chart({ symbol, iv, orders }: { symbol: string; iv: Iv; orders: Order[] }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>();
  const limit = IV.find((x) => x.k === iv)!.limit;
  const { data } = useApi<Kline[]>(`/klines?symbol=${symbol}&interval=${iv}&limit=${limit}`, { poll: 30000 });

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#000000" }, textColor: "#5C5C5C", fontSize: 11, fontFamily: "Inter Variable, system-ui" },
      grid: { vertLines: { color: "#111111" }, horzLines: { color: "#111111" } },
      rightPriceScale: { borderColor: "#1F1F1F", scaleMargins: { top: 0.08, bottom: 0.22 } },
      timeScale: { borderColor: "#1F1F1F", timeVisible: iv !== "1d", rightOffset: 6 },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { color: "#5C5C5C", labelBackgroundColor: "#2E2E2E" }, horzLine: { color: "#5C5C5C", labelBackgroundColor: "#2E2E2E" } },
    });
    chart.current = c;
    return () => c.remove();
  }, [iv]);

  useEffect(() => {
    const c = chart.current;
    if (!c || !data?.length) return;
    const candle = c.addCandlestickSeries({
      upColor: "#20B26C", downColor: "#EF454A", borderUpColor: "#20B26C", borderDownColor: "#EF454A",
      wickUpColor: "#20B26C", wickDownColor: "#EF454A", priceLineColor: "#8C8C8C",
    });
    const vol = c.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    c.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    candle.setData(data.map((k) => ({ time: Math.floor(k.t) as UTCTimestamp, open: k.o, high: k.h, low: k.l, close: k.c })));
    vol.setData(data.map((k) => ({ time: Math.floor(k.t) as UTCTimestamp, value: k.v, color: k.c >= k.o ? "rgba(32,178,108,0.35)" : "rgba(239,69,74,0.35)" })));
    const step = data.length > 1 ? data[1].t - data[0].t : 3600;
    const start = data[0].t;
    candle.setMarkers(orders
      .filter((o) => o.symbol === symbol && (o.status === "filled" || o.status === "submitted") && o.ts >= start)
      .map((o) => ({
        time: Math.floor(start + Math.floor((o.ts - start) / step) * step) as UTCTimestamp,
        position: o.side === "BUY" ? ("belowBar" as const) : ("aboveBar" as const),
        color: o.side === "BUY" ? "#20B26C" : "#EF454A",
        shape: o.side === "BUY" ? ("arrowUp" as const) : ("arrowDown" as const),
        text: usd(o.quote_usd, 0),
      })).sort((a, b) => a.time - b.time));
    c.timeScale().setVisibleLogicalRange({ from: Math.max(0, data.length - 120), to: data.length + 4 });
    return () => {
      try { c.removeSeries(candle); c.removeSeries(vol); } catch { /* chart disposed */ }
    };
  }, [data, orders, symbol]);

  return <div ref={el} className="h-[420px] w-full md:h-[520px]" />;
}

export default function Trade() {
  const ov = useApi<Overview>("/overview", { poll: 60000 });
  const orders = useApi<Order[]>("/orders?limit=500", { kinds: ["order"] });
  const approvals = useApi<Approval[]>("/approvals?limit=200", { kinds: ["approval", "approval_created"] });
  const tick = useTicker();
  const [params, setParams] = useSearchParams();
  const symbols = Object.keys(ov.data?.market ?? {}).map((t) => `${t}USDT`);
  const sym = params.get("s") ?? "BTCUSDT";
  const [iv, setIv] = useState<Iv>("4h");
  const [tab, setTab] = useState<"orders" | "approvals">("orders");
  const t = tick[sym];
  const mine = (orders.data ?? []).filter((o) => o.symbol === sym);

  return (
    <Page className="flex min-w-0 flex-col gap-6">
      {/* pair header */}
      <section className="flex min-w-0 flex-wrap items-center gap-x-8 gap-y-3 border-b border-line pb-4">
        <div className="no-scrollbar -mx-1 flex w-full gap-1 overflow-x-auto sm:mx-0 sm:w-auto">
          {(symbols.length ? symbols : ["BTCUSDT"]).map((s) => (
            <button key={s} onClick={() => setParams({ s })}
              className={`shrink-0 rounded-md px-2.5 py-1 text-[14px] font-semibold transition-colors ${s === sym ? "bg-hover text-fg" : "text-fg-2 hover:text-fg"}`}>
              {token(s)}<span className="font-normal text-fg-3">/USDT</span>
            </button>
          ))}
        </div>
        <div className="flex items-baseline gap-3">
          <span className={`text-[24px] font-semibold ${t && t.change_pct < 0 ? "text-down" : "text-up"}`}><FlashNum value={t?.price}>{fmtPrice(t?.price)}</FlashNum></span>
          <Change v={t?.change_pct} />
        </div>
        <dl className="flex gap-6 text-[12px]">
          <div><dt className="text-fg-3">24h 最高</dt><dd className="num mt-0.5 text-[13px]">{fmtPrice(t?.high)}</dd></div>
          <div><dt className="text-fg-3">24h 最低</dt><dd className="num mt-0.5 text-[13px]">{fmtPrice(t?.low)}</dd></div>
          <div><dt className="text-fg-3">24h 成交额</dt><dd className="num mt-0.5 text-[13px]">{t ? `${(t.quote_volume / 1e6).toFixed(2)}M` : "—"}</dd></div>
        </dl>
      </section>

      {/* chart + fills */}
      <section className="grid min-w-0 gap-6 lg:grid-cols-[1fr_300px]">
        <div className="min-w-0">
          <div className="mb-2"><Seg id="iv" items={IV.map((x) => ({ k: x.k, label: x.label }))} value={iv} onChange={setIv} /></div>
          <Chart symbol={sym} iv={iv} orders={orders.data ?? []} />
        </div>
        <aside>
          <p className="border-b border-line pb-2.5 text-[14px] font-semibold">AI 成交</p>
          <div className="grid grid-cols-3 py-2 text-[12px] text-fg-3"><span>时间</span><span className="text-right">金额</span><span className="text-right">价格</span></div>
          {mine.length ? mine.slice(0, 18).map((o) => (
            <div key={o.id} className="grid grid-cols-3 py-1.5 text-[13px]">
              <span className="num text-fg-2">{clock(o.ts).slice(6)}</span>
              <span className={`num text-right ${o.side === "BUY" ? "text-up" : "text-down"}`}>{usd(o.quote_usd, 0)}</span>
              <span className="num text-right">{o.price ? fmtPrice(o.price) : "—"}</span>
            </div>
          )) : <Empty text="暂无成交" />}
        </aside>
      </section>

      {/* history */}
      <section>
        <Tabs id="hist" value={tab} onChange={setTab} items={[{ k: "orders", label: "成交记录" }, { k: "approvals", label: "审批记录" }]} />
        <div className="overflow-x-auto">
          {tab === "orders" ? (
            orders.data?.length ? (
              <table className="w-full min-w-[720px]">
                <thead><tr className="border-b border-line">
                  <th className="th">时间</th><th className="th">交易对</th><th className="th">方向</th><th className="th text-right">金额</th>
                  <th className="th text-right">数量</th><th className="th text-right">均价</th><th className="th pl-6">来源</th><th className="th">状态</th>
                </tr></thead>
                <tbody>
                  {orders.data.map((o) => (
                    <tr key={o.id} className="border-b border-line">
                      <td className="td num text-fg-2">{clock(o.ts)}</td>
                      <td className="td font-semibold">{token(o.symbol)}/USDT</td>
                      <td className="td"><Side side={o.side} /></td>
                      <td className="td num text-right">{usd(o.quote_usd)}</td>
                      <td className="td num text-right text-fg-2">{o.qty ? o.qty.toPrecision(6) : "—"}</td>
                      <td className="td num text-right">{o.price ? fmtPrice(o.price) : "—"}</td>
                      <td className="td pl-6 text-fg-2">{o.via === "auto" ? "自动" : "批准"}{o.mode === "paper" ? " · 模拟" : ""}</td>
                      <td className={`td ${o.status === "failed" ? "text-down" : "text-fg-2"}`}>{{ filled: "已成交", submitted: "已提交", failed: "失败" }[o.status] ?? o.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <Empty text="暂无成交" />
          ) : approvals.data?.length ? (
            <table className="w-full min-w-[640px]">
              <thead><tr className="border-b border-line">
                <th className="th">时间</th><th className="th">交易对</th><th className="th">方向</th><th className="th text-right">金额</th><th className="th pl-6">结果</th><th className="th">处理</th>
              </tr></thead>
              <tbody>
                {approvals.data.map((a) => (
                  <tr key={a.id} className="border-b border-line">
                    <td className="td num text-fg-2">{clock(a.created)}</td>
                    <td className="td font-semibold">{token(a.symbol)}/USDT</td>
                    <td className="td"><Side side={a.side} /></td>
                    <td className="td num text-right">{usd(a.quote_usd, 0)}</td>
                    <td className={`td pl-6 ${{ approved: "text-up", rejected: "text-fg-2", expired: "text-fg-3", failed: "text-down", pending: "text-warn" }[a.status]}`}>
                      {{ approved: "已批准", rejected: "已拒绝", expired: "已过期", failed: "未执行", pending: "待处理" }[a.status]}
                    </td>
                    <td className="td text-fg-3">{{ web: "网页", telegram: "Telegram", system: "系统" }[a.resolved_by] ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="暂无审批" />}
        </div>
      </section>
    </Page>
  );
}
