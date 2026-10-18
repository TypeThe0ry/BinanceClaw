"""Auto-subscribing every idle asset to Simple Earn flexible products (on by default)."""
import pytest

from binanceclaw.config import Settings

from .conftest import plan, use


def test_defaults_are_on_and_flexible_for_every_asset():
    e = Settings().earn
    assert e.enabled and e.auto_all


def _buy_btc(svc, usd=300):
    svc.update_settings({"limits": {"auto_execute_max_usd": 500, "max_order_usd": 500}})
    use(svc, plan(("BUY", "BTCUSDT", usd)))
    assert svc.run_cycle("manual").risk[0]["decision"] == "execute"


def test_sweep_subscribes_every_idle_asset_paper(svc):
    _buy_btc(svc)
    btc = svc.broker.balances()["BTC"]
    msgs = svc.earn.sweep()
    assert any(m.startswith("BTC") for m in msgs) and any(m.startswith("USDT") for m in msgs)
    pos = svc.earn.positions(0)
    assert pos["BTC"]["amount"] == pytest.approx(btc, abs=1e-8)
    assert svc.broker.balances().get("BTC", 0) < 1e-8
    assert svc.broker.balances()["USDT"] == pytest.approx(svc.settings.earn.keep_liquid_usd, abs=0.01)
    prices, *_ = svc.market_snapshot(0)
    assert svc.portfolio(prices).balances["BTC"] == pytest.approx(btc, abs=1e-8)   # AI still sees its BTC


def test_locked_and_small_balances_stay_in_spot(svc):
    _buy_btc(svc)
    btc = svc.broker.balances()["BTC"]
    svc.update_settings({"capital": {"locked": {"BTC": btc}}})
    svc.earn.sweep()
    assert "BTC" not in svc.earn.positions(0)
    svc.update_settings({"capital": {"locked": {}}, "earn": {"min_move_usd": 1000}})
    svc.earn.sweep()
    assert "BTC" not in svc.earn.positions(0)                      # ~$300 is below the minimum move


def test_sell_redeems_from_earn_first(svc):
    _buy_btc(svc)
    svc.earn.sweep()
    assert svc.broker.balances().get("BTC", 0) < 1e-8
    svc.update_settings({"limits": {"allow_sell": True}})
    use(svc, plan(("SELL", "BTCUSDT", 100)))
    r = svc.run_cycle("manual").risk[0]
    assert r["decision"] == "approve"                              # sells always need a person
    ok, msg = svc.resolve_approval(r["approval_id"], True, "web")
    assert ok, msg
    left = svc.earn.positions(0)["BTC"]["amount"]
    assert 0.0024 < left < 0.0025                                  # only the ~$100 sold was redeemed


def test_auto_all_off_only_touches_chosen_stablecoins(svc):
    _buy_btc(svc)
    svc.update_settings({"earn": {"auto_all": False}})
    svc.earn.sweep()
    assert set(svc.earn.positions(0)) == {"USDT"}
    assert svc.broker.balances()["BTC"] > 0


def test_disabled_sweeps_nothing(svc):
    svc.update_settings({"earn": {"enabled": False}})
    assert svc.earn.sweep() == []
    assert svc.earn.positions(0) == {}


def test_live_sweep_skips_assets_without_a_product(svc):
    svc.settings_store.update({"mode": "live"}, allow_mode_change=True)
    svc._rebuild()
    svc.client.balances = lambda: {"USDT": 1000.0, "BTC": 0.01, "XYZ": 5000.0}
    svc.client.prices_map["XYZUSDT"] = 1.0
    products = svc.client.earn_products
    svc.client.earn_products = lambda a: [] if a == "XYZ" else [{**products(a)[0], "minPurchaseAmount": "0.0001"}]
    svc.earn.sweep()
    assert set(svc.client.earn) == {"USDT", "BTC"}
    assert svc.client.earn["BTC"] == pytest.approx(0.01)
    assert svc.client.earn["USDT"] == pytest.approx(800)
