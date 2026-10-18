"""AI 自主分配 (all assets / whitelist / blacklist) and 手动分配 with automatic rebalancing."""
import pytest

from binanceclaw.config import ConfirmationRequired, Settings
from binanceclaw.db import Approval
from binanceclaw.exchange.binance_spot import ExchangeError
from binanceclaw.notify.telegram import TelegramBot
from binanceclaw.risk.engine import Portfolio
from binanceclaw.risk.rebalance import drift, rebalance_actions

from .conftest import plan, use

TRADING = {"BTC", "ETH", "QQQB", "NVDAB", "SOL"}         # TON is listed but suspended


# ---------------------------------------------------------------- AI 自主分配
def test_ai_mode_defaults_to_every_spot_asset(svc):
    assert svc.settings.allocation.universe == "all"
    svc.update_settings({"allocation": {"mode": "ai"}})
    assert set(svc.settings.tradable_tokens()) == TRADING          # not limited to ``enabled``
    use(svc, plan(("BUY", "SOLUSDT", 150)))
    c = svc.run_cycle("manual")
    assert c.risk[0]["decision"] == "execute"
    assert c.context["allocation"]["scope"].startswith("主人允许交易币安全部现货资产")


def test_holdings_come_first_and_the_round_is_capped(svc, monkeypatch):
    monkeypatch.setattr(type(svc.broker), "balances", lambda self: {"USDT": 5000.0, "TON": 1000.0, "SOL": 0.001})
    svc.update_settings({"allocation": {"mode": "ai", "ai_candidates": 5}})
    toks = svc.settings.tradable_tokens()
    assert toks[0] == "TON"                         # held (even while suspended) so the AI can manage it
    assert len(toks) == 5                           # SOL dust (< $1) does not take a slot of its own
    assert svc.settings.allocation.ai_candidates == 5


def test_blacklist_excludes_assets(svc):
    svc.update_settings({"allocation": {"mode": "ai", "universe": "blacklist", "blocked": ["eth", "SOL"]}})
    assert set(svc.settings.tradable_tokens()) == TRADING - {"ETH", "SOL"}
    assert {r["token"] for r in svc.markets("") if r["blocked"]} == {"ETH", "SOL"}
    use(svc, plan(("BUY", "ETHUSDT", 100)))
    assert svc.run_cycle("manual").risk[0]["decision"] == "reject"


def test_whitelist_only_trades_the_list(svc):
    svc.update_settings({"allocation": {"mode": "ai", "universe": "whitelist", "enabled": ["BTC"], "blocked": ["BTC"]}})
    assert svc.settings.tradable_tokens() == ["BTC"]               # the blacklist only applies to its own mode


def test_market_list_unavailable_falls_back_to_the_explicit_list(svc, monkeypatch):
    def down():
        raise ExchangeError("down")
    monkeypatch.setattr(svc.client, "spot_markets", down)
    svc.update_settings({"allocation": {"mode": "ai", "enabled": ["BTC", "ETH"], "blocked": ["ETH"]}})
    assert svc.settings.tradable_tokens() == ["BTC"]


def test_saved_ai_mode_keeps_its_explicit_list():
    old = Settings.model_validate({"allocation": {"mode": "ai", "enabled": ["BTC", "SOL"]}})
    assert old.allocation.universe == "whitelist" and old.tradable_tokens() == ["BTC", "SOL"]
    assert Settings.model_validate({"allocation": {"mode": "targets"}}).allocation.universe == "all"


@pytest.mark.parametrize("patch", [
    {"allocation": {"universe": "blacklist"}}, {"allocation": {"blocked": ["DOGE"]}}, {"allocation": {"enabled": ["BTC"]}},
    {"allocation": {"auto_rebalance": False}}, {"allocation": {"rebalance_drift": 0.1}},
])
def test_allocation_changes_need_confirmation(svc, patch):
    svc.update_settings({"allocation": {"auto_rebalance": True}})
    with pytest.raises(ConfirmationRequired):
        svc.update_settings(patch, guard=True)


