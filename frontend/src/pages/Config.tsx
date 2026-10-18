import { useState } from "react";
import { post } from "../api";
import AllocationEditor from "../components/AllocationEditor";
import { type Alloc, type FuturesCfg, Num, Row, SettingsNav, useSettingsDraft } from "../components/settingsKit";
import TokenPicker from "../components/TokenPicker";
import { Page, Seg, SectionHead, Skeleton } from "../components/ui";

const UNIVERSE_HINT: Record<Alloc["universe"], string> = {
  all: "币安全部现货资产都可以交易。每轮把你的持仓和成交额最高的资产交给 AI 判断。",
  whitelist: "只交易下面选中的资产。",
  blacklist: "除黑名单外的币安现货资产都可以交易。",
};
const toggle = (list: string[], t: string) => (list.includes(t) ? list.filter((x) => x !== t) : [...list, t]);

function Chips({ tokens, on, onToggle }: { tokens: string[]; on: string[]; onToggle: (t: string) => void }) {
  return (
    <div className="mt-2.5 flex flex-wrap gap-2">
      {tokens.map((t) => (
        <button key={t} onClick={() => onToggle(t)}
          className={`rounded-md border px-2.5 py-1 text-[13px] font-medium transition-colors ${on.includes(t) ? "border-fg bg-fg text-bg" : "border-line-strong text-fg-2 hover:text-fg"}`}>
          {t}
        </button>
      ))}
    </div>
  );
}

const FUT_LIMITS: { k: keyof FuturesCfg; label: string; unit: string; pct?: boolean }[] = [
  { k: "max_leverage", label: "最大杠杆", unit: "x" },
  { k: "max_position_margin_usd", label: "单仓保证金上限", unit: "USD" },
  { k: "max_total_margin_usd", label: "总保证金上限", unit: "USD" },
  { k: "max_loss_per_trade_usd", label: "单笔止损最大亏损", unit: "USD" },
  { k: "max_stop_distance", label: "止损最远距离", unit: "%", pct: true },
  { k: "max_opens_per_day", label: "每日开仓次数", unit: "次" },
];

const PRESET_LABEL: Record<string, string> = { conservative: "保守", balanced: "均衡", aggressive: "积极", custom: "自定义" };

const LIMITS: { k: string; label: string; unit: string; pct?: boolean }[] = [
  { k: "max_order_usd", label: "单笔上限", unit: "USD" },
  { k: "auto_execute_max_usd", label: "单笔自动执行", unit: "USD" },
  { k: "max_auto_daily_usd", label: "每日自动执行", unit: "USD" },
  { k: "max_orders_per_day", label: "每日笔数", unit: "笔" },
  { k: "max_daily_notional_usd", label: "每日金额", unit: "USD" },
  { k: "stable_floor", label: "稳定币下限", unit: "%", pct: true },
  { k: "btc_7d_drop_pause", label: "BTC 7 日暂停线", unit: "%", pct: true },
];

