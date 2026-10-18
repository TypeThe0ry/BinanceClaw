"""BinanceClaw service: market data -> AI plan -> risk engine -> execute / approval -> audit."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from sqlmodel import desc, select

from ..brain import allocation as alloc
from ..brain import indicators as ta
from ..brain.prompt import CHAT_SYSTEM, SYSTEM, build_context, build_user_prompt
from ..brain.providers import ProviderError, make_provider
from ..brain.schema import PlanError, parse_plan
from ..config import DATA_DIR, ConfirmationRequired, Secrets, SettingsStore, dangerous_changes, decision_changes
from ..db import Approval, ChatMessage, Cycle, Database, FuturesPosition, FuturesTrade, Order, PriceAlert, Report
from ..exchange.binance_futures import BinanceFutures
from ..exchange.binance_spot import BinanceSpot, ExchangeError
from ..exchange.broker import Broker, OrderUnknown
from ..exchange.futures_broker import FuturesBroker
from ..exchange.stream import PriceStream
from ..risk import gate
from ..risk.engine import Check, Portfolio, RiskState, Verdict, evaluate, normalize_symbol, pause_trigger
from ..risk.futures import OPEN_OPS, FuturesView, evaluate_futures, perp_symbol
from ..risk.rebalance import rebalance_actions
from . import categories as cats
from .autopilot import Autopilot
from .earn import EarnManager
from .events import EventBus
from .track import TrackRecord

FUTURES_ASSET = "FUTURES"          # pseudo-asset: equity of BinanceClaw's futures positions, in USD

log = logging.getLogger("binanceclaw")


class Service:
    def __init__(self, data_dir=DATA_DIR, secrets: Secrets | None = None, client: BinanceSpot | None = None,
                 fclient: BinanceFutures | None = None):
        self.data_dir = data_dir
        self.settings_store = SettingsStore(data_dir / "settings.json")
        self.secrets = secrets or Secrets.from_env()
        self.db = Database(data_dir / "binanceclaw.db")
        self.events = EventBus()
        self.client = client or BinanceSpot(self.secrets.binance_api_key, self.secrets.binance_api_secret)
        self.fclient = fclient or BinanceFutures(self.secrets.binance_api_key, self.secrets.binance_api_secret)
        self.earn = EarnManager(self)
        self.stream = PriceStream()
        self.autopilot = Autopilot(self)
        self.track = TrackRecord(self)
        self.events.on(self.autopilot.on_event)
        self._confirm_key = ((self.secrets.session_secret or os.urandom(32).hex()) + ":confirm").encode()
        self._cycle_lock = threading.Lock()
        self._exec_lock = threading.RLock()
        self._chat_lock = threading.Lock()
        self._kline_cache: dict[tuple, tuple[float, list[dict]]] = {}
        self._rebuild()

    # ------------------------------------------------------------------ plumbing
    def _rebuild(self):
        s = self.settings_store.get()
        self.broker = Broker(self.client, self.db, s.mode, s.paper)
        self.fbroker = FuturesBroker(self.fclient, self.client, self.db, s.mode, s.paper)
        if hasattr(self, "earn"):
            self.earn._pos = None
        self.provider = make_provider(s.provider, self.secrets)
        self.fallback = make_provider(s.provider_fallback, self.secrets) if s.provider_fallback else None

    def complete(self, system: str, messages: list[dict], json_mode: bool = False):
        """Primary model, then the fallback model if one is configured. Raises if both fail."""
        try:
            return self.provider.complete(system, messages, json_mode=json_mode)
        except ProviderError as e:
            if not self.fallback:
                raise
            log.warning("primary model failed, using fallback: %s", e)
            try:
                comp = self.fallback.complete(system, messages, json_mode=json_mode)
            except ProviderError as e2:
                raise ProviderError(f"主模型失败：{e}；备用模型也失败：{e2}") from e2
            comp.usage = {**(comp.usage or {}), "fallback": True, "primary_error": str(e)[:300]}
            return comp

    @property
    def settings(self):
        s = self.settings_store.get()
        if s.allocation.mode == "ai" and s.allocation.universe != "whitelist":
            s._universe = self.ai_universe(s)
        return s

    UNIVERSE_TTL = 600

    def ai_universe(self, s) -> list[str] | None:
        """AI 自主分配 over all of Binance spot (minus the blacklist): the assets tradable this round.

        Every asset with a <TOKEN><quote> pair is eligible. Each round needs a klines call per
        asset, so the round's universe is what the account holds, assets with a trade waiting for
        approval, then the most-traded others, up to ``allocation.ai_candidates``. Cached for 10
        minutes; None (fall back to the explicit list) when the market list cannot be read."""
        al = s.allocation
        key = (s.mode, s.quote, al.universe, tuple(al.blocked), al.ai_candidates, tuple(s.stables))
        cached = getattr(self, "_universe", None)
        if cached and cached[1] == key and time.time() - cached[0] < self.UNIVERSE_TTL:
            return cached[2]
        if getattr(self, "_universe_busy", False):          # settings read while resolving it
            return cached[2] if cached and cached[1] == key else None
        self._universe_busy = True
        try:
            pairs = {m["base"]: m.get("status", "TRADING") for m in self._spot_markets() if m["quote"] == s.quote}
            tickers = self._tickers_24h()
            held = self.broker.balances()
        except ExchangeError as e:
            log.warning("AI universe unavailable: %s", e)
            return cached[2] if cached and cached[1] == key else None
        finally:
            self._universe_busy = False
        skip = set(s.stables) | {s.quote} | set(al.blocked)
        px = {b: (tickers.get(f"{b}{s.quote}") or {}).get("price") or 0.0 for b in pairs}
        mine = sorted((b for b, amt in held.items() if b in pairs and b not in skip and amt * px.get(b, 0.0) >= 1),
                      key=lambda b: -held[b] * px[b])
        pending = [a.symbol[: -len(s.quote)] for a in self.db.pending_approvals()
                   if a.kind == "spot" and a.symbol.endswith(s.quote)]
        top = sorted((b for b, st in pairs.items() if st == "TRADING" and b not in skip),
                     key=lambda b: -((tickers.get(f"{b}{s.quote}") or {}).get("quote_volume") or 0))
        out = [b for b in dict.fromkeys([*mine, *pending, *top]) if b in pairs and b not in skip][:al.ai_candidates]
        self._universe = (time.time(), key, out)
        return out

    def _tickers_24h(self) -> dict[str, dict]:
        tk = getattr(self, "_tickers", None)
        if not tk or time.time() - tk[0] > 120:
            tk = (time.time(), self.client.all_tickers_24h())
            self._tickers = tk
        return tk[1]

    def tz(self):
        return ZoneInfo(self.settings.schedule.timezone)

    def now(self) -> dt.datetime:
        return dt.datetime.now(self.tz())

    def today(self) -> str:
        return self.now().strftime("%Y-%m-%d")

    def update_settings(self, patch: dict, *, guard: bool = False, confirm: str = ""):
        """Merge ``patch`` into the settings. With ``guard`` (every change a person makes from the web
        or Telegram), a change to a dangerous setting raises ConfirmationRequired unless ``confirm`` is
        the token issued for exactly that change."""
        if "allocation" in patch and "enabled" in (patch.get("allocation") or {}):
            self._validate_tokens(patch["allocation"]["enabled"])
        fut = patch.get("futures") or {}
        if "symbols" in fut:
            self._validate_perps(fut["symbols"])
        if fut.get("enabled") and not self.settings.futures.enabled and self.settings.mode == "live":
            chk = self.live_check(features=["futures"])
            if not chk["ok"]:
                raise ValueError("实盘下开启合约需要 Key 权限：" + "；".join(chk["blockers"]))
        if ("limits" in patch or "schedule" in patch) and "risk_preset" not in patch:
            patch = {**patch, "risk_preset": "custom"}
        if guard:
            changes = dangerous_changes(self.settings, self.settings_store.preview(patch))
            if changes and not self._confirm_ok(confirm, changes):
                raise ConfirmationRequired(changes, self._confirm_token(changes))
        s = self.settings_store.update(patch)
        self._rebuild()
        self.events.emit("settings", {"mode": s.mode})
        return s

    def rerun_offer(self, before) -> list[str]:
        """What changed since ``before`` that the AI should adapt to, for the "run a new round now?" prompt.
        Empty when nothing decision-relevant changed or the bot is paused (a round would only be skipped)."""
        if self.paused()[0]:
            return []
        return decision_changes(before, self.settings_store.get())

    def _confirm_sig(self, changes: list[dict], exp: int) -> str:
        msg = json.dumps(changes, sort_keys=True, ensure_ascii=False, default=str) + f"|{exp}"
        return hmac.new(self._confirm_key, msg.encode(), hashlib.sha256).hexdigest()

    def _confirm_token(self, changes: list[dict], ttl: int = 600) -> str:
        """Token bound to these exact changes (old and new values), valid for ``ttl`` seconds."""
        exp = int(time.time()) + ttl
        return f"{exp}.{self._confirm_sig(changes, exp)}"

    def _confirm_ok(self, token: str, changes: list[dict]) -> bool:
        exp, _, sig = (token or "").partition(".")
        if not exp.isdigit() or int(exp) < time.time():
            return False
        return hmac.compare_digest(sig, self._confirm_sig(changes, int(exp)))

    def _validate_tokens(self, tokens: list[str]):
        s = self.settings
        toks = [t.upper() for t in tokens if t.upper() not in s.stables]
        if not toks:
            return
        try:
            rules = self.client.symbol_rules([f"{t}{s.quote}" for t in toks])
        except ExchangeError as e:
            raise ValueError(f"无法在币安确认这些交易对：{e}") from e
        # Any listed pair is accepted, including one Binance has suspended (status BREAK): the owner
        # may want it in the plan, and the risk engine refuses to trade it until it resumes.
        bad = [t for t in toks if f"{t}{s.quote}" not in rules]
        if bad:
            raise ValueError(f"币安没有 {', '.join(t + s.quote for t in bad)} 现货交易对")

    def _validate_perps(self, tokens: list[str]):
        try:
            rules = self.fbroker.rules()
        except ExchangeError as e:
            raise ValueError(f"无法读取币安合约列表：{e}") from e
        bad = [t for t in tokens if perp_symbol(t) not in rules]
        if bad:
            raise ValueError(f"币安没有可交易的 USDT 永续合约：{', '.join(perp_symbol(t) for t in bad)}")

    def apply_preset(self, name: str, *, guard: bool = False, confirm: str = ""):
        from ..config import RISK_PRESETS
        if name not in RISK_PRESETS:
            raise ValueError(f"未知预设 {name}")
        return self.update_settings({**RISK_PRESETS[name], "risk_preset": name}, guard=guard, confirm=confirm)

    # ------------------------------------------------------------------ state
    def paused(self) -> tuple[bool, str]:
        p = self.db.kv_get("paused", {"on": False, "reason": ""})
        return bool(p["on"]), p.get("reason", "")

    def pause(self, reason: str, by: str = "system"):
        self.db.kv_set("paused", {"on": True, "reason": reason, "ts": time.time(), "by": by})
        self.events.emit("paused", {"reason": reason, "by": by})

    def resume(self, by: str = "user"):
        self.db.kv_set("paused", {"on": False, "reason": "", "ts": time.time(), "by": by})
        self.db.kv_set("anomaly_count", 0)
        self.events.emit("resumed", {"by": by})

    def kill_switch(self, by: str = "user"):
        self.pause("紧急停止（Kill Switch）", by)
        for a in self.db.pending_approvals():
            if self.db.claim_approval(a.id, "rejected", by):
                self.db.update(Approval, a.id, result="紧急停止，已作废")
                self.events.emit("approval", {"id": a.id, "status": "rejected"})

    def next_cycle_at(self) -> float:
        return self.db.kv_get("next_cycle_at", 0)

    # ------------------------------------------------------------------ market
    def market_snapshot(self, max_age: float = 30):
        """Cached for ``max_age`` seconds; execution paths call with max_age=0 for fresh data."""
        cached = getattr(self, "_mkt_cache", None)
        if cached and time.time() - cached[0] < max_age:
            return cached[1]
        result = self._market_snapshot()
        self._mkt_cache = (time.time(), result)
        return result

    # Candle history changes slowly: re-fetch the daily series every few minutes, the 4h one a bit more
    # often. Live prices are always fresh; this only spares the per-asset klines calls.
    KLINE_TTL = {"1d": 300, "4h": 120}
    KLINE_WORKERS = 8

    def _klines(self, symbol: str, interval: str, limit: int) -> list[dict]:
        key = (symbol, interval, limit)
        hit = self._kline_cache.get(key)
        if hit and time.time() - hit[0] < self.KLINE_TTL.get(interval, 60):
            return hit[1]
        k = self.client.klines(symbol, interval, limit)
        self._kline_cache[key] = (time.time(), k)
        return k

    def _klines_many(self, reqs: list[tuple[str, str, int]]) -> dict[tuple, list[dict]]:
        """Fetch many series in parallel (one round used to make them one by one). Raises the first error."""
        with ThreadPoolExecutor(max_workers=min(self.KLINE_WORKERS, max(len(reqs), 1))) as ex:
            futs = {r: ex.submit(self._klines, *r) for r in reqs}
            return {r: f.result() for r, f in futs.items()}

    def _market_snapshot(self):
        s = self.settings
        tokens = s.tradable_tokens()
        watch = tokens + ([] if "BTC" in tokens else ["BTC"])      # BTC always: drawdown circuit breaker
        prices_sym = self.client.prices([f"{t}{s.quote}" for t in watch])
        prices = {sym[: -len(s.quote)]: p for sym, p in prices_sym.items()}
        market, anomaly = {}, None
        missing = [t for t in tokens if t not in prices]
        if missing:
            anomaly = f"缺少价格：{', '.join(missing)}"
        btc_change = None
        have = [t for t in watch if t in prices]
        series = self._klines_many([(f"{t}{s.quote}", iv, 60) for t in have for iv in ("1d", "4h")])
        for tok in have:
            k = series[(f"{tok}{s.quote}", "1d", 60)]
            h4 = series[(f"{tok}{s.quote}", "4h", 60)]
            closes = [c["c"] for c in k]
            m30 = k[-31:]
            info = {"price": prices[tok]}
            if len(closes) >= 8:
                info["change_7d"] = round(prices[tok] / closes[-8] - 1, 4)
            if len(closes) >= 31:
                info["change_30d"] = round(prices[tok] / closes[-31] - 1, 4)
            info["high_30d"] = max(c["h"] for c in m30) if m30 else None
            info["low_30d"] = min(c["l"] for c in m30) if m30 else None
            info["daily_closes_14d"] = [round(c, 4) for c in closes[-14:]]
            info.update(ta.summarize(prices[tok], k, h4))
            market[tok] = info
            if tok == "BTC":
                btc_change = info.get("change_7d")
                if k and time.time() - k[-1]["t"] > 2 * 86400:
                    anomaly = anomaly or "BTC 日线停止更新"
                elif closes and abs(prices["BTC"] / closes[-1] - 1) > s.limits.price_deviation_anomaly:
                    anomaly = anomaly or "BTC 实时价与日线收盘价偏离过大"
        return prices, market, btc_change, anomaly

    def live_prices(self, tokens) -> dict[str, float]:
        """Latest price per token: the websocket stream when fresh, else REST (cached 10 s)."""
        q = self.settings.quote
        syms = [f"{t}{q}" for t in tokens]
        got = self.stream.prices(syms)
        missing = [x for x in syms if x not in got]
        if missing:
            cached = getattr(self, "_rest_px", (0.0, {}))
            if time.time() - cached[0] > 10 or any(x not in cached[1] for x in missing):
                try:
                    cached = (time.time(), {**cached[1], **self.client.prices(missing)})
                except ExchangeError:
                    pass
                self._rest_px = cached
            got.update({x: cached[1][x] for x in missing if x in cached[1]})
        return {x[: -len(q)]: p for x, p in got.items()}

    def token_tags(self) -> dict[str, list[str]]:
        """Binance's categories per asset (cached 6 h). Empty when binance.com is unreachable."""
        cached = getattr(self, "_tags", None)
        if cached and time.time() - cached[0] < 6 * 3600:
            return cached[1]
        try:
            data = self.client.asset_tags()
        except (ExchangeError, AttributeError) as e:
            log.warning("binance categories unavailable: %s", e)
            data = cached[1] if cached else {}
            self._tags = (time.time() - 5 * 3600, data)        # retry in an hour
            return data
        self._tags = (time.time(), data)
        return data

    def tags_of(self, token: str) -> list[str]:
        return cats.tags_for(token, self.token_tags(), self.settings.stables)

    def categories(self) -> list[dict]:
        """Binance categories present on spot (with a <TOKEN><quote> pair), in display order."""
        counts: dict[str, int] = {}
        quote = self.settings.quote
        try:
            bases = {m["base"] for m in self._spot_markets() if m["quote"] == quote}
        except ExchangeError:
            bases = set(self.token_tags())
        for base in bases:
            for t in self.tags_of(base):
                counts[t] = counts.get(t, 0) + 1
        return [{"tag": t, "label": cats.label(t), "count": n}
                for t, n in sorted(counts.items(), key=lambda kv: (cats.rank(kv[0]), -kv[1], kv[0])) if t != cats.STABLE]

    def _spot_markets(self) -> list[dict]:
        mk = getattr(self, "_markets", None)
        if not mk or time.time() - mk[0] > 3600:
            mk = (time.time(), self.client.spot_markets())
            self._markets = mk
        return mk[1]

    def markets(self, q: str = "", limit: int = 50, tag: str = "") -> list[dict]:
        """Searchable list of every asset on Binance spot, with 24h stats (cached).

        One row per base asset. Assets with a <TOKEN><quote> pair are selectable whatever that
        pair's status: a suspended pair (status BREAK) is shown as such and the risk engine
        refuses to trade it until Binance resumes it. Assets only quoted in other currencies
        are listed too, marked not selectable, so nothing on Binance silently goes missing.
        ``tag`` keeps only assets in that Binance category."""
        now = time.time()
        quote = self.settings.quote
        mk = (now, self._spot_markets())
        tags = self.token_tags()
        tk = (now, self._tickers_24h())
        held = set(self.broker.balances())
        enabled = set(self.settings.allocation.enabled)
        blocked = set(self.settings.allocation.blocked)
        stables = set(self.settings.stables)
        q = q.strip().upper()
        pairs: dict[str, dict] = {}
        others: dict[str, set] = {}
        for m in mk[1]:
            base = m["base"]
            if base in stables or (q and q not in base) or (tag and tag not in tags.get(base, ())):
                continue
            if m["quote"] == quote:
                pairs[base] = m
            else:
                others.setdefault(base, set()).add(m["quote"])
        rows = []
        for base, m in pairs.items():
            t = tk[1].get(m["symbol"], {})
            status = m.get("status", "TRADING")
            rows.append({"token": base, "symbol": m["symbol"], "price": t.get("price"), "change_pct": t.get("change_pct"),
                         "quote_volume": t.get("quote_volume", 0.0), "held": base in held, "enabled": base in enabled,
                         "blocked": base in blocked, "status": status, "trading": status == "TRADING", "selectable": True,
                         "tags": tags.get(base, []), "category": cats.label(cats.primary(tags.get(base, [])))})
        for base, quotes in others.items():
            if base in pairs:
                continue
            rows.append({"token": base, "symbol": "", "price": None, "change_pct": None, "quote_volume": 0.0,
                         "held": base in held, "enabled": base in enabled, "blocked": base in blocked, "status": "NO_PAIR", "trading": False,
                         "selectable": False, "quotes": sorted(quotes), "tags": tags.get(base, []),
                         "category": cats.label(cats.primary(tags.get(base, [])))})
        rows.sort(key=lambda r: (not r["enabled"], not r["held"], r["token"] != q, not r["selectable"],
                                 not r["trading"], -(r["quote_volume"] or 0)))
        return rows[:limit]

    def asset_prices(self, assets) -> dict[str, float]:
        """USD prices for arbitrary held assets (not just tradable ones), cached 60 s."""
        cached = getattr(self, "_all_px", None)
        if not cached or time.time() - cached[0] > 60:
            try:
                cached = (time.time(), self.client.all_prices())
            except (ExchangeError, AttributeError):
                cached = (time.time(), {})
            self._all_px = cached
        q = self.settings.quote
        return {a: cached[1][f"{a}{q}"] for a in assets if f"{a}{q}" in cached[1]}

    def spot_balances(self) -> dict[str, float]:
        """Spot balances without Binance's LD<asset> Earn mirrors (Earn is counted from its own endpoint)."""
        mirrored = set(self.settings.stables) | (set(self.earn.positions()) if self.settings.mode == "live" else set())
        return {a: v for a, v in self.broker.balances().items() if not (a.startswith("LD") and a[2:] in mirrored)}

    def futures_equity(self, marks: dict | None = None) -> float:
        if not self.fbroker.open_rows():
            return 0.0
        try:
            return self.fbroker.equity(marks)
        except ExchangeError:
            return sum(p.margin for p in self.fbroker.open_rows())

    def account_portfolio(self, prices: dict) -> Portfolio:
        raw = self.spot_balances()
        for a, pos in self.earn.positions().items():
            raw[a] = raw.get(a, 0.0) + pos["amount"]
        px = {**self.asset_prices([a for a in raw if a not in self.settings.stables]), **prices}
        eq = self.futures_equity()
        if eq > 0:
            raw[FUTURES_ASSET], px[FUTURES_ASSET] = eq, 1.0
        return Portfolio(raw, px, self.settings)

    def risk_state(self, btc_change=None, anomaly=None) -> RiskState:
        orders = self.db.orders_for_day(self.today())
        buys = [o for o in orders if o.side == "BUY"]
        paused, reason = self.paused()
        return RiskState(today_orders=len(buys), today_notional=sum(o.quote_usd for o in buys),
                         deployed_usd=self.deployed_usd() + sum(p.margin for p in self.fbroker.open_rows()),
                         today_auto_notional=sum(o.quote_usd for o in buys if o.via == "auto"),
                         paused=paused, pause_reason=reason, btc_change_7d=btc_change, anomaly=anomaly,
                         halted=self.halted_symbols())

    def halted_symbols(self) -> dict[str, str]:
        """Whitelisted pairs Binance is not trading right now ({symbol: status}). On a lookup
        failure every pair counts as halted: better to skip a trade than to send a doomed order."""
        syms = self.settings.symbols()
        try:
            rules = self.broker.rules(syms)
        except ExchangeError:
            return {sym: "UNKNOWN" for sym in syms}
        return {sym: (rules[sym].status if sym in rules else "MISSING") for sym in syms
                if sym not in rules or rules[sym].status != "TRADING"}

    def deployed_usd(self) -> float:
        """Net capital the AI has deployed in the current mode (cost of buys minus proceeds of sells)."""
        rows = self.db.all(select(Order).where(Order.mode == self.settings.mode,
                                               Order.status.in_(("filled", "submitted", "unknown"))))
        return sum(o.quote_usd if o.side == "BUY" else -o.quote_usd for o in rows)

    def managed_balances(self, raw: dict[str, float]) -> dict[str, float]:
        """What the AI may see and use: selected tokens + stablecoins, minus locked amounts."""
        s = self.settings
        keep = set(s.tradable_tokens()) | set(s.stables)
        out = {}
        for asset, amt in raw.items():
            if asset in keep:
                left = amt - s.capital.locked.get(asset, 0.0)
                if left > 1e-12:
                    out[asset] = left
        return out

    def portfolio(self, prices) -> Portfolio:
        raw = self.spot_balances()
        for a, amt in self.earn.managed_amounts().items():
            raw[a] = raw.get(a, 0.0) + amt
        bal = self.managed_balances(raw)
        eq = self.futures_equity()
        if eq > 0:
            bal[FUTURES_ASSET] = eq
            prices = {**prices, FUTURES_ASSET: 1.0}
        return Portfolio(bal, prices, self.settings)

    # ------------------------------------------------------------------ futures state
    def futures_view(self, pf: Portfolio, st: RiskState, fresh: bool = False) -> FuturesView:
        s = self.settings
        fb = self.fbroker
        marks = fb.marks(0 if fresh else 5)
        rules = fb.rules()
        rows = fb.open_rows()
        positions = {p.symbol: {"side": p.side, "qty": p.qty, "entry": p.entry, "margin": p.margin, "stop": p.stop}
                     for p in rows if p.status == "open"}
        try:
            foreign = fb.foreign_symbols()
        except ExchangeError:
            foreign = set(rules)                       # can't see the account: allow no new opens
        total = pf.total()
        quote_bal = float(pf.balances.get(s.quote, 0.0))
        spendable = min(quote_bal, pf.stable_value() - s.limits.stable_floor * total) - total * s.limits.fee_buffer_ratio
        opens = self.db.all(select(FuturesTrade).where(FuturesTrade.mode == s.mode, FuturesTrade.day == self.today(),
                                                       FuturesTrade.action.in_(OPEN_OPS),
                                                       FuturesTrade.status.in_(("filled", "unknown"))))
        budget = s.capital.budget_usd
        return FuturesView(settings=s, marks=marks, rules=rules, positions=positions, foreign=foreign,
                           spendable_usd=max(spendable, 0.0), margin_used=sum(p.margin for p in rows),
                           opens_today=len(opens), paused=st.paused, pause_reason=st.pause_reason,
                           trigger=pause_trigger(s, st),
                           budget_left=(budget - st.deployed_usd) if budget is not None else float("inf"))

    def futures_context(self, fv: FuturesView | None) -> dict:
        f = self.settings.futures
        if not f.enabled:
            return {"enabled": False}
        out = {"enabled": True, "rules": {
            "max_leverage": f.max_leverage, "max_position_margin_usd": f.max_position_margin_usd,
            "max_total_margin_usd": f.max_total_margin_usd, "max_loss_per_trade_usd": f.max_loss_per_trade_usd,
            "max_stop_distance": f.max_stop_distance, "allow_short": f.allow_short, "auto_open": f.auto_open,
            "max_opens_per_day": f.max_opens_per_day, "margin_type": "ISOLATED"}}
        if fv is None:
            return out
        prem = self.fbroker.premium()
        out["whitelist"] = [f"{t}USDT" for t in f.symbols]
        out["markets"] = {sym: {"mark": round(prem[sym]["mark"], 6), "funding_rate_8h": prem[sym]["funding_rate"]}
                          for sym in out["whitelist"] if sym in prem}
        out["positions"] = [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in p.items()
                             if k in ("symbol", "side", "qty", "entry", "mark", "margin", "leverage", "stop",
                                      "take_profit", "upnl", "liq_est")} for p in self.fbroker.snapshot(fv.marks)]
        out["margin_available_usd"] = round(min(fv.spendable_usd, f.max_total_margin_usd - fv.margin_used), 2)
        out["opens_today"] = fv.opens_today
        return out

    # ------------------------------------------------------------------ cycle
    def run_cycle(self, trigger: str = "schedule", why: str = "") -> Cycle | None:
        if not self._cycle_lock.acquire(blocking=False):
            return None
        row = None
        try:
            self.autopilot.started(trigger)
            row = self._run_cycle(trigger, why)
            return row
        finally:
            try:
                self.autopilot.finished(row.plan if row else None)
            except Exception:
                log.exception("could not book the next decision round")
            self._cycle_lock.release()

    def _run_cycle(self, trigger: str, why: str = "") -> Cycle:
        s = self.settings
        c = self.db.add(Cycle(mode=s.mode, trigger=trigger, provider=s.provider.name, model=self.provider.model))
        self.events.emit("cycle_started", {"id": c.id})
        t0 = time.time()
        timing: dict[str, int] = {}

        def lap(name: str, since: float) -> float:
            now = time.time()
            timing[name] = int((now - since) * 1000)
            return now

        def finish(**kw):
            kw.setdefault("duration_ms", int((time.time() - t0) * 1000))
            kw["timing"] = timing
            row = self.db.update(Cycle, c.id, **kw)
            self.events.emit("cycle", {"id": c.id, "status": row.status, "summary": row.summary, "error": row.error})
            return row

        try:
            prices, market, btc_change, anomaly = self.market_snapshot(max_age=0)
            t1 = lap("market_ms", t0)
            pf = self.portfolio(prices)
        except ExchangeError as e:
            n =self.db.kv_get("anomaly_count", 0) + 1
            self.db.kv_set("anomaly_count", n)
            if n >= s.limits.anomaly_failures_to_pause and not self.paused()[0]:
                self.pause(f"连续 {n} 次无法获取行情或账户：{e}")
            return finish(status="error", error=f"获取数据失败（第 {n} 次）：{e}")

        st = self.risk_state(btc_change, anomaly)
        if anomaly:
            n = self.db.kv_get("anomaly_count", 0) + 1
            self.db.kv_set("anomaly_count", n)
            if n < s.limits.anomaly_failures_to_pause:
                st.anomaly = None   # tolerate transient glitches; still recorded below
        else:
            self.db.kv_set("anomaly_count", 0)

        recent = self.recent_decisions(5)
        context = build_context(s, pf, st, market, recent)
        if st.halted:
            context["halted"] = {"symbols": st.halted, "note": "这些交易对币安目前暂停交易，本轮不要对它们提出买卖"}
        fv = None
        if s.futures.enabled:
            try:
                fv = self.futures_view(pf, st, fresh=True)
            except ExchangeError as e:
                log.warning("futures data unavailable: %s", e)
        context["futures"] = self.futures_context(fv)
        if s.futures.enabled and fv is None:
            context["futures"]["note"] = "合约数据暂不可用，本轮不要提出合约动作"
        if s.earn.enabled:
            context["earn"] = {"enabled": True, "in_earn": {a: round(v, 2) for a, v in self.earn.managed_amounts().items()},
                               "note": "活期理财里的资产已计入 portfolio 余额，买入或卖出时系统自动赎回；不需要你操作理财"}
        try:
            context["account_total_usd"] = round(self.account_portfolio(prices).total(), 2)
        except ExchangeError:
            pass
        try:
            tr = self.track.for_prompt(prices)
            if tr:
                context["track_record"] = tr
        except Exception:
            log.exception("track record unavailable")
        if self.autopilot.auto:
            context["autonomy"] = self.autopilot.context(trigger, why)
        t2 = lap("account_ms", t1)
        c = self.db.update(Cycle, c.id, context=context)

        if st.paused:
            return finish(status="skipped", summary=f"已暂停：{st.pause_reason}。本轮不调用模型、不交易。")
        trig = pause_trigger(s, st)
        if trig:
            self.pause(trig)
            return finish(status="skipped", summary=f"触发暂停条件：{trig}")

        prompt = build_user_prompt(context)
        self.db.update(Cycle, c.id, prompt=prompt)
        if trigger != "manual" and self.autopilot.auto:
            self.autopilot.count_call()
        try:
            comp = self.complete(SYSTEM, [{"role": "user", "content": prompt}], json_mode=True)
        except ProviderError as e:
            lap("model_ms", t2)
            self.events.emit("alert", {"text": f"模型调用失败，本轮不交易：{e}"})
            return finish(status="error", error=str(e))
        t3 = lap("model_ms", t2)
        self.db.update(Cycle, c.id, raw_output=comp.text, model=comp.model, usage=comp.usage)
        try:
            plan = parse_plan(comp.text)
        except PlanError as e:
            self.events.emit("alert", {"text": f"模型输出不合规，本轮不交易：{e}"})
            return finish(status="error", error=f"输出校验失败：{e}")

        actions, superseded = self._with_rebalance([a.model_dump() for a in plan.actions], pf)
        verdicts = evaluate(actions, pf, st)
        risk = []
        for a, v in zip(actions, verdicts, strict=True):
            row = v.to_dict()
            row["source"] = a.get("source", "ai")
            row["reason_model"] = a["reason"]
            row["invalidation"] = a["invalidation"]
            if v.decision == "execute":
                ok, msg, oid = self.execute(v.side, v.symbol, v.approved_usd, via="auto", cycle_id=c.id)
                row["result"] = msg
                row["order_id"] = oid
                if not ok:
                    row["decision"] = "failed"
            elif v.decision == "approve":
                ap = self.create_approval(c.id, v.side, v.symbol, v.approved_usd, a["reason"], a["invalidation"])
                row["approval_id"] = ap.id
                row["result"] = "等待批准"
            risk.append(row)
        risk.extend(superseded)
        if plan.futures:
            risk.extend(self._handle_futures(plan.futures, fv, c.id))
        if plan.alerts:
            self._replace_ai_alerts(plan.alerts, prices)
        lap("execute_ms", t3)
        return finish(status="done", plan=plan.model_dump(), risk=risk, summary=plan.summary)

    def _with_rebalance(self, actions: list[dict], pf: Portfolio) -> tuple[list[dict], list[dict]]:
        """手动分配: put the rebalancing trades first; the model's own actions on those assets are
        dropped (and reported) so the same asset is not traded twice in one round."""
        q = pf.settings.quote
        pending = {a.symbol[: -len(q)] for a in self.db.pending_approvals() if a.kind == "spot" and a.symbol.endswith(q)}
        reb = rebalance_actions(pf, skip=pending)
        if not reb:
            return actions, []
        mine = {a["symbol"] for a in reb}
        keep, dropped = [], []
        for a in actions:
            sym = normalize_symbol(str(a.get("symbol", "")), q)
            if sym in mine:
                v = Verdict(-1, str(a.get("side", "")).upper(), sym, float(a.get("quote_usd") or 0), reason="本轮由自动再平衡处理这个资产",
                            checks=[Check("自动再平衡", False, "本轮由自动再平衡处理这个资产")])
                dropped.append({**v.to_dict(), "source": "ai", "reason_model": a.get("reason", ""),
                                "invalidation": a.get("invalidation", "")})
            else:
                keep.append(a)
        return reb + keep, dropped

    def _handle_futures(self, acts, fv: FuturesView | None, cycle_id: str) -> list[dict]:
        actions = [a.model_dump() for a in acts]
        if fv is None:
            return [{"kind": "futures", "op": a["op"], "symbol": a["symbol"], "decision": "reject",
                     "reason": "合约未开启或数据不可用", "checks": [], "reason_model": a["reason"],
                     "invalidation": a["invalidation"]} for a in actions]
        out = []
        for a, v in zip(actions, evaluate_futures(actions, fv), strict=True):
            row = v.to_dict()
            row["reason_model"] = a["reason"]
            row["invalidation"] = a["invalidation"]
            act = self._capped_action(a, v)
            if v.decision == "execute":
                ok, msg = self.execute_futures(act, via="auto", cycle_id=cycle_id)
                row["result"] = msg
                if not ok:
                    row["decision"] = "failed"
            elif v.decision == "approve":
                ap = self.create_approval(cycle_id, v.op, v.symbol, v.margin, a["reason"], a["invalidation"],
                                          kind="futures", payload=act)
                row["approval_id"] = ap.id
                row["result"] = "等待批准"
            out.append(row)
        return out

    @staticmethod
    def _capped_action(a: dict, v) -> dict:
        """The action as the risk engine allowed it (capped margin/leverage), for execution or approval."""
        act = {**a, "symbol": v.symbol}
        if v.op in OPEN_OPS:
            act.update(margin_usd=v.margin, leverage=v.leverage, stop_loss=v.stop_loss, take_profit=v.take_profit)
        if v.op == "REDUCE":
            act["fraction"] = v.fraction
        return act

    def execute_futures(self, action: dict, via: str, cycle_id: str = "") -> tuple[bool, str]:
        """Re-check on fresh data, then act. Approved opens may shrink but never grow."""
        with self._exec_lock:
            s = self.settings
            try:
                prices, _m, btc_change, anomaly = self.market_snapshot(max_age=0)
                pf = self.portfolio(prices)
                st = self.risk_state(btc_change, anomaly)
                fv = self.futures_view(pf, st, fresh=True)
            except ExchangeError as e:
                return False, f"未执行：获取最新数据失败：{e}"
            v = evaluate_futures([action], fv)[0]
            allowed = v.decision == "execute" or (v.decision == "approve" and via in ("approved", "manual"))
            if not allowed:
                return False, f"未执行：{v.reason}"
            op, sym = v.op, v.symbol
            label = "模拟盘" if s.mode == "paper" else "实盘"
            cid = "bclawf" + uuid.uuid4().hex[:18]
            fb = self.fbroker
            row = next((p for p in fb.open_rows() if p.symbol == sym and p.status == "open"), None)
            try:
                if op in OPEN_OPS:
                    margin = min(v.margin, float(action.get("margin_usd") or v.margin))
                    self.earn.ensure_liquid(s.quote, margin * 1.03)
                    side = "LONG" if op == "OPEN_LONG" else "SHORT"
                    pos, fill = fb.open(sym, side, margin, v.leverage, v.stop_loss, v.take_profit, cid,
                                        {"cycle_id": cycle_id, "reason": str(action.get("reason", ""))[:1500],
                                         "invalidation": str(action.get("invalidation", ""))[:800]})
                    self._ftrade(op, sym, pos.id, fill.qty, fill.price, pos.margin, 0, fill.fee, via, cid, cycle_id, fill.raw)
                    msg = (f"[{label}] {'开多' if side == 'LONG' else '开空'} {sym} {fill.qty:g} @ {fill.price:,.4g}，"
                           f"保证金 ${pos.margin:,.2f} × {v.leverage}x，止损 {v.stop_loss:g}"
                           + (f"，止盈 {v.take_profit:g}" if v.take_profit else ""))
                elif op in ("CLOSE", "REDUCE"):
                    fill = fb.close(row, v.fraction, "AI 平仓" if via == "auto" else "手动平仓", cid=cid)
                    self._ftrade(op, sym, row.id, fill.qty, fill.price, 0, fill.pnl, fill.fee, via, cid, cycle_id, fill.raw)
                    msg = (f"[{label}] {'平仓' if op == 'CLOSE' else f'减仓 {v.fraction:.0%}'} {sym} {fill.qty:g} @ "
                           f"{fill.price:,.4g}，盈亏 ${fill.pnl:+,.2f}")
                else:
                    old = row.stop
                    fb.set_stop(row, v.stop_loss)
                    self._ftrade(op, sym, row.id, 0, v.stop_loss, 0, 0, 0, via, "", cycle_id, {"old": old})
                    msg = f"[{label}] {sym} 止损 {old:g} → {v.stop_loss:g}"
            except OrderUnknown as e:
                self._ftrade(op, sym, row.id if row else "", 0, 0, 0, 0, 0, via, cid, cycle_id, {"error": str(e)}, "unknown")
                self.events.emit("alert", {"text": f"{sym} 合约下单结果未知，稍后自动对账：{e}"})
                return False, str(e)
            except ExchangeError as e:
                self._ftrade(op, sym, row.id if row else "", 0, 0, 0, 0, 0, via, cid, cycle_id, {"error": str(e)}, "failed")
                self.events.emit("futures", {"symbol": sym, "op": op, "ok": False, "text": f"{sym} {op} 失败：{e}"})
                return False, f"执行失败：{e}"
            self.events.emit("futures", {"symbol": sym, "op": op, "ok": True, "text": msg, "via": via})
            return True, msg

    def _ftrade(self, action, symbol, pid, qty, price, margin, pnl, fee, via, cid, cycle_id, detail, status="filled"):
        self.db.add(FuturesTrade(day=self.today(), mode=self.settings.mode, position_id=pid, symbol=symbol, action=action,
                                 qty=qty, price=price, margin=margin, pnl=pnl, fee=fee, status=status, via=via,
                                 client_order_id=cid, cycle_id=cycle_id, detail=detail or {}))

    def close_position(self, pid: str, by: str = "web", fraction: float = 1.0) -> tuple[bool, str]:
        p = self.db.get(FuturesPosition, pid)
        if not p or p.status != "open" or p.mode != self.settings.mode:
            return False, "仓位不存在或已平仓"
        act = {"op": "CLOSE" if fraction >= 1 else "REDUCE", "symbol": p.symbol, "fraction": fraction,
               "reason": f"{by} 手动平仓", "invalidation": "-"}
        # closing is allowed even when futures is switched off, so the owner can always get out
        if not self.settings.futures.enabled:
            with self._exec_lock:
                try:
                    fill = self.fbroker.close(p, fraction, "手动平仓")
                except ExchangeError as e:
                    return False, f"平仓失败：{e}"
                self._ftrade(act["op"], p.symbol, p.id, fill.qty, fill.price, 0, fill.pnl, fill.fee, "manual", "", "", fill.raw)
                msg = f"平仓 {p.symbol} {fill.qty:g} @ {fill.price:,.4g}，盈亏 ${fill.pnl:+,.2f}"
                self.events.emit("futures", {"symbol": p.symbol, "op": "CLOSE", "ok": True, "text": msg, "via": "manual"})
                return True, msg
        return self.execute_futures(act, via="manual")

    def flatten_futures(self, by: str = "web") -> list[str]:
        return [self.close_position(p.id, by)[1] for p in self.fbroker.open_rows() if p.status == "open"]

    def futures_watch(self):
        """Paper: fire stops / take-profits / liquidations. Live: sync with the exchange, keep every stop in place."""
        fb = self.fbroker
        if not fb.open_rows():
            return
        with self._exec_lock:
            if self.settings.mode == "live":
                for p, kind, text in fb.live_sync():
                    self._ftrade(kind, p.symbol, p.id, p.qty, 0, 0, 0, 0, "exchange", "", "", {"text": text})
                    self.events.emit("futures", {"symbol": p.symbol, "op": kind, "ok": kind != "STOP_FAILED",
                                                 "text": f"[实盘] {p.symbol} {text}"})
                return
            for p, kind, fill in fb.paper_triggers(fb.marks(0)):
                self._ftrade(kind, p.symbol, p.id, fill.qty, fill.price, 0, fill.pnl, fill.fee, "exchange", "", "", fill.raw)
                name = {"LIQUIDATED": "强平", "STOP_HIT": "触发止损", "TP_HIT": "触发止盈"}[kind]
                self.events.emit("futures", {"symbol": p.symbol, "op": kind, "ok": True,
                                             "text": f"[模拟盘] {p.symbol} {name} @ {fill.price:,.4g}，盈亏 ${fill.pnl:+,.2f}"})

    def futures_overview(self) -> dict:
        s = self.settings
        rows = self.db.all(select(FuturesPosition).where(FuturesPosition.mode == s.mode, FuturesPosition.status == "closed")
                           .order_by(desc(FuturesPosition.closed)).limit(50))
        try:
            pos = self.fbroker.snapshot() if self.fbroker.open_rows() else []
            err = None
        except ExchangeError as e:
            pos, err = [], str(e)
        trades = self.db.all(select(FuturesTrade).where(FuturesTrade.mode == s.mode).order_by(desc(FuturesTrade.ts)).limit(100))
        realized = sum(r.realized for r in self.db.all(select(FuturesPosition).where(
            FuturesPosition.mode == s.mode, FuturesPosition.status == "closed")))
        return {"enabled": s.futures.enabled, "settings": s.futures.model_dump(), "positions": pos, "error": err,
                "closed": [r.model_dump() for r in rows], "trades": [t.model_dump() for t in trades],
                "realized": realized, "unrealized": sum(p["upnl"] for p in pos),
                "margin_used": sum(p["margin"] for p in pos), "equity": sum(max(p["margin"] + p["upnl"], 0) for p in pos)}

    def futures_markets(self, q: str = "", limit: int = 50) -> list[dict]:
        rules = self.fbroker.rules()
        tk = getattr(self, "_ftickers", None)
        if not tk or time.time() - tk[0] > 120:
            tk = (time.time(), self.fclient.tickers_24h())
            self._ftickers = tk
        q = q.strip().upper()
        on = {f"{t}USDT" for t in self.settings.futures.symbols}
        rows = [{"token": r.base, "symbol": sym, "price": tk[1].get(sym, {}).get("price"),
                 "change_pct": tk[1].get(sym, {}).get("change_pct"), "quote_volume": tk[1].get(sym, {}).get("quote_volume", 0.0),
                 "enabled": sym in on, "held": False}
                for sym, r in rules.items() if not q or q in r.base]
        rows.sort(key=lambda r: (not r["enabled"], r["token"] != q, -(r["quote_volume"] or 0)))
        return rows[:limit]

    def reset_paper(self):
        self.broker.reset_paper()
        for p in self.db.all(select(FuturesPosition).where(FuturesPosition.mode == "paper",
                                                           FuturesPosition.status.in_(("open", "pending")))):
            self.db.update(FuturesPosition, p.id, status="closed", closed=time.time(), close_reason="模拟盘重置")
        self.earn._pos = None

    def recent_decisions(self, n: int) -> list[dict]:
        rows = self.db.all(select(Cycle).where(Cycle.status == "done").order_by(desc(Cycle.ts)).limit(n))
        out = []
        for r in rows:
            out.append({
                "time": dt.datetime.fromtimestamp(r.ts, self.tz()).strftime("%m-%d %H:%M"),
                "trigger": r.trigger,
                "summary": r.summary[:300],
                "actions": [{"side": x.get("side") or x.get("op"), "symbol": x.get("symbol"),
                             "usd": x.get("approved_usd", x.get("margin")),
                             "decision": x.get("decision"), "result": x.get("result")} for x in (r.risk or [])],
            })
        return out

    # ------------------------------------------------------------------ execution
    def execute(self, side: str, symbol: str, usd: float, via: str, cycle_id: str = "") -> tuple[bool, str, str]:
        """Last line of defence: re-run every risk check on fresh data, then trade."""
        with self._exec_lock:
            s = self.settings
            try:
                prices, _m, btc_change, anomaly = self.market_snapshot(max_age=0)
                pf = self.portfolio(prices)
            except ExchangeError as e:
                return False, f"未执行：获取最新数据失败：{e}", ""
            st = self.risk_state(btc_change, anomaly)
            v = evaluate([{"side": side, "symbol": symbol, "quote_usd": usd}], pf, st)[0]
            allowed = v.decision == "execute" or (v.decision == "approve" and via == "approved")
            if not allowed:
                return False, f"未执行：{v.reason}", ""
            amount = min(usd, v.approved_usd)
            cid = "bclaw" + uuid.uuid4().hex[:18]
            btc_ref = prices.get("BTC")
            try:
                if side == "BUY":
                    self.earn.ensure_liquid(s.quote, amount)
                    fill = self.broker.buy(symbol, amount, cid)
                else:
                    token = symbol[: -len(s.quote)]
                    qty = amount / prices[token]
                    self.earn.ensure_liquid(token, qty)
                    fill = self.broker.sell(symbol, qty, cid)
            except OrderUnknown as e:
                # may exist on the exchange: counts toward today's limits until the reconciler resolves it
                o = self.db.add(Order(day=self.today(), mode=s.mode, symbol=symbol, side=side, quote_usd=amount,
                                      status="unknown", via=via, client_order_id=cid, cycle_id=cycle_id,
                                      detail={"error": str(e)}))
                self.events.emit("alert", {"text": f"{symbol} 下单结果未知，已计入今日额度，稍后自动对账：{e}"})
                self.events.emit("order", {"id": o.id, "status": "unknown"})
                return False, str(e), o.id
            except ExchangeError as e:
                o = self.db.add(Order(day=self.today(), mode=s.mode, symbol=symbol, side=side, quote_usd=amount,
                                      status="failed", via=via, client_order_id=cid, cycle_id=cycle_id,
                                      detail={"error": str(e)}))
                self.events.emit("order", {"id": o.id, "status": "failed"})
                return False, f"下单失败：{e}", o.id
            o = self.db.add(Order(day=self.today(), mode=s.mode, symbol=symbol, side=side,
                                  quote_usd=abs(fill.quote_spent), qty=fill.qty, price=fill.avg_price, fee=fill.fee,
                                  status=fill.status, via=via, client_order_id=cid, cycle_id=cycle_id,
                                  detail={**(fill.raw or {}), "btc_price": btc_ref}))
            label = "模拟盘" if s.mode == "paper" else "实盘"
            msg = (f"[{label}] {'买入' if side == 'BUY' else '卖出'} {fill.qty:.8g} {symbol[: -len(s.quote)]} "
                   f"@ ${fill.avg_price:,.2f}，金额 ${abs(fill.quote_spent):,.2f}")
            self.events.emit("order", {"id": o.id, "status": o.status, "text": msg, "via": via})
            return True, msg, o.id

    # ------------------------------------------------------------------ performance
    def performance(self) -> dict:
        """Average-cost P&L per asset from BinanceClaw's own fills (current mode), plus a
        'what if every buy had been BTC' benchmark. Holdings acquired outside BinanceClaw are not costed."""
        s = self.settings
        prices, *_ = self.market_snapshot()
        rows = self.db.all(select(Order).where(Order.mode == s.mode, Order.status.in_(("filled", "submitted")))
                           .order_by(Order.ts))
        prices = {**self.asset_prices({o.symbol[: -len(s.quote)] for o in rows}), **prices}
        pos: dict[str, dict] = {}
        bought = btc_equiv = bench_bought = 0.0
        cycle_btc: dict[str, float | None] = {}
        for o in rows:
            tok = o.symbol[: -len(s.quote)]
            p = pos.setdefault(tok, {"qty": 0.0, "cost": 0.0, "realized": 0.0, "bought": 0.0})
            if o.side == "BUY":
                p["qty"] += o.qty
                p["cost"] += o.quote_usd
                p["bought"] += o.quote_usd
                bought += o.quote_usd
                ref = (o.detail or {}).get("btc_price") or (o.price if tok == "BTC" else None)
                if not ref and o.cycle_id:
                    if o.cycle_id not in cycle_btc:
                        c = self.db.get(Cycle, o.cycle_id)
                        cycle_btc[o.cycle_id] = ((c.context or {}).get("market", {}).get("BTC", {}) or {}).get("price") if c else None
                    ref = cycle_btc[o.cycle_id]
                if ref:                       # buys without a BTC reference are left out of the benchmark
                    btc_equiv += o.quote_usd / ref
                    bench_bought += o.quote_usd
            elif p["qty"] > 0:
                q = min(o.qty, p["qty"])
                avg = p["cost"] / p["qty"]
                p["realized"] += o.quote_usd - avg * q
                p["qty"] -= q
                p["cost"] -= avg * q
        assets, value, cost, realized = {}, 0.0, 0.0, 0.0
        for tok, p in pos.items():
            px = prices.get(tok)
            v = p["qty"] * px if px else None
            assets[tok] = {"qty": p["qty"], "avg_cost": p["cost"] / p["qty"] if p["qty"] > 1e-12 else None,
                           "cost": p["cost"], "value": v, "price": px, "realized": p["realized"],
                           "unrealized": (v - p["cost"]) if v is not None else None,
                           "unrealized_pct": ((v / p["cost"] - 1) if v is not None and p["cost"] > 0 else None)}
            value += v or 0
            cost += p["cost"]
            realized += p["realized"]
        pnl = value - cost + realized
        bench_value = btc_equiv * prices["BTC"] if prices.get("BTC") else None
        closed = self.db.all(select(FuturesPosition).where(FuturesPosition.mode == s.mode, FuturesPosition.status == "closed"))
        fut = {"realized": sum(p.realized for p in closed), "trades": len(closed),
               "wins": len([p for p in closed if p.realized > 0])}
        try:
            fut["unrealized"] = sum(p["upnl"] for p in self.fbroker.snapshot()) if self.fbroker.open_rows() else 0.0
        except ExchangeError:
            fut["unrealized"] = None
        return {"mode": s.mode, "futures": fut, "assets": assets, "invested": bought, "open_cost": cost, "value": value,
                "unrealized": value - cost, "realized": realized, "pnl": pnl,
                "roi": pnl / bought if bought else None,
                "benchmark": {"name": "全部买 BTC", "value": bench_value, "covered_usd": bench_bought,
                              "pnl": (bench_value - bench_bought) if bench_value is not None else None,
                              "roi": (bench_value / bench_bought - 1) if bench_value and bench_bought else None}}

    # ------------------------------------------------------------------ price alerts
    def add_alert(self, symbol: str, op: str, price: float, note: str = "", source: str = "manual") -> PriceAlert:
        s = self.settings
        from ..risk.engine import normalize_symbol
        sym = normalize_symbol(symbol, s.quote)
        if op not in ("below", "above"):
            raise ValueError("op 只能是 below 或 above")
        if not (price > 0):
            raise ValueError("价格必须为正数")
        if sym[: -len(s.quote)] not in set(s.tradable_tokens()) | {"BTC"}:
            raise ValueError(f"{sym} 不在可交易资产中")
        if len(self.db.all(select(PriceAlert).where(PriceAlert.status == "active"))) >= 50:
            raise ValueError("提醒太多了（最多 50 个），先删掉一些")
        a = self.db.add(PriceAlert(symbol=sym, op=op, price=float(price), note=note[:200], source=source))
        self.events.emit("alerts", {"id": a.id})
        return a

    def cancel_alert(self, aid: str) -> bool:
        a = self.db.get(PriceAlert, aid)
        if not a or a.status != "active":
            return False
        self.db.update(PriceAlert, aid, status="cancelled")
        self.events.emit("alerts", {"id": aid})
        return True

    def active_alerts(self) -> list[PriceAlert]:
        return self.db.all(select(PriceAlert).where(PriceAlert.status == "active").order_by(PriceAlert.created))

    def _replace_ai_alerts(self, specs, prices: dict):
        """The AI restates its watch levels each round: replace its previous active alerts.
        Levels more than 50% away from the current price are dropped as nonsensical."""
        for a in self.active_alerts():
            if a.source == "ai":
                self.db.update(PriceAlert, a.id, status="cancelled")
        for sp in specs:
            tok = sp.symbol.upper().replace("-", "").replace("/", "").removesuffix(self.settings.quote)
            px = prices.get(tok)
            if not px or abs(sp.price / px - 1) > 0.5:
                continue
            if (sp.op == "below" and sp.price >= px) or (sp.op == "above" and sp.price <= px):
                continue                                   # would fire immediately: not a watch level
            try:
                self.add_alert(tok, sp.op, sp.price, sp.note, source="ai")
            except ValueError:
                continue

    def check_alerts(self) -> int:
        alerts = self.active_alerts()
        if not alerts:
            return 0
        syms = sorted({a.symbol for a in alerts})
        try:
            px = self.client.prices(syms)
        except ExchangeError:
            return 0
        fired = 0
        for a in alerts:
            p = px.get(a.symbol)
            if p is None:
                continue
            if (a.op == "below" and p <= a.price) or (a.op == "above" and p >= a.price):
                self.db.update(PriceAlert, a.id, status="triggered", triggered_at=time.time(), trigger_price=p)
                self.events.emit("price_alert", {"id": a.id, "symbol": a.symbol, "op": a.op, "price": a.price,
                                                 "now": p, "note": a.note, "source": a.source})
                fired += 1
        return fired

    def reconcile_orders(self) -> int:
        """Resolve live orders whose final state we don't know yet (submitted / unknown)."""
        rows = self.db.all(select(Order).where(Order.mode == "live", Order.status.in_(("submitted", "unknown")),
                                               Order.ts > time.time() - 3 * 86400))
        fixed = 0
        for o in rows:
            try:
                r = self.client.order_status(o.symbol, o.client_order_id)
            except ExchangeError as e:
                if getattr(e, "code", None) == -2013 and time.time() - o.ts > 600:
                    self.db.update(Order, o.id, status="failed", detail={**(o.detail or {}), "reconcile": "not found"})
                    fixed += 1
                continue
            f = self.broker.parse_live(r, o.symbol[: -len(self.settings.quote)])
            if f.status in ("submitted",):
                continue
            self.db.update(Order, o.id, status=f.status, qty=f.qty, price=f.avg_price,
                           quote_usd=abs(f.quote_spent) or o.quote_usd, detail={**(o.detail or {}), "reconciled": r})
            self.events.emit("order", {"id": o.id, "status": f.status, "via": "reconcile"})
            fixed += 1
        return fixed

    # ------------------------------------------------------------------ approvals
    def create_approval(self, cycle_id, side, symbol, usd, reason, invalidation, kind: str = "spot",
                        payload: dict | None = None) -> Approval:
        ttl = self.settings.schedule.approval_timeout_minutes * 60
        a = self.db.add(Approval(expires=time.time() + ttl, cycle_id=cycle_id, side=side, symbol=symbol,
                                 quote_usd=usd, reason=reason, invalidation=invalidation, kind=kind, payload=payload or {}))
        self.events.emit("approval_created", {"id": a.id})
        return a

    def resolve_approval(self, aid: str, approve: bool, by: str) -> tuple[bool, str]:
        a = self.db.get(Approval, aid)
        if not a or a.status != "pending":
            return False, "这笔已处理或不存在"
        if time.time() > a.expires:
            if self.db.claim_approval(aid, "expired", "system"):
                self.events.emit("approval", {"id": aid, "status": "expired"})
            return False, "已过期"
        if not approve:
            if self.db.claim_approval(aid, "rejected", by):
                self.db.update(Approval, aid, result="已拒绝")
                self.events.emit("approval", {"id": aid, "status": "rejected", "by": by})
                return True, "已拒绝"
            return False, "这笔已处理"
        if not self.db.claim_approval(aid, "approved", by):
            return False, "这笔已处理"
        if a.kind == "futures":
            ok, msg = self.execute_futures(a.payload or {}, via="approved", cycle_id=a.cycle_id)
        else:
            ok, msg, _oid = self.execute(a.side, a.symbol, a.quote_usd, via="approved", cycle_id=a.cycle_id)
        self.db.update(Approval, aid, result=msg, status="approved" if ok else "failed")
        self.events.emit("approval", {"id": aid, "status": "approved" if ok else "failed", "text": msg, "by": by})
        return ok, msg

    def expire_approvals(self):
        now = time.time()
        for a in self.db.pending_approvals():
            if a.expires < now and self.db.claim_approval(a.id, "expired", "system"):
                self.db.update(Approval, a.id, result="超时作废")
                self.events.emit("approval", {"id": a.id, "status": "expired"})

    # ------------------------------------------------------------------ live gate
    def live_check(self, features: list[str] | None = None) -> dict:
        if not (self.secrets.binance_api_key and self.secrets.binance_api_secret):
            return {"ok": False, "rows": [], "blockers": ["未配置币安 API Key"]}
        try:
            r = self.client.api_restrictions()
        except ExchangeError as e:
            return {"ok": False, "rows": [], "blockers": [f"无法读取 Key 权限：{e}"]}
        allow = self.settings.live_gate_allow
        if features is None:
            features = ["futures"] if self.settings.futures.enabled else []
        need = gate.needed(features)
        b = gate.blockers(r, allow, need)
        return {"ok": not b, "rows": gate.check_restrictions(r, allow, need), "blockers": b}

    def set_mode(self, mode: str, confirm: str = "", by: str = "web") -> tuple[bool, str]:
        if mode == "paper":
            self.settings_store.update({"mode": "paper"}, allow_mode_change=True)
            self._rebuild()
            self.events.emit("mode", {"mode": "paper", "by": by})
            return True, "已切换到模拟盘"
        if mode != "live":
            return False, "未知模式"
        if confirm.strip() != gate.CONFIRM_PHRASE:
            return False, f"请输入确认短语：{gate.CONFIRM_PHRASE}"
        chk = self.live_check()
        if not chk["ok"]:
            return False, "无法开启实盘：" + "；".join(chk["blockers"])
        self.settings_store.update({"mode": "live"}, allow_mode_change=True)
        self._rebuild()
        self.events.emit("mode", {"mode": "live", "by": by})
        return True, "已切换到实盘"

    # ------------------------------------------------------------------ overview
    def overview(self) -> dict:
        s = self.settings
        try:
            prices, market, btc_change, anomaly = self.market_snapshot()
            pf = self.portfolio(prices)
            err = None
        except ExchangeError as e:
            prices, market, btc_change, anomaly, pf, err = {}, {}, None, None, None, str(e)
        paused, reason = self.paused()
        st = self.risk_state(btc_change, anomaly)
        out = {
            "mode": s.mode, "paused": paused, "pause_reason": reason, "error": err,
            "provider": {"name": s.provider.name, "model": self.provider.model},
            "next_cycle_at": self.next_cycle_at(), "cycle_minutes": s.schedule.cycle_minutes,
            "autopilot": self.autopilot.status(),
            "today": {"orders": st.today_orders, "notional": st.today_notional,
                      "max_orders": s.limits.max_orders_per_day, "max_notional": s.limits.max_daily_notional_usd},
            "pending_approvals": len(self.db.pending_approvals()),
            "btc_change_7d": btc_change, "anomaly": anomaly, "market": market,
            "targets": s.group_targets(),
            "allocation": s.allocation.model_dump(),
            "capital": {"locked": s.capital.locked, "budget_usd": s.capital.budget_usd,
                        "deployed_usd": round(self.deployed_usd(), 2)},
            "risk_preset": s.risk_preset,
            "limits": s.limits.model_dump(),
            "futures": {"enabled": s.futures.enabled, "open": len([p for p in self.fbroker.open_rows() if p.status == "open"])},
            "earn": {"enabled": s.earn.enabled, "positions": self.earn.positions() if s.earn.enabled else {}},
        }
        if pf:
            out.update(total=pf.total(), values=pf.values(), balances=pf.balances,
                       token_weights=pf.token_weights(), category_weights=pf.category_weights())
            try:
                acct = self.account_portfolio(prices)
                out.update(account_balances=acct.balances, account_values=acct.values(), account_total=acct.total())
            except ExchangeError:
                pass
        shown = {*out.get("account_values", {}), *out.get("values", {}), *(t for g in out["targets"].values() for t in g["tokens"])}
        out["binance_categories"] = {}
        for t in sorted(shown - {FUTURES_ASSET}):
            tags = self.tags_of(t)
            out["binance_categories"][t] = {"primary": cats.label(cats.primary(tags)), "tags": [cats.label(x) for x in tags]}
        return out

    def equity_series(self, limit: int = 500) -> list[dict]:
        rows = self.db.all(select(Cycle).order_by(desc(Cycle.ts)).limit(limit))
        # account total (incl. locked / unselected assets) so changing what the AI manages
        # does not show up as profit or loss; older rows fall back to the managed total
        pts = [{"t": r.ts, "total": (r.context or {}).get("account_total_usd", (r.context or {}).get("portfolio", {}).get("total_usd")),
                "mode": r.mode} for r in rows if (r.context or {}).get("portfolio")]
        return list(reversed(pts))

    # ------------------------------------------------------------------ chat & reports
    def chat(self, text: str, channel: str = "web") -> str:
        with self._chat_lock:
            self.db.add(ChatMessage(channel=channel, role="user", text=text))
            cut = self.db.kv_get("chat_cut_ts", 0)
            hist = list(reversed(self.db.all(select(ChatMessage).where(ChatMessage.ts > cut)
                                             .order_by(desc(ChatMessage.ts)).limit(20))))
            snapshot = self.overview()
            slim = {k: snapshot.get(k) for k in ("mode", "paused", "pause_reason", "total", "token_weights",
                                                  "category_weights", "targets", "today", "btc_change_7d", "pending_approvals")}
            slim["recent_decisions"] = self.recent_decisions(3)
            import json as _json
            system = CHAT_SYSTEM + "\n<data>\n" + _json.dumps(slim, ensure_ascii=False, default=str) + "\n</data>"
            msgs = [{"role": m.role, "content": m.text} for m in hist]
            try:
                comp = self.complete(system, msgs)
                reply = comp.text.strip() or "（空回复）"
            except ProviderError as e:
                reply = f"模型调用失败：{e}"
            self.db.add(ChatMessage(channel=channel, role="assistant", text=reply, provider=self.provider.name))
            self.events.emit("chat", {"channel": channel})
            return reply

    # ------------------------------------------------------------------ allocation proposals
    PROPOSAL_TTL = 86400

    @staticmethod
    def _flat_targets(categories: dict) -> dict[str, float]:
        return {t: w for c in categories.values() for t, w in (c["tokens"] if isinstance(c, dict) else c.tokens).items()}

    def propose_allocation(self, instruction: str | None = None, source: str = "web") -> dict:
        """Ask the model for target weights (fresh, or the current ones edited per ``instruction``).
        Validated and stored as a pending proposal; settings change only via apply_allocation."""
        s = self.settings
        instruction = (instruction or "").strip()[:2000] or None
        with self._chat_lock:
            index = {r["token"]: r for r in self.markets("", 100_000)}
            current = {k: v.model_dump() for k, v in s.categories.items()}
            words = {w.upper() for w in re.findall(r"[A-Za-z][A-Za-z0-9]{1,14}", instruction or "")}
            keep = set(s.allocation.enabled) | set(self._flat_targets(current)) | (words & set(index))
            top = [r["token"] for r in sorted(index.values(), key=lambda r: -(r["quote_volume"] or 0))
                   if r["trading"] and r["selectable"]][:40]
            cands = {}
            for tok in list(dict.fromkeys([*sorted(keep), *top])):
                r = index.get(tok)
                if r and r["selectable"]:
                    cands[tok] = {"price": r["price"], "change_24h": r["change_pct"],
                                  "quote_volume_24h": round(r["quote_volume"] or 0), "status": r["status"]}
            try:
                prices, market, _b, _a = self.market_snapshot()
                pf = self.portfolio(prices)
                weights = {k: round(v, 4) for k, v in pf.token_weights().items()}
                total = round(pf.total(), 2)
            except ExchangeError:
                market, weights, total = {}, {}, None
            context = {
                "investor_notes": s.investor_notes, "risk_preset": s.risk_preset, "stables": s.stables,
                "limits": {"stable_floor": s.limits.stable_floor, "max_order_usd": s.limits.max_order_usd},
                "current": {"mode": s.allocation.mode, "categories": current, "enabled": s.allocation.enabled},
                "portfolio": {"total_usd": total, "token_weights": weights},
                "market": market, "candidates": cands,
                "halted": sorted(t for t, r in cands.items() if r["status"] != "TRADING"),
            }
            try:
                comp = self.complete(alloc.SYSTEM, [{"role": "user", "content": alloc.build_prompt(context, instruction)}],
                                     json_mode=True)
            except ProviderError as e:
                raise ValueError(f"模型调用失败：{e}") from e
            try:
                prop = alloc.parse_proposal(comp.text)
                cats, notes = alloc.normalize(prop, s.stables)
            except PlanError as e:
                raise ValueError(f"模型给出的配置不合规：{e}") from e
            tokens = [t for t in self._flat_targets(cats) if t not in s.stables]
            try:
                rules = self.broker.rules([f"{t}{s.quote}" for t in tokens])
            except ExchangeError as e:
                raise ValueError(f"无法在币安确认这些交易对：{e}") from e
            missing = [t for t in tokens if f"{t}{s.quote}" not in rules]
            if missing:
                raise ValueError(f"模型给出的资产在币安没有 {s.quote} 现货交易对：{', '.join(missing)}")
            halted = [t for t in tokens if rules[f"{t}{s.quote}"].status != "TRADING"]
            if halted:
                notes.append(f"{', '.join(halted)} 目前在币安暂停交易，恢复前不会买入")
            if sum(cats["STABLE"]["tokens"].values()) < s.limits.stable_floor - 1e-9:
                notes.append(f"稳定币目标低于稳定币下限 {s.limits.stable_floor:.0%}，风控仍按下限执行")
            prop_row = {"id": uuid.uuid4().hex[:12], "ts": time.time(), "source": source, "instruction": instruction,
                        "summary": prop.summary, "categories": cats, "notes": notes, "status": "pending",
                        "current": self._flat_targets(current), "proposed": self._flat_targets(cats),
                        "model": comp.model}
            self.db.kv_set("alloc_proposal", prop_row)
            self.events.emit("allocation", {"id": prop_row["id"], "status": "pending"})
            return prop_row

    def allocation_proposal(self) -> dict | None:
        p = self.db.kv_get("alloc_proposal")
        if p and p.get("status") == "pending" and time.time() - p["ts"] > self.PROPOSAL_TTL:
            p["status"] = "expired"
        return p

    def resolve_allocation(self, pid: str, apply: bool, by: str = "web") -> dict:
        """Apply (or discard) a pending proposal. Applying sets the targets, switches to "targets"
        mode and adds the new assets to the tradable list; trades happen in later cycles, under the risk engine."""
        p = self.allocation_proposal()
        if not p or p["id"] != pid:
            raise ValueError("这个配置提案已经不存在，请重新生成")
        if p["status"] != "pending":
            raise ValueError(f"这个配置提案已经{ {'applied': '应用', 'discarded': '放弃', 'expired': '过期'}.get(p['status'], '处理') }")
        if apply:
            s = self.settings
            new_tokens = [t for t in p["proposed"] if t not in s.stables]
            enabled = list(dict.fromkeys([*new_tokens, *s.allocation.enabled]))[:alloc.MAX_TOKENS]
            self.update_settings({"categories": p["categories"], "allocation": {"mode": "targets", "enabled": enabled}})
        p.update(status="applied" if apply else "discarded", resolved_by=by, resolved_ts=time.time())
        self.db.kv_set("alloc_proposal", p)
        self.events.emit("allocation", {"id": pid, "status": p["status"]})
        return p

    def daily_report(self) -> Report:
        s = self.settings
        ov = self.overview()
        since = time.time() - 86400
        orders = self.db.all(select(Order).where(Order.ts >= since).order_by(Order.ts))
        lines = [f"📊 BinanceClaw 日报 {self.today()}（{'模拟盘' if s.mode == 'paper' else '实盘'}）",
                 f"总资产 ${ov.get('total', 0):,.2f}" + ("  ⏸ 已暂停" if ov["paused"] else "")]
        for cat, w in (ov.get("category_weights") or {}).items():
            band = ov["targets"].get(cat, {}).get("band", [0, 0])
            lines.append(f"· {cat} {w:.1%}（区间 {band[0]:.0%}–{band[1]:.0%}）")
        if ov.get("btc_change_7d") is not None:
            lines.append(f"BTC 7 日 {ov['btc_change_7d']:+.1%}")
        if s.futures.enabled or self.fbroker.open_rows():
            try:
                for p in self.fbroker.snapshot():
                    lines.append(f"合约 {p['symbol']} {'多' if p['side'] == 'LONG' else '空'} {p['leverage']}x "
                                 f"保证金 ${p['margin']:,.0f} 浮盈 ${p['upnl']:+,.2f} 止损 {p['stop']:g}")
            except ExchangeError:
                pass
        lines.append(f"过去 24 小时成交 {len([o for o in orders if o.status in ('filled', 'submitted')])} 笔：")
        for o in orders:
            lines.append(f"  · {dt.datetime.fromtimestamp(o.ts, self.tz()):%m-%d %H:%M} {o.side} {o.symbol} ${o.quote_usd:,.2f} {o.status}")
        body = "\n".join(lines)
        try:
            comp = self.complete(CHAT_SYSTEM, [{"role": "user", "content":
                "根据以下数据写今日点评（不超过 300 字）：当前配置偏离、过去 24 小时决策是否合理、"
                "距离暂停条件多远、需要我关注的事（每条写理由和判断错了的信号）。\n<data>\n" + body + "\n</data>"}])
            body += "\n\n— AI 点评 —\n" + comp.text.strip()
        except ProviderError as e:
            body += f"\n\n（AI 点评失败：{e}）"
        rep = Report(day=self.today(), text=body)
        with self.db.write_lock, self.db.session() as ses:
            ses.merge(rep)
            ses.commit()
        self.events.emit("report", {"day": rep.day, "text": body})
        return rep

    # ------------------------------------------------------------------ scheduler
    def start_scheduler(self):
        self.db.kv_set("started_at", time.time())
        self.stream.start()
        threading.Thread(target=self._supervise, name="scheduler", daemon=True).start()

    def _supervise(self):
        """Restart the scheduler loop if it ever dies."""
        while True:
            try:
                self._loop()
            except Exception:
                log.exception("scheduler loop crashed; restarting in 10 s")
                time.sleep(10)

    def last_good_cycle(self) -> float:
        rows = self.db.all(select(Cycle).where(Cycle.status.in_(("done", "skipped"))).order_by(desc(Cycle.ts)).limit(1))
        return rows[0].ts if rows else 0.0

    def watchdog(self):
        """Alert once if no decision round has succeeded for 3 cycle intervals (while not paused).
        In auto mode the interval is the longest gap allowed, and a spent daily call budget is not a fault."""
        sch = self.settings.schedule
        limit = 3 * (sch.max_gap_minutes if self.autopilot.auto else sch.cycle_minutes) * 60 + 300
        last = self.last_good_cycle() or self.db.kv_get("started_at", time.time())
        stale = time.time() - last > limit and not self.paused()[0]
        if self.autopilot.auto and self.autopilot.calls_left() <= 0:
            stale = False
        alerted = self.db.kv_get("watchdog_alerted", False)
        if stale and not alerted:
            mins = int((time.time() - last) / 60)
            self.events.emit("alert", {"text": f"已经 {mins} 分钟没有成功完成一轮决策，请检查模型或网络（/status、网页 AI 页）"})
            self.db.kv_set("watchdog_alerted", True)
        elif not stale and alerted:
            self.db.kv_set("watchdog_alerted", False)

    def backup_db(self, keep: int = 14):
        """Daily online backup of the SQLite database to data/backups/."""
        import sqlite3
        d = self.data_dir / "backups"
        d.mkdir(exist_ok=True)
        target = d / f"binanceclaw-{self.today()}.db"
        if target.exists():
            return
        src = sqlite3.connect(self.data_dir / "binanceclaw.db")
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        target.chmod(0o600)
        for old in sorted(d.glob("binanceclaw-*.db"))[:-keep]:
            old.unlink()

    def health(self) -> dict:
        hb = self.db.kv_get("heartbeat", 0)
        last = self.last_good_cycle()
        return {"scheduler_alive": time.time() - hb < 120, "heartbeat_age_s": int(time.time() - hb) if hb else None,
                "last_good_cycle_age_s": int(time.time() - last) if last else None,
                "paused": self.paused()[0], "mode": self.settings.mode}

    def _every(self, name: str, seconds: float, fn):
        last = getattr(self, "_ticks", {})
        self._ticks = last
        if time.time() - last.get(name, 0) >= seconds:
            last[name] = time.time()
            try:
                fn()
            except Exception:
                log.exception("periodic task %s failed", name)

    def _watch_stream(self):
        s = self.settings
        tokens = set(s.tradable_tokens()) | {"BTC"}
        self.stream.watch(f"{t}{s.quote}" for t in tokens if t not in s.stables)

    def _loop(self):
        if not self.next_cycle_at() or self.next_cycle_at() < time.time():
            self.db.kv_set("next_cycle_at", time.time() + 20)
        while True:
            try:
                self.db.kv_set("heartbeat", time.time())
                self.expire_approvals()
                self._every("reconcile", 60, self.reconcile_orders)
                self._every("alerts", 30, self.check_alerts)
                self._every("futures", 10, self.futures_watch)
                self._every("earn", 600, self.earn.sweep)
                self._every("watchdog", 300, self.watchdog)
                self._every("backup", 3600, self.backup_db)
                self._every("stream", 60, self._watch_stream)
                due = self.autopilot.due()
                if due:
                    if due[0] == "schedule":
                        self.db.kv_set("next_cycle_at", time.time() + self.settings.schedule.cycle_minutes * 60)
                    self.run_cycle(*due)
                hh, mm = map(int, self.settings.schedule.daily_report_time.split(":"))
                now = self.now()
                if (now.hour, now.minute) >= (hh, mm) and self.db.kv_get("last_report_day") != self.today():
                    self.db.kv_set("last_report_day", self.today())
                    self.daily_report()
            except Exception:
                log.exception("scheduler iteration failed")
            time.sleep(5)
