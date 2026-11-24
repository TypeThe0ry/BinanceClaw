"""Binance Stocks (real US stocks / ETFs) as one asset per ticker.

A stock can be held two ways and BinanceClaw shows and trades them as one holding:
* tokenized: the stock's token (NVDA -> NVDAB) in the spot wallet, read from the balances;
* not tokenized: shares held by Binance's broker. No endpoint lists them, so the shares
  BinanceClaw bought are kept in its own ledger (StockLot).

Buys go through Binance Stocks (USDC, market order); a buy of a tokenization-enabled stock settles
as its token when stocks.tokenize is on. Sells use the ledger shares first, then the token through
its spot pair. Every spot limit applies: order size, auto-execute threshold, sells, pause."""
from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field

from sqlmodel import select

from ..db import Order, StockLot, StockTrade
from ..exchange.binance_equity import DISCLAIMER_MISSING, DONE_STATUSES, FILLED, stock_cid
from ..exchange.binance_spot import ExchangeError

log = logging.getLogger("binanceclaw.stocks")

STOCKS_ASSET = "STOCKS"          # pseudo-asset: value of the ledger shares, in USD
MAX_STOCK_ACTIONS = 5
POLL_TRIES = 3


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class StockVerdict:
    index: int
    side: str
    symbol: str
    usd: float
    decision: str = "reject"          # execute | approve | reject
    reason: str = ""
    checks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"kind": "stock", "index": self.index, "side": self.side, "symbol": self.symbol, "approved_usd": round(self.usd, 2),
                "decision": self.decision, "reason": self.reason, "checks": self.checks}


