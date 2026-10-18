import { Sparkle, X } from "@phosphor-icons/react";
import { useState } from "react";
import { api, post, type Category } from "../api";
import { CATEGORY_LABEL, useApi, useToast } from "../lib";
import TokenPicker from "./TokenPicker";
import { Spinner } from "./ui";

export interface Proposal {
  id: string; ts: number; source: string; instruction: string | null; summary: string; notes: string[];
  status: "pending" | "applied" | "discarded" | "expired";
  categories: Record<string, Category>; current: Record<string, number>; proposed: Record<string, number>;
}

const NEW = "__new__";
const pct1 = (v: number) => `${(v * 100).toFixed(1).replace(/\.0$/, "")}%`;

function Pct({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <label className="flex h-9 items-center rounded-lg bg-field pr-3">
      <input type="number" step={0.5} min={0} max={100} className="num h-9 w-20 bg-transparent px-3 text-right text-[14px] focus:outline-none"
        value={Number.isFinite(value) ? Math.round(value * 1000) / 10 : ""} onChange={(e) => onChange(parseFloat(e.target.value) / 100)} />
      <span className="text-[12px] text-fg-3">%</span>
    </label>
  );
}

/** Target weights: edit by hand, or have the model draft / edit them; a draft changes nothing until applied. */
export default function AllocationEditor({ categories, enabled, notes, stables, mode, onSaved }: {
  categories: Record<string, Category>; enabled: string[]; notes: string; stables: string[]; mode: "targets" | "ai"; onSaved: () => void;
}) {
  const toast = useToast();
  const [cats, setCats] = useState<Record<string, Category>>(() => structuredClone(categories));
  const [inv, setInv] = useState(notes);
  const [into, setInto] = useState(NEW);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const prop = useApi<Proposal | null>("/allocation/proposal", { kinds: ["allocation"] });
  const p = prop.data?.status === "pending" ? prop.data : null;

  const targetTokens = Object.values(cats).flatMap((c) => Object.keys(c.tokens));
  const total = Object.values(cats).reduce((s, c) => s + Object.values(c.tokens).reduce<number>((a, w) => a + (w ?? 0), 0), 0);

  const setTok = (cat: string, tok: string, w: number | null) => {
    const c = cats[cat];
    const tokens = { ...c.tokens };
    if (w == null) delete tokens[tok]; else tokens[tok] = w;
    const next = { ...cats, [cat]: { ...c, tokens } };
    if (!Object.keys(tokens).length && cat !== "STABLE") delete next[cat];
    setCats(next);
  };
  const add = (tok: string) => {
    if (targetTokens.includes(tok)) {
      const cat = Object.keys(cats).find((k) => tok in cats[k].tokens)!;
      setTok(cat, tok, null);
      return;
    }
    const isStable = stables.includes(tok);
    const cat = isStable ? "STABLE" : into === NEW ? tok : into;
    const c = cats[cat] ?? { tokens: {}, band: [0, 0.1] as [number, number] };
    setCats({ ...cats, [cat]: { ...c, tokens: { ...c.tokens, [tok]: 0.05 } } });
  };

  async function save() {
    try {
      const en = Array.from(new Set([...enabled, ...targetTokens.filter((t) => !stables.includes(t))]));
      await api("/settings", { method: "PUT", json: { categories: cats, investor_notes: inv, allocation: { enabled: en } } });
      toast("已保存");
      onSaved();
    } catch (e) { toast((e as Error).message, "err"); }
  }
  async function propose() {
    setBusy(true);
    try { await post("/allocation/propose", { instruction }); setInstruction(""); prop.reload(); }
    catch (e) { toast((e as Error).message, "err"); }
    setBusy(false);
  }
  async function resolve(apply: boolean) {
    if (!p) return;
    try {
      await post("/allocation/resolve", { id: p.id, apply });
      toast(apply ? "已应用新的目标配置" : "已放弃");
      prop.reload();
      if (apply) onSaved();
    } catch (e) { toast((e as Error).message, "err"); }
  }

  const rows = p ? Array.from(new Set([...Object.keys(p.proposed), ...Object.keys(p.current)])) : [];

  return (
    <>
      {mode === "ai" && (
        <p className="border-b border-line py-3 text-[13px] text-fg-3">当前是「AI 自主分配」，目标配置暂不生效；应用 AI 提案会切换为「手动分配」。</p>
      )}
      {Object.entries(cats).map(([cat, c]) => (
        <div key={cat} className="border-b border-line py-3">
          <p className="text-[14px] font-medium">{CATEGORY_LABEL[cat] ?? cat}</p>
          <div className="mt-2 flex flex-wrap gap-x-6 gap-y-2">
            {Object.entries(c.tokens).map(([tok, w]) => (
              <label key={tok} className="flex items-center gap-2 text-[13px] text-fg-2">{tok}
                <Pct value={w ?? 0} onChange={(x) => setTok(cat, tok, x)} />
                {!(cat === "STABLE" && Object.keys(c.tokens).length === 1) && (
                  <button type="button" aria-label={`移除 ${tok}`} className="text-fg-3 hover:text-fg" onClick={() => setTok(cat, tok, null)}><X size={13} /></button>
                )}
              </label>
            ))}
            <label className="flex items-center gap-2 text-[13px] text-fg-2">下限
              <Pct value={c.band[0]} onChange={(x) => setCats({ ...cats, [cat]: { ...c, band: [x, c.band[1]] } })} />
            </label>
            <label className="flex items-center gap-2 text-[13px] text-fg-2">上限
              <Pct value={c.band[1]} onChange={(x) => setCats({ ...cats, [cat]: { ...c, band: [c.band[0], x] } })} />
            </label>
          </div>
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-3 border-b border-line py-3">
        <TokenPicker selected={targetTokens} onToggle={add} placeholder="添加资产到目标配置" />
        <select className="input h-9 w-36" value={into} onChange={(e) => setInto(e.target.value)} aria-label="放入类别">
          <option value={NEW}>单独一类</option>
          {Object.keys(cats).filter((k) => k !== "STABLE").map((k) => <option key={k} value={k}>{CATEGORY_LABEL[k] ?? k}</option>)}
        </select>
        <span className={`num ml-auto text-[13px] ${total > 1.0001 ? "text-down" : "text-fg-3"}`}>合计 {pct1(total)}{total > 1.0001 && "（超过 100%）"}</span>
      </div>
      <div className="border-b border-line py-3">
        <p className="text-[14px] font-medium">投资说明</p>
        <textarea className="input mt-2 h-20 py-2" value={inv} onChange={(e) => setInv(e.target.value)} />
      </div>
      <div className="pt-4"><button className="btn-primary" disabled={total > 1.0001} onClick={save}>保存</button></div>

      <div className="mt-6 rounded-lg border border-line p-4">
        <p className="flex items-center gap-1.5 text-[14px] font-medium"><Sparkle size={15} /> 让 AI 生成或修改</p>
        <textarea className="input mt-2 h-16 py-2" value={instruction} maxLength={2000} onChange={(e) => setInstruction(e.target.value)}
          placeholder="例如：加 10% TON，从 BTC 里减；或者：美股代币提高到 20%。留空则让 AI 按投资说明生成一份完整配置。" />
        <div className="mt-2 flex items-center gap-3">
          <button className="btn-secondary" disabled={busy} onClick={propose}>
            {busy ? <Spinner /> : instruction.trim() ? "按指令修改" : "AI 生成配置"}
          </button>
          <span className="text-[12px] text-fg-3">AI 只出提案，你应用之后才生效；之后的买入照常经过风控和审批。</span>
        </div>
        {p && (
          <div className="mt-4 border-t border-line pt-3">
            {p.instruction && <p className="text-[12.5px] text-fg-3">指令：{p.instruction}</p>}
            <p className="mt-1 whitespace-pre-wrap text-[13.5px]">{p.summary}</p>
            <div className="mt-3 grid grid-cols-[auto_1fr_1fr] gap-x-6 gap-y-1 text-[13px]">
              <span className="text-fg-3">资产</span><span className="text-right text-fg-3">当前</span><span className="text-right text-fg-3">提案</span>
              {rows.map((t) => {
                const a = p.current[t] ?? 0, b = p.proposed[t] ?? 0;
                return [
                  <span key={t + "k"} className="font-medium">{t}</span>,
                  <span key={t + "a"} className="num text-right text-fg-2">{pct1(a)}</span>,
                  <span key={t + "b"} className={`num text-right ${b > a + 1e-6 ? "text-up" : b < a - 1e-6 ? "text-down" : ""}`}>{pct1(b)}</span>,
                ];
              })}
            </div>
            {p.notes.map((n) => <p key={n} className="mt-2 text-[12.5px] text-down">{n}</p>)}
            <div className="mt-3 flex gap-2">
              <button className="btn-primary" onClick={() => resolve(true)}>应用</button>
              <button className="btn-secondary" onClick={() => resolve(false)}>放弃</button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
