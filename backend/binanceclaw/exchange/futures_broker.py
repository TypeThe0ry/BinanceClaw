"""Futures account: a simulated isolated-margin ledger (paper) or the live USDⓈ-M account.

Safety invariants (live):
* isolated margin; works in one-way and hedge position mode (the account setting is never changed);
* a position never stays open without an exchange-side STOP_MARKET (closePosition). If the stop
  cannot be placed right after the entry fills, the position is closed immediately;
* margin is moved spot -> futures just before an open, and swept back once BinanceClaw has no
  open positions left. No other transfer type exists in the client.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from sqlmodel import select

from ..db import Database, FuturesPosition, PaperBalance
from ..risk.futures import MAINT_MARGIN, TAKER_FEE, floor_step, liq_distance
from .binance_spot import BinanceSpot, ExchangeError
from .broker import AMBIGUOUS_CODES, NOT_FOUND, OrderUnknown

log = logging.getLogger("binanceclaw.futures")

QUOTE = "USDT"


@dataclass
class FFill:
    qty: float
    price: float
    fee: float
    pnl: float = 0.0
    raw: dict | None = None


def _cid(prefix: str = "bcf") -> str:
    return prefix + uuid.uuid4().hex[:20]


def _dir(side: str) -> int:
    return 1 if side == "LONG" else -1


class FuturesBroker:
    def __init__(self, client, spot: BinanceSpot, db: Database, mode: str, paper_cfg):
        self.client = client            # BinanceFutures (market data is always real)
        self.spot = spot
        self.db = db
        self.mode = mode
        self.paper_cfg = paper_cfg
        self._rules: tuple[float, dict] | None = None
        self._prem: tuple[float, dict] | None = None
        self._dual: tuple[float, bool] | None = None

    # ---------------------------------------------------------------- market data
    def rules(self) -> dict:
        if not self._rules or time.time() - self._rules[0] > 3600:
            self._rules = (time.time(), self.client.perp_rules())
        return self._rules[1]

    def premium(self, max_age: float = 5) -> dict[str, dict]:
        if not self._prem or time.time() - self._prem[0] > max_age:
            self._prem = (time.time(), self.client.premium())
        return self._prem[1]

    def marks(self, max_age: float = 5) -> dict[str, float]:
        return {k: v["mark"] for k, v in self.premium(max_age).items()}

    def hedge(self) -> bool:
        if not self._dual or time.time() - self._dual[0] > 60:
            self._dual = (time.time(), bool(self.client.dual_side()))
        return self._dual[1]

    def _pside(self, side: str) -> str | None:
        """positionSide to send: LONG/SHORT in hedge mode, nothing in one-way mode."""
        return side if self.hedge() else None

    def exchange_positions(self) -> dict[tuple[str, str], dict]:
        """(symbol, LONG|SHORT) -> positionRisk row, for both position modes."""
        out = {}
        for r in self.client.positions():
            amt = float(r.get("positionAmt") or 0)
            ps = r.get("positionSide") or "BOTH"
            side = ps if ps in ("LONG", "SHORT") else ("LONG" if amt > 0 else "SHORT")
            out[(r["symbol"], side)] = r
        return out

    # ---------------------------------------------------------------- positions
    def open_rows(self) -> list[FuturesPosition]:
        return self.db.all(select(FuturesPosition).where(FuturesPosition.mode == self.mode,
                                                         FuturesPosition.status.in_(("open", "pending"))))

    def snapshot(self, marks: dict[str, float] | None = None) -> list[dict]:
        """BinanceClaw's open positions with mark price, unrealised P&L and liquidation estimate."""
        marks = marks if marks is not None else self.marks()
        out = []
        for p in self.open_rows():
            m = marks.get(p.symbol) or p.entry
            upnl = (m - p.entry) * p.qty * _dir(p.side) if p.status == "open" else 0.0
            liq = p.entry * (1 - _dir(p.side) * liq_distance(p.leverage)) if p.entry else None
            out.append({"id": p.id, "symbol": p.symbol, "side": p.side, "qty": p.qty, "entry": p.entry, "mark": m,
                        "margin": p.margin, "leverage": p.leverage, "stop": p.stop, "take_profit": p.take_profit,
                        "upnl": upnl, "roe": upnl / p.margin if p.margin else None, "liq_est": liq,
                        "opened": p.opened, "status": p.status, "reason": p.reason, "invalidation": p.invalidation,
                        "realized": p.realized})
        return out

    def equity(self, marks: dict[str, float] | None = None) -> float:
        """Margin + unrealised P&L of BinanceClaw's positions (isolated: a position can't lose more than its margin)."""
        return sum(max(p["margin"] + p["upnl"], 0.0) for p in self.snapshot(marks))

    def foreign_symbols(self) -> set[str]:
        """Live only: symbols with an exchange position BinanceClaw did not open."""
        if self.mode != "live":
            return set()
        ours = {p.symbol for p in self.open_rows()}
        return {sym for sym, _side in self.exchange_positions()} - ours

    # ---------------------------------------------------------------- paper helpers
    def _paper_apply(self, delta: float):
        with self.db.write_lock, self.db.session() as s:
            row = s.get(PaperBalance, QUOTE) or PaperBalance(asset=QUOTE, free=0)
            row.free += delta
            if row.free < -1e-9:
                raise ExchangeError(f"模拟盘 {QUOTE} 余额不足")
            s.add(row)
            s.commit()

    # ---------------------------------------------------------------- live helpers
    def _place(self, place, symbol: str, cid: str) -> dict:
        try:
            return place()
        except ExchangeError as e:
            if e.code not in AMBIGUOUS_CODES:
                raise
            first = e
        for wait in (0.5, 1.5, 3.0):
            time.sleep(wait)
            try:
                return self.client.order_status(symbol, cid)
            except ExchangeError as e:
                if e.code != NOT_FOUND and e.code not in AMBIGUOUS_CODES:
                    break
        raise OrderUnknown(f"合约下单结果未知（{first}），稍后自动对账", None)

    @staticmethod
    def _parse(r: dict) -> FFill:
        qty = float(r.get("executedQty") or 0)
        price = float(r.get("avgPrice") or 0)
        return FFill(qty, price, qty * price * TAKER_FEE, raw=r)

    def _fund(self, need: float):
        """Make sure the futures wallet has ``need`` USDT available; move the shortfall from spot."""
        avail = float(self.client.account().get("availableBalance") or 0)
        short = need - avail
        if short > 0:
            self.spot.transfer("MAIN_UMFUTURE", QUOTE, round(short + 0.01, 2))

    def _sweep_back(self):
        """Return idle futures USDT to spot once BinanceClaw has no open positions (live)."""
        if self.open_rows() or self.exchange_positions():
            return
        avail = float(self.client.account().get("availableBalance") or 0)
        if avail >= 1:
            self.spot.transfer("UMFUTURE_MAIN", QUOTE, floor_step(avail, 0.01))

    def _protect(self, symbol: str, side: str, stop: float, tp: float | None) -> tuple[str, str]:
        exit_side = "SELL" if side == "LONG" else "BUY"
        ps = self._pside(side)
        r = self.client.protect(symbol, exit_side, "STOP_MARKET", stop, _cid("bcs"), ps)
        sid = str(r.get("algoId", ""))
        tid = ""
        if tp:
            try:
                tid = str(self.client.protect(symbol, exit_side, "TAKE_PROFIT_MARKET", tp, _cid("bct"), ps).get("algoId", ""))
            except ExchangeError as e:
                log.warning("take-profit for %s not placed: %s", symbol, e)
        return sid, tid

    # ---------------------------------------------------------------- trading
    def open(self, symbol: str, side: str, margin: float, leverage: int, stop: float, tp: float | None,
             cid: str, meta: dict) -> tuple[FuturesPosition, FFill]:
        rule = self.rules().get(symbol)
        if rule is None:
            raise ExchangeError(f"{symbol} 不是可交易的 USDT 永续合约")
        mark = self.marks(0)[symbol]
        qty = floor_step(margin * leverage / mark, rule.step_size)
        if qty < rule.min_qty or qty * mark < max(rule.min_notional, 5):
            raise ExchangeError(f"{symbol} 数量 {qty} 低于交易所最小值")
        base = dict(mode=self.mode, symbol=symbol, side=side, leverage=leverage, stop=stop, take_profit=tp, **meta)
        if self.mode != "live":
            price = mark * (1 + _dir(side) * self.paper_cfg.slippage)
            fee = qty * price * TAKER_FEE
            used = qty * price / leverage
            self._paper_apply(-(used + fee))
            row = self.db.add(FuturesPosition(qty=qty, entry=price, margin=used, fees=fee, **base))
            return row, FFill(qty, price, fee, raw={"paper": True, "mark": mark})

        self.client.set_margin_type(symbol, "ISOLATED")
        self.client.set_leverage(symbol, leverage)
        self._fund(margin * 1.02 + qty * mark * TAKER_FEE * 2)
        entry_side = "BUY" if side == "LONG" else "SELL"
        try:
            r = self._place(lambda: self.client.market_order(symbol, entry_side, qty, cid, position_side=self._pside(side)),
                            symbol, cid)
        except OrderUnknown:
            self.db.add(FuturesPosition(qty=0, entry=0, margin=margin, status="pending", **base))
            raise
        fill = self._parse(r)
        if fill.qty <= 0:
            raise ExchangeError(f"{symbol} 开仓未成交（{r.get('status')}）")
        row = self.db.add(FuturesPosition(qty=fill.qty, entry=fill.price, margin=fill.qty * fill.price / leverage,
                                          fees=fill.fee, **base))
        try:
            sid, tid = self._protect(symbol, side, stop, tp)
        except ExchangeError as e:
            # never leave a naked position: close it right away
            log.error("stop-loss for %s failed (%s); closing position", symbol, e)
            self.close(row, 1.0, "止损单下单失败，已立即平仓")
            raise ExchangeError(f"止损单下单失败（{e}），为安全起见已立即平仓") from e
        row = self.db.update(FuturesPosition, row.id, stop_algo_id=sid, tp_algo_id=tid)
        return row, fill

    def close(self, row: FuturesPosition, fraction: float, reason: str, price: float | None = None,
              cid: str | None = None) -> FFill:
        """Close ``fraction`` of a position (reduce-only). ``price`` forces the paper fill price (stop/TP/liquidation)."""
        rule = self.rules().get(row.symbol)
        step = rule.step_size if rule else 0
        qty = row.qty if fraction >= 0.999 else floor_step(row.qty * fraction, step)
        if qty <= 0:
            raise ExchangeError("减仓数量为 0")
        full = qty >= row.qty - 1e-12
        if self.mode != "live":
            mark = price or self.marks(0).get(row.symbol) or row.entry
            px = price or mark * (1 - _dir(row.side) * self.paper_cfg.slippage)
            fee = qty * px * TAKER_FEE
            released = row.margin * qty / row.qty
            pnl = (px - row.entry) * qty * _dir(row.side)
            pnl = max(pnl, -released)                         # isolated: loss capped at the position's margin
            self._paper_apply(max(released + pnl - fee, 0.0))
            fill = FFill(qty, px, fee, pnl - fee, {"paper": True})
        else:
            cid = cid or _cid()
            exit_side = "SELL" if row.side == "LONG" else "BUY"
            ps = self._pside(row.side)
            r = self._place(lambda: self.client.market_order(row.symbol, exit_side, qty, cid, reduce_only=True,
                                                             position_side=ps), row.symbol, cid)
            fill = self._parse(r)
            fill.pnl = (fill.price - row.entry) * fill.qty * _dir(row.side) - fill.fee
            released = row.margin * fill.qty / row.qty if row.qty else 0
            if full:
                for aid in (row.stop_algo_id, row.tp_algo_id):   # only ours: never touch the owner's own orders
                    if aid:
                        try:
                            self.client.cancel_algo(row.symbol, aid)
                        except ExchangeError as e:
                            log.info("cancel algo %s on %s: %s", aid, row.symbol, e)
        self._book_close(row, fill, released, full, reason)
        if self.mode == "live" and full:
            try:
                self._sweep_back()
            except ExchangeError as e:
                log.warning("sweep back to spot failed: %s", e)
        return fill

    def _book_close(self, row: FuturesPosition, fill: FFill, released: float, full: bool, reason: str):
        if full:
            self.db.update(FuturesPosition, row.id, status="closed", closed=time.time(), close_price=fill.price,
                           realized=row.realized + fill.pnl, fees=row.fees + fill.fee, close_reason=reason)
        else:
            self.db.update(FuturesPosition, row.id, qty=row.qty - fill.qty, margin=row.margin - released,
                           realized=row.realized + fill.pnl, fees=row.fees + fill.fee)

    def set_stop(self, row: FuturesPosition, stop: float) -> None:
        if self.mode != "live":
            self.db.update(FuturesPosition, row.id, stop=stop)
            return
        exit_side = "SELL" if row.side == "LONG" else "BUY"
        ps = self._pside(row.side)
        # Binance allows a single closePosition stop per direction (-4130): cancel, then place
        if row.stop_algo_id:
            try:
                self.client.cancel_algo(row.symbol, row.stop_algo_id)
            except ExchangeError as e:
                log.warning("cancel old stop for %s failed: %s", row.symbol, e)
        try:
            r = self.client.protect(row.symbol, exit_side, "STOP_MARKET", stop, _cid("bcs"), ps)
        except ExchangeError as e:
            try:
                r = self.client.protect(row.symbol, exit_side, "STOP_MARKET", row.stop, _cid("bcs"), ps)
                self.db.update(FuturesPosition, row.id, stop_algo_id=str(r.get("algoId", "")))
            except ExchangeError:
                self.close(row, 1.0, "无法重新挂止损，已平仓")
                raise ExchangeError(f"新止损下单失败（{e}），恢复原止损也失败，已平仓") from e
            raise ExchangeError(f"新止损下单失败，已恢复原止损：{e}") from e
        self.db.update(FuturesPosition, row.id, stop=stop, stop_algo_id=str(r.get("algoId", "")))

    # ---------------------------------------------------------------- watching
    def paper_triggers(self, marks: dict[str, float]) -> list[tuple[FuturesPosition, str, FFill]]:
        """Paper: fire stop-loss / take-profit / liquidation against the mark price."""
        fired = []
        for p in self.open_rows():
            m = marks.get(p.symbol)
            if not m or p.status != "open":
                continue
            d = _dir(p.side)
            liq = p.entry * (1 - d * (1.0 / p.leverage - MAINT_MARGIN))
            if (m - liq) * d <= 0:
                kind, px = "LIQUIDATED", liq
            elif (m - p.stop) * d <= 0:
                kind, px = "STOP_HIT", p.stop
            elif p.take_profit and (m - p.take_profit) * d >= 0:
                kind, px = "TP_HIT", p.take_profit
            else:
                continue
            reason = {"LIQUIDATED": "强平", "STOP_HIT": "触发止损", "TP_HIT": "触发止盈"}[kind]
            # stops are market orders in reality: fill a little worse than the trigger
            px = px * (1 - d * self.paper_cfg.slippage) if kind != "LIQUIDATED" else px
            fill = self.close(p, 1.0, reason, price=px)
            fired.append((p, kind, fill))
        return fired

    def live_sync(self) -> list[tuple[FuturesPosition, str, str]]:
        """Live: reconcile BinanceClaw's rows with the exchange and make sure every position has a stop.
        Returns events (row, kind, text)."""
        events = []
        ex = self.exchange_positions()
        for p in self.open_rows():
            r = ex.get((p.symbol, p.side))
            amt = float(r["positionAmt"]) if r else 0.0
            if p.status == "pending":
                if amt:
                    p = self.db.update(FuturesPosition, p.id, status="open", qty=abs(amt), entry=float(r["entryPrice"]))
                    events.append((p, "ADOPTED", "下单结果未知的仓位已在币安确认"))
                elif time.time() - p.opened > 600:
                    self.db.update(FuturesPosition, p.id, status="closed", closed=time.time(), close_reason="未成交")
                    continue
                else:
                    continue
            if not amt:
                total, px = self._closed_pnl(p)
                self.db.update(FuturesPosition, p.id, status="closed", closed=time.time(), close_price=px,
                               realized=total, close_reason="交易所侧平仓（止损 / 止盈 / 强平 / 手动）")
                events.append((p, "EXCHANGE_CLOSED", f"已在币安平仓，本仓位累计盈亏约 ${total:,.2f}"))
                continue
            self.db.update(FuturesPosition, p.id, qty=abs(amt), entry=float(r["entryPrice"]),
                           margin=float(r.get("isolatedWallet") or p.margin))
            algos = self.client.open_algo_orders(p.symbol)
            mine = [o for o in algos if (o.get("positionSide") or "BOTH") in ("BOTH", p.side)]
            if not any(str(o.get("orderType") or o.get("type")) == "STOP_MARKET" for o in mine):
                try:
                    sid, _ = self._protect(p.symbol, p.side, p.stop, None)
                    self.db.update(FuturesPosition, p.id, stop_algo_id=sid)
                    events.append((p, "STOP_RESTORED", f"止损单丢失，已重新挂单 {p.stop:g}"))
                except ExchangeError as e:
                    self.close(p, 1.0, "止损单丢失且无法重挂，已平仓")
                    events.append((p, "STOP_FAILED", f"止损单丢失且无法重挂（{e}），已平仓"))
        if not self.open_rows():
            try:
                self._sweep_back()
            except ExchangeError as e:
                log.warning("sweep back failed: %s", e)
        return events

    def _closed_pnl(self, p: FuturesPosition) -> tuple[float, float | None]:
        """Total realised P&L of the position over its life (fills since it was opened), and the last fill price."""
        try:
            trades = self.client.user_trades(p.symbol, int(p.opened * 1000))
        except (ExchangeError, AttributeError):
            trades = []
        if trades:
            pnl = sum(float(t.get("realizedPnl") or 0) - float(t.get("commission") or 0) for t in trades)
            return pnl, float(trades[-1].get("price") or 0) or None
        m = self.marks(0).get(p.symbol)
        return p.realized + ((m - p.entry) * p.qty * _dir(p.side) if m else 0.0), m
