import { Check, Warning, X } from "@phosphor-icons/react";
import { useEffect, useState, type ReactNode } from "react";
import { api, post, type Category } from "../api";
import { Page, Seg, SectionHead, Skeleton, Spinner } from "../components/ui";
import { CATEGORY_LABEL, useApi, useToast } from "../lib";
import TokenPicker from "../components/TokenPicker";

interface SettingsResp {
  settings: {
    mode: "paper" | "live";
    categories: Record<string, Category>;
    limits: Record<string, number | boolean>;
    schedule: { cycle_minutes: number; approval_timeout_minutes: number; daily_report_time: string; timezone: string };
    provider: { name: string; model: string; base_url: string; timeout_seconds: number; temperature: number };
    investor_notes: string;
    live_gate_allow: string[];
    risk_preset: string;
    capital: { locked: Record<string, number>; budget_usd: number | null };
    allocation: { mode: "targets" | "ai"; enabled: string[]; max_asset_weight: number };
    provider_fallback: { name: string; model: string; base_url: string; timeout_seconds: number; temperature: number } | null;
    stables: string[];
    futures: FuturesCfg;
    earn: { enabled: boolean; assets: string[]; keep_liquid_usd: number; min_move_usd: number };
  };
  presets: Record<string, unknown>;
  candidates: string[];
  deployed_usd: number;
  secrets: Record<string, boolean>;
  providers: Record<string, { ok: boolean; message: string; default_model: string }>;
  bounds: Record<string, [number, number]>;
  confirm_phrase: string;
}
interface FuturesCfg {
  enabled: boolean; symbols: string[]; max_leverage: number; max_position_margin_usd: number; max_total_margin_usd: number;
  max_loss_per_trade_usd: number; max_stop_distance: number; allow_short: boolean; auto_open: boolean;
  auto_open_max_margin_usd: number; max_opens_per_day: number;
}
interface EarnResp { enabled: boolean; positions: Record<string, { amount: number; apr: number | null }>;
  products: Record<string, { apr?: string | null; error?: string }> }
const FUT_LIMITS: { k: keyof FuturesCfg; label: string; unit: string; pct?: boolean }[] = [
  { k: "max_leverage", label: "最大杠杆", unit: "x" },
  { k: "max_position_margin_usd", label: "单仓保证金上限", unit: "USD" },
  { k: "max_total_margin_usd", label: "总保证金上限", unit: "USD" },
  { k: "max_loss_per_trade_usd", label: "单笔止损最大亏损", unit: "USD" },
  { k: "max_stop_distance", label: "止损最远距离", unit: "%", pct: true },
  { k: "max_opens_per_day", label: "每日开仓次数", unit: "次" },
];

interface LiveCheck { ok: boolean; blockers: string[]; rows: { label: string; field: string; expected: boolean; actual: boolean | null; ok: boolean; waived: boolean; waivable: boolean }[] }

const PRESET_LABEL: Record<string, string> = { conservative: "保守", balanced: "均衡", aggressive: "积极", custom: "自定义" };

interface Device { id: string; current: boolean; created: number; last_login: number; ua: string; ip: string }

function deviceName(ua: string) {
  const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Mac OS/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "设备";
  const br = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : /Firefox\//.test(ua) ? "Firefox" : "";
  return `${os}${br ? " · " + br : ""}`;
}

const PROVIDER: Record<string, string> = { claude_code: "Claude 订阅", openai: "OpenAI", openai_compat: "OpenAI 兼容", anthropic_api: "Anthropic API" };
const LIMITS: { k: string; label: string; unit: string; pct?: boolean }[] = [
  { k: "max_order_usd", label: "单笔上限", unit: "USD" },
  { k: "auto_execute_max_usd", label: "单笔自动执行", unit: "USD" },
  { k: "max_auto_daily_usd", label: "每日自动执行", unit: "USD" },
  { k: "max_orders_per_day", label: "每日笔数", unit: "笔" },
  { k: "max_daily_notional_usd", label: "每日金额", unit: "USD" },
  { k: "stable_floor", label: "稳定币下限", unit: "%", pct: true },
  { k: "btc_7d_drop_pause", label: "BTC 7 日暂停线", unit: "%", pct: true },
];

