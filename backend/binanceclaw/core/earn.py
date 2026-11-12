"""Simple Earn flexible (redeemable at any time) for idle balances.

* sweep(): idle spot balances are subscribed to their flexible product. With ``auto_all`` (the
  default) that is every asset Binance offers a flexible product for; otherwise only the chosen
  stablecoins. Stablecoins keep ``keep_liquid_usd`` (plus what pending approvals may need) in spot;
  locked amounts are never moved;
* ensure_liquid(): right before a buy, a sell or a futures margin transfer, the shortfall is
  redeemed back to spot. ensure_stable() also converts between stablecoins (USDC <-> USDT ...)
  when the one the trade needs is short: through their 1:1 pair, or Binance Convert when there is none;
* prefund(): converts ahead for open limit buys and pending approvals.

For the risk engine and the AI, balances in Earn count as available.

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
from ..exchange.broker import OrderUnknown
from ..exchange.futures_broker import QUOTE as FUTURES_QUOTE

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
        """Make spot hold ``need`` of the quote asset before a buy. The risk engine counts every
        stablecoin as spendable, so this is what lets USDC fund a BTCUSDT buy (and USDT a BTCUSDC one)."""
        self.ensure_stable(self.svc.settings_store.get().quote, need)

    def ensure_stable(self, target: str, need: float) -> None:
        """Make spot hold ``need`` of stablecoin ``target``: first redeem it from Earn, then convert
        other stablecoins (spot or Earn), largest first, e.g. USDC -> USDT or USDT -> USDC."""
        if need - self._spot_free(target) <= 0:
            return
        in_earn = self.positions(0).get(target, {}).get("amount", 0.0) if self.covers(target) else 0.0
        if in_earn > 0:
            self.ensure_liquid(target, min(need, self._spot_free(target) + in_earn))
        short = need - self._spot_free(target)
        if short <= 0:
            return
        if not self.cfg.swap:
            raise ExchangeError(f"{target} 余额不足：还差 {short:,.2f}（稳定币自动互换已关闭）")
        left, tried = self._swap_into(target, short)
        if left > 0:
            why = f"（{'；'.join(tried)}）" if tried else ""
            raise ExchangeError(f"{target} 余额不足：还差 {left:,.2f}，其他稳定币也不够换{why}")

    def _swap_into(self, target: str, amount: float) -> tuple[float, list[str]]:
        """Convert other stablecoins until spot holds ``amount`` more ``target``.
        Returns (what is still missing, why each failed source failed)."""
        goal = self._spot_free(target) + amount
        short, tried = amount, []
        for avail, a in self._other_stables(target):
            if short <= 0:
                break
            try:
                self._convert(a, target, min(avail, max(short * 1.003 + 0.01, self.CONVERT_MIN_USD)))
            except OrderUnknown:
                raise
            except ExchangeError as e:
                log.warning("convert %s -> %s failed: %s", a, target, e)
                tried.append(f"{a}：{e}")
            short = goal - self._spot_free(target)
        return short, tried

    def _other_stables(self, target: str) -> list[tuple[float, str]]:
        """(available, asset) for every other stablecoin, spot + managed Earn, largest first."""
        s = self.svc.settings_store.get()
        pos = self.positions(0)
        out = []
        for a in s.stables:
            if a == target:
                continue
            avail = max(self._spot_free(a), 0.0) + (pos.get(a, {}).get("amount", 0.0) if self.covers(a) else 0.0)
            if avail > 0.01:
                out.append((avail, a))
        return sorted(out, reverse=True)

    def stable_needs(self) -> dict[str, float]:
        """Stablecoins that approved-but-waiting trades will spend: open limit buys and pending buy
        approvals need the quote, pending futures opens need USDT margin."""
        s = self.svc.settings_store.get()
        need: dict[str, float] = {}
        for a in self.svc.db.pending_approvals():
            if a.kind == "limit":
                continue
            if a.side == "BUY":
                need[s.quote] = need.get(s.quote, 0.0) + a.quote_usd
            elif a.side in ("OPEN_LONG", "OPEN_SHORT"):
                need[FUTURES_QUOTE] = need.get(FUTURES_QUOTE, 0.0) + a.quote_usd * 1.03
        for lo in self.svc.open_limit_orders():
            if lo.side == "BUY":
                need[s.quote] = need.get(s.quote, 0.0) + lo.quote_usd
        return need

    def prefund(self) -> list[str]:
        """Convert ahead of time, so a limit buy that triggers or an approval that is granted finds its
        stablecoin already there: what each stablecoin is short of (spot + Earn) comes from the others."""
        if not (self.cfg.swap and self.cfg.swap_prefund) or self.svc.paused()[0]:
            return []
        msgs = []
        with self.svc._exec_lock:
            for target, need in self.stable_needs().items():
                held = self._spot_free(target) + (self.positions(0).get(target, {}).get("amount", 0.0)
                                                  if self.covers(target) else 0.0)
                short = need - held
                if short < max(self.cfg.min_move_usd, self.CONVERT_MIN_USD):
                    continue
                try:
                    left, _ = self._swap_into(target, short)
                except ExchangeError as e:
                    log.warning("prefund %s failed: %s", target, e)
                    continue
                if left < short:
                    msgs.append(f"{target} 已提前补足 {short - max(left, 0):,.2f}")
        return msgs

    CONVERT_MIN_USD = 6        # Binance's minimum notional is $5; stay a little above it

    def _convert(self, src: str, target: str, amount: float) -> None:
        """Turn ``amount`` of stablecoin ``src`` into ``target``: a market order on their 1:1 pair
        (USDCUSDT ...), or Binance Convert when there is no tradable pair or the order is refused."""
        broker = self.svc.broker
        pair_sell, pair_buy = f"{src}{target}", f"{target}{src}"
        rules = broker.rules([pair_sell, pair_buy])
        rule = rules.get(pair_sell) or rules.get(pair_buy)
        self.ensure_liquid(src, amount)
        amount = min(amount, self._spot_free(src))
        if amount <= 0:
            raise ExchangeError(f"现货没有可用的 {src}")
        before = self._spot_free(target)
        how = "交易对"
        try:
            if rule is None or rule.status != "TRADING":
                raise ExchangeError(f"没有可交易的 {pair_sell} 交易对")
            self._convert_pair(rule, src, target, amount)
        except OrderUnknown:
            raise
        except ExchangeError as e:
            log.info("%s -> %s via pair failed (%s), trying Binance Convert", src, target, e)
            self._convert_api(src, target, amount)
            how = "闪兑"
        got = self._spot_free(target) - before
        msg = f"{src} {amount:,.2f} 已换成 {target} {got:,.2f}（{how}）"
        log.info(msg)
        self.svc.events.emit("earn", {"asset": src, "amount": amount, "dir": "convert", "to": target,
                                      "text": msg, "by": "system"})

    def _convert_pair(self, rule, src: str, target: str, amount: float) -> None:
        broker = self.svc.broker
        cid = "bclawc" + uuid.uuid4().hex[:18]
        if rule.symbol == f"{src}{target}":
            qty = math.ceil(amount / rule.step_size - 1e-9) * rule.step_size if rule.step_size > 0 else amount
            fill = broker.sell(rule.symbol, min(qty, self._spot_free(src)), cid)
        else:
            fill = broker.buy(rule.symbol, math.floor(amount * 100) / 100, cid)
        if fill.status != "filled":
            raise ExchangeError(f"{src} 换 {target} 未成交（{fill.status}）")

    def _convert_api(self, src: str, target: str, amount: float) -> None:
        """Binance Convert (闪兑), spot wallet to spot wallet. Paper mode converts 1:1."""
        amount = math.floor(amount * 100) / 100
        if not self.live:
            with self.svc.db.write_lock, self.svc.db.session() as s:
                a = s.get(PaperBalance, src)
                if not a or a.free + 1e-9 < amount:
                    raise ExchangeError(f"模拟盘 {src} 余额不足")
                b = s.get(PaperBalance, target) or PaperBalance(asset=target, free=0)
                a.free -= amount
                b.free += amount
                s.add(a)
                s.add(b)
                s.commit()
            return
        client = self.svc.client
        q = client.convert_quote(src, target, amount)
        if not q.get("quoteId"):
            raise ExchangeError(f"{src} 换 {target} 闪兑无报价")
        r = client.convert_accept(q["quoteId"])
        oid = r.get("orderId")
        status = r.get("orderStatus")
        for wait in (0.5, 1, 2, 3):
            if status in ("SUCCESS", "FAIL") or not oid:
                break
            time.sleep(wait)
            try:
                status = client.convert_status(str(oid)).get("orderStatus")
            except ExchangeError as e:
                log.warning("convert status %s: %s", oid, e)
        if status != "SUCCESS":
            raise ExchangeError(f"{src} 换 {target} 闪兑未完成（{status or '无结果'}）")

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
        pending = sum(a.quote_usd for a in self.svc.db.pending_approvals()
                      if a.side in ("BUY", "OPEN_LONG", "OPEN_SHORT") and a.kind != "limit")
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
