import { Check, Warning, X } from "@phosphor-icons/react";
import { useState } from "react";
import { post } from "../api";
import { Page, Seg, SectionHead, Skeleton, Spinner } from "../components/ui";
import { useApi } from "../lib";
import { setPref, useThemePref } from "../theme";
import { DEBATE_DEFAULT, type DebateCfg, NEWS_DEFAULT, type NewsCfg, type NotifyCfg, Num, type Routing, Row, type Seat, SettingsNav, type Tier, useSettingsDraft } from "../components/settingsKit";

interface EarnResp { enabled: boolean; positions: Record<string, { amount: number; apr: number | null }>;
  products: Record<string, { apr?: string | null; error?: string }> }

interface LiveCheck { ok: boolean; blockers: string[]; rows: { label: string; field: string; expected: boolean; actual: boolean | null; ok: boolean; waived: boolean; waivable: boolean }[] }

interface Device { id: string; current: boolean; created: number; last_login: number; ua: string; ip: string }

function deviceName(ua: string) {
  const os = /iPhone|iPad/.test(ua) ? "iOS" : /Android/.test(ua) ? "Android" : /Mac OS/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "设备";
  const br = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : /Firefox\//.test(ua) ? "Firefox" : "";
  return `${os}${br ? " · " + br : ""}`;
}

const TIER_LABEL: Record<Tier, string> = { fast: "轻量", strong: "决策", main: "主模型" };
const PROVIDER: Record<string, string> = { claude_code: "Claude 订阅", openai: "OpenAI", openai_compat: "OpenAI 兼容", anthropic_api: "Anthropic API" };
const NEWS_SOURCE: Record<NewsCfg["source"], string> = { web: "模型联网搜索", feeds: "新闻源" };
const SEATS: [Seat, string][] = [["bull", "多头"], ["bear", "空头"], ["risk", "风控"], ["judge", "决策"]];

