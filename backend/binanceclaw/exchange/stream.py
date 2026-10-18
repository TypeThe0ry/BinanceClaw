"""Real-time prices from Binance's public market-data websocket (one miniTicker stream per pair).
Read-only and keyless. Callers fall back to REST whenever a price is missing or stale."""
from __future__ import annotations

import json
import logging
import threading
import time

WS_BASE = "wss://stream.binance.com:9443/stream?streams="

log = logging.getLogger("binanceclaw")


class PriceStream:
    def __init__(self, base: str = WS_BASE, stale_after: float = 15):
        self.base = base
        self.stale_after = stale_after
        self._px: dict[str, tuple[float, float]] = {}    # symbol -> (price, received at)
        self._symbols: frozenset[str] = frozenset()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False

    def watch(self, symbols) -> None:
        """Set the pairs to follow; the connection is reopened when the set changes."""
        with self._lock:
            self._symbols = frozenset(s.upper() for s in symbols)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="price-stream", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def on_message(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
            d = msg.get("data", msg)
            sym, px = d["s"], float(d["c"])
        except (ValueError, KeyError, TypeError):
            return
        if px > 0:
            with self._lock:
                self._px[sym] = (px, time.time())

    def prices(self, symbols) -> dict[str, float]:
        """Fresh prices only; anything older than ``stale_after`` seconds is left out."""
        now = time.time()
        with self._lock:
            got = {s: self._px.get(s) for s in symbols}
        return {s: v[0] for s, v in got.items() if v and now - v[1] <= self.stale_after}

    def _run(self) -> None:
        from websockets.sync.client import connect

        backoff = 1.0
        while not self._stop.is_set():
            syms = self._symbols
            if not syms:
                time.sleep(1)
                continue
            url = self.base + "/".join(f"{s.lower()}@miniTicker" for s in sorted(syms))
            try:
                with connect(url, open_timeout=10, close_timeout=2, max_size=2**20) as ws:
                    self.connected, backoff = True, 1.0
                    while not self._stop.is_set() and self._symbols == syms:
                        try:
                            self.on_message(ws.recv(timeout=5))
                        except TimeoutError:
                            continue
            except Exception as e:  # network down, Binance's 24 h disconnect, proxy refusal ...
                log.info("price stream disconnected: %s", e)
            finally:
                self.connected = False
            if self._symbols == syms:           # a symbol change reconnects at once; errors back off
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
