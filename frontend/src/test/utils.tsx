import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { ToastProvider } from "../lib";

type Handler = unknown | ((body: unknown) => unknown);
export interface Call { method: string; path: string; body: unknown }

/**
 * Stub `fetch` with a route table keyed by "METHOD /path?query" (the `/api` prefix is stripped).
 * Values are JSON responses, or functions of the request body. Unmatched requests return 404.
 */
export function mockApi(routes: Record<string, Handler>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const path = String(input).replace(/^\/api/, "");
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, path, body });
    const key = `${method} ${path}`;
    if (!(key in routes)) return new Response(JSON.stringify({ detail: `no mock for ${key}` }), { status: 404 });
    const h = routes[key];
    const out = typeof h === "function" ? (h as (b: unknown) => unknown)(body) : h;
    return new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, find: (method: string, path: string) => calls.filter((c) => c.method === method && c.path === path) };
}

export function renderPage(ui: ReactElement) {
  return render(
    <ToastProvider>
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{ui}</MemoryRouter>
    </ToastProvider>,
  );
}
