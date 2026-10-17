import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api";

// ---------------------------------------------------------------- formatting
export const usd = (v: number | undefined | null, digits = 2) =>
  v == null || Number.isNaN(v) ? "—" : `$${v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
export const pct = (v: number | undefined | null, digits = 1) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);
export const signedPct = (v: number | undefined | null, digits = 1) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;

export function ago(ts: number) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 45) return "刚刚";
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`;
  const d = new Date(ts * 1000);
  const today = new Date();
  const hm = d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  if (d.toDateString() === today.toDateString()) return `今天 ${hm}`;
  const y = new Date(today.getTime() - 86400000);
  if (d.toDateString() === y.toDateString()) return `昨天 ${hm}`;
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${hm}`;
}

export const clock = (ts: number) =>
  new Date(ts * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });

export const token = (symbol: string) => symbol.replace(/USDT$/, "");

export const CATEGORY_LABEL: Record<string, string> = { BTC: "比特币", ETH: "以太坊", US_STOCK: "美股代币", STABLE: "稳定币", OTHER: "其他" };
export const CATEGORY_COLOR: Record<string, string> = {
  BTC: "#F7931A", ETH: "#8C8CFF", US_STOCK: "#20B26C", STABLE: "#8C8C8C", OTHER: "#3A3A3A",
};

// ---------------------------------------------------------------- live events (SSE)
type Evt = { kind: string; ts: number; data: Record<string, unknown> };
const LiveCtx = createContext<{ tick: number; last: Evt | null; connected: boolean }>({ tick: 0, last: null, connected: false });

export function LiveProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState({ tick: 0, last: null as Evt | null, connected: false });
  useEffect(() => {
    let es: EventSource | null = null;
    let retry: number | undefined;
    const open = () => {
      es = new EventSource("/api/events");
      es.onopen = () => setState((s) => ({ ...s, connected: true }));
      es.onmessage = (m) => {
        try {
          const evt = JSON.parse(m.data) as Evt;
          setState((s) => ({ tick: s.tick + 1, last: evt, connected: true }));
        } catch {
          /* ignore */
        }
      };
      es.onerror = () => {
        setState((s) => ({ ...s, connected: false }));
        es?.close();
        retry = window.setTimeout(open, 4000);
      };
    };
    open();
    return () => {
      es?.close();
      window.clearTimeout(retry);
    };
  }, []);
  return <LiveCtx.Provider value={state}>{children}</LiveCtx.Provider>;
}

export const useLive = () => useContext(LiveCtx);

/** Fetch JSON, refetch whenever a live event arrives (debounced) or every `poll` ms. */
export function useApi<T>(path: string | null, opts: { poll?: number; kinds?: string[] } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const { tick, last } = useLive();
  const timer = useRef<number>();
  const load = useCallback(async () => {
    if (!path) return;
    try {
      setData(await api<T>(path));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!tick) return;
    if (opts.kinds && last && !opts.kinds.includes(last.kind)) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(load, 250);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);
  useEffect(() => {
    if (!opts.poll) return;
    const id = window.setInterval(load, opts.poll);
    return () => window.clearInterval(id);
  }, [load, opts.poll]);
  return { data, error, loading, reload: load, setData };
}

// ---------------------------------------------------------------- toast
const ToastCtx = createContext<(msg: string, tone?: "ok" | "err") => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; msg: string; tone: "ok" | "err" }[]>([]);
  const push = useCallback((msg: string, tone: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, msg, tone }]);
    window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), 4200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 top-4 z-[60] flex flex-col items-center gap-2 px-4">
        {items.map((t) => (
          <div key={t.id}
            className={`pointer-events-auto max-w-md rounded-lg border border-line-strong bg-raise px-4 py-2.5 text-[14px] shadow-2xl ${t.tone === "err" ? "text-down" : "text-fg"}`}
            role="status">
            {t.msg}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

// ---------------------------------------------------------------- live ticker (shared, polled)
export interface Tick { price: number; change_pct: number; high: number; low: number; quote_volume: number }
const TickerCtx = createContext<Record<string, Tick>>({});
export const useTicker = () => useContext(TickerCtx);

export function TickerProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<Record<string, Tick>>({});
  useEffect(() => {
    let alive = true;
    const load = () => api<Record<string, Tick>>("/ticker").then((d) => alive && setData(d)).catch(() => {});
    load();
    const id = window.setInterval(() => { if (!document.hidden) load(); }, 4000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);
  return <TickerCtx.Provider value={data}>{children}</TickerCtx.Provider>;
}

/** Remembers the previous value and returns "up" | "down" for one render after it changes. */
export function useFlash(value: number | undefined) {
  const prev = useRef<number | undefined>(value);
  const [dir, setDir] = useState<"up" | "down" | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    if (value != null && prev.current != null && value !== prev.current) {
      setDir(value > prev.current ? "up" : "down");
      setN((x) => x + 1);
    }
    prev.current = value;
  }, [value]);
  return { dir, key: n };
}

export function fmtPrice(v: number | undefined) {
  if (v == null) return "—";
  const d = v >= 1000 ? 2 : v >= 1 ? 2 : 4;
  return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}
