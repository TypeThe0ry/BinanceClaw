import pytest

from binanceclaw.db import PriceAlert

from .conftest import plan, use


def _buy(svc, sym, usd):
    ok, msg, _ = svc.execute("BUY", sym, usd, via="approved")
    assert ok, msg


def test_performance_avg_cost_and_benchmark(svc):
    _buy(svc, "BTCUSDT", 400)
    svc.client.prices_map["BTCUSDT"] = 88000.0      # +10%
    svc._mkt_cache = None
    pf = svc.performance()
    btc = pf["assets"]["BTC"]
    assert btc["avg_cost"] == pytest.approx(80000 * 1.0005 / 0.999, rel=1e-3)   # ask+slippage, fee in base
    assert pf["invested"] == pytest.approx(400, abs=1)   # lot-size rounding
    assert 0.08 < btc["unrealized_pct"] < 0.10
    assert pf["benchmark"]["roi"] == pytest.approx(0.10, abs=0.01)   # bought BTC itself -> matches


def test_benchmark_for_non_btc_buy(svc):
    _buy(svc, "ETHUSDT", 250)
    svc.client.prices_map["BTCUSDT"] = 96000.0      # BTC +20%, ETH flat
    svc._mkt_cache = None
    pf = svc.performance()
    assert pf["benchmark"]["roi"] == pytest.approx(0.20, abs=0.01)
    assert pf["roi"] < 0.0                           # ETH flat minus fees


def test_manual_alert_triggers_once(svc):
    events = []
    svc.events.on(lambda k, d: events.append((k, d)))
    a = svc.add_alert("BTC", "below", 79000, "test")
    assert svc.check_alerts() == 0
    svc.client.prices_map["BTCUSDT"] = 78500.0
    assert svc.check_alerts() == 1
    assert svc.check_alerts() == 0
    assert svc.db.get(PriceAlert, a.id).status == "triggered"
    assert [k for k, _ in events].count("price_alert") == 1


def test_alert_validation(svc):
    with pytest.raises(ValueError):
        svc.add_alert("DOGE", "below", 1)
    with pytest.raises(ValueError):
        svc.add_alert("BTC", "sideways", 1)
    with pytest.raises(ValueError):
        svc.add_alert("BTC", "below", -5)


def test_ai_sets_and_replaces_its_own_alerts(svc):
    p1 = plan()
    p1["alerts"] = [{"symbol": "BTCUSDT", "op": "below", "price": 76000, "note": "跌破 30 日低点"},
                    {"symbol": "ETHUSDT", "op": "above", "price": 999999, "note": "absurd"},          # >50% away
                    {"symbol": "BTCUSDT", "op": "below", "price": 85000, "note": "would fire now"}]  # wrong side
    p2 = plan()
    p2["alerts"] = [{"symbol": "BTCUSDT", "op": "below", "price": 74000, "note": "new level"}]
    use(svc, p1, p2)
    svc.run_cycle("manual")
    act = svc.active_alerts()
    assert [(a.price, a.source) for a in act] == [(76000, "ai")]
    svc.run_cycle("manual")
    assert [a.price for a in svc.active_alerts()] == [74000]


def test_manual_alerts_survive_ai_replacement(svc):
    svc.add_alert("BTC", "above", 90000)
    p = plan()
    p["alerts"] = [{"symbol": "BTC", "op": "below", "price": 76000}]
    use(svc, p)
    svc.run_cycle("manual")
    assert sorted(a.source for a in svc.active_alerts()) == ["ai", "manual"]



def test_deselected_holding_still_valued_in_pnl_and_account(svc):
    ok, msg, _ = svc.execute("BUY", "QQQBUSDT", 100, via="approved")
    assert ok, msg
    svc.update_settings({"allocation": {"enabled": ["BTC", "SOL"]}})
    svc._mkt_cache = None
    pf = svc.performance()
    assert pf["assets"]["QQQB"]["value"] is not None and abs(pf["roi"]) < 0.02
    ov = svc.overview()
    assert ov["account_values"].get("QQQB", 0) > 90
    assert "QQQB" not in ov["values"]           # not managed by the AI any more


def test_locking_does_not_create_fake_pnl_in_equity(svc):
    from .conftest import plan, use
    use(svc, plan(), plan())
    svc.run_cycle("manual")
    svc.update_settings({"capital": {"locked": {"USDT": 4000}}})
    svc.run_cycle("manual")
    pts = svc.equity_series()
    assert abs(pts[-1]["total"] - pts[0]["total"]) < 1


def test_equity_series_only_current_mode(svc):
    from binanceclaw.db import Cycle

    from .conftest import plan, use
    use(svc, plan())
    svc.run_cycle("manual")
    paper = svc.equity_series()
    assert paper and all(p["mode"] == "paper" for p in paper)
    # a live round on a much smaller real account must not be the base of paper's day change, and vice versa
    svc.db.add(Cycle(mode="live", status="done", context={"portfolio": {"total_usd": 50}, "account_total_usd": 50}))
    assert svc.equity_series() == paper
    svc.settings_store.update({"mode": "live"}, allow_mode_change=True)
    live = svc.equity_series()
    assert [p["total"] for p in live] == [50] and live[0]["mode"] == "live"
