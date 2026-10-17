import json
import time

import pytest

from binanceclaw.brain.providers import Completion, Provider, ProviderError
from binanceclaw.config import Secrets
from binanceclaw.exchange.binance_spot import SymbolRules

PRICES = {"BTCUSDT": 80000.0, "ETHUSDT": 2500.0, "QQQBUSDT": 700.0, "NVDABUSDT": 200.0}

SAFE_RESTRICTIONS = {
    "ipRestrict": True, "enableReading": True, "enableSpotAndMarginTrading": True,
    "enableWithdrawals": False, "enableInternalTransfer": False, "permitsUniversalTransfer": False,
    "enableVanillaOptions": False, "enableFutures": False, "enableMargin": False,
    "enablePortfolioMarginTrading": False,
}


class FakeSpot:
    """Offline stand-in for BinanceSpot."""

    def __init__(self):
        self.prices_map = dict(PRICES)
        self.restrictions = dict(SAFE_RESTRICTIONS)
        self.fail = False
        self.live_orders = []

    def _chk(self):
        from binanceclaw.exchange.binance_spot import ExchangeError
        if self.fail:
            raise ExchangeError("network down")

    def prices(self, symbols):
        self._chk()
        return {s: self.prices_map[s] for s in symbols if s in self.prices_map}

    def ticker_24h(self, symbols):
        self._chk()
        return {s: {"price": self.prices_map[s], "change_pct": 0.01, "high": 0, "low": 0, "quote_volume": 0} for s in symbols}

    def book(self, symbols):
        self._chk()
        return {s: (self.prices_map[s] * 0.9999, self.prices_map[s] * 1.0001) for s in symbols}

    def klines(self, symbol, interval="1d", limit=30):
        self._chk()
        p = PRICES[symbol]          # history stays at the base price; tests move only the live price
        now = time.time()
        return [{"t": now - (limit - i - 1) * 86400, "o": p, "h": p * 1.01, "l": p * 0.99, "c": p, "v": 1, "close_t": now}
                for i in range(limit)]

    def symbol_rules(self, symbols):
        out = {}
        for s in symbols:
            base = s[:-4]
            out[s] = SymbolRules(s, base, "USDT", "TRADING", 1e-5 if base == "BTC" else 1e-3, 1e-5, 5.0, 0.01, 8)
        return out

    def api_restrictions(self):
        self._chk()
        return dict(self.restrictions)

    def balances(self):
        return {"USDT": 1000.0}

    def market_buy_quote(self, symbol, quote_usd, client_order_id):
        self.live_orders.append((symbol, quote_usd, client_order_id))
        p = self.prices_map[symbol]
        q = quote_usd / p
        return {"status": "FILLED", "executedQty": str(q), "cummulativeQuoteQty": str(quote_usd),
                "fills": [{"commission": str(q * 0.001), "commissionAsset": symbol[:-4]}]}


class FakeProvider(Provider):
    name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.model = "fake-model"
        self.calls = []

    def complete(self, system, messages, json_mode=False):
        self.calls.append((system, messages, json_mode))
        if not self.replies:
            raise ProviderError("no more replies")
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return Completion(r if isinstance(r, str) else json.dumps(r), "fake-model", {})


def plan(*actions, summary="test"):
    return {"summary": summary, "market_view": "", "confidence": 0.6,
            "actions": [{"side": a[0], "symbol": a[1], "quote_usd": a[2], "reason": "r", "invalidation": "i"} for a in actions]}


@pytest.fixture
def svc(tmp_path):
    from binanceclaw.core.service import Service
    s = Service(data_dir=tmp_path, secrets=Secrets(binance_api_key="k", binance_api_secret="s"), client=FakeSpot())
    return s


def use(svc, *replies):
    p = FakeProvider(replies)
    svc.provider = p
    return p
