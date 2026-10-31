"""HTTP API + static dashboard. Single owner, password login, signed httpOnly cookie."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import secrets as pysecrets
import threading
import time
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from pydantic import BaseModel, ValidationError
from sqlmodel import desc, select

from ..brain.providers import PROVIDERS, ProviderError
from ..config import (
    CANDIDATE_TOKENS,
    HARD_BOUNDS,
    MODEL_TASKS,
    NOTIFY_TYPES,
    RISK_PRESETS,
    TIER_MODELS,
    ConfirmationRequired,
    ProviderSettings,
)
from ..core.backtest import Busy
from ..db import AiQuestion, Approval, Backtest, ChatMessage, Cycle, LimitOrder, Order, Report
from ..exchange.binance_spot import ExchangeError
from ..risk.gate import CONFIRM_PHRASE

COOKIE = "bclaw_session"
CONFIRM_HEADER = "X-Confirm-Token"
SESSION_DAYS = 7
DEVICE_COOKIE = "bclaw_device"
DEVICE_DAYS = 180
_ph = PasswordHasher()

# Login throttling (module-level so tests can shrink the waits).
LOGIN_LIMITS = {"per_ip": 5, "global": 20, "trusted_global": 10, "window": 900, "slow_lane_wait": 3.0, "fail_delay": 1.0,
                "queue_timeout": 10.0}


class Login(BaseModel):
    password: str


class ChatIn(BaseModel):
    text: str


class PresetIn(BaseModel):
    name: str


class AllocProposeIn(BaseModel):
    instruction: str = ""


class AnswerIn(BaseModel):
    option: int


class AllocResolveIn(BaseModel):
    id: str
    apply: bool


class AlertIn(BaseModel):
    symbol: str
    op: str
    price: float
    note: str = ""


class CloseIn(BaseModel):
    fraction: float = 1.0


class EarnMoveIn(BaseModel):
    asset: str
    amount: float
    direction: str          # subscribe | redeem


class ArenaContestantIn(BaseModel):
    name: str
    provider: str
    model: str = ""
    base_url: str = ""


class ArenaContestantPatch(BaseModel):
    name: str | None = None
    model: str | None = None
    base_url: str | None = None
    enabled: bool | None = None


class ModeIn(BaseModel):
    mode: str
    confirm: str = ""


def _first_error(e: ValueError) -> str:
    """The message of a pydantic ValidationError without its boilerplate, or the error itself."""
    errs = getattr(e, "errors", None)
    if callable(errs):
        x = errs()[0]
        return str(x.get("ctx", {}).get("error") or x.get("msg", e)).removeprefix("Value error, ")
    return str(e)


def create_app(svc, static_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="BinanceClaw", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(ConfirmationRequired)
    async def needs_confirm(request: Request, e: ConfirmationRequired):
        # 409 + the exact changes: the client shows them, then resends with the token in X-Confirm-Token
        return JSONResponse({"detail": {"message": str(e), "changes": e.changes, "confirm_token": e.token}}, status_code=409)
    signer = TimestampSigner(svc.secrets.session_secret or os.urandom(32).hex())
    failures: dict[str, list[float]] = {}
    global_failures: list[float] = []
    trusted_failures: list[float] = []     # separate, small budget for devices with a device token
    attempts_lock = threading.Lock()
    slow_lane = asyncio.Lock()
    device_signer = TimestampSigner((svc.secrets.session_secret or os.urandom(32).hex()) + ":device")

    # ------------------------------------------------------------------ auth
    def require_auth(request: Request):
        """A session is valid only while its device is still registered and the password is unchanged,
        so revoking a device (logout / revoke-others) or changing the password ends its session at once."""
        tok = request.cookies.get(COOKIE, "")
        try:
            raw = signer.unsign(tok, max_age=SESSION_DAYS * 86400).decode()
        except (BadSignature, SignatureExpired, UnicodeDecodeError):
            raise HTTPException(401, "未登录") from None
        kind, _, rest = raw.partition(":")
        did, _, fp = rest.partition(":")
        if kind != "owner" or not did or fp != pw_fingerprint() or did not in devices():
            raise HTTPException(401, "未登录")

    def release(ip: str, ts: float):
        with attempts_lock:
            if ts in failures.get(ip, []):
                failures[ip].remove(ts)
            if ts in global_failures:
                global_failures.remove(ts)
            if ts in trusted_failures:
                trusted_failures.remove(ts)

    # Device trust: the signed token holds a random device id and a fingerprint of the current
    # password hash. Ids live in a server-side registry, so a device can be revoked (logout, "revoke
    # all"), and changing the password invalidates every device token at once.
    def pw_fingerprint() -> str:
        return hashlib.sha256((svc.secrets.admin_password_hash or "").encode()).hexdigest()[:16]

    def devices() -> dict:
        return svc.db.kv_get("trusted_devices", {})

    def known_device(request: Request) -> str | None:
        tok = request.cookies.get(DEVICE_COOKIE, "")
        try:
            raw = device_signer.unsign(tok, max_age=DEVICE_DAYS * 86400).decode()
        except (BadSignature, SignatureExpired, UnicodeDecodeError):
            return None
        did, _, fp = raw.partition(":")
        if fp != pw_fingerprint() or did not in devices():
            return None
        return did

    def register_device(request: Request, did: str | None) -> str:
        reg = devices()
        did = did or pysecrets.token_hex(12)
        ua = (request.headers.get("user-agent") or "")[:160]
        reg[did] = {**reg.get(did, {"created": time.time()}), "last_login": time.time(), "ua": ua,
                    "ip": request.client.host if request.client else "?"}
        svc.db.kv_set("trusted_devices", reg)
        return did

    @app.post("/api/auth/login")
    async def login(body: Login, request: Request, response: Response):
        if not svc.secrets.admin_password_hash:
            raise HTTPException(503, "尚未设置登录密码：请运行 binanceclaw setup")
        L = LOGIN_LIMITS
        ip = request.client.host if request.client else "?"
        now = time.time()
        device_id = known_device(request)
        # Check-and-reserve atomically: the attempt is counted *before* verifying, so concurrent
        # requests cannot all slip past the per-IP limit.
        with attempts_lock:
            recent = [t for t in failures.get(ip, []) if now - t < L["window"]]
            global_failures[:] = [t for t in global_failures if now - t < L["window"]]
            if len(recent) >= L["per_ip"]:
                raise HTTPException(429, "尝试次数过多，请 15 分钟后再试")
            trusted_failures[:] = [t for t in trusted_failures if now - t < L["window"]]
            # Trusted devices skip the global slow lane, but share their own small budget so a
            # single leaked device token cannot buy unlimited distributed attempts.
            if device_id:
                pressure = len(trusted_failures) >= L["trusted_global"]
                trusted_failures.append(now)
            else:
                pressure = len(global_failures) >= L["global"]
            failures[ip] = recent + [now]
            global_failures.append(now)

        def check() -> bool:
            try:
                _ph.verify(svc.secrets.admin_password_hash, body.password)
                return True
            except VerifyMismatchError:
                return False

        if pressure:
            # Under global pressure unknown devices share ONE fair (FIFO) slow lane: wait + verify
            # while holding an asyncio lock, so total throughput stays ~1 attempt / 3 s however many
            # addresses are used, and waiting costs no worker threads. Devices that have logged in
            # before carry a signed device cookie and skip the lane, so the owner is never starved.
            try:
                await asyncio.wait_for(slow_lane.acquire(), timeout=L["queue_timeout"])
            except TimeoutError:
                release(ip, now)            # never verified: don't charge this attempt
                raise HTTPException(429, "登录繁忙，请稍后再试") from None
            try:
                await asyncio.sleep(L["slow_lane_wait"])
                ok = await asyncio.to_thread(check)
            finally:
                slow_lane.release()
        else:
            ok = await asyncio.to_thread(check)
        if not ok:
            await asyncio.sleep(L["fail_delay"])
            raise HTTPException(401, "密码错误")
        with attempts_lock:   # success: clear this source's failures and the reserved slot
            failures.pop(ip, None)
        release(ip, now)
        secure = request.url.scheme == "https"
        did = register_device(request, device_id)
        response.set_cookie(COOKIE, signer.sign(f"owner:{did}:{pw_fingerprint()}").decode(), max_age=SESSION_DAYS * 86400,
                            httponly=True, samesite="strict", secure=secure)
        response.set_cookie(DEVICE_COOKIE, device_signer.sign(f"{did}:{pw_fingerprint()}").decode(),
                            max_age=DEVICE_DAYS * 86400, httponly=True, samesite="strict", secure=secure)
        return {"ok": True}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        did = known_device(request)
        if did:
            reg = devices()
            reg.pop(did, None)
            svc.db.kv_set("trusted_devices", reg)
        response.delete_cookie(COOKIE)
        response.delete_cookie(DEVICE_COOKIE)
        return {"ok": True}

    auth = [Depends(require_auth)]

    @app.get("/api/devices", dependencies=auth)
    def list_devices(request: Request):
        me = known_device(request)
        return [{"id": k[:6], "current": k == me, **{f: v.get(f) for f in ("created", "last_login", "ua", "ip")}}
                for k, v in sorted(devices().items(), key=lambda kv: -kv[1].get("last_login", 0))]

    @app.post("/api/devices/revoke-others", dependencies=auth)
    def revoke_others(request: Request):
        me = known_device(request)
        reg = devices()
        svc.db.kv_set("trusted_devices", {k: v for k, v in reg.items() if k == me})
        return {"ok": True, "revoked": len(reg) - (1 if me in reg else 0)}

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

    @app.get("/api/equity/history", dependencies=auth)
    def equity_history(range: str = "7d"):
        try:
            return svc.valuation.history(range)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

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

    @app.get("/api/limit-orders", dependencies=auth)
    def limit_orders(status: str | None = None, limit: int = 50):
        stmt = select(LimitOrder).order_by(desc(LimitOrder.created)).limit(min(limit, 500))
        if status:
            stmt = stmt.where(LimitOrder.status == status)
        return [lo.model_dump() for lo in svc.db.all(stmt)]

    @app.post("/api/limit-orders/{lid}/cancel", dependencies=auth)
    def cancel_limit_order(lid: str):
        ok = svc.cancel_limit_order(lid, "web")
        return {"ok": ok, "message": "已撤单" if ok else "这笔挂单已成交、已撤或不存在"}

    @app.get("/api/questions", dependencies=auth)
    def questions(status: str | None = None, limit: int = 50):
        stmt = select(AiQuestion).order_by(desc(AiQuestion.created)).limit(min(limit, 500))
        if status:
            stmt = stmt.where(AiQuestion.status == status)
        return [q.model_dump() for q in svc.db.all(stmt)]

    @app.post("/api/questions/{qid}/answer", dependencies=auth)
    def answer_question(qid: str, body: AnswerIn):
        ok, msg = svc.answer_question(qid, body.option, "web")
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

    @app.get("/api/markets", dependencies=auth)
    def markets(q: str = "", limit: int = 50, tag: str = ""):
        try:
            return svc.markets(q[:20], min(max(limit, 1), 200), tag[:40])
        except ExchangeError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/categories", dependencies=auth)
    def categories():
        return svc.categories()

    # ------------------------------------------------------------------ futures
    @app.get("/api/futures", dependencies=auth)
    def futures():
        return svc.futures_overview()

    @app.get("/api/futures/markets", dependencies=auth)
    def futures_markets(q: str = "", limit: int = 50):
        try:
            return svc.futures_markets(q[:20], min(max(limit, 1), 200))
        except ExchangeError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/futures/klines", dependencies=auth)
    def futures_klines(symbol: str, interval: str = "1h", limit: int = 200):
        if interval not in ("15m", "1h", "4h", "1d", "1w"):
            raise HTTPException(400, "bad interval")
        if symbol not in svc.fbroker.rules():
            raise HTTPException(400, "unknown perpetual")
        try:
            return svc.fclient.klines(symbol, interval, min(limit, 1000))
        except ExchangeError as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/api/futures/positions/{pid}/close", dependencies=auth)
    def futures_close(pid: str, body: CloseIn):
        if not (0 < body.fraction <= 1):
            raise HTTPException(400, "fraction must be in (0, 1]")
        ok, msg = svc.close_position(pid, "web", body.fraction)
        return {"ok": ok, "message": msg}

    @app.post("/api/futures/flatten", dependencies=auth)
    def futures_flatten():
        msgs = svc.flatten_futures("web")
        return {"ok": True, "message": "；".join(msgs) or "没有持仓"}

    # ------------------------------------------------------------------ earn
    @app.get("/api/earn", dependencies=auth)
    def earn():
        s = svc.settings
        pos = svc.earn.positions()
        out = {"enabled": s.earn.enabled, "settings": s.earn.model_dump(), "positions": pos, "products": {}}
        for a in dict.fromkeys([*s.earn.assets, *pos]):
            try:
                pr = svc.earn.product(a) or {}
                out["products"][a] = {"apr": pr.get("latestAnnualPercentageRate"), "can_purchase": pr.get("canPurchase"),
                                      "min": pr.get("minPurchaseAmount")}
            except ExchangeError as e:
                out["products"][a] = {"error": str(e)}
        return out

    @app.post("/api/earn/move", dependencies=auth)
    def earn_move(body: EarnMoveIn):
        s = svc.settings
        asset = body.asset.upper()
        if not (asset in s.stables or svc.earn.covers(asset) or (body.direction == "redeem" and asset in svc.earn.positions())):
            raise HTTPException(400, "这个资产不在理财范围内")
        if not (body.amount > 0) or body.direction not in ("subscribe", "redeem"):
            raise HTTPException(400, "金额必须为正")
        try:
            msg = (svc.earn.subscribe if body.direction == "subscribe" else svc.earn.redeem)(asset, body.amount, by="web")
        except ExchangeError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, "message": msg}

    @app.post("/api/earn/sweep", dependencies=auth)
    def earn_sweep():
        msgs = svc.earn.sweep()
        return {"ok": True, "message": "；".join(msgs) or "没有可申购的闲置资产"}

    @app.get("/api/track-record", dependencies=auth)
    def track_record():
        try:
            return svc.track.compute()
        except ExchangeError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/performance", dependencies=auth)
    def performance():
        try:
            return svc.performance()
        except ExchangeError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/analytics", dependencies=auth)
    def analytics(range: str = "30d"):
        try:
            return svc.analytics.report(range)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.get("/api/backtests", dependencies=auth)
    def backtests(limit: int = 30):
        return svc.backtests.list(min(max(limit, 1), 100))

    @app.post("/api/backtests", dependencies=auth)
    def start_backtest(body: dict):
        try:
            return svc.backtests.start(body)
        except ValidationError as e:
            raise HTTPException(400, "；".join(str(x["msg"]).removeprefix("Value error, ") for x in e.errors()[:3])) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        except Busy as e:
            raise HTTPException(409, str(e)) from None

    @app.get("/api/backtests/{bid}", dependencies=auth)
    def backtest(bid: str):
        row = svc.db.get(Backtest, bid)
        if not row:
            raise HTTPException(404, "回测不存在")
        return row

    @app.post("/api/backtests/{bid}/cancel", dependencies=auth)
    def cancel_backtest(bid: str):
        if not svc.backtests.cancel(bid):
            raise HTTPException(409, "回测不在运行")
        return {"ok": True}

    @app.delete("/api/backtests/{bid}", dependencies=auth)
    def delete_backtest(bid: str):
        if not svc.backtests.delete(bid):
            raise HTTPException(409, "回测不存在或仍在运行")
        return {"ok": True}

    @app.get("/api/alerts", dependencies=auth)
    def alerts(status: str = "active", limit: int = 100):
        from ..db import PriceAlert
        stmt = select(PriceAlert).order_by(desc(PriceAlert.created)).limit(min(limit, 500))
        if status != "all":
            stmt = stmt.where(PriceAlert.status == status)
        return [a.model_dump() for a in svc.db.all(stmt)]

    @app.post("/api/alerts", dependencies=auth)
    def add_alert(body: AlertIn):
        try:
            return svc.add_alert(body.symbol, body.op, body.price, body.note, "manual").model_dump()
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.delete("/api/alerts/{aid}", dependencies=auth)
    def del_alert(aid: str):
        return {"ok": svc.cancel_alert(aid)}

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
        ids = [a for m in rows if isinstance(m.approvals, list) for a in m.approvals]
        plans = {}
        if ids:
            limits = {lo.approval_id: lo.model_dump()
                      for lo in svc.db.all(select(LimitOrder).where(LimitOrder.approval_id.in_(ids)))}
            plans = {a.id: {**a.model_dump(), "limit": limits.get(a.id)}
                     for a in svc.db.all(select(Approval).where(Approval.id.in_(ids)))}
        return {"cut": cut, "plans": plans,
                "messages": [{**m.model_dump(), "approvals": m.approvals if isinstance(m.approvals, list) else []}
                             for m in reversed(rows)]}

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
            avail[name] = {"ok": ok, "message": msg if not ok else "", "default_model": cls.default_model,
                           "web_search": cls.web_search}
        return {"settings": s.model_dump(), "secrets": svc.secrets.configured(), "providers": avail,
                "presets": RISK_PRESETS, "candidates": CANDIDATE_TOKENS, "deployed_usd": round(svc.deployed_usd(), 2),
                "bounds": HARD_BOUNDS, "confirm_phrase": CONFIRM_PHRASE,
                "model_tasks": MODEL_TASKS, "tier_models": TIER_MODELS,
                "telegram": bool(svc.secrets.telegram_bot_token and svc.secrets.telegram_chat_id),
                "notify_types": {k: {"label": label, "always": not mutable} for k, (label, mutable) in NOTIFY_TYPES.items()}}

    @app.put("/api/settings", dependencies=auth)
    def put_settings(patch: dict, request: Request):
        before = svc.settings_store.get()
        try:
            s = svc.update_settings(patch, guard=True, confirm=request.headers.get(CONFIRM_HEADER, ""))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        # "rerun": decision-relevant groups that changed; the client offers to run a manual round now
        return {**s.model_dump(), "rerun": svc.rerun_offer(before)}

    @app.get("/api/allocation/proposal", dependencies=auth)
    def allocation_proposal():
        return svc.allocation_proposal()

    @app.post("/api/allocation/propose", dependencies=auth)
    def allocation_propose(body: AllocProposeIn):
        try:
            return svc.propose_allocation(body.instruction, "web")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except ExchangeError as e:
            raise HTTPException(502, str(e)) from e

    @app.post("/api/allocation/resolve", dependencies=auth)
    def allocation_resolve(body: AllocResolveIn):
        before = svc.settings_store.get()
        try:
            p = svc.resolve_allocation(body.id, body.apply, "web")
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {**p, "rerun": svc.rerun_offer(before)}

    @app.post("/api/risk-preset", dependencies=auth)
    def risk_preset(body: PresetIn, request: Request):
        before = svc.settings_store.get()
        try:
            s = svc.apply_preset(body.name, guard=True, confirm=request.headers.get(CONFIRM_HEADER, ""))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {**s.model_dump(), "rerun": svc.rerun_offer(before)}

    @app.post("/api/providers/test", dependencies=auth)
    def test_provider():
        # Every distinct model the task routing uses, so a mistyped fast/strong model shows up here too.
        t0 = time.time()
        seen, models = set(), []
        ps = [svc.routed(task) for task in MODEL_TASKS]
        db = svc.settings.debate
        if db.enabled:   # the seats may sit on other providers and models
            ps += [svc.seat_provider(getattr(db, k), "decision" if k == "judge" else "debate") for k in ("bull", "bear", "risk", "judge")]
        for p in ps:
            if (p.name, p.model) not in seen:
                seen.add((p.name, p.model))
                models.append(p)
        replied = []
        for p in models:
            try:
                c = p.complete("你是连通性测试。只回复：OK", [{"role": "user", "content": "ping"}])
            except ProviderError as e:
                return {"ok": False, "reply": f"{p.model or '默认模型'}：{e}", "ms": int((time.time() - t0) * 1000)}
            replied.append(c)
        return {"ok": True, "reply": replied[-1].text[:200], "model": "、".join(dict.fromkeys(c.model for c in replied)),
                "ms": int((time.time() - t0) * 1000)}

    @app.get("/api/news", dependencies=auth)
    def get_news():
        return svc.latest_news() or {}

    @app.post("/api/news/refresh", dependencies=auth)
    def refresh_news():
        s = svc.settings
        if not s.news.enabled:
            raise HTTPException(400, "联网新闻未开启")
        try:
            tokens = svc.news_tokens(s, svc.portfolio(svc.market_snapshot()[0]))
        except ExchangeError:
            tokens = list(dict.fromkeys(["BTC", *s.tradable_tokens()]))[: svc.NEWS_TOKENS]
        return svc.news_digest(tokens, force=True)

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
        svc.reset_paper()
        svc.events.emit("paper_reset", {})
        return {"ok": True}

    # ------------------------------------------------------------------ model arena (virtual accounts only)
    @app.get("/api/arena", dependencies=auth)
    def arena():
        return svc.arena.leaderboard()

    @app.put("/api/arena/config", dependencies=auth)
    def arena_config(patch: dict):
        try:
            return svc.arena.update_config(patch).model_dump()
        except ValueError as e:
            raise HTTPException(400, _first_error(e)) from None

    @app.post("/api/arena/contestants", dependencies=auth)
    def arena_add(body: ArenaContestantIn):
        try:
            return svc.arena.add(body.name, body.provider, body.model, body.base_url).model_dump()
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.patch("/api/arena/contestants/{cid}", dependencies=auth)
    def arena_edit(cid: str, body: ArenaContestantPatch):
        try:
            return svc.arena.edit(cid, **body.model_dump()).model_dump()
        except KeyError:
            raise HTTPException(404) from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None

    @app.delete("/api/arena/contestants/{cid}", dependencies=auth)
    def arena_remove(cid: str):
        if not svc.arena.remove(cid):
            raise HTTPException(404)
        return {"ok": True}

    @app.get("/api/arena/curves", dependencies=auth)
    def arena_curves():
        return svc.arena.curves()

    @app.get("/api/arena/rounds", dependencies=auth)
    def arena_rounds(contestant: str | None = None, limit: int = 20):
        return svc.arena.rounds(contestant, min(max(limit, 1), 100))

    @app.post("/api/arena/run", dependencies=auth)
    def arena_run():
        if svc.arena.running:
            raise HTTPException(409, "本轮比赛还在进行")
        if not svc.arena.contestants(enabled_only=True):
            raise HTTPException(400, "没有参赛模型")
        threading.Thread(target=svc.arena.run_round, args=("manual",), daemon=True).start()
        return {"ok": True}

    @app.post("/api/arena/reset", dependencies=auth)
    def arena_reset():
        if svc.arena.running:
            raise HTTPException(409, "本轮比赛还在进行")
        svc.arena.reset()
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
    def health(response: Response):
        h = svc.health()
        h["ok"] = h["scheduler_alive"] or not h["heartbeat_age_s"]   # not started yet counts as ok
        if not h["ok"]:
            response.status_code = 503
        return {"ok": h["ok"], "scheduler_alive": h["scheduler_alive"]}

    @app.get("/api/health/detail", dependencies=auth)
    def health_detail():
        return svc.health()

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
        # CSRF backstop on top of the SameSite=Strict cookie: browsers mark every request with
        # Sec-Fetch-Site, so a write coming from another site (or another port on the same host) is refused.
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("sec-fetch-site", "same-origin") not in (
                "same-origin", "none"):
            return JSONResponse({"detail": "跨站请求被拒绝"}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    return app