/** 配置: how the AI invests (allocation, targets, capital, risk limits, futures). System settings live on 设置. */
export default function Config() {
  const { data, reload, d, setD, save, toast } = useSettingsDraft();
  const [lockAsset, setLockAsset] = useState("");
  const [lockAmt, setLockAmt] = useState("");
  if (!data || !d) return <Skeleton className="h-96" />;
  const b = data.bounds;
  const lim = d.limits;
  const al = d.allocation;
  const setLim = (k: string, v: number | boolean) => setD({ ...d, limits: { ...lim, [k]: v } });

  return (
    <Page className="mx-auto flex max-w-[820px] flex-col gap-10">
      <SettingsNav current="/config" />

      <section>
        <SectionHead title="风险预设" doc="risk-presets" />
        <Row label="预设">
          <Seg id="preset" value={(d.risk_preset in PRESET_LABEL ? d.risk_preset : "custom") as string}
            items={["conservative", "balanced", "aggressive", "custom"].map((k) => ({ k, label: PRESET_LABEL[k] }))}
            onChange={async (k) => {
              if (k === "custom") return;
              try { await post("/risk-preset", { name: k }); toast(`已套用：${PRESET_LABEL[k]}`); reload(); } catch (e) { toast((e as Error).message, "err"); }
            }} />
        </Row>
      </section>

      <section>
        <SectionHead title="资产与分配" doc="capital-and-allocation" />
        <Row label="分配方式">
          <Seg id="alloc" value={d.allocation.mode} items={[{ k: "ai" as const, label: "AI 自主分配" }, { k: "targets" as const, label: "手动分配" }]}
            onChange={(k) => setD({ ...d, allocation: { ...al, mode: k } })} />
        </Row>
        {al.mode === "ai" ? (
          <>
            <Row label="可交易范围">
              <Seg id="universe" value={al.universe}
                items={[{ k: "all" as const, label: "全部资产" }, { k: "whitelist" as const, label: "白名单" }, { k: "blacklist" as const, label: "黑名单" }]}
                onChange={(k) => setD({ ...d, allocation: { ...al, universe: k } })} />
            </Row>
            <p className="border-b border-line py-3 text-[13px] text-fg-3">{UNIVERSE_HINT[al.universe]}AI 按自己的判断分配比例，并说明配置思路。</p>
            {al.universe === "whitelist" && (
              <div className="border-b border-line py-3">
                <p className="text-[14px] text-fg-2">白名单</p>
                <Chips tokens={Array.from(new Set([...data.candidates, ...al.enabled]))} on={al.enabled}
                  onToggle={(t) => setD({ ...d, allocation: { ...al, enabled: toggle(al.enabled, t) } })} />
                <div className="mt-3">
                  <TokenPicker selected={al.enabled} onToggle={(t) => setD({ ...d, allocation: { ...al, enabled: toggle(al.enabled, t) } })} />
                </div>
              </div>
            )}
            {al.universe === "blacklist" && (
              <div className="border-b border-line py-3">
                <p className="text-[14px] text-fg-2">黑名单 <span className="text-fg-3">· AI 永远不会买卖这些资产</span></p>
                {al.blocked.length > 0
                  ? <Chips tokens={al.blocked} on={al.blocked} onToggle={(t) => setD({ ...d, allocation: { ...al, blocked: toggle(al.blocked, t) } })} />
                  : <p className="mt-2 text-[13px] text-fg-3">还没有，搜索添加</p>}
                <div className="mt-3">
                  <TokenPicker selected={al.blocked} placeholder="搜索要排除的资产"
                    onToggle={(t) => setD({ ...d, allocation: { ...al, blocked: toggle(al.blocked, t) } })} />
                </div>
              </div>
            )}
            {al.universe !== "whitelist" && (
              <Row label="每轮交给 AI 的资产数">
                <Num unit="个" value={al.ai_candidates} step={1} min={5} max={30}
                  onChange={(x) => setD({ ...d, allocation: { ...al, ai_candidates: x } })} />
              </Row>
            )}
            <Row label="单币占比上限">
              <Num unit="%" value={Math.round(al.max_asset_weight * 1000) / 10} step={1} min={5} max={100}
                onChange={(x) => setD({ ...d, allocation: { ...al, max_asset_weight: x / 100 } })} />
            </Row>
          </>
        ) : (
          <>
            <p className="border-b border-line py-3 text-[13px] text-fg-3">你在下方「目标配置」里设定每个资产的比例，系统把持仓维持在这些比例附近。</p>
            <Row label="自动再平衡">
              <input type="checkbox" className="h-4 w-4 accent-white" checked={al.auto_rebalance} aria-label="自动再平衡"
                onChange={(e) => setD({ ...d, allocation: { ...al, auto_rebalance: e.target.checked } })} />
            </Row>
            {al.auto_rebalance && (
              <Row label={<>偏离阈值 <span className="text-fg-3">· 实际比例偏离目标超过这么多就调回</span></>}>
                <Num unit="%" value={Math.round(al.rebalance_drift * 1000) / 10} step={0.5} min={1} max={50}
                  onChange={(x) => setD({ ...d, allocation: { ...al, rebalance_drift: x / 100 } })} />
              </Row>
            )}
            <div className="border-b border-line py-3">
              <p className="text-[14px] text-fg-2">可交易资产</p>
              <Chips tokens={Array.from(new Set([...data.candidates, ...al.enabled]))} on={al.enabled}
                onToggle={(t) => setD({ ...d, allocation: { ...al, enabled: toggle(al.enabled, t) } })} />
              <div className="mt-3">
                <TokenPicker selected={al.enabled} onToggle={(t) => setD({ ...d, allocation: { ...al, enabled: toggle(al.enabled, t) } })} />
              </div>
            </div>
          </>
        )}
        <div className="pt-4"><button className="btn-primary" onClick={() => save({ allocation: d.allocation })}>保存</button></div>
      </section>

      <section>
        <SectionHead title="目标配置" doc="capital-and-allocation" />
        <AllocationEditor key={JSON.stringify([data.settings.categories, data.settings.investor_notes])} categories={data.settings.categories}
          enabled={data.settings.allocation.enabled} notes={data.settings.investor_notes} stables={d.stables}
          mode={data.settings.allocation.mode} onSaved={reload} />
      </section>

      <section>
        <SectionHead title="资金管理" doc="capital-and-allocation" />
        <Row label={<>AI 投入预算 <span className="num text-fg-3">· 已投入 ${data.deployed_usd.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span></>}>
          <label className="flex h-9 items-center rounded-lg bg-field pr-3">
            <input type="number" className="num h-9 w-28 bg-transparent px-3 text-right text-[14px] focus:outline-none" placeholder="不限"
              value={d.capital.budget_usd ?? ""} min={0}
              onChange={(e) => setD({ ...d, capital: { ...d.capital, budget_usd: e.target.value === "" ? null : parseFloat(e.target.value) } })} />
            <span className="w-8 text-[12px] text-fg-3">USD</span>
          </label>
        </Row>
        {Object.entries(d.capital.locked).map(([a, amt]) => (
          <Row key={a} label={<>锁定 <b className="text-fg">{a}</b></>}>
            <span className="num text-[14px]">{amt}</span>
            <button className="btn-secondary btn-sm" onClick={() => { const l = { ...d.capital.locked }; delete l[a]; setD({ ...d, capital: { ...d.capital, locked: l } }); }}>移除</button>
          </Row>
        ))}
        <form className="flex flex-wrap items-center gap-2 border-b border-line py-3" onSubmit={(e) => {
          e.preventDefault();
          const a = lockAsset.trim().toUpperCase(); const v = parseFloat(lockAmt);
          if (a && v > 0) setD({ ...d, capital: { ...d.capital, locked: { ...d.capital.locked, [a]: v } } });
          setLockAsset(""); setLockAmt("");
        }}>
          <span className="mr-auto text-[14px] text-fg-2">添加锁定</span>
          <input className="input h-9 w-24" placeholder="BTC" value={lockAsset} onChange={(e) => setLockAsset(e.target.value)} />
          <input className="input num h-9 w-28" type="number" step="any" min={0} placeholder="数量" value={lockAmt} onChange={(e) => setLockAmt(e.target.value)} />
          <button className="btn-secondary h-9">添加</button>
        </form>
        <div className="pt-4"><button className="btn-primary" onClick={() => save({ capital: d.capital })}>保存</button></div>
      </section>

      <section>
        <SectionHead title="风控" doc="decisions-and-risk" />
        {LIMITS.map((f) => {
          const v = lim[f.k] as number;
          const [lo, hi] = b[f.k] ?? [undefined, undefined];
          return (
            <Row key={f.k} label={f.label}>
              <Num unit={f.unit} value={f.pct ? Math.round(v * 1000) / 10 : v} step={f.pct ? 0.5 : 1}
                min={lo != null ? (f.pct ? lo * 100 : lo) : undefined} max={hi != null ? (f.pct ? hi * 100 : hi) : undefined}
                onChange={(x) => setLim(f.k, f.pct ? x / 100 : x)} />
            </Row>
          );
        })}
        <Row label="允许卖出">
          <input type="checkbox" className="h-4 w-4 accent-white" checked={!!lim.allow_sell} onChange={(e) => setLim("allow_sell", e.target.checked)} />
        </Row>
        <div className="pt-4"><button className="btn-primary" onClick={() => save({ limits: lim })}>保存</button></div>
      </section>

      <section id="futures">
        <SectionHead title="合约" doc="futures" />
        <Row label="USDⓈ-M 永续合约">
          <input type="checkbox" className="h-4 w-4 accent-white" checked={d.futures.enabled}
            onChange={(e) => setD({ ...d, futures: { ...d.futures, enabled: e.target.checked } })} />
        </Row>
        <div className="border-b border-line py-3">
          <p className="text-[14px] text-fg-2">可交易合约</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            {d.futures.symbols.map((t) => (
              <button key={t} onClick={() => setD({ ...d, futures: { ...d.futures, symbols: d.futures.symbols.filter((x) => x !== t) } })}
                className="rounded-md border border-fg bg-fg px-2.5 py-1 text-[13px] font-medium text-bg">{t}</button>
            ))}
          </div>
          <div className="mt-3">
            <TokenPicker endpoint="/futures/markets" placeholder="搜索 USDT 永续合约" selected={d.futures.symbols}
              onToggle={(t) => setD({ ...d, futures: { ...d.futures, symbols: d.futures.symbols.includes(t) ? d.futures.symbols.filter((x) => x !== t) : [...d.futures.symbols, t] } })} />
          </div>
        </div>
        {FUT_LIMITS.map((f) => {
          const v = d.futures[f.k] as number;
          const [lo, hi] = b[f.k] ?? [undefined, undefined];
          return (
            <Row key={f.k} label={f.label}>
              <Num unit={f.unit} value={f.pct ? Math.round(v * 1000) / 10 : v} step={f.pct ? 0.5 : 1}
                min={lo != null ? (f.pct ? lo * 100 : lo) : undefined} max={hi != null ? (f.pct ? hi * 100 : hi) : undefined}
                onChange={(x) => setD({ ...d, futures: { ...d.futures, [f.k]: f.pct ? x / 100 : x } })} />
            </Row>
          );
        })}
        <Row label="允许做空">
          <input type="checkbox" className="h-4 w-4 accent-white" checked={d.futures.allow_short}
            onChange={(e) => setD({ ...d, futures: { ...d.futures, allow_short: e.target.checked } })} />
        </Row>
        <Row label="小额自动开仓">
          <input type="checkbox" className="h-4 w-4 accent-[#F0A23B]" checked={d.futures.auto_open}
            onChange={(e) => setD({ ...d, futures: { ...d.futures, auto_open: e.target.checked } })} />
          {d.futures.auto_open && <Num unit="USD" value={d.futures.auto_open_max_margin_usd} min={0}
            onChange={(x) => setD({ ...d, futures: { ...d.futures, auto_open_max_margin_usd: x } })} />}
        </Row>
        <div className="pt-4"><button className="btn-primary" onClick={() => save({ futures: d.futures })}>保存</button></div>
      </section>
    </Page>
  );
}
