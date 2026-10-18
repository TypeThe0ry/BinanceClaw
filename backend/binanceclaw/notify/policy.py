"""Which Telegram messages go out, and when.

Every event kind maps to a message type from ``config.NOTIFY_TYPES``. The owner can mute types,
collect them into a digest every N minutes, or hold them during quiet hours (sent as one digest
when the window ends). Approval requests, the AI's questions and critical risk alerts always go
out at once. Event kinds with no type pass straight through, so a new kind is never silenced by accident.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from ..config import NOTIFY_TYPES, Notify

ALWAYS = frozenset(k for k, (_label, mutable) in NOTIFY_TYPES.items() if not mutable)
CRITICAL_FUTURES = ("STOP_HIT", "LIQUIDATED", "STOP_FAILED")

# Fixed kind -> type. Kinds that depend on their payload are handled in classify().
EVENT_TYPES: dict[str, str] = {
    "approval_created": "approval",
    "approval": "approval",
    "allocation": "approval",          # a target-allocation proposal waiting for 应用 / 放弃
    "question_created": "question",
    "question": "question",
    "paused": "critical",
    "resumed": "alerts",
    "mode": "alerts",
    "price_alert": "price_alert",
    "report": "report",
    "earn": "earn",
    "cycle": "decision",
}


def register(kind: str, type_key: str):
    """Let a feature route its own event kind to a message type."""
    if type_key not in NOTIFY_TYPES:
        raise ValueError(f"unknown notify type {type_key}")
    EVENT_TYPES[kind] = type_key


def classify(kind: str, d: dict) -> str | None:
    if kind == "order":
        if d.get("status") == "failed":
            return "alerts"
        return "rebalance" if d.get("source") == "rebalance" else "trade"
    if kind == "futures":
        if d.get("op") in CRITICAL_FUTURES:
            return "critical"
        return "trade" if d.get("ok") else "alerts"
    if kind == "alert":
        return "critical" if d.get("level") == "critical" else "alerts"
    return EVENT_TYPES.get(kind)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_quiet(n: Notify, now: dt.datetime) -> bool:
    q = n.quiet
    if not q.enabled:
        return False
    a, b, t = _minutes(q.start), _minutes(q.end), now.hour * 60 + now.minute
    if a == b:
        return False
    return a <= t < b if a < b else (t >= a or t < b)


def route(n: Notify, type_key: str | None, now: dt.datetime) -> Literal["send", "hold", "drop"]:
    if type_key is None or type_key in ALWAYS:
        return "send"
    if not n.types.get(type_key, True):
        return "drop"
    if in_quiet(n, now) or n.mode == "digest":
        return "hold"
    return "send"


def digest_due(n: Notify, now: dt.datetime, oldest: float | None) -> bool:
    """Send the held messages now? ``oldest`` is when the first of them was held. Never during quiet hours;
    in digest mode once the oldest has waited digest_minutes; in instant mode as soon as quiet hours end."""
    if oldest is None or in_quiet(n, now):
        return False
    if n.mode == "instant":
        return True
    return now.timestamp() - oldest >= n.digest_minutes * 60
