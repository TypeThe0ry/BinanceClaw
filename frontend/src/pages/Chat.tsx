import { PaperPlaneRight, TelegramLogo } from "@phosphor-icons/react";
import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { post, type ChatMsg } from "../api";
import { Md } from "../components/Md";
import { Card, PageTitle, Spinner } from "../components/ui";
import { ago, useApi, useToast } from "../lib";

const SUGGEST = ["现在的配置离目标差多少？", "上一轮为什么这么决定？", "BTC 跌到多少会触发暂停？", "这周 AI 一共买了什么？"];

export default function Chat() {
  const { data, reload, setData } = useApi<{ cut: number; messages: ChatMsg[] }>("/chat?limit=200", { kinds: ["chat"] });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const toast = useToast();
  const msgs = data?.messages ?? [];

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [msgs.length, busy]);

  async function send(t = text) {
    const q = t.trim();
    if (!q || busy) return;
    setText("");
    setBusy(true);
    setData((d) => ({ cut: d?.cut ?? 0, messages: [...(d?.messages ?? []), { id: -Date.now(), ts: Date.now() / 1000, channel: "web", role: "user", text: q }] }));
    try {
      await post("/chat", { text: q });
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
      reload();
    }
  }

  async function fresh() {
    await post("/chat/new");
    toast("已开始新的对话");
    reload();
  }

  return (
    <div className="flex h-[calc(100dvh-140px)] flex-col md:h-[calc(100dvh-110px)]">
      <PageTitle title="对话" sub="和当前模型聊账户与行情。它只能看，不能下单；Telegram 里的对话也在这里。"
        right={<button className="btn-ghost" onClick={fresh}>新对话</button>} />
      <Card className="flex min-h-0 flex-1 flex-col">
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {!msgs.length && (
            <div className="flex flex-wrap gap-2">
              {SUGGEST.map((s) => <button key={s} className="btn-ghost text-[13.5px]" onClick={() => send(s)}>{s}</button>)}
            </div>
          )}
          <AnimatePresence initial={false}>
            {msgs.map((m) => {
              const old = data && m.ts <= data.cut;
              return (
                <motion.div key={m.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: old ? 0.45 : 1, y: 0 }}
                  className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[85%] rounded-2xl px-4 py-3 text-[14.5px] leading-[1.7] ${m.role === "user" ? "rounded-br-md bg-mint/15 text-ink" : "rounded-bl-md border border-white/[0.06] bg-white/[0.04] text-ink"}`}>
                    <div className="break-words">{m.role === "assistant" ? <Md text={m.text} /> : <p className="whitespace-pre-wrap">{m.text}</p>}</div>
                    <p className="mt-1.5 flex items-center gap-1 text-[11.5px] text-ink-mute">
                      {m.channel === "telegram" && <TelegramLogo size={12} />}{ago(m.ts)}
                    </p>
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>
          {busy && <div className="flex items-center gap-2 text-[13.5px] text-ink-mute"><Spinner />模型在思考…</div>}
          <div ref={end} />
        </div>
        <form className="flex gap-2 border-t border-white/[0.06] p-3" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <input className="input" placeholder="问点什么…" value={text} onChange={(e) => setText(e.target.value)} maxLength={4000} />
          <button className="btn-primary px-3.5" disabled={busy || !text.trim()} aria-label="发送"><PaperPlaneRight size={18} weight="fill" /></button>
        </form>
      </Card>
    </div>
  );
}
