import "@fontsource-variable/inter";
import "@fontsource/instrument-serif/400.css";
import "./index.css";

import { motion } from "framer-motion";
import { StrictMode, useEffect, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { api, post } from "./api";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui";
import { LiveProvider, ToastProvider } from "./lib";
import Approvals from "./pages/Approvals";
import Chat from "./pages/Chat";
import { DecisionDetail, Decisions } from "./pages/Decisions";
import Overview from "./pages/Overview";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";
import Trades from "./pages/Trades";

function Login({ onOk }: { onOk: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await post("/auth/login", { password: pw });
      onOk();
    } catch (x) {
      setErr((x as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex min-h-screen items-center justify-center px-5">
      <motion.form onSubmit={submit} initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
        className="glass-strong w-full max-w-sm p-8">
        <svg width="40" height="40" viewBox="0 0 64 64" aria-hidden>
          <rect width="64" height="64" rx="16" fill="#0E1A2E" />
          <path d="M18 46c4-14 10-24 14-28M28 48c3-12 8-21 12-25M38 48c2-9 6-16 9-19" stroke="#5EF2C4" strokeWidth="4" strokeLinecap="round" fill="none" />
        </svg>
        <h1 className="mt-5 font-display text-[34px] leading-tight">BinanceClaw</h1>
        <p className="mt-1 text-[14px] text-ink-mute">AI 管理你的币安现货，风控由代码把关。</p>
        <label className="mt-7 block">
          <span className="label">密码</span>
          <input type="password" autoFocus className="input mt-1.5" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" />
        </label>
        {err && <p className="mt-3 text-[13.5px] text-rose">{err}</p>}
        <button className="btn-primary mt-6 w-full" disabled={busy || !pw}>{busy ? <Spinner /> : "进入"}</button>
      </motion.form>
    </div>
  );
}

function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  useEffect(() => {
    api("/auth/me").then(() => setAuthed(true)).catch(() => setAuthed(false));
    const off = () => setAuthed(false);
    window.addEventListener("bclaw:unauthorized", off);
    return () => window.removeEventListener("bclaw:unauthorized", off);
  }, []);
  if (authed === null) return <div className="flex min-h-screen items-center justify-center"><Spinner /></div>;
  if (!authed) return <Login onOk={() => setAuthed(true)} />;
  return (
    <LiveProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="decisions" element={<Decisions />} />
            <Route path="decisions/:id" element={<DecisionDetail />} />
            <Route path="approvals" element={<Approvals />} />
            <Route path="trades" element={<Trades />} />
            <Route path="chat" element={<Chat />} />
            <Route path="reports" element={<Reports />} />
            <Route path="settings" element={<Settings />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </LiveProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </StrictMode>,
);
