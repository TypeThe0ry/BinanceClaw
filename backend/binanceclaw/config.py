"""Configuration.

Two layers:
* ``Secrets`` — read only from environment variables (``data/.env`` in deployments, chmod 600).
  Never returned by the API, never logged.
* ``Settings`` — non-secret, editable from the web UI, persisted as ``data/settings.json``.
  Every numeric limit is clamped to ``HARD_BOUNDS`` so the UI (or a bad file) cannot
  configure something reckless.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

DATA_DIR = Path(os.environ.get("BINANCECLAW_DATA", "./data")).resolve()

# (min, max) for every risk number. Settings outside these are clamped on load/save.
HARD_BOUNDS: dict[str, tuple[float, float]] = {
    "max_order_usd": (5, 5000),
    "auto_execute_max_usd": (0, 5000),
    "max_auto_daily_usd": (0, 20000),
    "max_orders_per_day": (1, 20),
    "max_daily_notional_usd": (5, 20000),
    "stable_floor": (0.05, 0.9),
    "btc_7d_drop_pause": (0.05, 0.6),
    "anomaly_failures_to_pause": (1, 10),
    "price_deviation_anomaly": (0.01, 0.2),
    "cycle_minutes": (5, 1440),
    "min_gap_minutes": (1, 240),
    "max_gap_minutes": (15, 1440),
    "max_calls_per_day": (1, 500),
    "move_trigger_pct": (0.003, 0.2),
    "approval_timeout_minutes": (5, 720),
    "question_timeout_minutes": (5, 1440),
    "max_asset_weight": (0.05, 1.0),
    "ai_candidates": (5, 30),
    "rebalance_drift": (0.01, 0.5),
    # futures
    "max_leverage": (1, 20),
    "max_position_margin_usd": (5, 10000),
    "max_total_margin_usd": (5, 50000),
    "max_loss_per_trade_usd": (1, 5000),
    "max_stop_distance": (0.005, 0.5),
    "auto_open_max_margin_usd": (0, 10000),
    "max_opens_per_day": (1, 20),
    # earn
    "keep_liquid_usd": (0, 1_000_000),
    "min_move_usd": (1, 100_000),
    # telegram
    "digest_minutes": (5, 1440),
}

# Suggestions for the asset picker (any TRADING <TOKEN>USDT spot pair can also be typed in).
CANDIDATE_TOKENS = ["BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "TRX", "TON",
                    "QQQB", "NVDAB"]

# One-click risk presets. Applying one overwrites these limit fields and the decision cadence;
# editing any limit afterwards switches the preset label to "custom".
RISK_PRESETS: dict[str, dict] = {
    "conservative": {"limits": {"max_order_usd": 300, "auto_execute_max_usd": 100, "max_auto_daily_usd": 200,
                                "max_orders_per_day": 2, "max_daily_notional_usd": 600, "stable_floor": 0.30,
                                "btc_7d_drop_pause": 0.15, "allow_sell": False},
                     "schedule": {"cycle_minutes": 120}, "allocation": {"max_asset_weight": 0.5}},
    "balanced": {"limits": {"max_order_usd": 800, "auto_execute_max_usd": 200, "max_auto_daily_usd": 400,
                            "max_orders_per_day": 3, "max_daily_notional_usd": 2400, "stable_floor": 0.15,
                            "btc_7d_drop_pause": 0.25, "allow_sell": False},
                 "schedule": {"cycle_minutes": 60}, "allocation": {"max_asset_weight": 0.7}},
    "aggressive": {"limits": {"max_order_usd": 2000, "auto_execute_max_usd": 500, "max_auto_daily_usd": 1000,
                              "max_orders_per_day": 6, "max_daily_notional_usd": 6000, "stable_floor": 0.08,
                              "btc_7d_drop_pause": 0.35, "allow_sell": False},
                   "schedule": {"cycle_minutes": 30}, "allocation": {"max_asset_weight": 0.85}},
}


class Category(BaseModel):
    tokens: dict[str, float]          # token -> target weight
    band: tuple[float, float]         # allowed [low, high] for the whole category

    @model_validator(mode="after")
    def _check(self):
        lo, hi = self.band
        if not (0 <= lo <= hi <= 1):
            raise ValueError("band must satisfy 0 <= low <= high <= 1")
        return self


class Limits(BaseModel):
    max_order_usd: float = 800
    auto_execute_max_usd: float = 200
    max_auto_daily_usd: float = 400      # 防止把大单拆成多笔小单绕过审批
    max_orders_per_day: int = 3
    max_daily_notional_usd: float = 2400
    stable_floor: float = 0.15
    btc_7d_drop_pause: float = 0.25
    allow_sell: bool = False           # SELL is never auto-executed; when False it is rejected outright
    anomaly_failures_to_pause: int = 3
    price_deviation_anomaly: float = 0.05
    fee_buffer_ratio: float = 0.002


class Schedule(BaseModel):
    # auto: the AI decides when to look again (next_check_minutes in its plan) and real-time events
    # (price moves, its own price levels, futures stops, fills, balance changes) wake it early;
    # fixed: one round every cycle_minutes, as before. Either way every action still goes through the risk engine.
    decision_mode: Literal["auto", "fixed"] = "auto"
    cycle_minutes: int = 60            # fixed mode interval; auto mode's default check-in when the AI names none
    min_gap_minutes: int = 5           # auto: never two rounds closer than this (manual rounds excepted)
    max_gap_minutes: int = 240         # auto: never longer than this without a round
    max_calls_per_day: int = 48        # auto: model-call budget per day (manual rounds not counted)
    move_trigger_pct: float = 0.02     # auto: wake on a move this large since the last round (the AI may tighten/loosen it)
    approval_timeout_minutes: int = 30
    daily_report_time: str = "08:00"
    timezone: str = "UTC"


class ProviderSettings(BaseModel):
    name: Literal["claude_code", "openai", "openai_compat", "anthropic_api"] = "claude_code"
    model: str = ""                    # empty = provider default
    base_url: str = ""                 # only for openai_compat
    timeout_seconds: int = 240
    temperature: float = 0.2


class Capital(BaseModel):
    """What the AI is allowed to touch."""
    locked: dict[str, float] = Field(default_factory=dict)   # asset -> amount that is never touched or seen
    budget_usd: float | None = None                           # cap on net capital the AI may deploy (None = no cap)

    @field_validator("locked")
    @classmethod
    def _locked(cls, v):
        out = {}
        for k, amt in v.items():
            if amt is None or float(amt) < 0:
                raise ValueError(f"locked amount for {k} must be >= 0")
            if float(amt) > 0:
                out[k.upper()] = float(amt)
        return out

    @field_validator("budget_usd")
    @classmethod
    def _budget(cls, v):
        if v is not None and v < 0:
            raise ValueError("budget must be >= 0")
        return v


def _token_list(v: list[str], cap: int) -> list[str]:
    out = []
    for t in v:
        t = t.upper().strip()
        if not t.isalnum() or len(t) > 15:
            raise ValueError(f"invalid token {t!r}")
        if t not in out:
            out.append(t)
    if len(out) > cap:
        raise ValueError(f"at most {cap} tokens")
    return out


class Allocation(BaseModel):
    # "targets" = 手动分配: the owner sets target weights and the system keeps the portfolio near them.
    # "ai" = AI 自主分配: no fixed targets, the model decides the weights itself.
    mode: Literal["targets", "ai"] = "targets"
    # "ai" mode: which assets the AI may trade. "all" = every Binance spot asset with a <TOKEN><quote>
    # pair; "whitelist" = only ``enabled``; "blacklist" = everything except ``blocked``.
    universe: Literal["all", "whitelist", "blacklist"] = "all"
    # Whitelist in "ai" mode; in "targets" mode only tokens that also have a target are tradable.
    enabled: list[str] = Field(default_factory=lambda: ["BTC", "ETH", "QQQB", "NVDAB"])
    blocked: list[str] = Field(default_factory=list)   # blacklist: never traded in "ai" mode
    max_asset_weight: float = 0.7        # "ai" mode: ceiling per asset (share of managed value)
    # "all" / "blacklist": assets with full market data each round (holdings first, then by 24h volume)
    ai_candidates: int = 30
    auto_rebalance: bool = True          # "targets" mode: trade back to target when an asset drifts too far
    rebalance_drift: float = 0.05        # ... by more than this (absolute weight, 0.05 = 5 percentage points)

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data):
        # Settings saved before universes existed: an "ai" mode owner chose an explicit list, keep it.
        if isinstance(data, dict) and "universe" not in data and data.get("mode") == "ai":
            data = {**data, "universe": "whitelist"}
        return data

    @field_validator("enabled")
    @classmethod
    def _enabled(cls, v):
        return _token_list(v, 30)

    @field_validator("blocked")
    @classmethod
    def _blocked(cls, v):
        return _token_list(v, 300)


class Futures(BaseModel):
    """USDⓈ-M perpetuals. Off by default; every position must carry an exchange-side stop-loss."""
    enabled: bool = False
    symbols: list[str] = Field(default_factory=lambda: ["BTC", "ETH"])    # base assets, <TOKEN>USDT perps
    max_leverage: int = 3
    max_position_margin_usd: float = 200
    max_total_margin_usd: float = 500
    max_loss_per_trade_usd: float = 30          # margin * leverage * stop distance (+ fees) must stay under this
    max_stop_distance: float = 0.08              # stop-loss at most 8% from mark price
    allow_short: bool = True
    auto_open: bool = False                      # False: every open/increase waits for approval
    auto_open_max_margin_usd: float = 50
    max_opens_per_day: int = 3

    @field_validator("symbols")
    @classmethod
    def _syms(cls, v):
        out = []
        for t in v:
            t = t.upper().strip().removesuffix("USDT")
            if not t.isalnum() or len(t) > 15:
                raise ValueError(f"invalid futures symbol {t!r}")
            if t not in out:
                out.append(t)
        if len(out) > 20:
            raise ValueError("at most 20 futures symbols")
        return out


class Earn(BaseModel):
    """Simple Earn flexible: park idle balances, redeem automatically when a trade needs them.
    Only flexible products (redeemable at any time), so the money stays usable for trading."""
    enabled: bool = True
    auto_all: bool = True                        # every asset with a flexible product; False = only ``assets``
    assets: list[str] = Field(default_factory=lambda: ["USDT"])   # stablecoins, used when auto_all is off
    keep_liquid_usd: float = 200                 # always leave this much in spot
    min_move_usd: float = 20                     # ignore smaller subscriptions/redemptions

    @field_validator("assets")
    @classmethod
    def _assets(cls, v):
        return sorted({a.upper().strip() for a in v if a.strip()})


class Questions(BaseModel):
    """The AI may ask the owner before a high-risk or preference-dependent decision (2–4 options, buttons on
    the web dashboard and Telegram). An answer wakes a decision round at once; the chosen action still goes
    through the risk engine and approvals."""
    enabled: bool = True
    question_timeout_minutes: int = 120
    # skip: no answer in time = nothing is done about that item; auto: the AI's recommended option is taken
    on_timeout: Literal["skip", "auto"] = "skip"


# Telegram message types: key -> (label, can be muted). Event kinds map onto these in notify/policy.py.
# Approval requests, the AI's questions and critical risk alerts can never be muted or delayed.
NOTIFY_TYPES: dict[str, tuple[str, bool]] = {
    "approval": ("审批请求", False),
    "question": ("AI 提问", False),
    "critical": ("重大风险提醒（止损、强平、风控暂停、下单结果未知）", False),
    "trade": ("自动成交（现货与合约）", True),
    "rebalance": ("自动再平衡成交", True),
    "decision": ("不操作的决策", True),
    "earn": ("理财申购 / 赎回", True),
    "report": ("日报", True),
    "price_alert": ("价格提醒", True),
    "alerts": ("系统提醒（模型失败、下单失败、恢复运行、切换模式等）", True),
}
NOTIFY_DEFAULT_OFF = {"earn"}   # pushed only once the owner turns it on


def _hhmm(v: str) -> str:
    h, _, m = str(v).strip().partition(":")
    if not (h.isdigit() and m.isdigit() and int(h) < 24 and int(m) < 60):
        raise ValueError(f"时间格式应为 HH:MM：{v}")
    return f"{int(h):02d}:{int(m):02d}"


class QuietHours(BaseModel):
    """No muteable pushes in this window (settings timezone); what arrives is sent as one digest when it ends."""
    enabled: bool = False
    start: str = "23:00"
    end: str = "08:00"

    @field_validator("start", "end")
    @classmethod
    def _time(cls, v):
        return _hhmm(v)


class Notify(BaseModel):
    """Which Telegram messages are pushed and how often."""
    types: dict[str, bool] = Field(default_factory=lambda: {k: k not in NOTIFY_DEFAULT_OFF for k in NOTIFY_TYPES})
    # instant: push as it happens; digest: collect muteable messages and send one summary every digest_minutes
    mode: Literal["instant", "digest"] = "instant"
    digest_minutes: int = 60
    quiet: QuietHours = Field(default_factory=QuietHours)

    @field_validator("types")
    @classmethod
    def _types(cls, v):
        out = {k: k not in NOTIFY_DEFAULT_OFF for k in NOTIFY_TYPES}
        out.update({k: bool(x) for k, x in v.items() if k in NOTIFY_TYPES})
        out.update({k: True for k, (_l, mutable) in NOTIFY_TYPES.items() if not mutable})
        return out


class PaperSettings(BaseModel):
    initial_balances: dict[str, float] = {"USDT": 10000}
    slippage: float = 0.0005
    fee_rate: float = 0.001


class Settings(BaseModel):
    mode: Literal["paper", "live"] = "paper"
    quote: str = "USDT"
    stables: list[str] = ["USDT", "USDC", "FDUSD"]
    categories: dict[str, Category] = Field(default_factory=lambda: {
        "BTC": Category(tokens={"BTC": 0.60}, band=(0.50, 0.70)),
        "ETH": Category(tokens={"ETH": 0.05}, band=(0.03, 0.08)),
        "US_STOCK": Category(tokens={"QQQB": 0.07, "NVDAB": 0.03}, band=(0.05, 0.15)),
        "STABLE": Category(tokens={"USDT": 0.25}, band=(0.15, 0.35)),
    })
    limits: Limits = Field(default_factory=Limits)
    risk_preset: Literal["conservative", "balanced", "aggressive", "custom"] = "balanced"
    capital: Capital = Field(default_factory=Capital)
    allocation: Allocation = Field(default_factory=Allocation)
    schedule: Schedule = Field(default_factory=Schedule)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    provider_fallback: ProviderSettings | None = None     # used when the primary model fails
    paper: PaperSettings = Field(default_factory=PaperSettings)
    futures: Futures = Field(default_factory=Futures)
    earn: Earn = Field(default_factory=Earn)
    questions: Questions = Field(default_factory=Questions)
    notify: Notify = Field(default_factory=Notify)
    investor_notes: str = "长期投资，BTC 为主。只做现货，不承诺收益。"
    # Resolved by the service for "ai" mode over all assets / a blacklist (needs exchange data)
    _universe: list[str] | None = PrivateAttr(default=None)
    # Key permissions the owner knowingly keeps enabled; validated against risk.gate.WAIVABLE
    live_gate_allow: list[str] = Field(default_factory=list)

    @field_validator("live_gate_allow")
    @classmethod
    def _waivable_only(cls, v):
        from .risk.gate import WAIVABLE
        bad = set(v) - WAIVABLE
        if bad:
            raise ValueError(f"these key permissions can never be waived: {sorted(bad)}")
        return sorted(set(v))

    @field_validator("stables")
    @classmethod
    def _upper(cls, v):
        return [s.upper() for s in v]

    @model_validator(mode="after")
    def _clamp(self):
        for sect in (self.limits, self.schedule, self.allocation, self.futures, self.earn, self.notify, self.questions):
            for k, (lo, hi) in HARD_BOUNDS.items():
                if hasattr(sect, k):
                    val = getattr(sect, k)
                    clamped = min(max(val, lo), hi)
                    setattr(sect, k, type(val)(clamped))
        sch = self.schedule
        sch.max_gap_minutes = max(sch.max_gap_minutes, sch.min_gap_minutes)
        f = self.futures
        f.max_position_margin_usd = min(f.max_position_margin_usd, f.max_total_margin_usd)
        f.auto_open_max_margin_usd = min(f.auto_open_max_margin_usd, f.max_position_margin_usd)
        bad = set(self.earn.assets) - set(self.stables)
        if bad:
            raise ValueError(f"理财只支持稳定币：{sorted(bad)}")
        if self.limits.auto_execute_max_usd > self.limits.max_order_usd:
            self.limits.auto_execute_max_usd = self.limits.max_order_usd
        if "STABLE" not in self.categories:
            raise ValueError("categories must include STABLE")
        targets = sum(sum(c.tokens.values()) for c in self.categories.values())
        if targets > 1.0001:
            raise ValueError(f"target weights sum to {targets:.2f} > 1")
        return self

    # ---- derived helpers ----
    def tradable_tokens(self) -> list[str]:
        al = self.allocation
        enabled = [t for t in al.enabled if t not in self.stables and t != self.quote]
        if al.mode == "ai":
            if al.universe != "whitelist" and self._universe is not None:
                return [t for t in self._universe if t not in al.blocked and t not in self.stables and t != self.quote]
            # whitelist, or the market list is not available: the explicit list (minus the blacklist)
            return [t for t in enabled if al.universe == "whitelist" or t not in al.blocked]
        with_target = [t for name, c in self.categories.items() if name != "STABLE" for t in c.tokens]
        return [t for t in with_target if t in enabled]

    def group_of(self, token: str) -> str:
        """Risk grouping: category in "targets" mode, the token itself in "ai" mode."""
        if token in self.stables:
            return "STABLE"
        if self.allocation.mode == "ai":
            return token if token in self.tradable_tokens() else "OTHER"
        return self.category_of(token)

    def group_ceiling(self, group: str) -> float:
        if self.allocation.mode == "ai":
            return 1.0 if group == "STABLE" else self.allocation.max_asset_weight
        return self.categories[group].band[1] if group in self.categories else 0.0

    def group_targets(self) -> dict[str, dict]:
        """Display/prompt view of targets for the current mode: {group: {tokens, band}}."""
        if self.allocation.mode == "ai":
            out = {t: {"tokens": {t: None}, "band": [0.0, self.allocation.max_asset_weight]} for t in self.tradable_tokens()}
            out["STABLE"] = {"tokens": {self.quote: None}, "band": [self.limits.stable_floor, 1.0]}
            return out
        out = {}
        for n, c in self.categories.items():
            toks = {t: w for t, w in c.tokens.items() if n == "STABLE" or t in self.tradable_tokens()}
            if toks:
                out[n] = {"tokens": toks, "band": list(c.band)}
        return out

    def symbols(self) -> list[str]:
        return [f"{t}{self.quote}" for t in self.tradable_tokens()]

    def category_of(self, token: str) -> str:
        for name, c in self.categories.items():
            if token in c.tokens:
                return name
        return "STABLE" if token in self.stables else "OTHER"


class Secrets(BaseModel):
    binance_api_key: str = ""
    binance_api_secret: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    openai_api_key: str = ""
    openai_compat_api_key: str = ""
    anthropic_api_key: str = ""
    claude_code_oauth_token: str = ""
    admin_password_hash: str = ""
    session_secret: str = ""

    @classmethod
    def from_env(cls) -> "Secrets":
        return cls(**{f: os.environ.get(f.upper(), "") for f in cls.model_fields})

    def configured(self) -> dict[str, bool]:
        """Safe to expose: only whether each secret is set."""
        return {k: bool(v) for k, v in self.model_dump().items()}


# Settings whose change needs a second confirmation. The web UI and Telegram show the changes and
# ask again; the server refuses the write unless it carries a token for exactly those changes.
DANGEROUS: dict[str, str] = {
    "limits.max_order_usd": "单笔上限",
    "limits.auto_execute_max_usd": "单笔自动执行",
    "limits.max_auto_daily_usd": "每日自动执行",
    "limits.max_orders_per_day": "每日笔数",
    "limits.max_daily_notional_usd": "每日金额",
    "limits.stable_floor": "稳定币下限",
    "limits.btc_7d_drop_pause": "BTC 7 日暂停线",
    "limits.allow_sell": "允许卖出",
    "limits.anomaly_failures_to_pause": "连续异常暂停次数",
    "limits.price_deviation_anomaly": "价格异常偏离",
    "limits.fee_buffer_ratio": "手续费缓冲",
    "categories": "目标配置与区间",
    "capital.locked": "锁定资产",
    "capital.budget_usd": "AI 投入预算",
    "allocation.mode": "分配方式",
    "allocation.universe": "AI 可交易范围",
    "allocation.enabled": "可交易资产 / 白名单",
    "allocation.blocked": "黑名单",
    "allocation.auto_rebalance": "自动再平衡",
    "allocation.rebalance_drift": "再平衡偏离阈值",
    "allocation.max_asset_weight": "单币占比上限",
    "futures.enabled": "合约交易",
    "futures.max_leverage": "最大杠杆",
    "futures.max_position_margin_usd": "单仓保证金上限",
    "futures.max_total_margin_usd": "总保证金上限",
    "futures.max_loss_per_trade_usd": "单笔止损最大亏损",
    "futures.max_stop_distance": "止损最远距离",
    "futures.allow_short": "允许做空",
    "futures.auto_open": "小额自动开仓",
    "futures.auto_open_max_margin_usd": "自动开仓保证金上限",
    "futures.max_opens_per_day": "每日开仓次数",
    "earn.enabled": "理财自动申购",
    "earn.auto_all": "申购全部可申购的活期产品",
    "earn.assets": "理财稳定币",
    "earn.keep_liquid_usd": "现货保留",
    "questions.on_timeout": "AI 提问超时后的处理",
    "quote": "计价币",
    "stables": "稳定币列表",
    "live_gate_allow": "保留的 Key 权限",
}


def dangerous_changes(old: Settings, new: Settings) -> list[dict]:
    """[{field, label, old, new}] for every dangerous setting that differs between the two."""
    a, b = old.model_dump(mode="json"), new.model_dump(mode="json")
    out = []
    for path, label in DANGEROUS.items():
        x, y = a, b
        for k in path.split("."):
            x, y = x.get(k), y.get(k)
        if x != y:
            out.append({"field": path, "label": label, "old": x, "new": y})
    return out


# Settings the AI reads (or the risk engine applies to its plans) when it decides. After a person changes
# one, they are offered a fresh manual round so the AI adapts now instead of at its next check-in.
DECISION_FIELDS: dict[str, str] = {
    "categories": "目标配置",
    "investor_notes": "投资备注",
    "allocation": "资产分配",
    "capital": "资金与预算",
    "limits": "风控限额",
    "futures": "合约",
    "earn": "理财",
    "stables": "稳定币列表",
    "provider": "AI 模型",
    "questions": "AI 提问",
}


def decision_changes(old: Settings, new: Settings) -> list[str]:
    """Labels of the decision-relevant setting groups that differ between the two."""
    a, b = old.model_dump(mode="json"), new.model_dump(mode="json")
    return [label for k, label in DECISION_FIELDS.items() if a.get(k) != b.get(k)]


class ConfirmationRequired(Exception):
    """A settings change touches dangerous fields and was sent without a valid confirmation token."""

    def __init__(self, changes: list[dict], token: str):
        super().__init__("修改危险设置需要二次确认：" + "、".join(c["label"] for c in changes))
        self.changes = changes
        self.token = token


class SettingsStore:
    """Thread-safe load/save of settings.json."""

    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "settings.json"
        self._lock = threading.Lock()
        self._settings = self._load()

    def _load(self) -> Settings:
        if self.path.exists():
            return Settings.model_validate(json.loads(self.path.read_text()))
        s = Settings()
        self._write(s)
        return s

    def _write(self, s: Settings):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(s.model_dump_json(indent=2))
        tmp.replace(self.path)

    def get(self) -> Settings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def preview(self, patch: dict, *, allow_mode_change: bool = False) -> Settings:
        """The validated result of ``update(patch)``, without saving it."""
        with self._lock:
            return self._merged(patch, allow_mode_change)

    def _merged(self, patch: dict, allow_mode_change: bool) -> Settings:
        data = self._settings.model_dump()
        if "mode" in patch and not allow_mode_change:
            patch = {k: v for k, v in patch.items() if k != "mode"}
        _deep_merge(data, patch)
        return Settings.model_validate(data)

    def update(self, patch: dict, *, allow_mode_change: bool = False) -> Settings:
        """Merge a partial dict. Mode changes go through the live gate, never through here."""
        with self._lock:
            new = self._merged(patch, allow_mode_change)
            self._write(new)
            self._settings = new
            return new.model_copy(deep=True)


def _deep_merge(dst: dict, src: dict):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict) and k not in ("categories", "locked", "tokens"):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
