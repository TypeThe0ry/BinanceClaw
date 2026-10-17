import { ArrowClockwise, Pause, Play } from "@phosphor-icons/react";
import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import { post, type Approval, type CycleRow, type Overview as OV } from "../api";
import { AllocationLegend, AllocationRing } from "../components/Allocation";
import { ApprovalCard } from "../components/ApprovalCard";
import { CycleCard } from "../components/CycleCard";
import { Card, Empty, Rolling, Skeleton } from "../components/ui";
import { clock, signedPct, usd, useApi, useToast } from "../lib";

const stagger = { hidden: {}, show: { transition: { staggerChildren: 0.06 } } };
const item = { hidden: { opacity: 0, y: 12 }, show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: [0.22, 1, 0.36, 1] } } };

function NextCycle({ at }: { at: number }) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(id);
  }, []);
  const s = Math.max(0, at - now);
  if (!at) return <span>—</span>;
  return <span className="num">{s < 1 ? "进行中" : `${Math.floor(s / 60)} 分 ${Math.floor(s % 60)} 秒后`}</span>;
}

export default function Overview() {
  const ov = useApi<OV>("/overview", { poll: 30000 });
  const eq = useApi<{ t: number; total: number }[]>("/equity", { kinds: ["cycle"] });
  const cycles = useApi<CycleRow[]>("/cycles?limit=3", { kinds: ["cycle", "cycle_started", "approval"] });
  const pending = useApi<Approval[]>("/approvals?status=pending", { kinds: ["approval", "approval_created"] });
  const toast = useToast();
  const [running, setRunning] = useState(false);
  const o = ov.data;

  async function runNow() {
    setRunning(true);
    await post("/cycles/run");
    toast("已让 AI 开始新一轮决策，完成后会出现在这里");
    window.setTimeout(() => setRunning(false), 4000);
  }
  async function togglePause() {
    await post(o?.paused ? "/control/resume" : "/control/pause");
    ov.reload();
  }

  const series = (eq.data ?? []).filter((p) => p.total != null);
  const first = series[0]?.total;
  const change = series.length > 1 && o?.total && first ? o.total / first - 1 : null;

  return (
    <motion.div variants={stagger} initial="hidden" animate="show" className="flex flex-col gap-5">
      {/* pending approvals first: the one thing that needs you */}
      <AnimatePresence mode="popLayout">
        {(pending.data ?? []).map((a) => (
          <ApprovalCard key={a.id} a={a} onDone={pending.reload} />
        ))}
      </AnimatePresence>

      {/* hero */}
      <motion.section variants={item} className="grid gap-5 lg:grid-cols-[1.15fr_1fr]">
        <Card className="relative overflow-hidden p-6 md:p-8">
          <div className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-mint/[0.07] blur-3xl" />
          <p className="label">总资产（{o?.mode === "live" ? "实盘" : "模拟盘"}）</p>
          {o?.total != null ? (
            <div className="mt-2 flex flex-wrap items-end gap-x-4 gap-y-1">
              <span className="font-display text-[52px] leading-none text-ink md:text-[68px]">
                <Rolling value={o.total} format={(v) => usd(v)} />
              </span>
              {change != null && (
                <span className={`num mb-2 text-[15px] ${change >= 0 ? "text-mint" : "text-rose"}`}>
                  {signedPct(change, 2)} <span className="text-ink-mute">自首次记录</span>
                </span>
              )}
            </div>
          ) : ov.error ? (
            <p className="mt-3 text-rose">{ov.error}</p>
          ) : (
            <Skeleton className="mt-3 h-16 w-72" />
          )}
          <div className={`mt-6 ${series.length > 1 ? "h-[110px]" : ""}`}>
            {series.length > 1 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={series} margin={{ top: 4, left: 0, right: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="eqfill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#5EF2C4" stopOpacity={0.28} />
                      <stop offset="100%" stopColor="#5EF2C4" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <YAxis domain={["dataMin", "dataMax"]} hide />
                  <Tooltip cursor={{ stroke: "rgba(255,255,255,0.15)" }}
                    contentStyle={{ background: "#0d1626", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 12, fontSize: 13 }}
                    labelFormatter={(_, p) => (p?.[0] ? clock(p[0].payload.t) : "")} formatter={(v: number) => [usd(v), "总资产"]} />
                  <Area type="monotone" dataKey="total" stroke="#5EF2C4" strokeWidth={1.8} fill="url(#eqfill)" isAnimationActive />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-[13px] text-ink-mute">AI 每跑一轮记录一次资产，两轮之后这里会出现曲线。</p>
            )}
          </div>
          <dl className="mt-5 grid grid-cols-2 gap-4 border-t border-white/[0.06] pt-5 text-[14px] sm:grid-cols-4">
            <div><dt className="label">今日成交</dt><dd className="num mt-1">{o ? `${o.today.orders} / ${o.today.max_orders} 笔` : "—"}</dd></div>
            <div><dt className="label">今日金额</dt><dd className="num mt-1">{o ? `${usd(o.today.notional, 0)} / ${usd(o.today.max_notional, 0)}` : "—"}</dd></div>
            <div><dt className="label">BTC 7 日</dt><dd className={`num mt-1 ${o?.btc_change_7d != null && o.btc_change_7d < -0.15 ? "text-amber" : ""}`}>{signedPct(o?.btc_change_7d)}</dd></div>
            <div><dt className="label">下一轮</dt><dd className="mt-1">{o ? <NextCycle at={o.next_cycle_at} /> : "—"}</dd></div>
          </dl>
        </Card>

        <Card className="flex flex-col items-center gap-6 p-6 sm:flex-row md:p-8">
          {o?.category_weights ? (
            <>
              <div className="relative">
                <AllocationRing weights={o.category_weights} targets={o.targets} />
                <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                  <span className="text-[12px] text-ink-mute">外圈实际</span>
                  <span className="text-[12px] text-ink-faint">内圈目标</span>
                </div>
              </div>
              <AllocationLegend weights={o.category_weights} targets={o.targets} />
            </>
          ) : (
            <Skeleton className="h-[184px] w-full" />
          )}
        </Card>
      </motion.section>

      {/* AI */}
      <motion.section variants={item}>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="font-display text-[26px] leading-tight">AI 最近在做什么</h2>
          <div className="flex gap-2">
            <button className="btn-ghost" onClick={togglePause}>
              {o?.paused ? <><Play size={16} weight="fill" />恢复</> : <><Pause size={16} weight="fill" />暂停</>}
            </button>
            <button className="btn-primary" onClick={runNow} disabled={running || o?.paused}>
              <ArrowClockwise size={16} weight="bold" className={running ? "animate-spin" : ""} />
              {running ? "思考中…" : "立即跑一轮"}
            </button>
          </div>
        </div>
        {o?.paused && (
          <p className="mb-3 rounded-xl border border-amber/30 bg-amber/10 px-4 py-3 text-[14px] text-amber">
            已暂停：{o.pause_reason}。暂停期间不调用模型、不交易。
          </p>
        )}
        <div className="grid gap-4">
          {cycles.data?.length ? cycles.data.map((c) => <CycleCard key={c.id} c={c} compact />) :
            cycles.loading ? <Skeleton className="h-40" /> :
              <Card><Empty title="还没有决策记录" hint="点「立即跑一轮」，或等待定时任务。" /></Card>}
        </div>
      </motion.section>
    </motion.div>
  );
}
