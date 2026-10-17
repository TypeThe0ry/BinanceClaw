import pytest

from binanceclaw.config import RISK_PRESETS, Settings
from binanceclaw.risk.engine import Portfolio, RiskState, evaluate

from .conftest import plan, use

P = {"BTC": 80000.0, "ETH": 2500.0, "QQQB": 700.0, "NVDAB": 200.0, "SOL": 150.0}


def ev(s, balances, actions, **st):
    return evaluate([{"side": a[0], "symbol": a[1], "quote_usd": a[2]} for a in actions], Portfolio(balances, P, s), RiskState(**st))


# ---------------------------------------------------------------- budget
def test_budget_caps_buys():
    s = Settings.model_validate({"capital": {"budget_usd": 1000}})
    v = ev(s, {"USDT": 10000}, [("BUY", "BTCUSDT", 800)], deployed_usd=900)[0]
    assert v.approved_usd == 100 and any("预算" in c.detail for c in v.checks)
    assert ev(s, {"USDT": 10000}, [("BUY", "BTCUSDT", 50)], deployed_usd=1000)[0].decision == "reject"


def test_budget_shared_across_actions_in_one_plan():
    s = Settings.model_validate({"capital": {"budget_usd": 300}})
    vs = ev(s, {"USDT": 10000}, [("BUY", "BTCUSDT", 200), ("BUY", "ETHUSDT", 200)])
    assert sum(v.approved_usd for v in vs) <= 300


# ---------------------------------------------------------------- asset selection / ai mode
def test_only_selected_assets_are_tradable():
    s = Settings.model_validate({"allocation": {"enabled": ["BTC"]}})
    assert s.symbols() == ["BTCUSDT"]
    assert ev(s, {"USDT": 10000}, [("BUY", "ETHUSDT", 50)])[0].decision == "reject"
    assert ev(s, {"USDT": 10000}, [("BUY", "BTCUSDT", 50)])[0].decision == "execute"


def test_targets_mode_ignores_tokens_without_target():
    s = Settings.model_validate({"allocation": {"enabled": ["BTC", "SOL"], "mode": "targets"}})
    assert s.tradable_tokens() == ["BTC"]


def test_ai_mode_uses_per_asset_ceiling():
    s = Settings.model_validate({"allocation": {"enabled": ["BTC", "SOL"], "mode": "ai", "max_asset_weight": 0.3}})
    assert set(s.symbols()) == {"BTCUSDT", "SOLUSDT"}
    # SOL already 28% of $10k -> at most ~2% more
    v = ev(s, {"USDT": 7200, "SOL": 2800 / 150}, [("BUY", "SOLUSDT", 800)])[0]
    assert v.approved_usd <= 200 + 1
    assert set(s.group_targets()) == {"BTC", "SOL", "STABLE"}


def test_bad_tokens_rejected_by_model():
    with pytest.raises(ValueError):
        Settings.model_validate({"allocation": {"enabled": ["BTC;DROP"]}})


# ---------------------------------------------------------------- presets
def test_presets_are_within_hard_bounds_and_auto_custom(svc):
    for name in RISK_PRESETS:
        s = svc.apply_preset(name)
        assert s.risk_preset == name and s.limits.auto_execute_max_usd <= s.limits.max_order_usd
    assert svc.settings.limits.max_order_usd == RISK_PRESETS["aggressive"]["limits"]["max_order_usd"]
    svc.update_settings({"limits": {"max_order_usd": 900}})
    assert svc.settings.risk_preset == "custom"
    with pytest.raises(ValueError):
        svc.apply_preset("yolo")


# ---------------------------------------------------------------- service: locked & managed view
def test_locked_assets_are_invisible_to_ai(svc):
    svc.update_settings({"capital": {"locked": {"USDT": 4000}}})
    p = use(svc, plan(("BUY", "BTCUSDT", 100)))
    svc.run_cycle("manual")
    ctx = svc.db.all(__import__("sqlmodel").select(__import__("binanceclaw.db", fromlist=["Cycle"]).Cycle))[-1].context
    assert ctx["portfolio"]["balances"]["USDT"] == pytest.approx(6000, abs=1)
    assert "4000" not in p.calls[0][1][0]["content"].split("<data>")[1].split("portfolio")[1][:200]


def test_deselected_holdings_are_out_of_scope(svc):
    svc.broker._paper_apply({"ETH": 1.0})
    svc.update_settings({"allocation": {"enabled": ["BTC"]}})
    prices, *_ = svc.market_snapshot(max_age=0)
    assert "ETH" not in svc.portfolio(prices).balances
    assert "BTC" in prices                      # BTC always priced for the drawdown breaker


def test_validate_tokens_against_exchange(svc):
    svc.update_settings({"allocation": {"enabled": ["BTC", "SOL"], "mode": "ai"}})
    assert svc.settings.tradable_tokens() == ["BTC", "SOL"]
    with pytest.raises(ValueError):
        svc.update_settings({"allocation": {"enabled": ["BTC", "NOTACOIN"]}})


def test_ai_mode_cycle_end_to_end(svc):
    svc.update_settings({"allocation": {"enabled": ["BTC", "SOL"], "mode": "ai"}})
    p = use(svc, plan(("BUY", "SOLUSDT", 150), ("BUY", "ETHUSDT", 50)))
    c = svc.run_cycle("manual")
    assert [r["decision"] for r in c.risk] == ["execute", "reject"]   # ETH not selected
    assert '"mode": "ai"' in p.calls[0][1][0]["content"]



def test_market_search(svc):
    rows = svc.markets("SO")
    assert [r["token"] for r in rows] == ["SOL"]
    allr = svc.markets("")
    assert {r["token"] for r in allr} >= {"BTC", "ETH", "SOL"}
    assert allr[0]["enabled"] is True                      # selected tokens first


def test_any_spot_token_can_be_added_to_targets(svc):
    svc.update_settings({"allocation": {"enabled": ["BTC", "SOL"]},
                         "categories": {**{k: v.model_dump() for k, v in svc.settings.categories.items() if k != "ETH"},
                                        "ALTS": {"tokens": {"SOL": 0.05}, "band": [0.0, 0.1]}}})
    assert svc.settings.tradable_tokens() == ["BTC", "SOL"]
    assert svc.settings.group_of("SOL") == "ALTS"
