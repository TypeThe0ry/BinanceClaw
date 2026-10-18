import { MagnifyingGlass, Plus, Check } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { fmtPrice } from "../lib";

export interface Market {
  token: string; symbol: string; price: number | null; change_pct: number | null; quote_volume: number; held: boolean; enabled: boolean;
  status?: string; trading?: boolean; selectable?: boolean; quotes?: string[]; tags?: string[]; category?: string;
}
interface Cat { tag: string; label: string; count: number }

export default function TokenPicker({ selected, onToggle, endpoint = "/markets", placeholder = "搜索币安现货" }:
  { selected: string[]; onToggle: (t: string) => void; endpoint?: string; placeholder?: string }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Market[]>([]);
  const [busy, setBusy] = useState(false);
  const [cats, setCats] = useState<Cat[]>([]);
  const [tag, setTag] = useState("");
  const withCats = endpoint === "/markets";            // Binance's categories exist for spot coins
  const box = useRef<HTMLDivElement>(null);
  const seq = useRef(0);

  useEffect(() => {
    if (!open) return;
    const id = setTimeout(async () => {
      const n = ++seq.current;                        // only the latest query may update the list
      setBusy(true);
      let next: Market[] = [];
      const filter = tag ? `&tag=${encodeURIComponent(tag)}&limit=200` : "&limit=40";
      try { next = await api<Market[]>(`${endpoint}?q=${encodeURIComponent(q.trim())}${filter}`); } catch { next = []; }
      if (n !== seq.current) return;
      setRows(next);
      setBusy(false);
    }, 200);
    return () => clearTimeout(id);
  }, [q, open, endpoint, tag]);

  useEffect(() => {
    if (!open || !withCats || cats.length) return;
    api<Cat[]>("/categories").then(setCats).catch(() => {});
  }, [open, withCats, cats.length]);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  return (
    <div ref={box} className="relative w-full max-w-[360px]">
      <label className="flex h-9 items-center gap-2 rounded-lg bg-field px-3">
        <MagnifyingGlass size={14} className="text-fg-3" />
        <input className="h-9 min-w-0 flex-1 bg-transparent text-[13px] focus:outline-none" placeholder={placeholder}
          value={q} onFocus={() => setOpen(true)} onChange={(e) => { setQ(e.target.value); setOpen(true); }} />
      </label>
      {open && (
        <div className="absolute z-30 mt-1 max-h-[360px] w-full overflow-y-auto rounded-lg border border-line-strong bg-raise py-1 shadow-xl">
          {withCats && cats.length > 0 && (
            <div className="sticky top-0 z-10 -mt-1 flex gap-1.5 overflow-x-auto border-b border-line bg-raise px-3 py-2" role="group" aria-label="币安分类">
              {[{ tag: "", label: "全部", count: 0 }, ...cats].map((c) => (
                <button key={c.tag || "all"} type="button" aria-pressed={tag === c.tag} onClick={() => setTag(c.tag)}
                  className={`shrink-0 rounded-md px-2 py-0.5 text-[12px] ${tag === c.tag ? "bg-fg text-bg" : "bg-field text-fg-2 hover:text-fg"}`}>
                  {c.label}{c.count ? <span className="ml-1 opacity-60">{c.count}</span> : null}
                </button>
              ))}
            </div>
          )}
          {busy && <div className="px-3 py-1 text-[12px] text-fg-3">…</div>}
          {!busy && !rows.length && <div className="px-3 py-2 text-[12.5px] text-fg-3">无匹配</div>}
          {rows.map((m) => {
            const on = selected.includes(m.token);
            const selectable = m.selectable !== false;
            const halted = selectable && m.trading === false;
            return (
              <button key={m.symbol || m.token} type="button" disabled={!selectable && !on} onClick={() => onToggle(m.token)}
                title={!selectable ? `币安上只有 ${m.quotes?.join("/")} 报价，没有 USDT 交易对` : halted ? "币安暂停交易中，可以选，恢复交易前不会买卖" : undefined}
                className="flex w-full items-center gap-3 px-3 py-1.5 text-left hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50">
                <span className="w-20 truncate text-[13px] font-medium">{m.token}{m.held && <span className="ml-1 text-[11px] text-fg-3">持有</span>}</span>
                {withCats && <span className="hidden w-24 truncate text-[11.5px] text-fg-3 sm:inline" title={m.tags?.join(" · ")}>{m.category}</span>}
                {!selectable ? (
                  <span className="flex-1 text-right text-[12px] text-fg-3">仅 {m.quotes?.join("/")} 报价</span>
                ) : (
                  <>
                    <span className="num flex-1 text-right text-[12.5px] text-fg-2">
                      {halted && <span className="mr-2 rounded bg-field px-1.5 py-0.5 text-[11px] text-down">暂停交易</span>}
                      {m.price != null ? fmtPrice(m.price) : "—"}
                    </span>
                    <span className={`num w-16 text-right text-[12.5px] ${(m.change_pct ?? 0) >= 0 ? "text-up" : "text-down"}`}>
                      {!halted && m.change_pct != null ? `${m.change_pct >= 0 ? "+" : ""}${(m.change_pct * 100).toFixed(2)}%` : ""}
                    </span>
                  </>
                )}
                {on ? <Check size={14} weight="bold" className="text-up" /> : <Plus size={14} className="text-fg-3" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
