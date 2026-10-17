"""HTTP API + static dashboard. Single owner, password login, signed httpOnly cookie."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from pydantic import BaseModel
from sqlmodel import desc, select

from ..brain.providers import PROVIDERS, ProviderError
from ..config import HARD_BOUNDS, ProviderSettings
from ..db import Approval, ChatMessage, Cycle, Order, Report
from ..exchange.binance_spot import ExchangeError
from ..risk.gate import CONFIRM_PHRASE

COOKIE = "bclaw_session"
SESSION_DAYS = 7
_ph = PasswordHasher()


class Login(BaseModel):
    password: str


class ChatIn(BaseModel):
    text: str


class ModeIn(BaseModel):
    mode: str
    confirm: str = ""


def create_app(svc, static_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="BinanceClaw", docs_url=None, redoc_url=None, openapi_url=None)
    signer = TimestampSigner(svc.secrets.session_secret or os.urandom(32).hex())
    failures: dict[str, list[float]] = {}
    global_failures: list[float] = []
    attempts_lock = threading.Lock()
    PER_IP, GLOBAL, WINDOW = 5, 20, 900

    # ------------------------------------------------------------------ auth
    def require_auth(request: Request):
        tok = request.cookies.get(COOKIE, "")
        try:
            signer.unsign(tok, max_age=SESSION_DAYS * 86400)
        except (BadSignature, SignatureExpired):
            raise HTTPException(401, "未登录") from None

    @app.post("/api/auth/login")
    def login(body: Login, request: Request, response: Response):
        if not svc.secrets.admin_password_hash:
            raise HTTPException(503, "尚未设置登录密码：请运行 binanceclaw setup")
        ip = request.client.host if request.client else "?"
        now = time.time()
        # Check-and-reserve atomically: the attempt is counted *before* verifying, so concurrent
        # requests cannot all slip past the limit. A global cap covers rotating source addresses.
        with attempts_lock:
            recent = [t for t in failures.get(ip, []) if now - t < WINDOW]
            global_failures[:] = [t for t in global_failures if now - t < WINDOW]
            if len(recent) >= PER_IP:
                raise HTTPException(429, "尝试次数过多，请 15 分钟后再试")
            # Global pressure only slows everyone down; it never hard-locks the owner out
            # (a hard global block would let anyone deny access by spraying bad passwords).
            backoff = 3.0 if len(global_failures) >= GLOBAL else 0.0
            failures[ip] = recent + [now]
            global_failures.append(now)
        if backoff:
            time.sleep(backoff)
        try:
            _ph.verify(svc.secrets.admin_password_hash, body.password)
        except VerifyMismatchError:
            time.sleep(1)
            raise HTTPException(401, "密码错误") from None
        with attempts_lock:   # success: release the reserved attempt
            failures.pop(ip, None)
            if now in global_failures:
                global_failures.remove(now)
        response.set_cookie(COOKIE, signer.sign("owner").decode(), max_age=SESSION_DAYS * 86400,
                            httponly=True, samesite="strict", secure=request.url.scheme == "https")
        return {"ok": True}

    @app.post("/api/auth/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE)
        return {"ok": True}

    auth = [Depends(require_auth)]

    @app.get("/api/auth/me", dependencies=auth)
    def me():
        return {"ok": True}

    # ------------------------------------------------------------------ overview
    @app.get("/api/overview", dependencies=auth)
    def overview():
        return svc.overview()

    @app.get("/api/equity", dependencies=auth)
    def equity(limit: int = 500):
        return svc.equity_series(min(limit, 5000))

    # ------------------------------------------------------------------ cycles
    @app.get("/api/cycles", dependencies=auth)
    def cycles(limit: int = 30, before: float | None = None):
        stmt = select(Cycle).order_by(desc(Cycle.ts)).limit(min(limit, 200))
        if before:
            stmt = stmt.where(Cycle.ts < before)
        return [{"id": c.id, "ts": c.ts, "mode": c.mode, "trigger": c.trigger, "status": c.status,
                 "provider": c.provider, "model": c.model, "summary": c.summary, "error": c.error,
                 "duration_ms": c.duration_ms, "risk": c.risk, "plan": c.plan,
                 "total_usd": (c.context or {}).get("portfolio", {}).get("total_usd")} for c in svc.db.all(stmt)]

    @app.get("/api/cycles/{cid}", dependencies=auth)
    def cycle(cid: str):
        c = svc.db.get(Cycle, cid)
        if not c:
            raise HTTPException(404)
        return c.model_dump()

    @app.post("/api/cycles/run", dependencies=auth)
    def run_now():
        threading.Thread(target=svc.run_cycle, args=("manual",), daemon=True).start()
        return {"ok": True}

    # ------------------------------------------------------------------ approvals & orders
    @app.get("/api/approvals", dependencies=auth)
    def approvals(status: str | None = None, limit: int = 50):
        stmt = select(Approval).order_by(desc(Approval.created)).limit(min(limit, 500))
        if status:
            stmt = stmt.where(Approval.status == status)
        return [a.model_dump() for a in svc.db.all(stmt)]

    @app.post("/api/approvals/{aid}/{action}", dependencies=auth)
    def resolve(aid: str, action: str):
        if action not in ("approve", "reject"):
            raise HTTPException(400)
        ok, msg = svc.resolve_approval(aid, action == "approve", "web")
        return {"ok": ok, "message": msg}

    @app.get("/api/orders", dependencies=auth)
    def orders(limit: int = 100):
        return [o.model_dump() for o in svc.db.all(select(Order).order_by(desc(Order.ts)).limit(min(limit, 1000)))]

    ticker_cache: dict = {"ts": 0.0, "data": {}}

    @app.get("/api/ticker", dependencies=auth)
    def ticker():
        if time.time() - ticker_cache["ts"] > 3:
            try:
                ticker_cache["data"] = svc.client.ticker_24h(svc.settings.symbols())
                ticker_cache["ts"] = time.time()
            except ExchangeError as e:
                if not ticker_cache["data"]:
                    raise HTTPException(502, str(e)) from e
        return ticker_cache["data"]

    @app.get("/api/klines", dependencies=auth)
    def klines(symbol: str, interval: str = "1d", limit: int = 120):
        if symbol not in svc.settings.symbols():
            raise HTTPException(400, "symbol not in whitelist")
        if interval not in ("15m", "1h", "4h", "1d", "1w"):
            raise HTTPException(400, "bad interval")
        try:
            return svc.client.klines(symbol, interval, min(limit, 1000))
        except ExchangeError as e:
            raise HTTPException(502, str(e)) from e

    # ------------------------------------------------------------------ chat
    @app.get("/api/chat", dependencies=auth)
    def chat_history(limit: int = 100):
        cut = svc.db.kv_get("chat_cut_ts", 0)
        rows = svc.db.all(select(ChatMessage).order_by(desc(ChatMessage.ts)).limit(min(limit, 500)))
        return {"cut": cut, "messages": [m.model_dump() for m in reversed(rows)]}

    @app.post("/api/chat", dependencies=auth)
    def chat(body: ChatIn):
        if not body.text.strip():
            raise HTTPException(400)
        return {"reply": svc.chat(body.text.strip()[:4000], "web")}

    @app.post("/api/chat/new", dependencies=auth)
    def chat_new():
        svc.db.kv_set("chat_cut_ts", time.time())
        return {"ok": True}

    # ------------------------------------------------------------------ settings & mode
    @app.get("/api/settings", dependencies=auth)
    def get_settings():
        s = svc.settings
        avail = {}
        for name, cls in PROVIDERS.items():
            p = cls(ProviderSettings(name=name, base_url=s.provider.base_url), svc.secrets)
            ok, msg = p.available()
            avail[name] = {"ok": ok, "message": msg if not ok else "", "default_model": cls.default_model}
        return {"settings": s.model_dump(), "secrets": svc.secrets.configured(), "providers": avail,
                "bounds": HARD_BOUNDS, "confirm_phrase": CONFIRM_PHRASE,
                "telegram": bool(svc.secrets.telegram_bot_token and svc.secrets.telegram_chat_id)}

    @app.put("/api/settings", dependencies=auth)
    def put_settings(patch: dict):
        try:
            return svc.update_settings(patch).model_dump()
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/providers/test", dependencies=auth)
    def test_provider():
        t0 = time.time()
        try:
            c = svc.provider.complete("你是连通性测试。只回复：OK", [{"role": "user", "content": "ping"}])
            return {"ok": True, "reply": c.text[:200], "model": c.model, "ms": int((time.time() - t0) * 1000)}
        except ProviderError as e:
            return {"ok": False, "reply": str(e), "ms": int((time.time() - t0) * 1000)}

    @app.get("/api/live/check", dependencies=auth)
    def live_check():
        return svc.live_check()

    @app.post("/api/mode", dependencies=auth)
    def set_mode(body: ModeIn):
        ok, msg = svc.set_mode(body.mode, body.confirm, "web")
        return {"ok": ok, "message": msg}

    @app.post("/api/control/{what}", dependencies=auth)
    def control(what: str):
        if what == "pause":
            svc.pause("你在网页手动暂停", "web")
        elif what == "resume":
            svc.resume("web")
        elif what == "kill":
            svc.kill_switch("web")
        else:
            raise HTTPException(400)
        return {"ok": True}

    @app.post("/api/paper/reset", dependencies=auth)
    def paper_reset():
        if svc.settings.mode != "paper":
            raise HTTPException(400, "只能在模拟盘重置")
        svc.broker.reset_paper()
        svc.events.emit("paper_reset", {})
        return {"ok": True}

    # ------------------------------------------------------------------ reports
    @app.get("/api/reports", dependencies=auth)
    def reports(limit: int = 30):
        return [r.model_dump() for r in svc.db.all(select(Report).order_by(desc(Report.day)).limit(min(limit, 365)))]

    @app.post("/api/reports/run", dependencies=auth)
    def report_now():
        threading.Thread(target=svc.daily_report, daemon=True).start()
        return {"ok": True}

    # ------------------------------------------------------------------ live events (SSE)
    @app.get("/api/events", dependencies=auth)
    async def events(request: Request):
        q = svc.events.subscribe()

        async def gen():
            try:
                yield "retry: 3000\n\n"
                while not await request.is_disconnected():
                    try:
                        evt = await asyncio.to_thread(q.get, True, 15)
                        yield f"data: {json.dumps(evt, ensure_ascii=False, default=str)}\n\n"
                    except queue.Empty:
                        yield ": ping\n\n"
            finally:
                svc.events.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/health")
    def health():
        return {"ok": True, "mode": svc.settings.mode}

    # ------------------------------------------------------------------ static SPA
    if static_dir and static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = static_dir / path
            if path and f.is_file() and static_dir in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(static_dir / "index.html")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    return app
