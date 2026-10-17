import { MagnifyingGlass, Plus, Check } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { fmtPrice } from "../lib";

export interface Market { token: string; symbol: string; price: number | null; change_pct: number | null; quote_volume: number; held: boolean; enabled: boolean }

export default function TokenPicker({ selected, onToggle, endpoint = "/markets", placeholder = "搜索币安现货" }:
  { selected: string[]; onToggle: (t: string) => void; endpoint?: string; placeholder?: string }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Market[]>([]);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const seq = useRef(0);

  useEffect(() => {
    if (!open) return;
    const id = setTimeout(async () => {
      const n = ++seq.current;                        // only the latest query may update the list
      setBusy(true);
      let next: Market[] = [];
      try { next = await api<Market[]>(`${endpoint}?q=${encodeURIComponent(q.trim())}&limit=40`); } catch { next = []; }
      if (n !== seq.current) return;
      setRows(next);
      setBusy(false);
    }, 200);
    return () => clearTimeout(id);
  }, [q, open, endpoint]);

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
        <div className="absolute z-30 mt-1 max-h-[320px] w-full overflow-y-auto rounded-lg border border-line-strong bg-raise py-1 shadow-xl">
          {busy && <div className="px-3 py-1 text-[12px] text-fg-3">…</div>}
          {!busy && !rows.length && <div className="px-3 py-2 text-[12.5px] text-fg-3">无匹配</div>}
          {rows.map((m) => {
            const on = selected.includes(m.token);
            return (
              <button key={m.symbol} type="button" onClick={() => onToggle(m.token)}
                className="flex w-full items-center gap-3 px-3 py-1.5 text-left hover:bg-hover">
                <span className="w-20 truncate text-[13px] font-medium">{m.token}{m.held && <span className="ml-1 text-[11px] text-fg-3">持有</span>}</span>
                <span className="num flex-1 text-right text-[12.5px] text-fg-2">{m.price != null ? fmtPrice(m.price) : "—"}</span>
                <span className={`num w-16 text-right text-[12.5px] ${(m.change_pct ?? 0) >= 0 ? "text-up" : "text-down"}`}>
                  {m.change_pct != null ? `${m.change_pct >= 0 ? "+" : ""}${(m.change_pct * 100).toFixed(2)}%` : ""}
                </span>
                {on ? <Check size={14} weight="bold" className="text-up" /> : <Plus size={14} className="text-fg-3" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