class StockDesk:
    def __init__(self, svc):
        self.svc = svc
        self._info: tuple[float, dict] | None = None
        self._tok: tuple[float, dict] | None = None
        self._quotes: dict[str, tuple[float, float]] = {}
        self._disclaimer = False

    @property
    def client(self):
        return self.svc.eclient

    @property
    def cfg(self):
        return self.svc.settings.stocks

    @property
    def mode(self) -> str:
        return self.svc.broker.mode

    # ---------------------------------------------------------------- catalog
    def info(self) -> dict[str, dict]:
        """ticker -> Binance Stocks exchangeInfo row, cached 6 h."""
        if not self._info or time.time() - self._info[0] > 6 * 3600:
            self._info = (time.time(), self.client.stock_info())
        return self._info[1]

    def tokens(self) -> dict[str, tuple[str, float]]:
        """ticker -> (token asset code, shares per token), e.g. NVDA -> (NVDAB, 1.0)."""
        if not self._tok or time.time() - self._tok[0] > 6 * 3600:
            out = {}
            for r in self.client.tokenized_assets():
                t, code = r.get("underlyingEquitySymbol"), r.get("assetCode")
                if t and code and r.get("multiplierValid", True) is not False:
                    out[t.upper()] = (code.upper(), _f(r.get("multiplier")) or 1.0)
            self._tok = (time.time(), out)
        return self._tok[1]

    RETRY_AFTER = 300

    def tokens_safe(self) -> dict[str, tuple[str, float]]:
        """tokens(), or the last known list (empty at first) when Binance can't be reached; retried in 5 min."""
        try:
            return self.tokens()
        except ExchangeError as e:
            log.warning("tokenized stock list unavailable: %s", e)
            last = self._tok[1] if self._tok else {}
            self._tok = (time.time() - 6 * 3600 + self.RETRY_AFTER, last)
            return last

    def codes(self) -> dict[str, tuple[str, float]]:
        """token asset code -> (ticker, shares per token)."""
        return {code: (t, m) for t, (code, m) in self.tokens_safe().items()}

    def ticker_of(self, raw: str) -> str:
        """NVDA, NVDAB, NVDAUSDT, NVDABUSDT -> NVDA (when Binance knows it)."""
        s = raw.upper().strip().replace("/", "")
        q = self.svc.settings.quote
        if s.endswith(q) and len(s) > len(q):
            s = s[: -len(q)]
        codes = self.codes()
        return codes[s][0] if s in codes else s

    def quote(self, ticker: str, max_age: float = 30) -> float | None:
        """Mid price of one share in USD (Binance Stocks quote), cached."""
        hit = self._quotes.get(ticker)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        q = self.client.stock_quote(ticker)
        bid, ask = _f((q or {}).get("bidPrice")), _f((q or {}).get("askPrice"))
        px = (bid + ask) / 2 if bid > 0 and ask > 0 else (bid or ask or None)
        if px:
            self._quotes[ticker] = (time.time(), px)
        return px

    def price(self, ticker: str) -> float | None:
        """Share price: the Binance Stocks quote, else the token's spot price per share."""
        try:
            px = self.quote(ticker)
            if px:
                return px
        except ExchangeError as e:
            log.warning("stock quote %s unavailable: %s", ticker, e)
        tok = self.tokens_safe().get(ticker)
        if tok:
            spot = self.svc.asset_prices({tok[0]}).get(tok[0])
            if spot:
                return spot / tok[1]
        return None

    # ---------------------------------------------------------------- holdings
    def lots(self) -> dict[str, StockLot]:
        return {r.symbol: r for r in self.svc.db.all(select(StockLot).where(StockLot.mode == self.mode)) if r.qty > 1e-12}

    def ledger_value(self) -> float:
        total = 0.0
        for t, lot in self.lots().items():
            px = self.price(t)
            total += lot.qty * (px if px else lot.cost_usd / lot.qty)
        return total

    def holdings(self, balances: dict[str, float] | None = None) -> dict[str, dict]:
        """ticker -> merged holding: ledger shares + tokens in the wallet (in shares)."""
        bal = balances if balances is not None else self.svc.spot_balances()
        out: dict[str, dict] = {}
        for code, (t, m) in self.codes().items():
            amt = bal.get(code, 0.0)
            if amt > 1e-12:
                out.setdefault(t, {"symbol": t, "shares": 0.0, "token": code, "token_qty": 0.0, "cost": None})
                out[t]["token_qty"] = amt
                out[t]["token_shares"] = amt * m
        for t, lot in self.lots().items():
            row = out.setdefault(t, {"symbol": t, "shares": 0.0, "token": (self.tokens_safe().get(t) or ("",))[0],
                                     "token_qty": 0.0, "cost": None})
            row["shares"] = lot.qty
            row["cost"] = lot.cost_usd
        for t, row in out.items():
            row["qty"] = row["shares"] + row.get("token_shares", 0.0)
            px = self.price(t)
            row["price"] = px
            row["value"] = row["qty"] * px if px else None
        return out

    # ---------------------------------------------------------------- risk
    def evaluate(self, actions: list[dict], pf, st) -> list[StockVerdict]:
        s = self.svc.settings
        lim = s.limits
        spendable = pf.stable_value() - lim.stable_floor * pf.total()
        try:
            held = self.holdings()
        except ExchangeError:
            held = {}
        out = []
        for i, a in enumerate(actions):
            side = str(a.get("side", "")).upper()
            t = self.ticker_of(str(a.get("symbol", "")))
            usd = float(a.get("usd") or 0)
            v = StockVerdict(i, side, t, usd)
            out.append(v)

            def fail(name, detail, v=v):
                v.checks.append({"name": name, "ok": False, "detail": detail})
                v.reason, v.decision = detail, "reject"

            def ok(name, detail, v=v):
                v.checks.append({"name": name, "ok": True, "detail": detail})

            if i >= MAX_STOCK_ACTIONS:
                fail("每轮动作数", f"每轮最多 {MAX_STOCK_ACTIONS} 个股票动作")
                continue
            if not s.stocks.enabled:
                fail("股票开关", "币安股票未开启（配置 → 币安股票）")
                continue
            try:
                row = self.info().get(t)
            except ExchangeError as e:
                fail("股票列表", f"读取币安股票列表失败：{e}")
                continue
            if not row:
                fail("股票代码", f"币安股票里没有 {t}")
                continue
            trad = row.get("tradability", "BUY_SELL")
            if (side == "BUY" and trad not in ("BUY_SELL", "BUY")) or (side == "SELL" and trad not in ("BUY_SELL", "SELL")):
                fail("可交易状态", f"{t} 目前不能{'买入' if side == 'BUY' else '卖出'}（{trad}）")
                continue
            ok("股票代码", f"{t} 可交易")
            if usd <= 0:
                fail("金额", "金额必须大于 0")
                continue
            if usd > lim.max_order_usd:
                usd = lim.max_order_usd
                ok("单笔上限", f"金额截断到 ${usd:,.0f}")
            if side == "BUY":
                if st.paused:
                    fail("暂停", f"自动交易已暂停：{st.pause_reason}")
                    continue
                if usd > spendable:
                    usd = math.floor(max(spendable, 0) * 100) / 100
                    ok("稳定币下限", f"受稳定币下限限制，金额降到 ${usd:,.2f}")
                mn = max(_f(row.get("minNotional")), 1.0)
                if usd < mn:
                    fail("最小金额", f"{t} 最小下单金额 ${mn:g}")
                    continue
            else:
                if not lim.allow_sell:
                    fail("卖出开关", "未允许卖出（配置 → 限额 → 允许卖出）")
                    continue
                h = held.get(t)
                val = (h or {}).get("value") or 0.0
                if val <= 0:
                    fail("持仓", f"没有 {t} 持仓")
                    continue
                if usd > val:
                    usd = val
                    ok("持仓", f"按持仓卖出 ${usd:,.2f}")
            v.usd = usd
            if side == "BUY" and usd <= lim.auto_execute_max_usd:
                v.decision, v.reason = "execute", f"≤ 自动执行额度 ${lim.auto_execute_max_usd:g}"
            elif st.unattended and (side == "BUY" or s.approval_free.include_sell or s.autonomy.full):
                v.decision, v.reason = "execute", "AI 全自主 / 免审批，限额内直接执行"
            else:
                v.decision, v.reason = "approve", "需要你批准"
        return out

    # ---------------------------------------------------------------- execution
    def _ensure_disclaimer(self):
        if self._disclaimer:
            return
        try:
            self.client.sign_stock_disclaimer()
            self._disclaimer = True
        except ExchangeError as e:
            log.warning("Binance Stocks disclaimer not signed: %s", e)

    def _book(self, t: str, dq: float, dcost: float):
        db = self.svc.db
        with db.write_lock, db.session() as ses:
            row = ses.exec(select(StockLot).where(StockLot.mode == self.mode, StockLot.symbol == t)).first() \
                or StockLot(mode=self.mode, symbol=t)
            if dq < 0 and row.qty > 0:
                dcost = row.cost_usd * (dq / row.qty)          # sell at average cost
            row.qty = max(row.qty + dq, 0.0)
            row.cost_usd = max(row.cost_usd + dcost, 0.0) if row.qty > 1e-12 else 0.0
            row.updated = time.time()
            ses.add(row)
            ses.commit()

    def _apply_fill(self, tr: StockTrade, d: dict) -> StockTrade:
        qty, total = _f(d.get("filledQty")), _f(d.get("filledTotal"))
        price = _f(d.get("avgFilledPrice")) or (total / qty if qty else 0.0)
        total = total or qty * price
        status = "filled" if d.get("status") == FILLED else ("canceled" if d.get("status") in DONE_STATUSES else "submitted")
        if status == "submitted":
            return tr
        if qty > 0 and not tr.tokenized:
            self._book(tr.symbol, qty if tr.side == "BUY" else -qty, total if tr.side == "BUY" else 0.0)
        return self.svc.db.update(StockTrade, tr.id, status="filled" if qty > 0 else status, qty=qty, price=price,
                                  usd=total or tr.usd, fee=_f(d.get("fee")), detail={**tr.detail, "binance": d})

    def _place(self, t: str, side: str, cid: str, **kw) -> dict:
        try:
            return self.client.stock_market_order(t, side, cid, **kw)
        except ExchangeError as e:
            if e.code == DISCLAIMER_MISSING:
                self._disclaimer = False
                self._ensure_disclaimer()
                return self.client.stock_market_order(t, side, cid, **kw)
            raise

    def _await(self, tr: StockTrade) -> StockTrade:
        for i in range(POLL_TRIES):
            try:
                d = self.client.stock_order(tr.client_order_id)
            except ExchangeError as e:
                log.warning("stock order %s status unavailable: %s", tr.client_order_id, e)
                d = {}
            if d.get("status") in DONE_STATUSES:
                return self._apply_fill(tr, d)
            time.sleep(1 + i)
        return tr

    def buy(self, t: str, usd: float, via: str, cycle_id: str = "") -> tuple[bool, str]:
        s = self.svc.settings
        tok = bool(s.stocks.tokenize and t in self.tokens())
        day = self.svc.today()
        if self.mode != "live":
            px = self.price(t)
            if not px:
                return False, f"{t} 没有报价"
            fill_px = px * (1 + s.paper.slippage)
            qty = usd / fill_px
            self.svc.broker._paper_apply({s.quote: -usd})
            self._book(t, qty, usd)
            self.svc.db.add(StockTrade(day=day, mode="paper", symbol=t, side="BUY", usd=usd, qty=qty, price=fill_px,
                                       tokenized=False, via=via, cycle_id=cycle_id, detail={"paper": True}))
            return True, f"[模拟盘] 买入 {qty:.6g} 股 {t} @ ${fill_px:,.2f}，金额 ${usd:,.2f}"
        self._ensure_disclaimer()
        amount = self.svc.earn.ensure_stable("USDC", usd, shrink=True)
        cid = stock_cid()
        tr = self.svc.db.add(StockTrade(day=day, mode="live", symbol=t, side="BUY", usd=amount, tokenized=tok,
                                        status="submitted", via=via, client_order_id=cid, cycle_id=cycle_id))
        try:
            ack = self._place(t, "BUY", cid, notional=amount, tokenize=tok)
        except ExchangeError as e:
            self.svc.db.update(StockTrade, tr.id, status="failed", detail={"error": str(e)})
            return False, f"{t} 买入失败：{e}"
        tr = self.svc.db.update(StockTrade, tr.id, detail={"order_id": ack.get("orderId")})
        tr = self._await(tr)
        held = f"（持仓为 {self.tokens()[t][0]}）" if tok else ""
        if tr.status == "filled":
            return True, f"[实盘] 买入 {tr.qty:.6g} 股 {t} @ ${tr.price:,.2f}，金额 ${tr.usd:,.2f}{held}"
        if tr.status == "submitted":
            return True, f"[实盘] {t} 买入 ${amount:,.2f} 已提交，成交后自动入账{held}"
        return False, f"{t} 买入未成交（{tr.status}）"

    def sell(self, t: str, usd: float, via: str, cycle_id: str = "") -> tuple[bool, str]:
        s = self.svc.settings
        px = self.price(t)
        if not px:
            return False, f"{t} 没有报价"
        need = usd / px
        lot = self.lots().get(t)
        msgs = []
        day = self.svc.today()
        have = lot.qty if lot else 0.0
        q1 = min(need, have)
        step = _f((self.info().get(t) or {}).get("stepSize"))
        if have - q1 <= max(step, have * 0.01):
            q1 = have                                    # don't leave dust behind
        elif step > 0:
            q1 = math.floor(q1 / step + 1e-9) * step
        if q1 > 0:
            if self.mode != "live":
                fill_px = px * (1 - s.paper.slippage)
                self._book(t, -q1, 0)
                self.svc.broker._paper_apply({s.quote: q1 * fill_px})
                self.svc.db.add(StockTrade(day=day, mode="paper", symbol=t, side="SELL", usd=q1 * fill_px, qty=q1,
                                           price=fill_px, via=via, cycle_id=cycle_id, detail={"paper": True}))
                msgs.append(f"[模拟盘] 卖出 {q1:.6g} 股 {t} @ ${fill_px:,.2f}")
            else:
                cid = stock_cid()
                tr = self.svc.db.add(StockTrade(day=day, mode="live", symbol=t, side="SELL", usd=q1 * px, status="submitted",
                                                via=via, client_order_id=cid, cycle_id=cycle_id))
                try:
                    self._place(t, "SELL", cid, qty=q1)
                except ExchangeError as e:
                    self.svc.db.update(StockTrade, tr.id, status="failed", detail={"error": str(e)})
                    return False, f"{t} 卖出失败：{e}"
                tr = self._await(tr)
                msgs.append(f"[实盘] 卖出 {tr.qty or q1:.6g} 股 {t}" + ("" if tr.status == "filled" else "，已提交，成交后自动入账"))
        rest = need - q1
        tok = self.tokens().get(t)
        if rest > 1e-9 and tok:
            code, mult = tok
            have = self.svc.spot_balances().get(code, 0.0)
            units = min(rest / mult, have)
            pair = f"{code}{s.quote}"
            if units > 0 and self.svc.is_spot_pair(pair):
                try:
                    msgs.append(self._sell_token(t, code, pair, units, via, cycle_id))
                except ExchangeError as e:
                    if not q1:
                        return False, f"{code} 卖出失败：{e}"
                    msgs.append(f"{code} 卖出失败：{e}")
            elif units > 0 and not q1:
                return False, f"{code} 没有 {s.quote} 现货交易对，暂时无法卖出"
        if not msgs:
            return False, f"没有可卖的 {t}"
        return True, "；".join(msgs)

    def _sell_token(self, t: str, code: str, pair: str, units: float, via: str, cycle_id: str) -> str:
        """The tokenized part of a stock sell: the token through its spot pair."""
        svc = self.svc
        cid = "bclaw" + uuid.uuid4().hex[:18]
        svc.earn.ensure_liquid(code, units)
        rules = svc.broker.rules([pair]).get(pair)
        if rules and rules.step_size > 0:
            units = math.floor(units / rules.step_size + 1e-9) * rules.step_size
        fill = svc.broker.sell(pair, units, cid)
        svc.db.add(Order(day=svc.today(), mode=self.mode, symbol=pair, side="SELL", quote_usd=abs(fill.quote_spent),
                         qty=fill.qty, price=fill.avg_price, fee=fill.fee, status=fill.status, via=via,
                         client_order_id=cid, cycle_id=cycle_id, detail={**(fill.raw or {}), "stock": t}))
        return f"卖出 {fill.qty:.6g} {code}（{t} 代币）@ ${fill.avg_price:,.2f}"

    def reconcile(self) -> None:
        """Submitted live orders: read their fill and book it."""
        if self.mode != "live":
            return
        for tr in self.svc.db.all(select(StockTrade).where(StockTrade.mode == "live", StockTrade.status == "submitted")):
            try:
                d = self.client.stock_order(tr.client_order_id)
            except ExchangeError as e:
                log.warning("stock order %s: %s", tr.client_order_id, e)
                continue
            if d.get("status") in DONE_STATUSES:
                tr = self._apply_fill(tr, d)
                self.svc.events.emit("order", {"id": tr.id, "status": tr.status,
                                               "text": f"{tr.symbol} {'买入' if tr.side == 'BUY' else '卖出'}已成交"
                                               if tr.status == "filled" else f"{tr.symbol} 订单{tr.status}"})

    # ---------------------------------------------------------------- AI context
    def context(self, balances: dict[str, float] | None = None) -> dict:
        s = self.svc.settings
        if not s.stocks.enabled:
            return {"enabled": False}
        out: dict = {"enabled": True, "tokenize": s.stocks.tokenize}
        try:
            hold = self.holdings(balances)
            out["holdings"] = {t: {"qty": round(h["qty"], 6), "price": h["price"] and round(h["price"], 4),
                                   "value": h["value"] and round(h["value"], 2),
                                   **({"token": h["token"]} if h.get("token_qty") else {}),
                                   **({"avg_cost": round(h["cost"] / h["shares"], 4)} if h.get("cost") and h["shares"] else {})}
                               for t, h in hold.items()}
            out["tokenized"] = sorted(self.tokens())
            out["count"] = len(self.info())
        except ExchangeError as e:
            out["note"] = f"币安股票数据暂不可用（{e}），本轮不要提出股票动作"
        return out

    def markets(self, q: str = "", limit: int = 50) -> list[dict]:
        q = q.strip().upper()
        info = self.info()
        toks = self.tokens()
        held = self.holdings()
        names = sorted((t for t in info if not q or q in t), key=lambda t: (t != q, t not in held, t not in toks, t))
        out = []
        for t in names[:limit]:
            r = info[t]
            out.append({"token": t, "symbol": t, "price": self.price(t) if (t == q or t in held) else None,
                        "change_pct": None, "quote_volume": 0.0, "held": t in held, "enabled": False,
                        "tradability": r.get("tradability"), "tokenized": t in toks,
                        "trading": r.get("tradability") == "BUY_SELL", "selectable": True, "stock": True})
        return out
