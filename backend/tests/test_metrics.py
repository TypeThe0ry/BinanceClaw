import math
import time

import pytest

from binanceclaw.core import metrics as m
from binanceclaw.db import EquitySnapshot, FuturesPosition, Order

DAY = 86400


def test_equity_stats_rising_line_has_no_drawdown():
    t0 = 1_700_000_000
    pts = [(t0 + d * DAY, 1000 * 1.01 ** d) for d in range(60)]
    s = m.equity_stats(pts)
    assert s["total_return"] == pytest.approx(1.01 ** 59 - 1, rel=1e-4)
    assert s["max_drawdown"] == 0
    assert s["period"] == "1d"
    assert s["cagr"] is not None and s["cagr"] > 1
    assert s["volatility"] == pytest.approx(0, abs=1e-6)
    assert s["sharpe"] is None          # no variance: undefined rather than infinite


def test_equity_stats_drawdown_and_recovery():
    t0 = 1_700_000_000
    vals = [100, 110, 120, 90, 60, 80, 120, 130]
    s = m.equity_stats([(t0 + i * DAY, v) for i, v in enumerate(vals)])
    assert s["max_drawdown"] == pytest.approx(0.5)            # 120 -> 60
    assert s["max_drawdown_days"] == pytest.approx(4)         # under water from day 2 to day 6
    assert s["worst_period"] == pytest.approx(60 / 90 - 1, abs=1e-4)
    assert s["sharpe"] is not None and s["sortino"] is not None


def test_equity_stats_short_history_uses_hourly_returns_and_no_cagr():
    t0 = 1_700_000_000
    pts = [(t0 + h * 3600, 1000 + (h % 3)) for h in range(48)]
    s = m.equity_stats(pts)
    assert s["period"] == "1h"
    assert s["cagr"] is None and s["calmar"] is None
    assert s["sharpe"] is not None


def test_equity_stats_too_little_data():
    assert m.equity_stats([])["total_return"] is None
    assert m.equity_stats([(1, 100)])["sharpe"] is None


def test_resample_keeps_last_per_bucket():
    assert m.resample([(0, 1), (10, 2), (DAY + 5, 3)], DAY) == [(10, 2), (DAY + 5, 3)]


def test_trade_stats():
    s = m.trade_stats([10, -5, -5, -5, 20, 0], [0.1, -0.05, -0.05, -0.05, 0.2, 0])
    assert s["trades"] == 6 and s["wins"] == 2 and s["losses"] == 3
    assert s["win_rate"] == pytest.approx(2 / 6, abs=1e-4)
    assert s["profit_factor"] == pytest.approx(2.0)
    assert s["payoff"] == pytest.approx(15 / 5)
    assert s["max_loss_streak"] == 3 and s["max_win_streak"] == 1
    assert s["net"] == pytest.approx(15)
    assert m.trade_stats([])["win_rate"] is None
    assert m.trade_stats([5, 5])["profit_factor"] is None    # no losses


def test_spot_round_trips_average_cost():
    orders = [
        {"ts": 1, "symbol": "ETHUSDT", "side": "BUY", "qty": 1, "quote_usd": 100},
        {"ts": 2, "symbol": "ETHUSDT", "side": "BUY", "qty": 1, "quote_usd": 200},
        {"ts": 3, "symbol": "ETHUSDT", "side": "SELL", "qty": 1, "quote_usd": 180},
        {"ts": 4, "symbol": "SOLUSDT", "side": "SELL", "qty": 3, "quote_usd": 30},   # never bought here: ignored
    ]
    rt = m.spot_round_trips(orders)
    assert len(rt) == 1
    assert rt[0]["pnl"] == pytest.approx(30)          # sold at 180 against an average cost of 150
    assert rt[0]["ret"] == pytest.approx(0.2)


def test_analytics_report(svc):
    now = time.time()
    for d in range(40, -1, -1):
        svc.db.add(EquitySnapshot(ts=now - d * DAY, mode="paper", total=1000 + (40 - d) * 5 - (30 if d == 10 else 0)))
    day = time.strftime("%Y-%m-%d")
    svc.db.add(Order(ts=now - 20 * DAY, day=day, mode="paper", symbol="ETHUSDT", side="BUY", quote_usd=100, qty=0.05,
                     price=2000, status="filled", via="auto"))
    svc.db.add(Order(ts=now - 5 * DAY, day=day, mode="paper", symbol="ETHUSDT", side="SELL", quote_usd=60, qty=0.025,
                     price=2400, status="filled", via="auto"))
    svc.db.add(FuturesPosition(mode="paper", symbol="BTCUSDT", side="LONG", qty=0.01, entry=80000, margin=100, leverage=3,
                               stop=75000, status="closed", closed=now - 2 * DAY, realized=-8))
    r = svc.analytics.report("30d")
    assert r["equity"]["total_return"] > 0
    assert 0 < r["equity"]["max_drawdown"] < 0.05
    assert r["spot"]["trades"] == 1 and r["spot"]["net"] == pytest.approx(10)
    assert r["futures"]["trades"] == 1 and r["futures"]["net"] == pytest.approx(-8)
    assert r["trades"]["trades"] == 2 and r["trades"]["profit_factor"] == pytest.approx(10 / 8)
    assert r["benchmark"]["return"] is not None and r["benchmark"]["excess"] is not None
    assert {x["symbol"] for x in r["by_symbol"]} == {"ETH", "BTCUSDT"}
    assert r["drawdown"] and all(0 <= p["dd"] < 1 for p in r["drawdown"])
    assert svc.analytics.report("7d")["spot"]["trades"] == 1
    with pytest.raises(ValueError):
        svc.analytics.report("2w")
    p = svc.analytics.for_prompt()
    assert p["closed_trades"]["trades"] == 2 and "max_drawdown" in p and not any(
        isinstance(v, float) and math.isnan(v) for v in p.values())


def test_analytics_empty_and_in_prompt(svc):
    assert svc.analytics.for_prompt() is None
    r = svc.analytics.report("all")
    assert r["equity"]["total_return"] is None and r["trades"]["trades"] == 0