def test_telegram_switches_scope_and_blacklist(svc):
    bot = TelegramBot.__new__(TelegramBot)
    bot.svc, bot._confirms, sent = svc, {}, []
    bot.send = lambda text, buttons=None: sent.append((text, buttons))

    def confirm():
        bot._resolve_confirm(sent[-1][1][0][0][1].split(":")[1], True)

    bot.handle_text("/assets ai")
    confirm()
    bot.handle_text("/assets blacklist")
    assert "黑名单" in sent[-1][0]
    confirm()
    bot.handle_text("/assets block DOGE,PEPE")
    confirm()
    bot.handle_text("/assets unblock PEPE")
    confirm()
    al = svc.settings.allocation
    assert (al.mode, al.universe, al.blocked) == ("ai", "blacklist", ["DOGE"])
    bot.handle_text("/assets")
    assert sent[-1][0] == "分配：AI 自主分配 · 范围 黑名单 · 黑名单 DOGE"


# ---------------------------------------------------------------- 手动分配 + 再平衡
def _pf(balances, **alloc):
    s = Settings.model_validate({"allocation": alloc, "limits": {"allow_sell": True}})
    return Portfolio(balances, {"BTC": 80000.0, "ETH": 2500.0, "QQQB": 700.0, "NVDAB": 200.0}, s)


def test_drift_and_rebalance_trades():
    # default targets: BTC 60%, ETH 5%, QQQB 7%, NVDAB 3%, USDT 25%
    pf = _pf({"BTC": 0.1, "ETH": 0.2, "USDT": 1500.0})         # BTC 80%, ETH 5%, USDT 15% of $10,000
    d = drift(pf)
    assert d["BTC"] == {"target": 0.6, "actual": 0.8, "drift": 0.2}
    acts = rebalance_actions(pf)
    assert [(a["side"], a["symbol"], a["quote_usd"]) for a in acts] == [("SELL", "BTCUSDT", 2000.0), ("BUY", "QQQBUSDT", 700.0)]
    assert all(a["source"] == "rebalance" for a in acts)        # NVDAB 3% and ETH are within the 5% threshold
    assert rebalance_actions(pf, skip={"BTC"})[0]["symbol"] == "QQQBUSDT"
    assert rebalance_actions(_pf(pf.balances, rebalance_drift=0.25)) == []
    assert rebalance_actions(_pf(pf.balances, auto_rebalance=False)) == []
    assert rebalance_actions(_pf(pf.balances, mode="ai")) == []


def test_no_rebalancing_sells_when_selling_is_off():
    pf = _pf({"BTC": 0.1, "ETH": 0.2, "USDT": 1500.0})
    pf.settings.limits.allow_sell = False
    assert [a["side"] for a in rebalance_actions(pf)] == ["BUY"]


def test_cycle_rebalances_to_targets_before_the_models_plan(svc):
    svc.update_settings({"allocation": {"auto_rebalance": True}})
    p = use(svc, plan(("BUY", "BTCUSDT", 100), ("BUY", "ETHUSDT", 100)), plan())
    c = svc.run_cycle("manual")
    reb = c.context["allocation"]["rebalance"]
    assert reb["auto"] == ["BTC", "QQQB"] and reb["drift_threshold"] == 0.05
    assert '"rebalance"' in p.calls[0][1][0]["content"]
    rows = [(r["source"], r["symbol"], r["decision"]) for r in c.risk]
    assert rows == [("rebalance", "BTCUSDT", "approve"),       # $6,000 under target, capped at the $800 order limit
                    ("rebalance", "QQQBUSDT", "approve"),
                    ("ai", "ETHUSDT", "execute"),
                    ("ai", "BTCUSDT", "reject")]               # the model's BTC buy is superseded this round
    assert c.risk[0]["approved_usd"] == 800
    assert c.risk[3]["reason"] == "本轮由自动再平衡处理这个资产"

    # the next round does not propose the same trades again while they wait for approval
    c2 = svc.run_cycle("manual")
    assert [r for r in c2.risk if r["source"] == "rebalance"] == []
    assert len(svc.db.all(Approval.__table__.select())) == 2
