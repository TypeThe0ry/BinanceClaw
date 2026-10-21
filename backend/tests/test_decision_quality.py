"""Faster rounds (parallel, cached candles; per-stage timing) and better-informed decisions
(multi-timeframe indicators, market breadth, the AI's own track record)."""
import time

from binanceclaw.brain import indicators as ta
from binanceclaw.db import Cycle, Order

from .conftest import plan, use


def _k(closes):
    return [{"t": i, "o": c, "h": c * 1.01, "l": c * 0.99, "c": c, "v": 1} for i, c in enumerate(closes)]


# ---------------------------------------------------------------- indicators
def test_rsi_extremes_and_flat():
    assert ta.rsi([float(i) for i in range(1, 40)]) == 100.0
    assert ta.rsi([float(i) for i in range(40, 1, -1)]) < 1
    assert ta.rsi([5.0] * 30) == 50.0
    assert ta.rsi([1.0, 2.0]) is None


def test_ema_and_sma():
    assert ta.sma([1, 2, 3, 4], 2) == 3.5
    assert ta.sma([1], 2) is None
    assert abs(ta.ema([10.0] * 30, 12) - 10.0) < 1e-9


def test_macd_cross_and_sign():
    flat_then_up = [100.0] * 40 + [100.0 + 2 * i for i in range(1, 6)]
    m = ta.macd(flat_then_up)
    assert len(m) == len(flat_then_up) - 33 and m[-1][0] > 0 and m[-1][2] > 0
    assert ta.macd([1.0] * 30) == []
    down = [200.0 - i for i in range(60)]
    assert ta.summarize(down[-1], _k(down), _k(down))["macd_4h"]["state"] == "bear"
    # long slide, then a sharp bounce on the last bar flips the histogram positive
    turn = [200.0 - i for i in range(50)] + [150.0, 149.0, 170.0]
    assert ta._macd_read(turn, turn[-1])["state"] == "bull_cross"


def test_bollinger_and_volume_ratio():
    mid, up, lo = ta.bollinger([10.0, 12.0] * 10)
    assert mid == 11.0 and abs(up - 13.0) < 1e-9 and abs(lo - 9.0) < 1e-9
    assert ta.bollinger([1.0] * 5) is None
    k = [{"t": i, "c": 1, "v": 10, "close_t": i} for i in range(25)]
    k[-1]["v"] = 30
    assert ta.volume_ratio(k) == 3.0
    k.append({"t": 99, "c": 1, "v": 1, "close_t": time.time() + 3600})     # still-forming candle is ignored
    assert ta.volume_ratio(k) == 3.0
    assert ta.volume_ratio(k[:5]) is None
    s = ta.summarize(10.0, [dict(x, h=1, l=1, o=1) for x in k], [])
    assert s["vol_ratio_1d"] == 3.0


def test_derivatives_reads_open_interest_and_funding():
    oi = [{"t": i, "oi": 100.0 + i, "oi_usd": 1.0} for i in range(25)][::-1]       # order-independent
    d = ta.derivatives(oi, [{"t": 1, "rate": -0.0001}, {"t": 2, "rate": 0.0003}, {"t": 3, "rate": 0.0003}])
    assert d["oi_change_4h"] == round(124 / 120 - 1, 4) and d["oi_change_24h"] == 0.24
    assert d["funding_avg_3"] == round(0.0005 / 3, 6) and d["funding_positive_streak"] == 2
    assert ta.derivatives([], []) == {}


def test_summarize_reads_regimes():
    up = [100 + i for i in range(60)]
    s = ta.summarize(up[-1] + 1, _k(up), _k(up))
    assert s["trend_1d"] == "up" and s["trend_4h"] == "up" and s["regime"] == "uptrend"
    assert s["rsi14_1d"] > 70 and s["atr14_pct"] > 0 and s["vs_sma20"] > 0
    assert s["macd_1d"]["state"] == "bull" and s["bb_1d"]["pct_b"] > 0.9 and s["vol_ratio_1d"] == 1.0
    down = [200 - i for i in range(60)]
    assert ta.summarize(down[-1] - 1, _k(down), _k(down))["regime"] == "downtrend"
    # daily uptrend, 4h dipping below its averages: a pullback inside the trend
    h4 = [100 + i for i in range(40)] + [140 - 1.5 * i for i in range(20)]
    assert ta.summarize(110, _k([50 + i for i in range(60)]), _k(h4))["regime"] == "pullback"
    assert ta.summarize(1.0, _k([1.0] * 5), _k([1.0] * 5)) == {}


def test_breadth():
    b = ta.breadth({"BTC": {"trend_1d": "down", "regime": "downtrend", "rsi14_1d": 30},
                    "ETH": {"trend_1d": "up"}, "SOL": {"trend_1d": "down"}, "X": {"price": 1}})
    assert b["assets"] == 3 and b["downtrend_share"] == 0.67 and b["btc_regime"] == "downtrend"
    assert ta.breadth({}) is None


