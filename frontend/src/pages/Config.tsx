import { useState } from "react";
import { post } from "../api";
import AllocationEditor from "../components/AllocationEditor";
import { type Alloc, type ApprovalFree, type FuturesCfg, Num, type Outlook, Row, SettingsNav, type Style,
  useSettingsDraft } from "../components/settingsKit";
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

const STYLE_HINT: Record<Style, string> = {
  short: "持仓数小时到数天，AI 每 5–60 分钟复查，入场离场更快。",
  medium: "持仓数天到数周，看日线趋势和支撑阻力，按「设置 → 节奏」里的间隔复查。",
  long: "持仓数月以上，分批建仓、少交易，AI 每 2–24 小时复查。",
};
const VIEW_LABEL: Record<number, string> = { [-2]: "强烈看空", [-1]: "偏空", 0: "中性", 1: "偏多", 2: "强烈看多" };

function ViewDial({ value, onChange, label }: { value: number; onChange: (v: number) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="flex overflow-hidden rounded-lg border border-line-strong">
      {[-2, -1, 0, 1, 2].map((v) => (
        <button key={v} role="radio" aria-checked={value === v} onClick={() => onChange(v)}
          className={`px-2.5 py-1.5 text-[12.5px] font-medium transition-colors ${value === v
            ? v < 0 ? "bg-down text-white" : v > 0 ? "bg-up text-white" : "bg-fg text-bg"
            : "text-fg-2 hover:text-fg"}`}>
          {VIEW_LABEL[v]}
        </button>
      ))}
    </div>
  );
}

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
  const [viewAsset, setViewAsset] = useState("");
  if (!data || !d) return <Skeleton className="h-96" />;
  const b = data.bounds;
  const lim = d.limits;
  const al = d.allocation;
  const setLim = (k: string, v: number | boolean) => setD({ ...d, limits: { ...lim, [k]: v } });
  const af = d.approval_free;
  const setAf = (patch: Partial<ApprovalFree>) => setD({ ...d, approval_free: { ...af, ...patch } });
  const ol = d.outlook;
  const setOl = (patch: Partial<Outlook>) => setD({ ...d, outlook: { ...ol, ...patch } });

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
        <SectionHead title="交易风格与观点" doc="decisions-and-risk" />
        <Row label="交易风格">
          <Seg id="style" value={d.strategy.style} onChange={(k) => setD({ ...d, strategy: { style: k } })}
            items={[{ k: "short" as const, label: "短线" }, { k: "medium" as const, label: "中期" }, { k: "long" as const, label: "长期" }]} />
        </Row>
        <p className="border-b border-line py-3 text-[13px] text-fg-3">{STYLE_HINT[d.strategy.style]}复查间隔仍不会越过「设置 → 节奏」里的最短和最长间隔。</p>
        <Row label={<>整体看法 <span className="text-fg-3">· 指导 AI 看好还是不看好</span></>}>
          <ViewDial label="整体看法" value={ol.market} onChange={(v) => setOl({ market: v })} />
        </Row>
        {Object.entries(ol.assets).map(([t, v]) => (
          <Row key={t} label={<>看法 <b className="text-fg">{t}</b></>}>
            <ViewDial label={`${t} 看法`} value={v} onChange={(x) => setOl({ assets: { ...ol.assets, [t]: x } })} />
            <button className="btn-secondary btn-sm" onClick={() => { const a = { ...ol.assets }; delete a[t]; setOl({ assets: a }); }}>移除</button>
          </Row>
        ))}
        <form className="flex flex-wrap items-center gap-2 border-b border-line py-3" onSubmit={(e) => {
          e.preventDefault();
          const t = viewAsset.trim().toUpperCase();
          if (t && !(t in ol.assets)) setOl({ assets: { ...ol.assets, [t]: 1 } });
          setViewAsset("");
        }}>
          <span className="mr-auto text-[14px] text-fg-2">单个币种的看法</span>
          <input className="input h-9 w-24" placeholder="ETH" value={viewAsset} onChange={(e) => setViewAsset(e.target.value)} />
          <button className="btn-secondary h-9">添加</button>
        </form>
        <div className="border-b border-line py-3">
          <p className="text-[14px] text-fg-2">我的观点</p>
          <textarea className="input mt-2 min-h-[72px] w-full py-2" maxLength={1000} value={ol.note} aria-label="我的观点"
            placeholder="例如：觉得 ETH 短期要回调，想做空对冲；BTC 跌到 7 万以下就多买"
            onChange={(e) => setOl({ note: e.target.value })} />
        </div>
        <p className="py-3 text-[13px] text-fg-3">观点只是给 AI 的指导：它仍要找行情依据，行情明显相反时会说明。风控限额不会因观点改变；现货不能做空，看空时 AI 会少买、多留稳定币，开启合约后才可能做空。</p>
        <div className="pt-1"><button className="btn-primary" onClick={() => save({ strategy: d.strategy, outlook: d.outlook })}>保存</button></div>
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

      <section>
        <SectionHead title="免审批" doc="approvals" />
        <Row label="免审批模式">
          <Seg id="approval-free" value={af.mode} onChange={(k) => setAf({ mode: k })}
            items={[{ k: "off" as const, label: "关闭" }, { k: "window" as const, label: "按时段" }, { k: "always" as const, label: "一直开启" }]} />
        </Row>
        {af.mode === "window" && (
          <Row label={`每天 · ${d.schedule.timezone}`}>
            <input type="time" aria-label="免审批开始" className="input num h-9 w-32" value={af.start} onChange={(e) => setAf({ start: e.target.value })} />
            <span className="text-fg-3">至</span>
            <input type="time" aria-label="免审批结束" className="input num h-9 w-32" value={af.end} onChange={(e) => setAf({ end: e.target.value })} />
          </Row>
        )}
        {af.mode !== "off" && (
          <>
            <Row label="也包含卖出">
              <input type="checkbox" className="h-4 w-4 accent-[#F0A23B]" checked={af.include_sell} aria-label="也包含卖出"
                onChange={(e) => setAf({ include_sell: e.target.checked })} />
            </Row>
            <Row label="也包含合约开仓">
              <input type="checkbox" className="h-4 w-4 accent-[#F0A23B]" checked={af.include_futures} aria-label="也包含合约开仓"
                onChange={(e) => setAf({ include_futures: e.target.checked })} />
            </Row>
          </>
        )}
        <p className="py-3 text-[13px] text-fg-3">适合睡觉或没空看手机的时候。免审批期间，本来要等你批准的交易会直接执行，但单笔上限、每日笔数和金额、稳定币下限、暂停条件等风控照常生效，超出的部分照样截断或拒绝。卖出需要先在「风控」里允许卖出。</p>
        <div className="pt-1"><button className="btn-primary" onClick={() => save({ approval_free: d.approval_free })}>保存</button></div>
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
