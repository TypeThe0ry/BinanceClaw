"""Broker = market data (always real Binance) + an account that is either the simulated
paper ledger or the live Binance account. The rest of the app only talks to this."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from sqlmodel import select

from ..db import Database, PaperBalance
from .binance_spot import BinanceSpot, ExchangeError, SymbolRules


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

    # ---- market data ----
    def rules(self, symbols: list[str]) -> dict[str, SymbolRules]:
        missing = [s for s in symbols if s not in self._rules]
        if missing:
            self._rules.update(self.client.symbol_rules(missing))
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
        return {r.asset: r.free for r in rows if r.free > 1e-12}

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

    # ---- trading ----
    def buy(self, symbol: str, quote_usd: float, client_order_id: str) -> Fill:
        rule = self.rules([symbol]).get(symbol)
        if rule is None or rule.status != "TRADING":
            raise ExchangeError(f"{symbol} is not tradable")
        if quote_usd < rule.min_notional:
            raise ExchangeError(f"{symbol}: ${quote_usd:.2f} below exchange minimum ${rule.min_notional}")
        if self.mode == "live":
            r = self.client.market_buy_quote(symbol, quote_usd, client_order_id)
            qty = float(r.get("executedQty", 0))
            spent = float(r.get("cummulativeQuoteQty", 0))
            fee = sum(float(f["commission"]) for f in r.get("fills", []))
            fee_asset = (r.get("fills") or [{}])[0].get("commissionAsset", rule.base)
            status = "filled" if r.get("status") == "FILLED" else "submitted"
            return Fill(status, qty, spent / qty if qty else 0, spent, fee, fee_asset, r)
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
            r = self.client.market_sell_qty(symbol, f"{qty:.8f}".rstrip("0").rstrip("."), client_order_id)
            got = float(r.get("cummulativeQuoteQty", 0))
            fee = sum(float(f["commission"]) for f in r.get("fills", []))
            return Fill("filled" if r.get("status") == "FILLED" else "submitted", qty,
                        got / qty if qty else 0, -got, fee, rule.quote, r)
        bid, _ask = self.client.book([symbol])[symbol]
        price = bid * (1 - self.paper_cfg.slippage)
        got = qty * price
        fee = got * self.paper_cfg.fee_rate
        self._paper_apply({rule.base: -qty, rule.quote: got - fee})
        return Fill("filled", qty, price, -(got - fee), fee, rule.quote, {"paper": True, "bid": bid})
