import json
import time

import pytest

from binanceclaw.brain.providers import Completion, Provider, ProviderError
from binanceclaw.config import Secrets
from binanceclaw.exchange.binance_spot import SymbolRules

PRICES = {"BTCUSDT": 80000.0, "ETHUSDT": 2500.0, "QQQBUSDT": 700.0, "NVDABUSDT": 200.0, "SOLUSDT": 150.0, "TONUSDT": 1.6}
HALTED = {"TONUSDT": "BREAK"}          # listed but suspended, like TON on Binance in October 2026

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
        self.orders = {}            # client id -> order response (what the exchange "knows")
        self.place_error = None      # ExchangeError raised by the next order placement
        self.place_error_after_fill = False   # order is created on the exchange, then the response is lost

    def _chk(self):
        from binanceclaw.exchange.binance_spot import ExchangeError
        if self.fail:
            raise ExchangeError("network down")

    def prices(self, symbols):
        self._chk()
        return {s: self.prices_map[s] for s in symbols if s in self.prices_map}

    def spot_markets(self):
        return ([{"symbol": s, "base": s[:-4], "quote": "USDT", "status": HALTED.get(s, "TRADING")} for s in PRICES]
                + [{"symbol": "XYZBTC", "base": "XYZ", "quote": "BTC", "status": "TRADING"}])

    def all_tickers_24h(self):
        return {s: {"price": p, "change_pct": 0.01, "quote_volume": 1e6 * (len(s))} for s, p in self.prices_map.items()}

    def asset_tags(self):
        self._chk()
        return {"BTC": ["Payments", "pow"], "ETH": ["Layer1_Layer2", "pos"], "SOL": ["Layer1_Layer2", "Solana", "pos"],
                "TON": ["Layer1_Layer2"], "XYZ": ["Meme"]}

    def all_prices(self):
        return dict(self.prices_map)

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
            if s not in PRICES:
                continue                      # unknown pair, like Binance's exchangeInfo would omit it
            base = s[:-4]
            out[s] = SymbolRules(s, base, "USDT", HALTED.get(s, "TRADING"), 1e-5 if base == "BTC" else 1e-3, 1e-5, 5.0, 0.01, 8)
        return out

    def api_restrictions(self):
        self._chk()
        return dict(self.restrictions)

    def balances(self):
        return {"USDT": 1000.0}

    def market_buy_quote(self, symbol, quote_usd, client_order_id):
        p = self.prices_map[symbol]
        q = quote_usd / p
        resp = {"status": "FILLED", "executedQty": str(q), "cummulativeQuoteQty": str(quote_usd),
                "fills": [{"commission": str(q * 0.001), "commissionAsset": symbol[:-4]}]}
        if self.place_error is not None:
            err, self.place_error = self.place_error, None
            if self.place_error_after_fill:
                self.orders[client_order_id] = {k: v for k, v in resp.items() if k != "fills"}
                self.live_orders.append((symbol, quote_usd, client_order_id))
            raise err
        self.live_orders.append((symbol, quote_usd, client_order_id))
        self.orders[client_order_id] = {k: v for k, v in resp.items() if k != "fills"}
        return resp

    transfers = None
    earn = None

    def transfer(self, direction, asset, amount):
        self.transfers = (self.transfers or []) + [(direction, asset, amount)]
        return {"tranId": 1}

    def earn_products(self, asset):
        return [{"asset": asset, "productId": f"{asset}001", "canPurchase": True, "minPurchaseAmount": "0.1",
                 "latestAnnualPercentageRate": "0.05", "status": "PURCHASING"}]

    def earn_positions(self):
        return [{"asset": a, "totalAmount": str(v), "productId": f"{a}001", "latestAnnualPercentageRate": "0.05"}
                for a, v in (self.earn or {}).items() if v]

    def earn_subscribe(self, pid, amount):
        self.earn = self.earn or {}
        self.earn[pid[:-3]] = self.earn.get(pid[:-3], 0) + amount
        return {"success": True}

    def earn_redeem(self, pid, amount):
        self.earn[pid[:-3]] -= amount
        return {"success": True}

    def order_status(self, symbol, client_order_id):
        from binanceclaw.exchange.binance_spot import ExchangeError
        if client_order_id not in self.orders:
            raise ExchangeError("Order does not exist.", -2013)
        return self.orders[client_order_id]


