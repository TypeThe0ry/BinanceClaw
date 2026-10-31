import { ArrowUp, TelegramLogo } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { post, type ChatMsg, type ChatPlan } from "../api";
import { Countdown, useNow } from "../components/ApprovalRow";
import { Md } from "../components/Md";
import { DocLink, Page, Side, Spinner } from "../components/ui";
import { ago, fmtPrice, token, usd, useApi, useToast } from "../lib";

const DONE: Record<string, [string, string]> = {
  rejected: ["已拒绝", "text-fg-2"], expired: ["已过期", "text-fg-3"], failed: ["未执行", "text-down"], approved: ["已成交", "text-up"],
  open: ["挂单中", "text-warn"], placing: ["下单中", "text-warn"], filled: ["已成交", "text-up"], cancelled: ["已撤单", "text-fg-2"],
};

function PlanCard({ a, onDone }: { a: ChatPlan; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const left = Math.max(0, a.expires - useNow());
  const limit = a.payload?.order === "limit";
  const lo = a.limit;
  const state = a.status === "pending" ? (left > 0 ? "pending" : "expired") : a.status === "approved" && lo ? lo.status : a.status;
  const result = lo?.result || a.result;

  async function act(path: string, kind: string) {
    setBusy(kind);
    try {
      const r = await post<{ ok: boolean; message: string }>(path);
      toast(r.message, r.ok ? "ok" : "err");
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(null);
      onDone();
    }
  }

  const [label, color] = a.resolved_by === "ai" && state === "rejected" ? ["已撤回", "text-fg-2"] : DONE[state] ?? [state, "text-fg-2"];
  return (
    <div className="mt-2 rounded-xl border border-line px-4 py-3" data-testid="plan">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="flex items-center gap-2 text-[14px]"><Side side={a.side} /><b className="font-semibold">{token(a.symbol)}</b></span>
        <span className="num text-[14px]">{usd(a.quote_usd)}</span>
        <span className="num text-[13px] text-fg-2">{limit ? `挂单 ${a.side === "BUY" ? "≤" : "≥"} ${fmtPrice(a.payload?.price)}` : "市价"}</span>
        <div className="ml-auto flex items-center gap-2">
          {state === "pending" ? (
            <>
              <Countdown left={left} />
              <button className="btn-secondary btn-sm" disabled={!!busy} onClick={() => act(`/approvals/${a.id}/reject`, "reject")}>{busy === "reject" ? "…" : "拒绝"}</button>
              <button className={`${a.side === "BUY" ? "btn-up" : "btn-down"} btn-sm`} disabled={!!busy} onClick={() => act(`/approvals/${a.id}/approve`, "approve")}>
                {busy === "approve" ? "…" : limit ? "批准挂单" : "批准下单"}
              </button>
            </>
          ) : (
            <>
              <span className={`text-[13px] ${color}`}>{label}</span>
              {state === "open" && lo && (
                <button className="btn-secondary btn-sm" disabled={!!busy} onClick={() => act(`/limit-orders/${lo.id}/cancel`, "cancel")}>{busy === "cancel" ? "…" : "撤单"}</button>
              )}
            </>
          )}
        </div>
      </div>
      {result && state !== "pending" && <p className="mt-1.5 text-[12.5px] text-fg-2">{result}</p>}
    </div>
  );
}

const SUGGEST = ["配置离目标差多少？", "上一轮为什么这么决定？", "BTC 跌到多少会暂停？", "这周买了什么？"];

export default function Chat() {
  const { data, reload, setData } = useApi<{ cut: number; messages: ChatMsg[]; plans?: Record<string, ChatPlan> }>("/chat?limit=200",
    { kinds: ["chat", "approval", "approval_created", "limit_order"] });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const toast = useToast();
  const msgs = (data?.messages ?? []).filter((m) => m.ts > (data?.cut ?? 0));

  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs.length, busy]);

  async function send(t = text) {
    const q = t.trim();
    if (!q || busy) return;
    setText("");
    setBusy(true);
    setData((d) => ({ cut: d?.cut ?? 0, plans: d?.plans, messages: [...(d?.messages ?? []), { id: -Date.now(), ts: Date.now() / 1000, channel: "web", role: "user", text: q }] }));
    try { await post("/chat", { text: q }); } catch (e) { toast((e as Error).message, "err"); } finally { setBusy(false); reload(); }
  }

  return (
    <Page className="mx-auto flex h-[calc(100dvh-190px)] max-w-[820px] flex-col md:h-[calc(100dvh-150px)]">
      <div className="flex items-center justify-between border-b border-line pb-3">
        <h1 className="flex items-center gap-1.5 text-[20px] font-semibold">对话<DocLink to="dashboard" /></h1>
        <button className="btn-secondary btn-sm" onClick={async () => { await post("/chat/new"); reload(); }}>新对话</button>
      </div>
      <div className="flex-1 space-y-6 overflow-y-auto py-6">
        {!msgs.length && (
          <div className="flex flex-wrap gap-2">
            {SUGGEST.map((s) => <button key={s} className="btn-secondary btn-sm" onClick={() => send(s)}>{s}</button>)}
          </div>
        )}
        {msgs.map((m) => m.role === "user" ? (
          <div key={m.id} className="flex justify-end">
            <div className="max-w-[80%] rounded-2xl rounded-br-md bg-hover px-4 py-2.5 text-[14.5px] leading-relaxed">
              <p className="whitespace-pre-wrap break-words">{m.text}</p>
            </div>
          </div>
        ) : (
          <div key={m.id} className="max-w-[92%]">
            <Md text={m.text} compact />
            {(m.approvals ?? []).map((id) => data?.plans?.[id] && <PlanCard key={id} a={data.plans[id]} onDone={reload} />)}
            <p className="mt-1.5 flex items-center gap-1 text-[11.5px] text-fg-3">{m.channel === "telegram" && <TelegramLogo size={12} />}{ago(m.ts)}</p>
          </div>
        ))}
        {busy && <Spinner />}
        <div ref={end} />
      </div>
      <form className="flex items-center gap-2 rounded-xl bg-field p-1.5 pl-3" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <input className="h-9 flex-1 bg-transparent text-[14.5px] placeholder:text-fg-3 focus:outline-none" placeholder="问点什么" value={text}
          onChange={(e) => setText(e.target.value)} maxLength={4000} />
        <button className="btn-primary h-8 w-8 rounded-lg px-0" disabled={busy || !text.trim()} aria-label="发送"><ArrowUp size={16} weight="bold" /></button>
      </form>
    </Page>
  );
}
