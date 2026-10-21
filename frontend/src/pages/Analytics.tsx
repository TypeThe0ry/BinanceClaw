import { useState } from "react";
import type { Analytics as AnalyticsResp, AnalyticsRange } from "../api";
import { AnalyticsNav } from "../components/AnalyticsNav";
import { DrawdownChart, EquityGrid, TradeGrid } from "../components/Metrics";
import { Empty, Page, SectionHead, Seg, Skeleton } from "../components/ui";
import { useApi } from "../lib";

const RANGES: { k: AnalyticsRange; label: string }[] = [
  { k: "7d", label: "7日" }, { k: "30d", label: "30天" }, { k: "90d", label: "90天" }, { k: "1y", label: "一年" }, { k: "all", label: "全部" },
];

export default function Analytics() {
  const [rng, setRng] = useState<AnalyticsRange>(() => {
    try { const v = localStorage.getItem("bclaw:analytics-range"); return RANGES.some((r) => r.k === v) ? (v as AnalyticsRange) : "30d"; } catch { return "30d"; }
  });
  const { data, error } = useApi<AnalyticsResp>(`/analytics?range=${rng}`, { kinds: ["order", "cycle", "futures", "mode"], poll: 300000 });
  const [kind, setKind] = useState<"all" | "spot" | "futures">("all");
  function pick(k: AnalyticsRange) {
    setRng(k);
    try { localStorage.setItem("bclaw:analytics-range", k); } catch { /* private mode */ }
  }
  const d = data?.range === rng ? data : null;
  const t = d ? (kind === "spot" ? d.spot : kind === "futures" ? d.futures : d.trades) : null;
  return (
    <Page className="flex flex-col gap-10">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <AnalyticsNav />
        <Seg id="analytics-range" value={rng} onChange={pick} items={RANGES} />
      </div>
      {error && !d ? <Empty text={`读取失败：${error}`} /> : !d ? <Skeleton className="h-64" /> : (
        <>
          <section>
            <SectionHead title="账户" />
            <div className="pt-5">
              {d.equity.points < 2 ? <Empty text="估值记录不足" /> : <EquityGrid e={d.equity} bench={d.benchmark.return} />}
            </div>
            {d.equity.points >= 2 && <div className="mt-6 h-40"><DrawdownChart points={d.drawdown} /></div>}
          </section>
          <section>
            <SectionHead title="已平仓交易" right={
              <Seg id="analytics-kind" value={kind} onChange={setKind}
                items={[{ k: "all" as const, label: "全部" }, { k: "spot" as const, label: "现货" }, { k: "futures" as const, label: "合约" }]} />
            } />
            <div className="pt-5">{t && t.trades ? <TradeGrid t={t} /> : <Empty text="这段时间没有平仓交易" />}</div>
          </section>
          {!!d.by_symbol.length && (
            <section>
              <SectionHead title="按币种" />
              <div className="overflow-x-auto">
                <table className="w-full min-w-[420px]">
                  <thead><tr className="border-b border-line"><th className="th">币种</th><th className="th text-right">笔数</th><th className="th text-right">胜率</th><th className="th text-right">盈亏</th></tr></thead>
                  <tbody>
                    {d.by_symbol.map((r) => (
                      <tr key={r.symbol} className="border-b border-line">
                        <td className="td font-semibold">{r.symbol}</td>
                        <td className="td num text-right">{r.trades}</td>
                        <td className="td num text-right">{((r.wins / r.trades) * 100).toFixed(0)}%</td>
                        <td className={`td num text-right ${r.pnl >= 0 ? "text-up" : "text-down"}`}>{r.pnl >= 0 ? "+" : ""}{r.pnl.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </Page>
  );
}
