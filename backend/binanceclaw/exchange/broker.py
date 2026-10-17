"""Broker = market data (always real Binance) + an account that is either the simulated
paper ledger or the live Binance account. The rest of the app only talks to this."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from sqlmodel import select

from ..db import Database, PaperBalance
from .binance_spot import BinanceSpot, ExchangeError, SymbolRules

# Binance error codes that mean "we don't know whether the order went through".
AMBIGUOUS_CODES = {None, -1000, -1001, -1006, -1007}
NOT_FOUND = -2013


class OrderUnknown(ExchangeError):
    """The order request failed ambiguously and the order could not be found afterwards.
    It may still exist; it must be treated as live until reconciled."""


@dataclass
class Fill:
    status: str              # filled | submitted
    qty: float
    avg_price: float
    quote_spent: float
    fee: float
    fee_asset: str
    raw: dict = field(default_factory=dict)


def floor_step(x: float, step: float) -> float:
    if step <= 0:
        return x
    return float(f"{math.floor(x / step + 1e-9) * step:.12f}")


class Broker:
    def __init__(self, client: BinanceSpot, db: Database, mode: str, paper_cfg):
        self.client = client
        self.db = db
        self.mode = mode
        self.paper_cfg = paper_cfg
        self._rules: dict[str, SymbolRules] = {}
        self._rules_ts: dict[str, float] = {}

    # ---- market data ----
    RULES_TTL = 600     # trading status changes (a pair halted or resumed) must be picked up without a restart

    def rules(self, symbols: list[str]) -> dict[str, SymbolRules]:
        now = time.time()
        missing = [s for s in symbols if s not in self._rules or now - self._rules_ts.get(s, 0) > self.RULES_TTL]
        if missing:
            try:
                fresh = self.client.symbol_rules(missing)
            except ExchangeError:
                if any(s not in self._rules for s in missing):
                    raise
                fresh = {}                       # refresh failed: keep the last known rules
            self._rules.update(fresh)
            self._rules_ts.update({s: now for s in fresh})
        return {s: self._rules[s] for s in symbols if s in self._rules}

    # ---- account ----
    def balances(self) -> dict[str, float]:
        if self.mode == "live":
            return self.client.balances()
        rows = self.db.all(select(PaperBalance))
        if not rows and not self.db.kv_get("paper_initialized"):
            for asset, amt in self.paper_cfg.initial_balances.items():
                self.db.add(PaperBalance(asset=asset, free=float(amt)))
            self.db.kv_set("paper_initialized", True)
            rows = self.db.all(select(PaperBalance))
        return {r.asset: r.free for r in rows if r.free > 1e-12 and ":" not in r.asset}   # EARN:* lives in core.earn

    def reset_paper(self):
        with self.db.write_lock, self.db.session() as s:
            for r in s.exec(select(PaperBalance)):
                s.delete(r)
            s.commit()
        self.db.kv_set("paper_initialized", False)

    def _paper_apply(self, deltas: dict[str, float]):
        with self.db.write_lock, self.db.session() as s:
            for asset, d in deltas.items():
                row = s.get(PaperBalance, asset) or PaperBalance(asset=asset, free=0)
                row.free = row.free + d
                if row.free < -1e-9:
                    raise ExchangeError(f"paper balance for {asset} would go negative")
                s.add(row)
            s.commit()

    # ---- live order helpers ----
    def _place_live(self, place, symbol: str, cid: str) -> dict:
        """Place an order; on an ambiguous failure look it up by client id instead of assuming it failed."""
        try:
            return place()
        except ExchangeError as e:
            if e.code not in AMBIGUOUS_CODES:
                raise                                   # definite rejection (e.g. insufficient balance)
            first = e
        for wait in (0.5, 1.5, 3.0):
            time.sleep(wait)
            try:
                return self.client.order_status(symbol, cid)
            except ExchangeError as e:
                if e.code != NOT_FOUND and e.code not in AMBIGUOUS_CODES:
                    break
        raise OrderUnknown(f"下单结果未知（{first}），已按订单号查询 3 次未找到，稍后自动对账", None)

    @staticmethod
    def parse_live(r: dict, base: str) -> Fill:
        qty = float(r.get("executedQty", 0) or 0)
        spent = float(r.get("cummulativeQuoteQty", 0) or 0)
        fills = r.get("fills") or []
        fee = sum(float(f["commission"]) for f in fills)
        fee_asset = fills[0].get("commissionAsset", base) if fills else base
        st = r.get("status", "")
        status = {"FILLED": "filled", "CANCELED": "canceled", "EXPIRED": "canceled", "REJECTED": "failed"}.get(st, "submitted")
        if status == "canceled" and qty > 0:
            status = "filled"                            # partially filled then expired: keep what filled
        return Fill(status, qty, spent / qty if qty else 0, spent, fee, fee_asset, r)

    # ---- trading ----
    def buy(self, symbol: str, quote_usd: float, client_order_id: str) -> Fill:
        rule = self.rules([symbol]).get(symbol)
        if rule is None or rule.status != "TRADING":
            raise ExchangeError(f"{symbol} is not tradable")
        if quote_usd < rule.min_notional:
            raise ExchangeError(f"{symbol}: ${quote_usd:.2f} below exchange minimum ${rule.min_notional}")
        if self.mode == "live":
            r = self._place_live(lambda: self.client.market_buy_quote(symbol, quote_usd, client_order_id),
                                 symbol, client_order_id)
            return self.parse_live(r, rule.base)
        # paper: fill at best ask + slippage, fee charged in base asset (like Binance without BNB)
        _bid, ask = self.client.book([symbol])[symbol]
        price = ask * (1 + self.paper_cfg.slippage)
        qty = floor_step(quote_usd / price, rule.step_size)
        if qty < rule.min_qty or qty <= 0:
            raise ExchangeError(f"{symbol}: quantity {qty} below minimum {rule.min_qty}")
        spent = round(qty * price, 8)
        fee = qty * self.paper_cfg.fee_rate
        self._paper_apply({rule.quote: -spent, rule.base: qty - fee})
        return Fill("filled", qty - fee, price, spent, fee, rule.base,
                    {"paper": True, "ask": ask, "gross_qty": qty})

    def sell(self, symbol: str, qty: float, client_order_id: str) -> Fill:
        rule = self.rules([symbol])[symbol]
        qty = floor_step(qty, rule.step_size)
        if self.mode == "live":
            r = self._place_live(lambda: self.client.market_sell_qty(symbol, f"{qty:.8f}".rstrip("0").rstrip("."), client_order_id),
                                 symbol, client_order_id)
            f = self.parse_live(r, rule.quote)
            return Fill(f.status, f.qty, f.avg_price, -f.quote_spent, f.fee, f.fee_asset, r)
        bid, _ask = self.client.book([symbol])[symbol]
        price = bid * (1 - self.paper_cfg.slippage)
        got = qty * price
        fee = got * self.paper_cfg.fee_rate
        self._paper_apply({rule.base: -qty, rule.quote: got - fee})
        return Fill("filled", qty, price, -(got - fee), fee, rule.quote, {"paper": True, "bid": bid})
