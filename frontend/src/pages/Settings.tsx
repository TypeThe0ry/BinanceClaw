import { CheckCircle, WarningCircle, XCircle } from "@phosphor-icons/react";
import { motion } from "framer-motion";
import { useEffect, useState, type ReactNode } from "react";
import { api, post, type Category } from "../api";
import { Card, PageTitle, Skeleton, Spinner } from "../components/ui";
import { CATEGORY_LABEL, useApi, useToast } from "../lib";

interface SettingsResp {
  settings: {
    mode: "paper" | "live";
    categories: Record<string, Category>;
    limits: Record<string, number | boolean>;
    schedule: { cycle_minutes: number; approval_timeout_minutes: number; daily_report_time: string; timezone: string };
    provider: { name: string; model: string; base_url: string; timeout_seconds: number; temperature: number };
    investor_notes: string;
  };
  secrets: Record<string, boolean>;
  providers: Record<string, { ok: boolean; message: string; default_model: string }>;
  bounds: Record<string, [number, number]>;
  confirm_phrase: string;
  telegram: boolean;
}
interface LiveCheck { ok: boolean; blockers: string[]; rows: { label: string; expected: boolean; actual: boolean | null; ok: boolean }[] }

const PROVIDER_LABEL: Record<string, { t: string; d: string }> = {
  claude_code: { t: "Claude 订阅", d: "通过 Claude Code CLI，用你的订阅额度，不需要 API Key" },
  openai: { t: "OpenAI", d: "ChatGPT 同款模型，按 API 计费" },
  openai_compat: { t: "OpenAI 兼容接口", d: "DeepSeek、通义、Ollama、OpenRouter 等" },
  anthropic_api: { t: "Anthropic API", d: "Claude 模型，按 API 计费" },
};

const LIMITS: { k: string; label: string; unit: string; pct?: boolean }[] = [
  { k: "max_order_usd", label: "单笔上限", unit: "USD" },
  { k: "auto_execute_max_usd", label: "单笔自动执行上限", unit: "USD" },
  { k: "max_auto_daily_usd", label: "每日自动执行总额", unit: "USD" },
  { k: "max_orders_per_day", label: "每日最多笔数", unit: "笔" },
  { k: "max_daily_notional_usd", label: "每日成交总额上限", unit: "USD" },
  { k: "stable_floor", label: "稳定币下限", unit: "%", pct: true },
  { k: "btc_7d_drop_pause", label: "BTC 7 日跌幅暂停线", unit: "%", pct: true },
];

function Section({ title, sub, children, delay = 0 }: { title: string; sub?: string; children: ReactNode; delay?: number }) {
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay }}>
      <Card className="p-6">
        <h2 className="font-display text-[24px] leading-tight">{title}</h2>
        {sub && <p className="mt-1 text-[13.5px] text-ink-mute">{sub}</p>}
        <div className="mt-5">{children}</div>
      </Card>
    </motion.div>
  );
}

function NumField({ label, value, onChange, unit, min, max, step = 1 }:
  { label: string; value: number; onChange: (v: number) => void; unit: string; min?: number; max?: number; step?: number }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      <div className="mt-1.5 flex items-center gap-2">
        <input type="number" className="input num" value={Number.isFinite(value) ? value : ""} min={min} max={max} step={step}
          onChange={(e) => onChange(parseFloat(e.target.value))} />
        <span className="w-10 shrink-0 text-[13px] text-ink-mute">{unit}</span>
      </div>
      {min != null && max != null && <span className="mt-1 block text-[12px] text-ink-faint">范围 {min}–{max}</span>}
    </label>
  );
}

