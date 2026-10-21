import "@fontsource-variable/inter";
import "./index.css";

import { StrictMode, useEffect, useState, type FormEvent } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { api, post } from "./api";
import { ConfirmProvider } from "./components/ConfirmDialog";
import { Layout, More } from "./components/Layout";
import { Spinner } from "./components/ui";
import { LiveProvider, TickerProvider, ToastProvider } from "./lib";
import { initTheme } from "./theme";
import { AIDetail, AIList } from "./pages/AI";
import Analytics from "./pages/Analytics";
import Approvals from "./pages/Approvals";
import Assets from "./pages/Assets";
import Chat from "./pages/Chat";
import Config from "./pages/Config";
import Docs from "./pages/Docs";
import Futures from "./pages/Futures";
import Reports from "./pages/Reports";
import Settings from "./pages/Settings";
import Trade from "./pages/Trade";

function Login({ onOk }: { onOk: () => void }) {
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try { await post("/auth/login", { password: pw }); onOk(); } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  }
  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <form onSubmit={submit} className="w-full max-w-[360px]">
        <svg width="34" height="34" viewBox="0 0 64 64" aria-hidden>
          <path d="M18 46c4-14 10-24 14-28M28 48c3-12 8-21 12-25M38 48c2-9 6-16 9-19" stroke="currentColor" strokeWidth="5" strokeLinecap="round" fill="none" />
        </svg>
        <h1 className="mt-6 text-[28px] font-semibold tracking-tight">登录 BinanceClaw</h1>
        <input type="password" autoFocus className="input mt-8 h-12" placeholder="密码" value={pw}
          onChange={(e) => setPw(e.target.value)} autoComplete="current-password" aria-label="密码" />
        {err && <p className="mt-3 text-[13px] text-down">{err}</p>}
        <button className="btn-primary mt-4 h-12 w-full text-[15px]" disabled={busy || !pw}>{busy ? <Spinner /> : "登录"}</button>
      </form>
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
      <TickerProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Assets />} />
              <Route path="trade" element={<Trade />} />
              <Route path="futures" element={<Futures />} />
              <Route path="ai" element={<AIList />} />
              <Route path="ai/:id" element={<AIDetail />} />
              <Route path="approvals" element={<Approvals />} />
              <Route path="chat" element={<Chat />} />
              <Route path="reports" element={<Reports />} />
              <Route path="analytics" element={<Analytics />} />
              <Route path="settings" element={<Settings />} />
              <Route path="config" element={<Config />} />
              <Route path="docs" element={<Docs />} />
              <Route path="docs/:slug" element={<Docs />} />
              <Route path="more" element={<More />} />
              <Route path="*" element={<Assets />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </TickerProvider>
    </LiveProvider>
  );
}

initTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <ConfirmProvider>
        <App />
      </ConfirmProvider>
    </ToastProvider>
  </StrictMode>,
);
