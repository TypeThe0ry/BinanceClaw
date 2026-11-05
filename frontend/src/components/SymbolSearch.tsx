import { MagnifyingGlass } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Change } from "./ui";
import { fmtPrice } from "../lib";
import type { Market } from "./TokenPicker";

/** Search every Binance spot pair or USDT perpetual and jump to it. */
export default function SymbolSearch({ endpoint, placeholder, onPick }:
  { endpoint: "/markets" | "/futures/markets"; placeholder: string; onPick: (symbol: string) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<Market[]>([]);
  const box = useRef<HTMLDivElement>(null);
  const seq = useRef(0);

  useEffect(() => {
    if (!open) return;
    const id = setTimeout(async () => {
      const n = ++seq.current;
      let next: Market[] = [];
      try { next = await api<Market[]>(`${endpoint}?q=${encodeURIComponent(q.trim())}&limit=30`); } catch { next = []; }
      if (n === seq.current) setRows(next.filter((r) => r.symbol && r.selectable !== false));
    }, 200);
    return () => clearTimeout(id);
  }, [q, open, endpoint]);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  function pick(sym: string) {
    onPick(sym);
    setOpen(false);
    setQ("");
  }

  return (
    <div ref={box} className="relative w-full sm:w-[240px]">
      <label className="flex h-8 items-center gap-2 rounded-lg bg-field px-3">
        <MagnifyingGlass size={14} className="text-fg-3" />
        <input className="h-8 min-w-0 flex-1 bg-transparent text-[13px] focus:outline-none" placeholder={placeholder}
          value={q} onFocus={() => setOpen(true)} onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onKeyDown={(e) => { if (e.key === "Enter" && rows[0]) pick(rows[0].symbol); if (e.key === "Escape") setOpen(false); }} />
      </label>
      {open && rows.length > 0 && (
        <ul role="listbox" className="absolute z-30 mt-1 max-h-[360px] w-full min-w-[260px] overflow-y-auto rounded-lg border border-line-strong bg-raise py-1 shadow-xl">
          {rows.map((r) => (
            <li key={r.symbol}>
              <button role="option" aria-selected={false} onClick={() => pick(r.symbol)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-hover">
                <span className="font-semibold">{r.token}</span>
                {r.held && <span className="tag bg-hover text-fg-2">持有</span>}
                {r.trading === false && <span className="tag bg-hover text-fg-3">暂停</span>}
                <span className="num ml-auto">{fmtPrice(r.price)}</span>
                <span className="w-14 text-right"><Change v={r.change_pct} /></span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