export default function Settings() {
  const { data, reload } = useApi<SettingsResp>("/settings", { kinds: ["settings", "mode"] });
  const live = useApi<LiveCheck>("/live/check");
  const toast = useToast();
  const [draft, setDraft] = useState<SettingsResp["settings"] | null>(null);
  const [phrase, setPhrase] = useState("");
  const [testing, setTesting] = useState(false);
  useEffect(() => { if (data) setDraft(structuredClone(data.settings)); }, [data]);
  if (!data || !draft) return <Skeleton className="h-96" />;
  const b = data.bounds;

  async function save(patch: Record<string, unknown>, msg = "已保存") {
    try {
      await api("/settings", { method: "PUT", json: patch });
      toast(msg);
      reload();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  }
  async function testProvider() {
    setTesting(true);
    const r = await post<{ ok: boolean; reply: string; model?: string; ms: number }>("/providers/test");
    setTesting(false);
    toast(r.ok ? `连通正常（${r.model}，${(r.ms / 1000).toFixed(1)} 秒）：${r.reply}` : `连通失败：${r.reply}`, r.ok ? "ok" : "err");
  }
  async function setMode(mode: "paper" | "live") {
    const r = await post<{ ok: boolean; message: string }>("/mode", { mode, confirm: phrase });
    toast(r.message, r.ok ? "ok" : "err");
    setPhrase("");
    reload();
  }

  const lim = draft.limits;
  const setLim = (k: string, v: number | boolean) => setDraft({ ...draft, limits: { ...lim, [k]: v } });

  return (
    <div className="flex flex-col gap-5">
      <PageTitle title="设置" sub="所有风控数值都有硬上下限，超出的部分保存时会自动收回到范围内。" />

      <Section title="交易模式" sub="模拟盘使用真实行情和虚拟资金；实盘会用你的币安账户真实下单。">
        <div className="flex flex-wrap items-center gap-3">
          <span className={`chip border ${draft.mode === "live" ? "border-live/50 bg-live/15 text-[#FF8A8A]" : "border-white/10 bg-white/[0.05] text-ink-soft"}`}>
            当前：{draft.mode === "live" ? "实盘" : "模拟盘"}
          </span>
          {draft.mode === "live" ? (
            <button className="btn-ghost" onClick={() => setMode("paper")}>切回模拟盘</button>
          ) : (
            <button className="btn-ghost" onClick={async () => { if (confirm("清空模拟盘持仓，恢复初始虚拟资金？")) { await post("/paper/reset"); toast("模拟盘已重置"); } }}>重置模拟盘</button>
          )}
        </div>
        {draft.mode === "paper" && (
          <div className="mt-6 rounded-xl border border-white/[0.07] bg-black/15 p-5">
            <p className="text-[15px] font-medium">开启实盘前的检查</p>
            <p className="mt-1 text-[13.5px] text-ink-mute">币安 API Key 必须只开读取和现货交易、绑定 IP 白名单，其余权限全部关闭。</p>
            {live.loading ? <Spinner className="mt-4" /> : (
              <ul className="mt-4 grid gap-2 sm:grid-cols-2">
                {(live.data?.rows ?? []).map((r) => (
                  <li key={r.label} className="flex items-center gap-2 text-[14px]">
                    {r.ok ? <CheckCircle size={18} weight="fill" className="text-mint" /> : <XCircle size={18} weight="fill" className="text-rose" />}
                    <span>{r.label}</span>
                    <span className="text-ink-mute">需要{r.expected ? "开启" : "关闭"}{!r.ok && `，当前${r.actual ? "开启" : "关闭"}`}</span>
                  </li>
                ))}
                {!live.data?.rows?.length && live.data?.blockers.map((x) => <li key={x} className="text-[14px] text-rose">{x}</li>)}
              </ul>
            )}
            <div className="mt-5 flex flex-wrap items-center gap-2">
              <input className="input max-w-xs" placeholder={`输入「${data.confirm_phrase}」`} value={phrase} onChange={(e) => setPhrase(e.target.value)} />
              <button className="btn-danger" disabled={!live.data?.ok || phrase !== data.confirm_phrase} onClick={() => setMode("live")}>开启实盘</button>
              <button className="btn-ghost" onClick={live.reload}>重新检查</button>
            </div>
            {!live.data?.ok && !live.loading && (
              <p className="mt-3 flex items-start gap-1.5 text-[13.5px] text-amber"><WarningCircle size={17} className="mt-0.5 shrink-0" />去币安 API 管理里改好以上权限，再点「重新检查」。</p>
            )}
          </div>
        )}
      </Section>

      <Section title="AI 模型" sub="每轮决策和对话都用这里选的模型。" delay={0.04}>
        <div className="grid gap-3 sm:grid-cols-2">
          {Object.entries(data.providers).map(([name, p]) => (
            <button key={name} onClick={() => setDraft({ ...draft, provider: { ...draft.provider, name, model: "" } })}
              className={`relative rounded-xl border p-4 text-left transition ${draft.provider.name === name ? "border-mint/50 bg-mint/[0.06]" : "border-white/[0.07] bg-white/[0.02] hover:bg-white/[0.04]"}`}>
              <p className="text-[15px] font-medium">{PROVIDER_LABEL[name]?.t ?? name}</p>
              <p className="mt-1 text-[13px] text-ink-mute">{PROVIDER_LABEL[name]?.d}</p>
              <p className={`mt-2 text-[12.5px] ${p.ok ? "text-mint" : "text-amber"}`}>{p.ok ? "可用" : p.message}</p>
            </button>
          ))}
        </div>
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="label">模型名称</span>
            <input className="input mt-1.5" value={draft.provider.model} placeholder={data.providers[draft.provider.name]?.default_model || "使用默认"}
              onChange={(e) => setDraft({ ...draft, provider: { ...draft.provider, model: e.target.value } })} />
          </label>
          {draft.provider.name === "openai_compat" && (
            <label className="block">
              <span className="label">Base URL</span>
              <input className="input mt-1.5" value={draft.provider.base_url} placeholder="https://api.deepseek.com/v1"
                onChange={(e) => setDraft({ ...draft, provider: { ...draft.provider, base_url: e.target.value } })} />
            </label>
          )}
        </div>
        <div className="mt-5 flex gap-2">
          <button className="btn-primary" onClick={() => save({ provider: draft.provider }, "模型已切换")}>保存</button>
          <button className="btn-ghost" onClick={testProvider} disabled={testing}>{testing ? <><Spinner />测试中…</> : "连通测试"}</button>
        </div>
        <p className="mt-3 text-[12.5px] text-ink-mute">API Key 只能在服务器上用 <code className="text-ink-soft">binanceclaw setup</code> 录入，网页上不显示也不能修改。</p>
      </Section>

      <Section title="风控" sub="AI 的每个提议都要先过这些限制；超出的部分截断或拒绝。" delay={0.08}>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {LIMITS.map((f) => {
            const v = lim[f.k] as number;
            const [lo, hi] = b[f.k] ?? [undefined, undefined];
            return (
              <NumField key={f.k} label={f.label} unit={f.unit}
                value={f.pct ? Math.round(v * 1000) / 10 : v}
                min={lo != null ? (f.pct ? lo * 100 : lo) : undefined} max={hi != null ? (f.pct ? hi * 100 : hi) : undefined}
                step={f.pct ? 0.5 : 1}
                onChange={(x) => setLim(f.k, f.pct ? x / 100 : x)} />
            );
          })}
        </div>
        <label className="mt-5 flex items-center gap-3 text-[14px]">
          <input type="checkbox" className="h-4 w-4 accent-[#5EF2C4]" checked={!!lim.allow_sell} onChange={(e) => setLim("allow_sell", e.target.checked)} />
          允许 AI 提出卖出（卖出永远需要你批准）
        </label>
        <button className="btn-primary mt-5" onClick={() => save({ limits: lim }, "风控已更新")}>保存风控</button>
      </Section>

      <Section title="目标配置" sub="AI 以此为锚做决定；区间上限由风控强制执行。" delay={0.12}>
        <div className="grid gap-4">
          {Object.entries(draft.categories).map(([cat, c]) => (
            <div key={cat} className="rounded-xl border border-white/[0.06] bg-black/10 p-4">
              <p className="text-[15px] font-medium">{CATEGORY_LABEL[cat] ?? cat}</p>
              <div className="mt-3 grid gap-3 sm:grid-cols-4">
                {Object.entries(c.tokens).map(([tok, w]) => (
                  <NumField key={tok} label={`${tok} 目标`} unit="%" value={Math.round(w * 1000) / 10} step={0.5}
                    onChange={(x) => setDraft({ ...draft, categories: { ...draft.categories, [cat]: { ...c, tokens: { ...c.tokens, [tok]: x / 100 } } } })} />
                ))}
                <NumField label="区间下限" unit="%" value={Math.round(c.band[0] * 1000) / 10} step={0.5}
                  onChange={(x) => setDraft({ ...draft, categories: { ...draft.categories, [cat]: { ...c, band: [x / 100, c.band[1]] } } })} />
                <NumField label="区间上限" unit="%" value={Math.round(c.band[1] * 1000) / 10} step={0.5}
                  onChange={(x) => setDraft({ ...draft, categories: { ...draft.categories, [cat]: { ...c, band: [c.band[0], x / 100] } } })} />
              </div>
            </div>
          ))}
        </div>
        <label className="mt-5 block">
          <span className="label">给 AI 的投资说明</span>
          <textarea className="input mt-1.5 min-h-[90px]" value={draft.investor_notes}
            onChange={(e) => setDraft({ ...draft, investor_notes: e.target.value })} />
        </label>
        <button className="btn-primary mt-5" onClick={() => save({ categories: draft.categories, investor_notes: draft.investor_notes }, "目标配置已更新")}>保存目标</button>
      </Section>

      <Section title="节奏" delay={0.16}>
        <div className="grid gap-4 sm:grid-cols-3">
          <NumField label="多久跑一轮" unit="分钟" value={draft.schedule.cycle_minutes} min={b.cycle_minutes?.[0]} max={b.cycle_minutes?.[1]}
            onChange={(x) => setDraft({ ...draft, schedule: { ...draft.schedule, cycle_minutes: x } })} />
          <NumField label="审批有效期" unit="分钟" value={draft.schedule.approval_timeout_minutes} min={b.approval_timeout_minutes?.[0]} max={b.approval_timeout_minutes?.[1]}
            onChange={(x) => setDraft({ ...draft, schedule: { ...draft.schedule, approval_timeout_minutes: x } })} />
          <label className="block">
            <span className="label">日报时间（{draft.schedule.timezone}）</span>
            <input type="time" className="input num mt-1.5" value={draft.schedule.daily_report_time}
              onChange={(e) => setDraft({ ...draft, schedule: { ...draft.schedule, daily_report_time: e.target.value } })} />
          </label>
        </div>
        <button className="btn-primary mt-5" onClick={() => save({ schedule: draft.schedule }, "节奏已更新")}>保存</button>
      </Section>

      <Section title="连接" sub="密钥保存在服务器的 data/.env，这里只显示是否已配置。" delay={0.2}>
        <ul className="grid gap-2 text-[14px] sm:grid-cols-2">
          {[["binance_api_key", "币安 API Key"], ["telegram_bot_token", "Telegram 机器人"], ["claude_code_oauth_token", "Claude Code token"],
            ["openai_api_key", "OpenAI API Key"], ["anthropic_api_key", "Anthropic API Key"], ["openai_compat_api_key", "兼容接口 API Key"]].map(([k, l]) => (
            <li key={k} className="flex items-center gap-2">
              {data.secrets[k] ? <CheckCircle size={18} weight="fill" className="text-mint" /> : <span className="h-[18px] w-[18px] rounded-full border border-white/15" />}
              <span className={data.secrets[k] ? "text-ink" : "text-ink-mute"}>{l}</span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
