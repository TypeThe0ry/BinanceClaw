"""Simple Earn flexible (redeemable at any time) for idle balances.

* sweep(): idle spot balances are subscribed to their flexible product. With ``auto_all`` (the
  default) that is every asset Binance offers a flexible product for; otherwise only the chosen
  stablecoins. Stablecoins keep ``keep_liquid_usd`` (plus what pending approvals may need) in spot;
  locked amounts are never moved;
* ensure_liquid(): right before a buy, a sell or a futures margin transfer, the shortfall is
  redeemed back to spot. ensure_quote() also converts other stablecoins (USDC -> USDT) when the
  quote asset alone is short. For the risk engine and the AI, balances in Earn count as available.

Paper mode keeps Earn balances in the paper ledger under ``EARN:<ASSET>`` (no interest accrues).
"""
from __future__ import annotations

import logging
import math
import time
import uuid

from sqlmodel import select

from ..db import PaperBalance
from ..exchange.binance_spot import ExchangeError

log = logging.getLogger("binanceclaw.earn")

PAPER_PREFIX = "EARN:"


class EarnManager:
    def __init__(self, svc):
        self.svc = svc
        self._pos: tuple[float, dict] | None = None
        self._products: dict[str, tuple[float, dict]] = {}

    @property
    def cfg(self):
        return self.svc.settings_store.get().earn

    @property
    def live(self) -> bool:
        return self.svc.settings_store.get().mode == "live"

    # ---------------------------------------------------------------- reading
    def positions(self, max_age: float = 60) -> dict[str, dict]:
        """asset -> {amount, apr, product_id}. Live: every flexible position on the account."""
        if self._pos and time.time() - self._pos[0] < max_age:
            return self._pos[1]
        out: dict[str, dict] = {}
        if self.live:
            try:
                for r in self.svc.client.earn_positions():
                    a = r["asset"]
                    out[a] = {"amount": out.get(a, {}).get("amount", 0.0) + float(r.get("totalAmount") or 0),
                              "apr": float(r.get("latestAnnualPercentageRate") or 0), "product_id": r.get("productId")}
            except (ExchangeError, AttributeError) as e:
                log.warning("earn positions unavailable: %s", e)
                return self._pos[1] if self._pos else {}
        else:
            for row in self.svc.db.all(select(PaperBalance)):
                if row.asset.startswith(PAPER_PREFIX) and row.free > 1e-9:
                    out[row.asset[len(PAPER_PREFIX):]] = {"amount": row.free, "apr": None, "product_id": "paper"}
        self._pos = (time.time(), out)
        return out

    def covers(self, asset: str) -> bool:
        """Whether BinanceClaw manages this asset's Earn balance (sweeps it, redeems it, counts it)."""
        return self.cfg.enabled and (self.cfg.auto_all or asset in self.cfg.assets)

    def managed_amounts(self) -> dict[str, float]:
        """Earn balances the AI may count as available (only when Earn is enabled)."""
        if not self.cfg.enabled:
            return {}
        return {a: p["amount"] for a, p in self.positions().items() if self.covers(a) and p["amount"] > 0}

    def _decimals(self, asset: str) -> int:
        return 2 if asset in self.svc.settings_store.get().stables else 8

    def _fmt(self, asset: str, amount: float) -> str:
        return f"{amount:,.2f}" if self._decimals(asset) == 2 else f"{amount:.8f}".rstrip("0").rstrip(".")

    def product(self, asset: str) -> dict | None:
        cached = self._products.get(asset)
        if cached and time.time() - cached[0] < 3600:
            return cached[1]
        if not self.live:
            p = {"productId": "paper", "canPurchase": True, "canRedeem": True, "minPurchaseAmount": "0",
                 "latestAnnualPercentageRate": None}
        else:
            rows = self.svc.client.earn_products(asset)
            p = next((r for r in rows if r.get("asset") == asset and r.get("status", "PURCHASING") == "PURCHASING"),
                     rows[0] if rows else None)
        self._products[asset] = (time.time(), p)
        return p

    # ---------------------------------------------------------------- moving
    def _paper_move(self, asset: str, amount: float, to_earn: bool):
        self.svc.broker.balances()                    # initialises the paper ledger on first use
        with self.svc.db.write_lock, self.svc.db.session() as s:
            spot = s.get(PaperBalance, asset) or PaperBalance(asset=asset, free=0)
            earn = s.get(PaperBalance, PAPER_PREFIX + asset) or PaperBalance(asset=PAPER_PREFIX + asset, free=0)
            src, dst = (spot, earn) if to_earn else (earn, spot)
            if src.free + 1e-9 < amount:
                raise ExchangeError(f"模拟盘 {asset} 余额不足")
            src.free -= amount
            dst.free += amount
            s.add(spot)
            s.add(earn)
            s.commit()

    def subscribe(self, asset: str, amount: float, by: str = "system") -> str:
        q = 10 ** self._decimals(asset)
        amount = int(amount * q) / q
        p = self.product(asset)
        if not p or not p.get("canPurchase", True) or p.get("isSoldOut"):
            raise ExchangeError(f"{asset} 活期理财当前不可申购")
        if amount < float(p.get("minPurchaseAmount") or 0):
            raise ExchangeError(f"{asset} 申购金额低于最小值 {p.get('minPurchaseAmount')}")
        if self.live:
            self.svc.client.earn_subscribe(p["productId"], amount)
        else:
            self._paper_move(asset, amount, True)
        self._pos = None
        msg = f"{asset} {self._fmt(asset, amount)} 已申购活期理财"
        self.svc.events.emit("earn", {"asset": asset, "amount": amount, "dir": "subscribe", "text": msg, "by": by})
        return msg

    def redeem(self, asset: str, amount: float, by: str = "system") -> str:
        have = self.positions(0).get(asset, {})
        q = 10 ** self._decimals(asset)
        amount = min(math.ceil(amount * q - 1e-6) / q, have.get("amount", 0.0))
        if amount <= 0:
            raise ExchangeError(f"{asset} 没有可赎回的活期理财")
        if self.live:
            pid = have.get("product_id") or (self.product(asset) or {}).get("productId")
            self.svc.client.earn_redeem(pid, amount)
        else:
            self._paper_move(asset, amount, False)
        self._pos = None
        msg = f"{asset} {self._fmt(asset, amount)} 已从活期理财赎回"
        self.svc.events.emit("earn", {"asset": asset, "amount": amount, "dir": "redeem", "text": msg, "by": by})
        return msg

    def _spot_free(self, asset: str) -> float:
        raw = self.svc.broker.balances().get(asset, 0.0)
        return raw - self.svc.settings_store.get().capital.locked.get(asset, 0.0)

    def ensure_liquid(self, asset: str, need: float) -> None:
        """Redeem enough so that spot holds ``need`` of ``asset`` (beyond locked amounts)."""
        if not self.covers(asset):
            return
        short = need - self._spot_free(asset)
        if short <= 0:
            return
        have = self.positions(0).get(asset, {}).get("amount", 0)
        if have <= 0:
            return
        if asset in self.svc.settings_store.get().stables:
            amount = max(short * 1.002, min(self.cfg.min_move_usd, short + 1))
        else:
            amount = min(short * 1.002, have)     # non-stables: no USD-sized rounding, just the shortfall
        self.redeem(asset, amount)
        if self.live:
            for wait in (0.5, 1, 2, 3, 4):           # flexible redemptions normally land within seconds
                if self._spot_free(asset) >= need:
                    return
                time.sleep(wait)
            if self._spot_free(asset) < need:
                raise ExchangeError(f"{asset} 已提交赎回，但资金尚未到账现货，稍后重试")

    def ensure_quote(self, need: float) -> None:
        """Make spot hold ``need`` of the quote asset before a buy or a margin transfer: first redeem
        the quote from Earn, then convert other stablecoins (spot or Earn) through their 1:1 pair,
        e.g. USDC -> USDT. The risk engine counts every stablecoin as spendable, so this is what
        lets USDC fund a BTCUSDT buy."""
        s = self.svc.settings_store.get()
        q = s.quote
        if need - self._spot_free(q) <= 0:
            return
        in_earn = self.positions(0).get(q, {}).get("amount", 0.0) if self.covers(q) else 0.0
        if in_earn > 0:
            self.ensure_liquid(q, min(need, self._spot_free(q) + in_earn))
        short = need - self._spot_free(q)
        if short <= 0:
            return
        pos = self.positions(0)
        others = []
        for a in s.stables:
            if a == q:
                continue
            avail = max(self._spot_free(a), 0.0) + (pos.get(a, {}).get("amount", 0.0) if self.covers(a) else 0.0)
            if avail > 0.01:
                others.append((avail, a))
        for avail, a in sorted(others, reverse=True):
            if short <= 0:
                break
            self._convert(a, q, min(avail, max(short * 1.003 + 0.01, self.CONVERT_MIN_USD)))
            short = need - self._spot_free(q)
        if short > 0:
            raise ExchangeError(f"{q} 余额不足：还差 {short:,.2f}，其他稳定币也不够换")

    CONVERT_MIN_USD = 6        # Binance's minimum notional is $5; stay a little above it

    def _convert(self, src: str, quote: str, amount: float) -> None:
        """Turn ``amount`` of stablecoin ``src`` into ``quote`` with a market order on their pair."""
        broker = self.svc.broker
        pair_sell, pair_buy = f"{src}{quote}", f"{quote}{src}"
        rules = broker.rules([pair_sell, pair_buy])
        rule = rules.get(pair_sell) or rules.get(pair_buy)
        if rule is None or rule.status != "TRADING":
            raise ExchangeError(f"{src} 无法换成 {quote}：币安没有可交易的 {pair_sell} 交易对")
        self.ensure_liquid(src, amount)
        amount = min(amount, self._spot_free(src))
        cid = "bclawc" + uuid.uuid4().hex[:18]
        if rule.symbol == pair_sell:
            qty = math.ceil(amount / rule.step_size - 1e-9) * rule.step_size if rule.step_size > 0 else amount
            fill = broker.sell(pair_sell, min(qty, self._spot_free(src)), cid)
            got = -fill.quote_spent
        else:
            fill = broker.buy(pair_buy, math.floor(amount * 100) / 100, cid)
            got = fill.qty
        if fill.status != "filled":
            raise ExchangeError(f"{src} 换 {quote} 未成交（{fill.status}）")
        msg = f"{src} {amount:,.2f} 已换成 {quote} {got:,.2f}"
        log.info(msg)
        self.svc.events.emit("earn", {"asset": src, "amount": amount, "dir": "convert", "text": msg, "by": "system"})

    def candidates(self) -> list[str]:
        """Assets sweep() looks at: everything held in spot (auto_all), or the chosen stablecoins."""
        if not self.cfg.auto_all:
            return list(self.cfg.assets)
        return sorted(a for a in self.svc.spot_balances() if a.isalnum())

    def sweep(self) -> list[str]:
        """Subscribe idle balances (beyond the stablecoin buffer and pending needs) to flexible Earn."""
        if not self.cfg.enabled:
            return []
        s = self.svc.settings_store.get()
        pending = sum(a.quote_usd for a in self.svc.db.pending_approvals() if a.side in ("BUY", "OPEN_LONG", "OPEN_SHORT"))
        # approved limit buys still waiting for their price spend the quote the moment they trigger
        pending += sum(lo.quote_usd for lo in self.svc.open_limit_orders() if lo.side == "BUY")
        assets = self.candidates()
        prices = self.svc.asset_prices([a for a in assets if a not in s.stables])
        msgs = []
        for asset in assets:
            stable = asset in s.stables
            price = 1.0 if stable else prices.get(asset)
            if not price:
                continue                               # no USD price: can't tell whether it is worth moving
            reserve = (self.cfg.keep_liquid_usd + (pending if asset == s.quote else 0)) if stable else 0.0
            excess = self._spot_free(asset) - reserve / price
            if excess * price < self.cfg.min_move_usd:
                continue
            try:
                p = self.product(asset)
                if not stable and (not p or not p.get("canPurchase", True) or p.get("isSoldOut")):
                    continue                           # no subscribable flexible product: leave it in spot
                msgs.append(self.subscribe(asset, excess))
            except ExchangeError as e:
                log.warning("earn subscribe %s failed: %s", asset, e)
        return msgs