/** 设置: system-level settings. Investment/AI decision config lives on the 配置 page (Config.tsx). */
export default function Settings() {
  const { data, reload, d, setD, save, toast } = useSettingsDraft();
  const live = useApi<LiveCheck>("/live/check");
  const devs = useApi<Device[]>("/devices");
  const earn = useApi<EarnResp>("/earn", { kinds: ["earn", "settings"] });
  const themePref = useThemePref();
  const [phrase, setPhrase] = useState("");
  const [testing, setTesting] = useState(false);
  const [searching, setSearching] = useState(false);
  if (!data || !d) return <Skeleton className="h-96" />;
  const b = data.bounds;

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
  const sch = d.schedule;
  const rt: Routing = d.model_routing ?? { enabled: false, tasks: {}, models: {} };
  const setRt = (patch: Partial<Routing>) => setD({ ...d, model_routing: { ...rt, ...patch } });
  const pname = d.provider.name;
  const db: DebateCfg = { ...DEBATE_DEFAULT, ...d.debate };
  const setDb = (patch: Partial<DebateCfg>) => setD({ ...d, debate: { ...db, ...patch } });
  const nw: NewsCfg = { ...NEWS_DEFAULT, ...d.news };
  const setNw = (patch: Partial<NewsCfg>) => setD({ ...d, news: { ...nw, ...patch } });
  const webOk = !!data.providers[pname]?.web_search;
  async function searchNews() {
    setSearching(true);
    try {
      const r = await post<{ items: unknown[]; source: string; errors?: string[] }>("/news/refresh");
      toast(r.items.length ? `${r.items.length} 条 · ${(r.source === "model" ? NEWS_SOURCE.web : NEWS_SOURCE.feeds)}` : (r.errors?.[0] ?? "0 条"), r.items.length ? "ok" : "err");
    } catch (e) { toast((e as Error).message, "err"); }
    setSearching(false);
  }
  const setSch = (patch: Partial<typeof sch>) => setD({ ...d, schedule: { ...sch, ...patch } });

  return (
    <Page className="mx-auto flex max-w-[820px] flex-col gap-10">
      <SettingsNav current="/settings" />

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
                    <input type="checkbox" className="h-3.5 w-3.5 accent-warn" checked={d.live_gate_allow.includes(r.field)}
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
        <Row label="按任务分配模型">
          <input type="checkbox" className="h-4 w-4 accent-white" checked={rt.enabled} aria-label="按任务分配模型"
            onChange={(e) => setRt({ enabled: e.target.checked })} />
        </Row>
        {rt.enabled && (
          <>
            {(["fast", "strong"] as const).map((t) => (
              <Row key={t} label={t === "fast" ? "轻量模型（更新、总结类）" : "决策模型（投资、决策类）"}>
                <input className="input h-9 w-56" aria-label={`${TIER_LABEL[t]}模型`} value={rt.models[pname]?.[t] ?? ""}
                  placeholder={data.tier_models?.[pname]?.[t] || "同主模型"}
                  onChange={(e) => setRt({ models: { ...rt.models, [pname]: { ...rt.models[pname], [t]: e.target.value } } })} />
              </Row>
            ))}
            {Object.entries(data.model_tasks ?? {}).map(([k, label]) => (
              <Row key={k} label={label}>
                <Seg id={`route-${k}`} value={rt.tasks[k] ?? "main"}
                  items={(["fast", "strong", "main"] as const).map((t) => ({ k: t, label: TIER_LABEL[t] }))}
                  onChange={(t) => setRt({ tasks: { ...rt.tasks, [k]: t } })} />
              </Row>
            ))}
          </>
        )}
        {!data.providers[d.provider.name]?.ok && <Row label="状态"><span className="text-[13px] text-warn">{data.providers[d.provider.name]?.message}</span></Row>}
        <div className="flex gap-2 pt-4">
          <button className="btn-primary" onClick={() => save({ provider: d.provider, provider_fallback: d.provider_fallback, model_routing: rt })}>保存</button>
          <button className="btn-secondary" onClick={test} disabled={testing}>{testing ? <Spinner /> : "连通测试"}</button>
        </div>
      </section>

      <section>
        <SectionHead title="多空辩论" doc="debate" />
        <Row label="开启">
          <input type="checkbox" className="h-4 w-4 accent-white" checked={db.enabled} aria-label="开启多空辩论"
            onChange={(e) => setDb({ enabled: e.target.checked })} />
        </Row>
        {db.enabled && (
          <>
            <Row label="轮数">
              <Seg id="debate-rounds" value={String(db.rounds)} onChange={(k) => setDb({ rounds: Number(k) })}
                items={["1", "2", "3"].map((k) => ({ k, label: `${k} 轮` }))} />
            </Row>
            {SEATS.map(([k, label]) => {
              const seat = db[k];
              const prov = seat.provider || pname;
              const set = (patch: Partial<typeof seat>) => setDb({ [k]: { ...seat, ...patch } });
              return (
                <Row key={k} label={label}>
                  <select className="input h-9 w-40" aria-label={`${label}角色提供方`} value={seat.provider}
                    onChange={(e) => set({ provider: e.target.value, model: "", base_url: "" })}>
                    <option value="">同主模型</option>
                    {Object.entries(data.providers).map(([p, x]) => <option key={p} value={p}>{PROVIDER[p] ?? p}{x.ok ? "" : "（未配置）"}</option>)}
                  </select>
                  <input className="input h-9 w-48" aria-label={`${label}角色模型`} value={seat.model}
                    placeholder={(seat.provider && data.providers[prov]?.default_model) || "默认"}
                    onChange={(e) => set({ model: e.target.value })} />
                  {seat.provider === "openai_compat" && seat.provider !== pname && (
                    <input className="input h-9 w-56" aria-label={`${label}角色 Base URL`} value={seat.base_url} placeholder="https://api.deepseek.com/v1"
                      onChange={(e) => set({ base_url: e.target.value })} />
                  )}
                </Row>
              );
            })}
          </>
        )}
        <div className="pt-4"><button className="btn-primary" onClick={() => save({ debate: db })}>保存</button></div>
      </section>

      <section>
        <SectionHead title="联网新闻" doc="news" />
        <Row label="开启">
          <input type="checkbox" className="h-4 w-4 accent-white" checked={nw.enabled} aria-label="开启联网新闻"
            onChange={(e) => setNw({ enabled: e.target.checked })} />
        </Row>
        {nw.enabled && (
          <>
            <Row label="来源">
              <Seg id="news-source" value={nw.source} onChange={(k) => setNw({ source: k })}
                items={(Object.keys(NEWS_SOURCE) as NewsCfg["source"][]).map((k) => ({ k, label: NEWS_SOURCE[k] }))} />
            </Row>
            {nw.source !== "feeds" && !webOk && (
              <Row label="状态"><span className="text-[13px] text-warn">{PROVIDER[pname] ?? pname} 不支持联网搜索，使用新闻源</span></Row>
            )}
            <Row label="刷新间隔"><Num unit="分钟" value={nw.refresh_minutes} min={b.refresh_minutes?.[0]} max={b.refresh_minutes?.[1]} onChange={(x) => setNw({ refresh_minutes: x })} /></Row>
            <Row label="时间范围"><Num unit="小时" value={nw.lookback_hours} min={b.lookback_hours?.[0]} max={b.lookback_hours?.[1]} onChange={(x) => setNw({ lookback_hours: x })} /></Row>
            <Row label="条数上限"><Num unit="条" value={nw.max_items} min={b.max_items?.[0]} max={b.max_items?.[1]} onChange={(x) => setNw({ max_items: x })} /></Row>
            <Row label="关注话题">
              <input className="input h-9 w-72" aria-label="关注话题" value={nw.topics.join("，")} placeholder="美联储，ETF"
                onChange={(e) => setNw({ topics: e.target.value.split(/[,，、]/) })} />
            </Row>
            {(nw.source === "feeds" || !webOk) && (
              <Row label="新闻源">
                <textarea className="input min-h-[96px] w-80 py-2 text-[13px]" aria-label="新闻源" value={nw.feeds.join("\n")}
                  onChange={(e) => setNw({ feeds: e.target.value.split("\n") })} />
              </Row>
            )}
          </>
        )}
        <div className="flex gap-2 pt-4">
          <button className="btn-primary" onClick={() => save({ news: { ...nw, topics: nw.topics.map((t) => t.trim()).filter(Boolean),
            feeds: nw.feeds.map((t) => t.trim()).filter(Boolean) } })}>保存</button>
          {nw.enabled && <button className="btn-secondary" onClick={searchNews} disabled={searching}>{searching ? <Spinner /> : "立即搜索"}</button>}
        </div>
      </section>

      <section>
        <SectionHead title="节奏" doc="decisions-and-risk" />
        <Row label="决策方式">
          <Seg id="decision" value={sch.decision_mode ?? "auto"} onChange={(k) => setSch({ decision_mode: k })}
            items={[{ k: "auto" as const, label: "AI 自主实时" }, { k: "fixed" as const, label: "固定间隔" }]} />
        </Row>
        {(sch.decision_mode ?? "auto") === "auto" ? (
          <>
            <Row label="AI 未指定时的复查间隔"><Num unit="分钟" value={sch.cycle_minutes} min={b.cycle_minutes?.[0]} max={b.cycle_minutes?.[1]} onChange={(x) => setSch({ cycle_minutes: x })} /></Row>
            <Row label="两轮最短间隔"><Num unit="分钟" value={sch.min_gap_minutes ?? 5} min={b.min_gap_minutes?.[0]} max={b.min_gap_minutes?.[1]} onChange={(x) => setSch({ min_gap_minutes: x })} /></Row>
            <Row label="最长不决策"><Num unit="分钟" value={sch.max_gap_minutes ?? 240} min={b.max_gap_minutes?.[0]} max={b.max_gap_minutes?.[1]} onChange={(x) => setSch({ max_gap_minutes: x })} /></Row>
            <Row label="每日模型调用上限"><Num unit="次" value={sch.max_calls_per_day ?? 48} min={b.max_calls_per_day?.[0]} max={b.max_calls_per_day?.[1]} onChange={(x) => setSch({ max_calls_per_day: x })} /></Row>
            <Row label="默认波动唤醒阈值"><Num unit="%" step={0.1} value={+((sch.move_trigger_pct ?? 0.02) * 100).toFixed(2)} onChange={(x) => setSch({ move_trigger_pct: x / 100 })} /></Row>
          </>
        ) : (
          <Row label="决策间隔"><Num unit="分钟" value={sch.cycle_minutes} min={b.cycle_minutes?.[0]} max={b.cycle_minutes?.[1]} onChange={(x) => setSch({ cycle_minutes: x })} /></Row>
        )}
        <Row label="审批有效期"><Num unit="分钟" value={d.schedule.approval_timeout_minutes} min={b.approval_timeout_minutes?.[0]} max={b.approval_timeout_minutes?.[1]} onChange={(x) => setD({ ...d, schedule: { ...d.schedule, approval_timeout_minutes: x } })} /></Row>
        <Row label={`日报时间 · ${d.schedule.timezone}`}>
          <input type="time" className="input num h-9 w-32" value={d.schedule.daily_report_time} onChange={(e) => setD({ ...d, schedule: { ...d.schedule, daily_report_time: e.target.value } })} />
        </Row>
        <div className="pt-4"><button className="btn-primary" onClick={() => save({ schedule: d.schedule })}>保存</button></div>
      </section>

      <section>
        <SectionHead title="理财" doc="earn" />
        <Row label="闲置资产自动申购活期">
          <input type="checkbox" className="h-4 w-4 accent-white" checked={d.earn.enabled} aria-label="闲置资产自动申购活期"
            onChange={(e) => setD({ ...d, earn: { ...d.earn, enabled: e.target.checked } })} />
        </Row>
        <Row label="申购全部可申购的产品">
          <input type="checkbox" className="h-4 w-4 accent-white" checked={d.earn.auto_all} aria-label="申购全部可申购的产品"
            onChange={(e) => setD({ ...d, earn: { ...d.earn, auto_all: e.target.checked } })} />
        </Row>
        {!d.earn.auto_all && (
          <Row label="只申购这些稳定币">
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
        )}
        <Row label="稳定币现货保留"><Num unit="USD" value={d.earn.keep_liquid_usd} min={0} onChange={(x) => setD({ ...d, earn: { ...d.earn, keep_liquid_usd: x } })} /></Row>
        <Row label="最小划转"><Num unit="USD" value={d.earn.min_move_usd} min={1} onChange={(x) => setD({ ...d, earn: { ...d.earn, min_move_usd: x } })} /></Row>
        {Object.entries(earn.data?.positions ?? {}).map(([a, p]) => (
          <Row key={a} label={<>活期 <b className="text-fg">{a}</b>{earn.data?.products?.[a]?.apr
            ? <span className="text-up"> {(parseFloat(earn.data.products[a].apr as string) * 100).toFixed(2)}%</span> : null}</>}>
            <span className="num text-[14px]">{p.amount.toLocaleString("en-US", { maximumFractionDigits: d.stables.includes(a) ? 2 : 8 })}</span>
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

      {d.notify && data.notify_types && (() => {
        const n = d.notify;
        const setN = (patch: Partial<NotifyCfg>) => setD({ ...d, notify: { ...n, ...patch } });
        return (
          <section>
            <SectionHead title="Telegram 推送" doc="telegram" />
            {data.telegram === false && <p className="py-2 text-[13px] text-warn">Telegram 未配置</p>}
            <Row label="推送频率">
              <Seg id="notify-mode" value={n.mode} onChange={(k) => setN({ mode: k })}
                items={[{ k: "instant" as const, label: "即时" }, { k: "digest" as const, label: "定时汇总" }]} />
            </Row>
            {n.mode === "digest" && (
              <Row label="汇总间隔"><Num unit="分钟" value={n.digest_minutes} min={b.digest_minutes?.[0]} max={b.digest_minutes?.[1]} onChange={(x) => setN({ digest_minutes: x })} /></Row>
            )}
            <Row label={`免打扰 · ${d.schedule.timezone}`}>
              <input type="checkbox" className="h-4 w-4 accent-white" checked={n.quiet.enabled} aria-label="免打扰"
                onChange={(e) => setN({ quiet: { ...n.quiet, enabled: e.target.checked } })} />
              {n.quiet.enabled && <>
                <input type="time" className="input num h-9 w-28" aria-label="免打扰开始" value={n.quiet.start} onChange={(e) => setN({ quiet: { ...n.quiet, start: e.target.value } })} />
                <span className="text-fg-3">–</span>
                <input type="time" className="input num h-9 w-28" aria-label="免打扰结束" value={n.quiet.end} onChange={(e) => setN({ quiet: { ...n.quiet, end: e.target.value } })} />
              </>}
            </Row>
            {Object.entries(data.notify_types).map(([k, t]) => (
              <Row key={k} label={t.label}>
                {t.always ? <span className="text-[13px] text-fg-3">始终推送</span> : (
                  <input type="checkbox" className="h-4 w-4 accent-white" aria-label={t.label} checked={n.types[k] ?? true}
                    onChange={(e) => setN({ types: { ...n.types, [k]: e.target.checked } })} />
                )}
              </Row>
            ))}
            <div className="pt-4"><button className="btn-primary" onClick={() => save({ notify: n })}>保存</button></div>
          </section>
        );
      })()}

      <section>
        <SectionHead title="外观" />
        <Row label={<>主题<span className="mt-0.5 block text-[12px] text-fg-3">跟随系统时随电脑、手机的深浅色自动切换，只对这个浏览器生效</span></>}>
          <Seg id="theme" value={themePref} onChange={setPref}
            items={[{ k: "system", label: "跟随系统" }, { k: "light", label: "浅色" }, { k: "dark", label: "深色" }]} />
        </Row>
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
