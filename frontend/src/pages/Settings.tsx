import { Check, Warning, X } from "@phosphor-icons/react";
import { useEffect, useState, type ReactNode } from "react";
import { api, post, type Category } from "../api";
import { Page, SectionHead, Skeleton, Spinner } from "../components/ui";
import { CATEGORY_LABEL, useApi, useToast } from "../lib";

interface SettingsResp {
  settings: {
    mode: "paper" | "live";
    categories: Record<string, Category>;
    limits: Record<string, number | boolean>;
    schedule: { cycle_minutes: number; approval_timeout_minutes: number; daily_report_time: string; timezone: string };
    provider: { name: string; model: string; base_url: string; timeout_seconds: number; temperature: number };
    investor_notes: string;
    live_gate_allow: string[];
  };
  secrets: Record<string, boolean>;
  providers: Record<string, { ok: boolean; message: string; default_model: string }>;
  bounds: Record<string, [number, number]>;
  confirm_phrase: string;
}
interface LiveCheck { ok: boolean; blockers: string[]; rows: { label: string; field: string; expected: boolean; actual: boolean | null; ok: boolean; waived: boolean; waivable: boolean }[] }

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
        {!data.providers[d.provider.name]?.ok && <Row label="状态"><span className="text-[13px] text-warn">{data.providers[d.provider.name]?.message}</span></Row>}
        <div className="flex gap-2 pt-4">
          <button className="btn-primary" onClick={() => save({ provider: d.provider })}>保存</button>
          <button className="btn-secondary" onClick={test} disabled={testing}>{testing ? <Spinner /> : "连通测试"}</button>
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
                  <Num unit="%" value={Math.round(w * 1000) / 10} step={0.5}
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
    </Page>
  );
}
