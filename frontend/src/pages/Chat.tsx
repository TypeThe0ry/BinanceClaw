import { ArrowUp, TelegramLogo } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { post, type ChatMsg } from "../api";
import { Md } from "../components/Md";
import { DocLink, Page, Spinner } from "../components/ui";
import { ago, useApi, useToast } from "../lib";

const SUGGEST = ["配置离目标差多少？", "上一轮为什么这么决定？", "BTC 跌到多少会暂停？", "这周买了什么？"];

export default function Chat() {
  const { data, reload, setData } = useApi<{ cut: number; messages: ChatMsg[] }>("/chat?limit=200", { kinds: ["chat"] });
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
    setData((d) => ({ cut: d?.cut ?? 0, messages: [...(d?.messages ?? []), { id: -Date.now(), ts: Date.now() / 1000, channel: "web", role: "user", text: q }] }));
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
