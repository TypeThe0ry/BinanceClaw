import { Brain, Warning } from "@phosphor-icons/react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { post, setConfirmer, setRerunOfferer, type DangerChange } from "../api";
import { useToast } from "../lib";

const PCT = new Set(["limits.stable_floor", "limits.btc_7d_drop_pause", "limits.price_deviation_anomaly", "limits.fee_buffer_ratio",
  "allocation.max_asset_weight", "allocation.rebalance_drift", "futures.max_stop_distance"]);
const ENUM: Record<string, string> = { targets: "手动分配", ai: "AI 自主分配", all: "全部资产", whitelist: "白名单", blacklist: "黑名单" };

/** Human-readable value of a setting, as shown in the second-confirmation dialog. */
export function fmtSetting(field: string, v: unknown): string {
  if (v == null) return "不限";
  if (typeof v === "boolean") return v ? "开启" : "关闭";
  if (typeof v === "number") return PCT.has(field) ? `${+(v * 100).toFixed(2)}%` : v.toLocaleString("en-US", { maximumFractionDigits: 8 });
  if (typeof v === "string") return ENUM[v] ?? v;
  if (Array.isArray(v)) return v.length ? v.join("、") : "无";
  if (field === "categories") return "目标配置有改动";
  const e = Object.entries(v as Record<string, unknown>);
  return e.length ? e.map(([k, x]) => `${k} ${fmtSetting("", x)}`).join("，") : "无";
}

/**
 * Owns the dialogs api() opens: the confirmation the server asks for before a dangerous change, and,
 * after a save that affects future decisions, the offer to run a new round now so the AI adapts.
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<{ changes: DangerChange[]; done: (ok: boolean) => void } | null>(null);
  const [rerun, setRerun] = useState<string[] | null>(null);
  const confirmBtn = useRef<HTMLButtonElement>(null);
  const toast = useToast();
  useEffect(() => {
    setConfirmer((changes) => new Promise<boolean>((done) => setPending({ changes, done })));
    setRerunOfferer((changed) => setRerun(changed));
    return () => { setConfirmer(null); setRerunOfferer(null); };
  }, []);
  async function runNow() {
    setRerun(null);
    try { await post("/cycles/run"); toast("已开始新一轮决策"); } catch (e) { toast((e as Error).message, "err"); }
  }
  useEffect(() => {
    if (!pending) return;
    confirmBtn.current?.focus();
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") close(false); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending]);
  function close(ok: boolean) {
    pending?.done(ok);
    setPending(null);
  }
  return (
    <>
      {children}
      {pending && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center" onMouseDown={() => close(false)}>
          <div role="dialog" aria-modal="true" aria-labelledby="confirm-title" onMouseDown={(e) => e.stopPropagation()}
            className="w-full max-w-[440px] rounded-xl border border-line-strong bg-raise p-5 shadow-2xl">
            <h2 id="confirm-title" className="flex items-center gap-2 text-[16px] font-semibold">
              <Warning size={18} weight="fill" className="text-warn" />确认修改危险设置
            </h2>
            <p className="mt-1.5 text-[13px] text-fg-2">这些设置会影响资金安全，确认后才会生效。</p>
            <ul className="mt-4 flex flex-col gap-2 text-[14px]">
              {pending.changes.map((c) => (
                <li key={c.field} className="flex flex-wrap items-baseline justify-between gap-x-4 border-b border-line pb-2">
                  <span className="text-fg-2">{c.label}</span>
                  <span className="num"><span className="text-fg-3 line-through">{fmtSetting(c.field, c.old)}</span> → <b>{fmtSetting(c.field, c.new)}</b></span>
                </li>
              ))}
            </ul>
            <div className="mt-5 flex justify-end gap-2">
              <button className="btn-secondary" onClick={() => close(false)}>取消</button>
              <button ref={confirmBtn} className="btn-down" onClick={() => close(true)}>确认修改</button>
            </div>
          </div>
        </div>
      )}
      {rerun && !pending && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-4 sm:items-center" onMouseDown={() => setRerun(null)}>
          <div role="dialog" aria-modal="true" aria-labelledby="rerun-title" onMouseDown={(e) => e.stopPropagation()}
            className="w-full max-w-[440px] rounded-xl border border-line-strong bg-raise p-5 shadow-2xl">
            <h2 id="rerun-title" className="flex items-center gap-2 text-[16px] font-semibold">
              <Brain size={18} weight="fill" />让 AI 按新设置重新跑一轮？
            </h2>
            <p className="mt-1.5 text-[14px] text-fg-2">
              已保存。这些改动会影响 AI 之后的决策：{rerun.join("、")}。现在运行一轮，AI 会立刻按新设置调整；
              否则等到它下一次复查时才生效。手动运行不占用每日调用额度。
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button className="btn-secondary" onClick={() => setRerun(null)}>稍后</button>
              <button autoFocus className="btn-primary" onClick={runNow}>立即重跑</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
