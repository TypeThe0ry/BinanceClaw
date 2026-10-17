"""Tiny in-process pub/sub used to push live updates to the dashboard (SSE) and Telegram."""
from __future__ import annotations

import queue
import threading
import time


class EventBus:
    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._listeners = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def on(self, fn):
        """Register a synchronous listener fn(kind, data) (e.g. Telegram notifier)."""
        self._listeners.append(fn)

    def emit(self, kind: str, data: dict | None = None):
        evt = {"kind": kind, "ts": time.time(), "data": data or {}}
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(evt)
            except queue.Full:
                pass
        for fn in self._listeners:
            try:
                fn(kind, evt["data"])
            except Exception:  # a broken listener must never break trading logic
                pass
