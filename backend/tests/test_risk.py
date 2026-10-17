import pytest

from binanceclaw.config import Settings
from binanceclaw.risk import gate
from binanceclaw.risk.engine import Portfolio, RiskState, evaluate

from .conftest import SAFE_RESTRICTIONS

P = {"BTC": 80000.0, "ETH": 2500.0, "QQQB": 700.0, "NVDAB": 200.0}


def ev(balances, actions, settings=None, **st):
    s = settings or Settings()
    return evaluate([{"side": a[0], "symbol": a[1], "quote_usd": a[2]} for a in actions],
                    Portfolio(balances, P, s), RiskState(**st))


def test_large_buy_capped_and_needs_approval():
    v = ev({"USDT": 10000}, [("BUY", "BTCUSDT", 5000)])[0]
    assert v.decision == "approve" and v.approved_usd == 800


def test_small_buy_auto_executes():
    v = ev({"USDT": 10000}, [("BUY", "BTC-USDT", 150)])[0]
    assert v.decision == "execute" and v.approved_usd == 150 and v.symbol == "BTCUSDT"


def test_symbol_formats_normalised():
    for sym in ("BTC", "btc/usdt", "BTC_USDT"):
        assert ev({"USDT": 10000}, [("BUY", sym, 50)])[0].decision == "execute"


@pytest.mark.parametrize("sym", ["DOGEUSDT", "BTCUSDT_PERP", "BTCUPUSDT", "ETHBTC", "PEPE"])
def test_non_whitelisted_rejected(sym):
    assert ev({"USDT": 10000}, [("BUY", sym, 50)])[0].decision == "reject"


def test_sell_rejected_by_default():
    v = ev({"USDT": 1000, "BTC": 1}, [("SELL", "BTCUSDT", 100)])[0]
    assert v.decision == "reject" and "只买不卖" in v.checks[-1].name


def test_sell_when_allowed_always_needs_approval():
    s = Settings()
    s.limits.allow_sell = True
    v = ev({"USDT": 1000, "BTC": 1}, [("SELL", "BTCUSDT", 50)], settings=s)[0]
    assert v.decision == "approve"


def test_daily_order_limit():
    assert ev({"USDT": 10000}, [("BUY", "BTCUSDT", 50)], today_orders=3)[0].decision == "reject"


def test_daily_notional_cap():
    v = ev({"USDT": 10000}, [("BUY", "BTCUSDT", 800)], today_orders=1, today_notional=2300)[0]
    assert v.approved_usd == 100


def test_stable_floor():
    # 85% BTC, 15% USDT: nothing spendable
    v = ev({"BTC": 0.10625, "USDT": 1500}, [("BUY", "ETHUSDT", 100)])[0]
    assert v.decision == "reject"


def test_category_band_upper_cap():
    # BTC at 69% of $10k -> at most ~1% more
    v = ev({"BTC": 0.08625, "USDT": 3100}, [("BUY", "BTCUSDT", 500)])[0]
    assert v.approved_usd <= 100 + 1


def test_multiple_actions_do_not_double_spend():
    vs = ev({"USDT": 1000}, [("BUY", "BTCUSDT", 600), ("BUY", "ETHUSDT", 600)])
    total = sum(v.approved_usd for v in vs)
    assert total <= 1000 - 0.15 * 1000


def test_max_actions_per_plan():
    vs = ev({"USDT": 100000}, [("BUY", "BTCUSDT", 20)] * 5)
    assert [v.decision for v in vs][3:] == ["reject", "reject"]


def test_pause_blocks_everything():
    assert ev({"USDT": 10000}, [("BUY", "BTCUSDT", 50)], paused=True, pause_reason="x")[0].decision == "reject"
    assert ev({"USDT": 10000}, [("BUY", "BTCUSDT", 50)], btc_change_7d=-0.3)[0].decision == "reject"
    assert ev({"USDT": 10000}, [("BUY", "BTCUSDT", 50)], anomaly="stale")[0].decision == "reject"
    assert ev({"USDT": 10000}, [("BUY", "BTCUSDT", 50)], btc_change_7d=-0.2)[0].decision == "execute"


@pytest.mark.parametrize("bad", [-5, 0, float("inf"), float("nan")])
def test_bad_amounts(bad):
    assert ev({"USDT": 10000}, [("BUY", "BTCUSDT", bad)])[0].decision == "reject"


def test_settings_hard_bounds_clamp():
    s = Settings.model_validate({"limits": {"max_order_usd": 10**9, "stable_floor": 0, "auto_execute_max_usd": 10**9}})
    assert s.limits.max_order_usd == 5000 and s.limits.stable_floor == 0.05
    assert s.limits.auto_execute_max_usd <= s.limits.max_order_usd


def test_gate_blocks_current_unsafe_key():
    unsafe = dict(SAFE_RESTRICTIONS, enableFutures=True, enableInternalTransfer=True, permitsUniversalTransfer=True)
    b = gate.blockers(unsafe)
    assert len(b) == 3 and any("合约" in x for x in b)
    assert gate.blockers(SAFE_RESTRICTIONS) == []
    assert gate.blockers({}) != []          # missing fields never pass


def test_splitting_to_dodge_approval_is_capped():
    vs = ev({"USDT": 10000}, [("BUY", "BTCUSDT", 200), ("BUY", "BTCUSDT", 200), ("BUY", "ETHUSDT", 200)])
    assert [v.decision for v in vs] == ["execute", "execute", "approve"]
    v = ev({"USDT": 10000}, [("BUY", "BTCUSDT", 150)], today_orders=2, today_auto_notional=350)[0]
    assert v.decision == "approve"
