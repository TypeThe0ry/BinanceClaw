import { ColorType, CrosshairMode, createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { post, type FPosition, type FuturesResp, type Kline } from "../api";
import { Empty, Page, SectionHead, Seg, Side, Skeleton, Tabs } from "../components/ui";
import { clock, fmtPrice, token, usd, useApi, useToast } from "../lib";

type Iv = "15m" | "1h" | "4h" | "1d";

const signed = (v: number | null | undefined, d = 2) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(d)}`);
const pnlCls = (v: number | null | undefined) => (v == null ? "text-fg-3" : v >= 0 ? "text-up" : "text-down");

function PerpChart({ symbol, iv, pos }: { symbol: string; iv: Iv; pos?: FPosition }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>();
  const { data } = useApi<Kline[]>(`/futures/klines?symbol=${symbol}&interval=${iv}&limit=300`, { poll: 30000 });

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#000000" }, textColor: "#5C5C5C", fontSize: 11, fontFamily: "Inter Variable, system-ui" },
      grid: { vertLines: { color: "#111111" }, horzLines: { color: "#111111" } },
      rightPriceScale: { borderColor: "#1F1F1F", scaleMargins: { top: 0.08, bottom: 0.08 } },
      timeScale: { borderColor: "#1F1F1F", timeVisible: iv !== "1d", rightOffset: 6 },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { color: "#5C5C5C", labelBackgroundColor: "#2E2E2E" }, horzLine: { color: "#5C5C5C", labelBackgroundColor: "#2E2E2E" } },
    });
    chart.current = c;
    return () => c.remove();
  }, [iv]);

  useEffect(() => {
    const c = chart.current;
    if (!c || !data?.length) return;
    const s = c.addCandlestickSeries({
      upColor: "#20B26C", downColor: "#EF454A", borderUpColor: "#20B26C", borderDownColor: "#EF454A",
      wickUpColor: "#20B26C", wickDownColor: "#EF454A", priceLineColor: "#8C8C8C",
    });
    s.setData(data.map((k) => ({ time: Math.floor(k.t) as UTCTimestamp, open: k.o, high: k.h, low: k.l, close: k.c })));
    if (pos) {
      s.createPriceLine({ price: pos.entry, color: "#8C8C8C", lineWidth: 1, lineStyle: 0, axisLabelVisible: true, title: "开仓" });
      s.createPriceLine({ price: pos.stop, color: "#EF454A", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "止损" });
      if (pos.take_profit) s.createPriceLine({ price: pos.take_profit, color: "#20B26C", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "止盈" });
      if (pos.liq_est) s.createPriceLine({ price: pos.liq_est, color: "#F0A23B", lineWidth: 1, lineStyle: 3, axisLabelVisible: true, title: "强平≈" });
    }
    c.timeScale().setVisibleLogicalRange({ from: Math.max(0, data.length - 120), to: data.length + 4 });
    return () => { try { c.removeSeries(s); } catch { /* disposed */ } };
  }, [data, pos]);

  return <div ref={el} className="h-[340px] w-full md:h-[420px]" />;
}

export default function Futures() {
  const { data, reload } = useApi<FuturesResp>("/futures", { poll: 5000, kinds: ["futures", "approval", "settings", "paper_reset"] });
  const toast = useToast();
  const [sel, setSel] = useState<string | null>(null);
  const [iv, setIv] = useState<Iv>("1h");
  const [tab, setTab] = useState<"closed" | "trades">("closed");
  const [busy, setBusy] = useState<string | null>(null);
  if (!data) return <Skeleton className="h-96" />;
  const syms = (data.settings.symbols as string[] | undefined) ?? ["BTC"];
  const symbol = sel ?? data.positions[0]?.symbol ?? `${syms[0] ?? "BTC"}USDT`;
  const pos = data.positions.find((p) => p.symbol === symbol);

  async function close(p: FPosition, fraction: number) {
    if (!window.confirm(`${fraction >= 1 ? "平掉" : "减仓 50%"} ${token(p.symbol)} ${p.side === "LONG" ? "多单" : "空单"}？`)) return;
    setBusy(p.id + fraction);
    try {
      const r = await post<{ ok: boolean; message: string }>(`/futures/positions/${p.id}/close`, { fraction });
      toast(r.message, r.ok ? "ok" : "err");
    } catch (e) { toast((e as Error).message, "err"); }
    setBusy(null);
    reload();
  }
  async function flatten() {
    if (!window.confirm("平掉 BinanceClaw 开的全部合约仓位？")) return;
    const r = await post<{ ok: boolean; message: string }>("/futures/flatten");
    toast(r.message);
    reload();
  }

  return (
    <Page className="flex min-w-0 flex-col gap-8">
      <section className="flex flex-wrap items-end justify-between gap-6">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-[20px] font-semibold">USDⓈ-M 永续</h1>
            {!data.enabled && <Link to="/settings#futures" className="tag bg-hover text-fg-2">未开启</Link>}
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-x-10 gap-y-3 text-[13px] sm:grid-cols-4">
            <div><dt className="text-fg-3">合约权益</dt><dd className="num mt-1 text-[18px] font-semibold">{usd(data.equity)}</dd></div>
            <div><dt className="text-fg-3">占用保证金</dt><dd className="num mt-1 text-[18px]">{usd(data.margin_used)}</dd></div>
            <div><dt className="text-fg-3">未实现盈亏</dt><dd className={`num mt-1 text-[18px] ${pnlCls(data.unrealized)}`}>{signed(data.unrealized)}</dd></div>
            <div><dt className="text-fg-3">已实现盈亏</dt><dd className={`num mt-1 text-[18px] ${pnlCls(data.realized)}`}>{signed(data.realized)}</dd></div>
          </dl>
        </div>
        {!!data.positions.length && <button className="btn-down" onClick={flatten}>全部平仓</button>}
      </section>
      {data.error && <p className="text-[13px] text-down">{data.error}</p>}

      <section className="min-w-0">
        <div className="mb-2 flex flex-wrap items-center gap-3">
          <div className="no-scrollbar flex gap-1 overflow-x-auto">
            {Array.from(new Set([...data.positions.map((p) => p.symbol), ...syms.map((t) => `${t}USDT`)])).map((s) => (
              <button key={s} onClick={() => setSel(s)}
                className={`shrink-0 rounded-md px-2.5 py-1 text-[14px] font-semibold ${s === symbol ? "bg-hover text-fg" : "text-fg-2 hover:text-fg"}`}>
                {token(s)}<span className="font-normal text-fg-3">USDT 永续</span>
              </button>
            ))}
          </div>
          <div className="ml-auto"><Seg id="fiv" value={iv} onChange={setIv}
            items={[{ k: "15m" as const, label: "15分" }, { k: "1h" as const, label: "1时" }, { k: "4h" as const, label: "4时" }, { k: "1d" as const, label: "1日" }]} /></div>
        </div>
        <PerpChart symbol={symbol} iv={iv} pos={pos} />
      </section>

      <section>
        <SectionHead title={<>持仓 <span className="num text-fg-2">{data.positions.length}</span></>} doc="futures" />
        <div className="overflow-x-auto">
          {data.positions.length ? (
            <table className="w-full min-w-[900px]">
              <thead><tr className="border-b border-line">
                <th className="th">合约</th><th className="th text-right">数量</th><th className="th text-right">开仓价</th><th className="th text-right">标记价</th>
                <th className="th text-right">强平价≈</th><th className="th text-right">保证金</th><th className="th text-right">未实现盈亏</th>
                <th className="th text-right">止损 / 止盈</th><th className="th text-right">操作</th>
              </tr></thead>
              <tbody>
                {data.positions.map((p) => (
                  <tr key={p.id} onClick={() => setSel(p.symbol)} className={`cursor-pointer border-b border-line ${p.symbol === symbol ? "bg-raise" : ""}`}>
                    <td className="td"><span className="flex items-center gap-2"><b className="font-semibold">{token(p.symbol)}</b><Side side={p.side} />
                      <span className="tag bg-hover text-fg-2">逐仓 {p.leverage}x</span>{p.status === "pending" && <span className="tag bg-hover text-warn">对账中</span>}</span></td>
                    <td className="td num text-right">{p.qty}</td>
                    <td className="td num text-right">{fmtPrice(p.entry)}</td>
                    <td className="td num text-right">{fmtPrice(p.mark)}</td>
                    <td className="td num text-right text-warn">{p.liq_est ? fmtPrice(p.liq_est) : "—"}</td>
                    <td className="td num text-right">{usd(p.margin)}</td>
                    <td className={`td num text-right ${pnlCls(p.upnl)}`}>{signed(p.upnl)} <span className="text-[12px]">({signed((p.roe ?? 0) * 100, 1)}%)</span></td>
                    <td className="td num text-right"><span className="text-down">{fmtPrice(p.stop)}</span><span className="text-fg-3"> / </span>
                      <span className="text-up">{p.take_profit ? fmtPrice(p.take_profit) : "—"}</span></td>
                    <td className="td text-right" onClick={(e) => e.stopPropagation()}>
                      <span className="inline-flex gap-1.5">
                        <button className="btn-secondary btn-sm" disabled={!!busy} onClick={() => close(p, 0.5)}>减半</button>
                        <button className="btn-secondary btn-sm" disabled={!!busy} onClick={() => close(p, 1)}>{busy === p.id + 1 ? "…" : "平仓"}</button>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="没有持仓" />}
        </div>
        {pos && (
          <dl className="mt-4 grid gap-3 text-[14px] leading-relaxed sm:grid-cols-2">
            <div><dt className="text-[12px] text-fg-3">开仓理由</dt><dd className="mt-1">{pos.reason || "—"}</dd></div>
            <div><dt className="text-[12px] text-fg-3">判断错了的信号</dt><dd className="mt-1">{pos.invalidation || "—"}</dd></div>
          </dl>
        )}
      </section>

      <section>
        <Tabs id="fhist" value={tab} onChange={setTab} items={[{ k: "closed", label: "历史仓位" }, { k: "trades", label: "成交与事件" }]} />
        <div className="overflow-x-auto">
          {tab === "closed" ? (data.closed.length ? (
            <table className="w-full min-w-[820px]">
              <thead><tr className="border-b border-line">
                <th className="th">平仓时间</th><th className="th">合约</th><th className="th text-right">开仓价</th><th className="th text-right">平仓价</th>
                <th className="th text-right">保证金</th><th className="th text-right">盈亏</th><th className="th pl-6">原因</th>
              </tr></thead>
              <tbody>{data.closed.map((c) => (
                <tr key={c.id} className="border-b border-line">
                  <td className="td num text-fg-2">{c.closed ? clock(c.closed) : "—"}</td>
                  <td className="td"><span className="flex items-center gap-2"><b>{token(c.symbol)}</b><Side side={c.side} /><span className="text-fg-3">{c.leverage}x</span></span></td>
                  <td className="td num text-right">{fmtPrice(c.entry)}</td>
                  <td className="td num text-right">{c.close_price ? fmtPrice(c.close_price) : "—"}</td>
                  <td className="td num text-right">{usd(c.margin)}</td>
                  <td className={`td num text-right ${pnlCls(c.realized)}`}>{signed(c.realized)}</td>
                  <td className="td pl-6 text-fg-2">{c.close_reason}</td>
                </tr>
              ))}</tbody>
            </table>
          ) : <Empty text="暂无历史仓位" />) : (data.trades.length ? (
            <table className="w-full min-w-[820px]">
              <thead><tr className="border-b border-line">
                <th className="th">时间</th><th className="th">合约</th><th className="th">动作</th><th className="th text-right">数量</th>
                <th className="th text-right">价格</th><th className="th text-right">盈亏</th><th className="th pl-6">来源</th><th className="th">状态</th>
              </tr></thead>
              <tbody>{data.trades.map((t) => (
                <tr key={t.id} className="border-b border-line">
                  <td className="td num text-fg-2">{clock(t.ts)}</td>
                  <td className="td font-semibold">{token(t.symbol)}</td>
                  <td className="td"><Side side={t.action} /></td>
                  <td className="td num text-right text-fg-2">{t.qty || "—"}</td>
                  <td className="td num text-right">{t.price ? fmtPrice(t.price) : "—"}</td>
                  <td className={`td num text-right ${t.pnl ? pnlCls(t.pnl) : "text-fg-3"}`}>{t.pnl ? signed(t.pnl) : "—"}</td>
                  <td className="td pl-6 text-fg-2">{{ auto: "自动", approved: "批准", manual: "手动", exchange: "交易所" }[t.via] ?? t.via}{t.mode === "paper" ? " · 模拟" : ""}</td>
                  <td className={`td ${t.status === "failed" ? "text-down" : "text-fg-2"}`}>{{ filled: "完成", failed: "失败", unknown: "未知" }[t.status] ?? t.status}</td>
                </tr>
              ))}</tbody>
            </table>
          ) : <Empty text="暂无记录" />)}
        </div>
      </section>
    </Page>
  );
}