function Row({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-[52px] flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-line py-2.5">
      <span className="text-[14px] text-fg-2">{label}</span>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

function Num({ value, onChange, unit, min, max, step = 1 }: { value: number; onChange: (v: number) => void; unit: string; min?: number; max?: number; step?: number }) {
  return (
    <label className="flex h-9 items-center rounded-lg bg-field pr-3">
      <input type="number" className="num h-9 w-24 bg-transparent px-3 text-right text-[14px] focus:outline-none" value={Number.isFinite(value) ? value : ""}
        min={min} max={max} step={step} onChange={(e) => onChange(parseFloat(e.target.value))} />
      <span className="w-8 text-[12px] text-fg-3">{unit}</span>
    </label>
  );
}

export default function Settings() {
  const { data, reload } = useApi<SettingsResp>("/settings", { kinds: ["settings", "mode"] });
  const live = useApi<LiveCheck>("/live/check");
  const devs = useApi<Device[]>("/devices");
  const earn = useApi<EarnResp>("/earn", { kinds: ["earn", "settings"] });
  const [lockAsset, setLockAsset] = useState("");
  const [lockAmt, setLockAmt] = useState("");
  const toast = useToast();
  const [d, setD] = useState<SettingsResp["settings"] | null>(null);
  const [phrase, setPhrase] = useState("");
  const [testing, setTesting] = useState(false);
  useEffect(() => { if (data) setD(structuredClone(data.settings)); }, [data]);
  if (!data || !d) return <Skeleton className="h-96" />;
  const b = data.bounds;

  async function save(patch: Record<string, unknown>, msg = "已保存") {
    try { await api("/settings", { method: "PUT", json: patch }); toast(msg); reload(); } catch (e) { toast((e as Error).message, "err"); }
  }
  async function setMode(mode: "paper" | "live") {
    const r = await post<{ ok: boolean; message: string }>("/mode", { mode, confirm: phrase });
    toast(r.message, r.ok ? "ok" : "err");
    setPhrase("");
    reload();
  }
  async function test() {
    setTesting(true);
    const r = await post<{ ok: boolean; reply: string; model?: string; ms: number }>("/providers/test");
    setTesting(false);
    toast(r.ok ? `${r.model} · ${(r.ms / 1000).toFixed(1)}s` : r.reply, r.ok ? "ok" : "err");
  }
  const lim = d.limits;
  const setLim = (k: string, v: number | boolean) => setD({ ...d, limits: { ...lim, [k]: v } });

  return (
    <Page className="mx-auto flex max-w-[820px] flex-col gap-10">
      <h1 className="text-[20px] font-semibold">设置</h1>

      <section>
        <SectionHead title="交易模式" doc="live-mode" />
        <Row label="当前">
          <span className={`text-[14px] font-medium ${d.mode === "live" ? "text-down" : ""}`}>{d.mode === "live" ? "实盘" : "模拟盘"}</span>
          {d.mode === "live"
            ? <button className="btn-secondary btn-sm" onClick={() => setMode("paper")}>切回模拟盘</button>
            : <button className="btn-secondary btn-sm" onClick={async () => { if (confirm("重置模拟盘？")) { await post("/paper/reset"); toast("已重置"); } }}>重置模拟盘</button>}
        </Row>
        {d.mode === "paper" && (
          <>
            {live.loading ? <div className="py-4"><Spinner /></div> : (live.data?.rows ?? []).map((r) => (
              <Row key={r.field} label={r.label}>
                <span className={`flex items-center gap-1 text-[13px] ${r.waived ? "text-warn" : r.ok ? "text-up" : "text-down"}`}>
                  {r.waived ? <Warning size={14} weight="fill" /> : r.ok ? <Check size={14} weight="bold" /> : <X size={14} weight="bold" />}
                  {r.actual === null || r.actual === undefined ? "未知" : r.actual ? "开启" : "关闭"}
                </span>
                {r.waivable && r.actual === true && (
                  <label className="flex items-center gap-1.5 text-[12.5px] text-fg-3">
                    <input type="checkbox" className="h-3.5 w-3.5 accent-[#F0A23B]" checked={d.live_gate_allow.includes(r.field)}
                      onChange={async (e) => {
                        const next = e.target.checked ? [...d.live_gate_allow, r.field] : d.live_gate_allow.filter((x) => x !== r.field);
                        await save({ live_gate_allow: next }); live.reload();
                      }} />
                    允许保留
                  </label>
                )}
              </Row>
            ))}
            {!live.data?.rows?.length && live.data?.blockers.map((x) => <Row key={x} label="Key"><span className="text-[13px] text-down">{x}</span></Row>)}
            <div className="flex flex-wrap items-center gap-2 pt-4">
              <input className="input max-w-[220px]" placeholder={data.confirm_phrase} value={phrase} onChange={(e) => setPhrase(e.target.value)} />
              <button className="btn-down" disabled={!live.data?.ok || phrase !== data.confirm_phrase} onClick={() => setMode("live")}>开启实盘</button>
              <button className="btn-secondary" onClick={live.reload}>重新检查</button>
            </div>
          </>
        )}
      </section>

      <section>
        <SectionHead title="AI 模型" doc="models" />
        <Row label="提供方">
          <select className="input h-9 w-44" value={d.provider.name} onChange={(e) => setD({ ...d, provider: { ...d.provider, name: e.target.value, model: "" } })}>
            {Object.entries(data.providers).map(([k, p]) => <option key={k} value={k}>{PROVIDER[k] ?? k}{p.ok ? "" : "（未配置）"}</option>)}
          </select>
        </Row>
        <Row label="模型">
          <input className="input h-9 w-56" value={d.provider.model} placeholder={data.providers[d.provider.name]?.default_model || "默认"}
            onChange={(e) => setD({ ...d, provider: { ...d.provider, model: e.target.value } })} />
        </Row>
        {d.provider.name === "openai_compat" && (
          <Row label="Base URL">
            <input className="input h-9 w-72" value={d.provider.base_url} placeholder="https://api.deepseek.com/v1"
              onChange={(e) => setD({ ...d, provider: { ...d.provider, base_url: e.target.value } })} />
          </Row>
        )}
        <Row label="备用模型">
          <select className="input h-9 w-44" value={d.provider_fallback?.name ?? ""}
            onChange={(e) => setD({ ...d, provider_fallback: e.target.value ? { ...d.provider, name: e.target.value, model: "" } : null })}>
            <option value="">无</option>
            {Object.entries(data.providers).filter(([k]) => k !== d.provider.name).map(([k, p]) => <option key={k} value={k}>{PROVIDER[k] ?? k}{p.ok ? "" : "（未配置）"}</option>)}
          </select>
        </Row>
        {!data.providers[d.provider.name]?.ok && <Row label="状态"><span className="text-[13px] text-warn">{data.providers[d.provider.name]?.message}</span></Row>}
        <div className="flex gap-2 pt-4">
          <button className="btn-primary" onClick={() => save({ provider: d.provider, provider_fallback: d.provider_fallback })}>保存</button>
          <button className="btn-secondary" onClick={test} disabled={testing}>{testing ? <Spinner /> : "连通测试"}</button>
        </div>
      </section>

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
          <Seg id="alloc" value={d.allocation.mode} items={[{ k: "targets" as const, label: "按目标配置" }, { k: "ai" as const, label: "AI 自主分配" }]}
            onChange={(k) => setD({ ...d, allocation: { ...d.allocation, mode: k } })} />
        </Row>
        <div className="border-b border-line py-3">
          <p className="text-[14px] text-fg-2">可交易资产</p>
          <div className="mt-2.5 flex flex-wrap gap-2">
            {Array.from(new Set([...data.candidates, ...d.allocation.enabled])).map((t) => {
              const on = d.allocation.enabled.includes(t);
              return (
                <button key={t} onClick={() => setD({ ...d, allocation: { ...d.allocation, enabled: on ? d.allocation.enabled.filter((x) => x !== t) : [...d.allocation.enabled, t] } })}
                  className={`rounded-md border px-2.5 py-1 text-[13px] font-medium transition-colors ${on ? "border-fg bg-fg text-bg" : "border-line-strong text-fg-2 hover:text-fg"}`}>
                  {t}
                </button>
              );
            })}
          </div>
          <div className="mt-3">
            <TokenPicker selected={d.allocation.enabled} onToggle={(t) => setD({ ...d, allocation: { ...d.allocation, enabled: d.allocation.enabled.includes(t) ? d.allocation.enabled.filter((x) => x !== t) : [...d.allocation.enabled, t] } })} />
          </div>
        </div>
        {d.allocation.mode === "ai" && (
          <Row label="单币占比上限">
            <Num unit="%" value={Math.round(d.allocation.max_asset_weight * 1000) / 10} step={1} min={5} max={100}
              onChange={(x) => setD({ ...d, allocation: { ...d.allocation, max_asset_weight: x / 100 } })} />
          </Row>
        )}
        <div className="pt-4"><button className="btn-primary" onClick={() => save({ allocation: d.allocation })}>保存</button></div>
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

      <section>
        <SectionHead title="理财" doc="earn" />
        <Row label="闲置稳定币自动申购活期">
          <input type="checkbox" className="h-4 w-4 accent-white" checked={d.earn.enabled}
            onChange={(e) => setD({ ...d, earn: { ...d.earn, enabled: e.target.checked } })} />
        </Row>
        <Row label="稳定币">
          <div className="flex flex-wrap gap-1.5">
            {d.stables.map((a) => {
              const on = d.earn.assets.includes(a);
              return (
                <button key={a} onClick={() => setD({ ...d, earn: { ...d.earn, assets: on ? d.earn.assets.filter((x) => x !== a) : [...d.earn.assets, a] } })}
                  className={`rounded-md border px-2.5 py-1 text-[13px] font-medium ${on ? "border-fg bg-fg text-bg" : "border-line-strong text-fg-2 hover:text-fg"}`}>
                  {a}{earn.data?.products?.[a]?.apr ? <span className={on ? "text-bg/70" : "text-up"}> {(parseFloat(earn.data.products[a].apr as string) * 100).toFixed(2)}%</span> : null}
                </button>
              );
            })}
          </div>
        </Row>
        <Row label="现货保留"><Num unit="USD" value={d.earn.keep_liquid_usd} min={0} onChange={(x) => setD({ ...d, earn: { ...d.earn, keep_liquid_usd: x } })} /></Row>
        <Row label="最小划转"><Num unit="USD" value={d.earn.min_move_usd} min={1} onChange={(x) => setD({ ...d, earn: { ...d.earn, min_move_usd: x } })} /></Row>
        {Object.entries(earn.data?.positions ?? {}).map(([a, p]) => (
          <Row key={a} label={<>活期 <b className="text-fg">{a}</b></>}>
            <span className="num text-[14px]">{p.amount.toLocaleString("en-US", { maximumFractionDigits: 2 })}</span>
            <button className="btn-secondary btn-sm" onClick={async () => {
              const r = await post<{ ok: boolean; message: string }>("/earn/move", { asset: a, amount: p.amount, direction: "redeem" });
              toast(r.message, r.ok ? "ok" : "err"); earn.reload();
            }}>全部赎回</button>
          </Row>
        ))}
        <div className="flex gap-2 pt-4">
          <button className="btn-primary" onClick={() => save({ earn: d.earn })}>保存</button>
          {d.earn.enabled && <button className="btn-secondary" onClick={async () => {
            const r = await post<{ ok: boolean; message: string }>("/earn/sweep"); toast(r.message); earn.reload();
          }}>立即申购闲置</button>}
        </div>
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
        <SectionHead title="目标配置" doc="decisions-and-risk" />
        {Object.entries(d.categories).map(([cat, c]) => (
          <div key={cat} className="border-b border-line py-3">
            <p className="text-[14px] font-medium">{CATEGORY_LABEL[cat] ?? cat}</p>
            <div className="mt-2 flex flex-wrap gap-x-6 gap-y-2">
              {Object.entries(c.tokens).map(([tok, w]) => (
                <label key={tok} className="flex items-center gap-2 text-[13px] text-fg-2">{tok}
                  <Num unit="%" value={Math.round((w ?? 0) * 1000) / 10} step={0.5}
                    onChange={(x) => setD({ ...d, categories: { ...d.categories, [cat]: { ...c, tokens: { ...c.tokens, [tok]: x / 100 } } } })} />
                </label>
              ))}
              <label className="flex items-center gap-2 text-[13px] text-fg-2">下限
                <Num unit="%" value={Math.round(c.band[0] * 1000) / 10} step={0.5}
                  onChange={(x) => setD({ ...d, categories: { ...d.categories, [cat]: { ...c, band: [x / 100, c.band[1]] } } })} />
              </label>
              <label className="flex items-center gap-2 text-[13px] text-fg-2">上限
                <Num unit="%" value={Math.round(c.band[1] * 1000) / 10} step={0.5}
                  onChange={(x) => setD({ ...d, categories: { ...d.categories, [cat]: { ...c, band: [c.band[0], x / 100] } } })} />
              </label>
            </div>
          </div>
        ))}
        <div className="border-b border-line py-3">
          <p className="text-[14px] font-medium">投资说明</p>
          <textarea className="input mt-2 h-20 py-2" value={d.investor_notes} onChange={(e) => setD({ ...d, investor_notes: e.target.value })} />
        </div>
        <div className="pt-4"><button className="btn-primary" onClick={() => save({ categories: d.categories, investor_notes: d.investor_notes })}>保存</button></div>
      </section>

      <section>
        <SectionHead title="节奏" doc="decisions-and-risk" />
        <Row label="决策间隔"><Num unit="分钟" value={d.schedule.cycle_minutes} min={b.cycle_minutes?.[0]} max={b.cycle_minutes?.[1]} onChange={(x) => setD({ ...d, schedule: { ...d.schedule, cycle_minutes: x } })} /></Row>
        <Row label="审批有效期"><Num unit="分钟" value={d.schedule.approval_timeout_minutes} min={b.approval_timeout_minutes?.[0]} max={b.approval_timeout_minutes?.[1]} onChange={(x) => setD({ ...d, schedule: { ...d.schedule, approval_timeout_minutes: x } })} /></Row>
        <Row label={`日报时间 · ${d.schedule.timezone}`}>
          <input type="time" className="input num h-9 w-32" value={d.schedule.daily_report_time} onChange={(e) => setD({ ...d, schedule: { ...d.schedule, daily_report_time: e.target.value } })} />
        </Row>
        <div className="pt-4"><button className="btn-primary" onClick={() => save({ schedule: d.schedule })}>保存</button></div>
      </section>

      <section>
        <SectionHead title="连接" doc="getting-started" />
        {[["binance_api_key", "币安 API"], ["telegram_bot_token", "Telegram"], ["claude_code_oauth_token", "Claude Code token"],
          ["openai_api_key", "OpenAI"], ["anthropic_api_key", "Anthropic"], ["openai_compat_api_key", "OpenAI 兼容"]].map(([k, l]) => (
          <Row key={k} label={l}>
            <span className={`text-[13px] ${data.secrets[k] ? "text-up" : "text-fg-3"}`}>{data.secrets[k] ? "已配置" : "未配置"}</span>
          </Row>
        ))}
      </section>

      <section>
        <SectionHead title="已登录设备" doc="faq" right={(devs.data?.length ?? 0) > 1 ? (
          <button className="btn-secondary btn-sm" onClick={async () => { const r = await post<{ revoked: number }>("/devices/revoke-others"); toast(`已撤销 ${r.revoked} 台设备`); devs.reload(); }}>撤销其他设备</button>
        ) : undefined} />
        {(devs.data ?? []).map((x) => (
          <Row key={x.id} label={<>{deviceName(x.ua)}{x.current && <span className="ml-2 text-[12px] text-up">本机</span>}</>}>
            <span className="num text-[13px] text-fg-3">{x.ip} · {new Date(x.last_login * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false })}</span>
          </Row>
        ))}
      </section>
    </Page>
  );
}