class FakeFutures:
    """Offline stand-in for BinanceFutures (market data + a minimal live account)."""

    def __init__(self):
        from binanceclaw.exchange.binance_futures import PerpRules
        self.marks_map = {"BTCUSDT": 80000.0, "ETHUSDT": 2500.0, "SOLUSDT": 150.0}
        self._rules = {s: PerpRules(s, s[:-4], "TRADING", 0.001 if s == "BTCUSDT" else 0.01, 0.001, 1e6, 5.0, 0.1)
                       for s in self.marks_map}
        self.calls = []
        self.dual = False
        self.protect_error = None
        self.pos = {}               # symbol -> signed amount (live simulation)
        self.algos = {}             # symbol -> list of algo orders
        self.available = 0.0
        self.hpos = {}              # hedge mode: (symbol, LONG|SHORT) -> signed amount

    def perp_rules(self):
        return dict(self._rules)

    def premium(self):
        return {s: {"mark": m, "index": m, "funding_rate": 0.0001, "next_funding": time.time() + 3600}
                for s, m in self.marks_map.items()}

    def tickers_24h(self):
        return {s: {"price": m, "change_pct": 0.0, "quote_volume": 1e9} for s, m in self.marks_map.items()}

    def klines(self, symbol, interval="1h", limit=30):
        p = self.marks_map[symbol]
        now = time.time()
        return [{"t": now - (limit - i) * 3600, "o": p, "h": p, "l": p, "c": p, "v": 1, "close_t": now} for i in range(limit)]

    def account(self):
        return {"availableBalance": str(self.available)}

    def positions(self):
        if self.dual:
            return [{"symbol": s, "positionSide": ps, "positionAmt": str(a), "entryPrice": str(self.marks_map[s]),
                     "isolatedWallet": "0"} for (s, ps), a in self.hpos.items() if a]
        return [{"symbol": s, "positionSide": "BOTH", "positionAmt": str(a), "entryPrice": str(self.marks_map[s]),
                 "isolatedWallet": "0"} for s, a in self.pos.items() if a]

    def dual_side(self):
        return self.dual

    def set_margin_type(self, symbol, mt="ISOLATED"):
        self.calls.append(("marginType", symbol, mt))

    def set_leverage(self, symbol, lev):
        self.calls.append(("leverage", symbol, lev))
        return {"leverage": lev}

    def market_order(self, symbol, side, qty, cid, reduce_only=False, position_side=None):
        assert not (self.dual and not position_side), "hedge mode needs positionSide"
        assert not (not self.dual and position_side), "one-way mode must not send positionSide"
        self.calls.append(("order", symbol, side, qty, reduce_only, position_side))
        d = qty if side == "BUY" else -qty
        if position_side:
            self.hpos[(symbol, position_side)] = self.hpos.get((symbol, position_side), 0) + d
        else:
            self.pos[symbol] = self.pos.get(symbol, 0) + d
        return {"status": "FILLED", "executedQty": str(qty), "avgPrice": str(self.marks_map[symbol]), "clientOrderId": cid}

    def order_status(self, symbol, cid):
        from binanceclaw.exchange.binance_spot import ExchangeError
        raise ExchangeError("Order does not exist.", -2013)

    def protect(self, symbol, side, kind, trigger, cid, position_side=None):
        from binanceclaw.exchange.binance_spot import ExchangeError
        if self.protect_error and kind == "STOP_MARKET":
            raise ExchangeError(self.protect_error)
        self.calls.append(("protect", symbol, side, kind, trigger, position_side))
        algo = {"algoId": len(self.calls), "orderType": kind, "triggerPrice": str(trigger),
                "positionSide": position_side or "BOTH"}
        self.algos.setdefault(symbol, []).append(algo)
        return algo

    def open_algo_orders(self, symbol=None):
        return list(self.algos.get(symbol, []))

    def cancel_algo(self, symbol, algo_id):
        self.algos[symbol] = [a for a in self.algos.get(symbol, []) if str(a["algoId"]) != str(algo_id)]
        return {}

    def cancel_all_algo(self, symbol):
        self.algos[symbol] = []
        return {}

    def user_trades(self, symbol, start_ms):
        return []


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


def plan(*actions, summary="test", futures=None):
    out = {"summary": summary, "market_view": "", "confidence": 0.6,
           "actions": [{"side": a[0], "symbol": a[1], "quote_usd": a[2], "reason": "r", "invalidation": "i"} for a in actions]}
    if futures:
        out["futures"] = [{"reason": "r", "invalidation": "i", **f} for f in futures]
    return out


@pytest.fixture
def svc(tmp_path):
    from binanceclaw.core.service import Service
    s = Service(data_dir=tmp_path, secrets=Secrets(binance_api_key="k", binance_api_secret="s"), client=FakeSpot(),
                fclient=FakeFutures())
    # most tests exercise the model's own plan; tests/test_universe.py covers rebalancing
    s.settings_store.update({"allocation": {"auto_rebalance": False}})
    return s


def use(svc, *replies):
    p = FakeProvider(replies)
    svc.provider = p
    return p
