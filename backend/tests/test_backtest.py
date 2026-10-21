import datetime as dt
import json
import re

import pytest

from binanceclaw.core.backtest import DAY, MAX_ROUNDS, STEPS, BacktestParams, History, fetch_range
from binanceclaw.db import Backtest

from .conftest import FakeSpot, plan

BASE = {"BTCUSDT": 50000.0, "ETHUSDT": 2000.0, "SOLUSDT": 100.0}
DRIFT = {"BTCUSDT": 0.01, "ETHUSDT": -0.005, "SOLUSDT": 0.0}     # per day


def px(sym, t):
    return BASE[sym] * (1 + DRIFT[sym] * (t / DAY - 20000))


class HistSpot(FakeSpot):
    """Candles on a straight line per symbol, paged like Binance (``start`` + ``limit``)."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def klines(self, symbol, interval="1d", limit=30, start=None):
        self.calls.append((symbol, interval, start))
        w = STEPS[interval]
        t0 = (start // w) * w if start % w == 0 else (start // w + 1) * w
        now = dt.datetime.now(dt.UTC).timestamp()
        out = []
        for i in range(limit):
            t = t0 + i * w
            if t + w > now:
                break
            o, c = px(symbol, t), px(symbol, t + w)
            out.append({"t": t, "o": o, "h": max(o, c) * 1.001, "l": min(o, c) * 0.999, "c": c, "v": 1, "close_t": t + w - 0.001})
        return out


def day(n):
    return dt.date(2024, 10, 1) + dt.timedelta(days=n)


@pytest.fixture
def lab(svc):
    svc.client = HistSpot()
    return svc.backtests


def run(lab, replies, **kw):
    seen = []

    def decide(prompt):
        seen.append(prompt)
        r = replies.pop(0) if replies else plan()
        if isinstance(r, Exception):
            raise r
        from binanceclaw.brain.providers import Completion
        return Completion(json.dumps(r), "fake-model", {"input_tokens": 100, "output_tokens": 20})

    params = {"tokens": ["BTC", "ETH"], "start": str(day(0)), "end": str(day(9)), "step": "1d", "capital": 10000, **kw}
    row = lab.start(params, decide=decide, wait=True)
    return row, seen


def test_params_bounds():
    with pytest.raises(ValueError, match="结束日期早于"):
        BacktestParams(tokens=["BTC"], start=day(5), end=day(1))
    with pytest.raises(ValueError, match="上限"):
        BacktestParams(tokens=["BTC"], start=day(0), end=day(MAX_ROUNDS // 6 + 5), step="4h")
    with pytest.raises(ValueError, match="昨天"):
        BacktestParams(tokens=["BTC"], start=day(0), end=dt.datetime.now(dt.UTC).date())
    p = BacktestParams(tokens=["btc", "BTC", " eth "], start=day(0), end=day(1), step="12h")
    assert p.tokens == ["BTC", "ETH"] and p.rounds() == 4


def test_history_has_no_lookahead():
    c = HistSpot()
    s = dt.datetime.combine(day(0), dt.time(), dt.UTC).timestamp()
    h = History(fetch_range(c, "BTCUSDT", "1d", s - 70 * DAY, s + 5 * DAY), fetch_range(c, "BTCUSDT", "4h", s - 10 * DAY, s + 5 * DAY))
    t = s + 2 * DAY + 10 * 3600            # 10:00 on day 2
    price, daily, h4 = h.at(t)
    assert all(x["t"] + 4 * 3600 <= t for x in h4)
    assert price == pytest.approx(px("BTCUSDT", s + 2 * DAY + 8 * 3600))     # the 04:00–08:00 candle's close
    assert daily[-1]["t"] == s + 2 * DAY and daily[-1]["c"] == price           # today's candle, still forming
    assert all(x["t"] + DAY <= s + 2 * DAY for x in daily[:-1])
    assert len(daily) > 60                  # warm-up history for the 50-day averages


def test_fetch_range_pages():
    c = HistSpot()
    s = dt.datetime.combine(day(0), dt.time(), dt.UTC).timestamp()
    rows = fetch_range(c, "BTCUSDT", "4h", s, s + 300 * 4 * 3600)
    assert len(rows) == 300 and len(c.calls) == 1
    rows = fetch_range(c, "BTCUSDT", "4h", s, s + 1500 * 4 * 3600)
    assert len(rows) == 1500 and len(c.calls) == 3
    assert [r["t"] for r in rows] == sorted({r["t"] for r in rows})


def test_replay_through_risk_engine(lab, svc):
    # round 0 buys BTC within the auto limit, round 1 asks for far more than the single-order cap
    row, prompts = run(lab, [plan(("BUY", "BTCUSDT", 150)), plan(("BUY", "ETHUSDT", 5000))])
    assert row.status == "done", row.error
    assert row.progress == row.total == 10 and len(prompts) == 10
    r = row.result
    assert r["log"][0]["side"] == "BUY" and r["log"][0]["quote_usd"] == 150
    # the second order went through the same caps a live round has
    second = r["rounds"][1]["actions"][0]
    assert second["approved_usd"] == svc.settings.limits.max_order_usd and second["decision"] == "execute"
    assert any("免审批" in c["name"] for c in second["checks"])
    assert len(r["equity"]) == 11 and r["equity"][0]["v"] == 10000
    # BTC drifts up, ETH down: holding BTC beats the equal-weight basket
    assert r["btc"]["total_return"] > 0 and r["btc"]["total_return"] > r["basket"]["total_return"]
    assert row.summary["btc_return"] == r["btc"]["total_return"]
    assert row.summary["trades"] == 2 and r["usage"]["calls"] == 10 and r["usage"]["input_tokens"] == 1000
    # no calendar dates and no live account in the prompt
    ctx = json.loads(prompts[3].split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])
    assert ctx["whitelist"] == ["BTCUSDT", "ETHUSDT"] and ctx["portfolio"]["total_usd"] > 0
    assert not re.search(r"20\d\d-\d\d", prompts[3])
    assert ctx["recent_decisions"][0]["time"].endswith("前")
    assert ctx["futures"] == {"enabled": False} and ctx["questions"] == {"enabled": False}
    assert "performance_30d" in ctx and "btc_return" in ctx["performance_30d"]


def test_skip_approvals_and_sells(lab, svc):
    svc.settings_store.update({"limits": {"allow_sell": False}})
    replies = [plan(("BUY", "BTCUSDT", 500)), plan(("BUY", "BTCUSDT", 100)), plan(("SELL", "BTCUSDT", 50))]
    row, _ = run(lab, replies, approvals="skip")
    acts = [r["actions"][0] for r in row.result["rounds"][:3]]
    assert acts[0]["decision"] == "skipped"            # above auto_execute_max_usd: would need a person
    assert acts[1]["decision"] == "execute"
    assert acts[2]["decision"] == "reject"             # owner does not allow sells
    row, _ = run(lab, [plan(("BUY", "BTCUSDT", 100)), plan(("SELL", "BTCUSDT", 50))], allow_sell=True)
    assert row.result["rounds"][1]["actions"][0]["decision"] == "execute"
    assert row.result["trades"]["trades"] == 1 and row.result["trades"]["wins"] == 1   # BTC rose between the rounds


def test_fees_and_slippage(lab):
    row, _ = run(lab, [plan(("BUY", "BTCUSDT", 100))], fee=0.001, slippage=0.001, end=str(day(0)))
    t = row.result["log"][0]
    s = dt.datetime.combine(day(0), dt.time(), dt.UTC).timestamp()
    assert t["price"] == pytest.approx(px("BTCUSDT", s) * 1.001)
    assert t["qty"] == pytest.approx(100 * 0.999 / t["price"])
    assert t["fee"] == pytest.approx(0.1)


def test_failures_stop_the_run(lab):
    from binanceclaw.brain.providers import ProviderError
    row, _ = run(lab, [plan(), "not json", ProviderError("down"), "{}", ProviderError("down")])
    assert row.status == "error" and "连续 3 轮失败" in row.error
    row, _ = run(lab, [ProviderError("down"), plan(), ProviderError("down")])
    assert row.status == "done"
    assert [r["status"] for r in row.result["rounds"][:3]] == ["error", "done", "error"]


def test_btc_crash_pauses_rounds(lab, svc):
    DRIFT["BTCUSDT"] = -0.05
    try:
        row, prompts = run(lab, [], end=str(day(4)))
    finally:
        DRIFT["BTCUSDT"] = 0.01
    assert any(r["status"] == "skipped" and "暂停" in r["summary"] for r in row.result["rounds"])
    assert len(prompts) < row.total


def test_validation_and_one_at_a_time(lab, svc):
    with pytest.raises(ValueError, match="稳定币"):
        lab.start({"tokens": ["USDC"], "start": str(day(0)), "end": str(day(1))})
    with pytest.raises(ValueError, match="交易对"):
        lab.start({"tokens": ["NOPE"], "start": str(day(0)), "end": str(day(1))})
    from binanceclaw.core.backtest import Busy
    busy = svc.db.add(Backtest(params={}, total=1))
    with pytest.raises(Busy):
        lab.start({"tokens": ["BTC"], "start": str(day(0)), "end": str(day(1))})
    assert lab.cancel(busy.id) and busy.id in lab._cancel
    assert not lab.delete(busy.id)


def test_cancel_and_restart(lab, svc):
    calls = []

    def decide(prompt):
        calls.append(1)
        if len(calls) == 2:
            lab._cancel.add(row_id[0])
        from binanceclaw.brain.providers import Completion
        return Completion(json.dumps(plan()), "m", {})

    row_id = []
    orig = svc.db.add

    def add(obj):
        out = orig(obj)
        if isinstance(obj, Backtest):
            row_id.append(out.id)
        return out
    svc.db.add = add
    row = lab.start({"tokens": ["BTC"], "start": str(day(0)), "end": str(day(9))}, decide=decide, wait=True)
    assert row.status == "cancelled" and row.progress == 2
    stale = svc.db.add(Backtest(params={}, total=1))
    from binanceclaw.core.backtest import BacktestLab
    BacktestLab(svc)
    assert svc.db.get(Backtest, stale.id).status == "error"
    assert lab.delete(row.id) and svc.db.get(Backtest, row.id) is None


def test_api(lab, svc):
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    from binanceclaw.api.app import create_app
    from binanceclaw.config import Secrets
    svc.secrets = Secrets(admin_password_hash=PasswordHasher().hash("pw-123456"), session_secret="x" * 64)
    c = TestClient(create_app(svc))
    assert c.get("/api/backtests").status_code == 401
    c.post("/api/auth/login", json={"password": "pw-123456"})
    r = c.post("/api/backtests", json={"tokens": ["BTC"], "start": str(day(3)), "end": str(day(1))})
    assert r.status_code == 400 and "早于" in r.json()["detail"]
    row, _ = run(lab, [plan(("BUY", "BTCUSDT", 100))], end=str(day(2)))
    lst = c.get("/api/backtests").json()
    assert lst[0]["id"] == row.id and "result" not in lst[0] and lst[0]["summary"]["trades"] == 1
    full = c.get(f"/api/backtests/{row.id}").json()
    assert full["result"]["log"][0]["token"] == "BTC"
    assert c.post(f"/api/backtests/{row.id}/cancel").status_code == 409
    assert c.delete(f"/api/backtests/{row.id}").status_code == 200
    assert c.get(f"/api/backtests/{row.id}").status_code == 404
