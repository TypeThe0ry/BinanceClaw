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

from pydantic import BaseModel, Field, field_validator, model_validator

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
    "approval_timeout_minutes": (5, 720),
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
    cycle_minutes: int = 60
    approval_timeout_minutes: int = 30
    daily_report_time: str = "08:00"
    timezone: str = "UTC"


class ProviderSettings(BaseModel):
    name: Literal["claude_code", "openai", "openai_compat", "anthropic_api"] = "claude_code"
    model: str = ""                    # empty = provider default
    base_url: str = ""                 # only for openai_compat
    timeout_seconds: int = 240
    temperature: float = 0.2


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
    schedule: Schedule = Field(default_factory=Schedule)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    paper: PaperSettings = Field(default_factory=PaperSettings)
    investor_notes: str = "长期投资，BTC 为主。只做现货，不承诺收益。"
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
        for sect in (self.limits, self.schedule):
            for k, (lo, hi) in HARD_BOUNDS.items():
                if hasattr(sect, k):
                    val = getattr(sect, k)
                    clamped = min(max(val, lo), hi)
                    setattr(sect, k, type(val)(clamped))
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
        return [t for name, c in self.categories.items() if name != "STABLE" for t in c.tokens]

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

    def update(self, patch: dict, *, allow_mode_change: bool = False) -> Settings:
        """Merge a partial dict. Mode changes go through the live gate, never through here."""
        with self._lock:
            data = self._settings.model_dump()
            if "mode" in patch and not allow_mode_change:
                patch = {k: v for k, v in patch.items() if k != "mode"}
            _deep_merge(data, patch)
            new = Settings.model_validate(data)
            self._write(new)
            self._settings = new
            return new.model_copy(deep=True)


def _deep_merge(dst: dict, src: dict):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict) and k != "categories":
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