# ---------------------------------------------------------------- speed
def test_candles_cached_between_rounds_and_trades(svc):
    svc.market_snapshot(max_age=0)
    n = svc.client.kline_calls
    assert n == 2 * len(svc.market_snapshot(max_age=0)[1])     # 1d + 4h per asset, once
    svc.market_snapshot(max_age=0)                              # prices refreshed, candles reused
    svc.execute("BUY", "BTCUSDT", 50, via="manual")
    assert svc.client.kline_calls == n
    svc._kline_cache.clear()
    svc.market_snapshot(max_age=0)
    assert svc.client.kline_calls == 2 * n


def test_live_price_still_fresh_with_cached_candles(svc):
    svc.market_snapshot(max_age=0)
    svc.client.prices_map["BTCUSDT"] = 50000.0
    prices, market, btc_change, _ = svc.market_snapshot(max_age=0)
    assert prices["BTC"] == 50000.0 and btc_change < -0.3


def test_cycle_records_stage_timing_and_indicators(svc):
    p = use(svc, plan())
    c = svc.run_cycle("manual")
    assert c.status == "done"
    assert {"market_ms", "account_ms", "model_ms", "execute_ms"} <= set(c.timing)
    btc = c.context["market"]["BTC"]
    assert {"trend_1d", "trend_4h", "rsi14_1d", "atr14_pct", "regime"} <= set(btc)
    assert len(btc["daily_closes_14d"]) == 7                    # trimmed for the prompt
    assert c.context["market_breadth"]["assets"] >= 1
    prompt = p.calls[0][1][0]["content"]
    assert "\n " not in prompt.split("<data>")[1]                 # compact JSON, no indentation
    assert "track_record" not in c.context                       # no AI trades yet


# ---------------------------------------------------------------- track record
def _old_order(svc, side, symbol, price, age, cycle_id="c1"):
    return svc.db.add(Order(ts=time.time() - age, day="x", mode="paper", symbol=symbol, side=side, quote_usd=100,
                            qty=100 / price, price=price, status="filled", via="auto", cycle_id=cycle_id))


def test_track_record_scores_trades_and_stores_outcomes(svc):
    svc.client.horizon_px = {"BTCUSDT": 88000.0, "ETHUSDT": 2400.0, "SOLUSDT": 150.0}
    _old_order(svc, "BUY", "BTCUSDT", 80000, 8 * 86400)     # +10%: win at both horizons
    _old_order(svc, "BUY", "ETHUSDT", 2500, 2 * 86400)      # -4%: loss
    _old_order(svc, "SELL", "SOLUSDT", 150, 2 * 86400)      # flat: inside the fee, not a win
    _old_order(svc, "BUY", "BTCUSDT", 80000, 3600)          # too recent to score
    svc.db.add(Order(ts=time.time() - 3 * 86400, day="x", mode="paper", symbol="ETHUSDT", side="BUY", quote_usd=50,
                     qty=1, price=2500, status="filled", via="manual"))          # manual: not the AI's
    t = svc.track.compute()
    assert t["trades"] == 4 and t["pending_24h"] == 1
    assert t["24h"] == {"evaluated": 3, "wins": 1, "win_rate": 0.333, "avg_return": round((0.1 - 0.04 + 0) / 3, 4)}
    assert t["7d"]["evaluated"] == 1 and t["7d"]["wins"] == 1
    assert t["by_side_24h"]["SELL"]["evaluated"] == 1
    assert t["recent"][0]["symbol"] == "BTC" and "ret_24h" not in t["recent"][0] and "ret_now" in t["recent"][0]
    calls = svc.client.kline_calls
    svc.track.compute()
    assert svc.client.kline_calls == calls                  # horizon prices fetched once, then stored


def test_track_record_skips_rebalancing_trades(svc):
    c = svc.db.add(Cycle(mode="paper", risk=[{"symbol": "BTCUSDT", "source": "rebalance"}]))
    _old_order(svc, "BUY", "BTCUSDT", 80000, 2 * 86400, cycle_id=c.id)
    assert svc.track.compute()["trades"] == 0


def test_track_record_reaches_the_model(svc):
    svc.client.horizon_px = {"ETHUSDT": 2000.0}
    _old_order(svc, "BUY", "ETHUSDT", 2500, 2 * 86400)
    p = use(svc, plan())
    c = svc.run_cycle("manual")
    tr = c.context["track_record"]
    assert tr["24h"]["win_rate"] == 0 and tr["recent"][0]["ret_24h"] == -0.2
    assert '"track_record"' in p.calls[0][1][0]["content"]
    assert "track_record" in p.calls[0][0]                    # the system prompt says how to use it

