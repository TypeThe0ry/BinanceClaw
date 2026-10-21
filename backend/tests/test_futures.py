"""USDⓈ-M futures: risk checks, paper ledger, approvals, live stop-loss safety, and Simple Earn."""
import pytest
from sqlmodel import select

from binanceclaw.db import FuturesPosition, FuturesTrade
from binanceclaw.exchange.binance_spot import ExchangeError

from .conftest import plan, use


def on(svc, **kw):
    svc.update_settings({"futures": {"enabled": True, **kw}})


def long_btc(margin=100, lev=3, stop=78000, **kw):
    return {"op": "OPEN_LONG", "symbol": "BTCUSDT", "margin_usd": margin, "leverage": lev, "stop_loss": stop, **kw}


def run(svc, *futures):
    use(svc, plan(futures=list(futures)))
    return svc.run_cycle("manual")


def test_futures_off_by_default_rejects(svc):
    c = run(svc, long_btc())
    assert c.risk[0]["decision"] == "reject"
    assert not svc.fbroker.open_rows()


def test_open_needs_approval_and_stop(svc):
    on(svc)
    c = run(svc, long_btc(), {"op": "OPEN_LONG", "symbol": "ETH", "margin_usd": 50, "leverage": 2})
    assert c.risk[0]["decision"] == "approve"
    assert c.risk[1]["decision"] == "reject" and "止损" in c.risk[1]["reason"]
    assert not svc.fbroker.open_rows()
    aid = c.risk[0]["approval_id"]
    ok, msg = svc.resolve_approval(aid, True, "web")
    assert ok, msg
    (p,) = svc.fbroker.open_rows()
    assert (p.side, p.symbol, p.leverage, p.stop) == ("LONG", "BTCUSDT", 3, 78000)
    bal = svc.broker.balances()["USDT"]
    assert 9915 < bal < 9925                      # margin (100 floored to the 0.001 BTC lot = ~$80) + fee left USDT


@pytest.mark.parametrize("cfg,act,check", [
    ({}, long_btc(stop=81000), "止损方向"),
    ({}, long_btc(stop=60000), "止损距离"),                                   # 25% away > 8% cap
    ({"max_leverage": 10, "max_stop_distance": 0.2}, long_btc(lev=10, stop=72800), "强平保护"),   # 9% vs ~7.6% safe
    ({}, {"op": "OPEN_SHORT", "symbol": "BTCUSDT", "margin_usd": 50, "leverage": 2, "stop_loss": 79000}, "止损方向"),
    ({}, {**long_btc(), "symbol": "DOGEUSDT"}, "合约白名单"),
    ({"allow_short": False}, {"op": "OPEN_SHORT", "symbol": "BTCUSDT", "margin_usd": 50, "leverage": 2, "stop_loss": 81000}, "做空"),
])
def test_open_rejections(svc, cfg, act, check):
    on(svc, **cfg)
    v = run(svc, act).risk[0]
    assert v["decision"] == "reject", v
    assert v["checks"][-1]["name"] == check, v["checks"]


def test_size_capped_by_loss_and_margin(svc):
    on(svc, max_loss_per_trade_usd=20, max_position_margin_usd=500, max_total_margin_usd=500)
    v = run(svc, long_btc(margin=400, lev=3, stop=76000)).risk[0]          # 5% stop
    # 20 / (3 * (0.05 + 0.001)) = 130.7
    assert v["decision"] == "approve" and 120 < v["margin"] < 131


def test_leverage_capped(svc):
    on(svc, max_leverage=2)
    v = run(svc, long_btc(lev=50, stop=78000)).risk[0]
    assert v["leverage"] == 2


def test_auto_open_small_and_paper_stop_hit(svc):
    on(svc, auto_open=True, auto_open_max_margin_usd=100)
    c = run(svc, long_btc(margin=80))
    assert c.risk[0]["decision"] == "execute", c.risk[0]
    (p,) = svc.fbroker.open_rows()
    svc.fclient.marks_map["BTCUSDT"] = 77500
    svc.fbroker._prem = None
    svc.futures_watch()
    assert not svc.fbroker.open_rows()
    closed = svc.db.get(FuturesPosition, p.id)
    assert closed.status == "closed" and closed.close_reason == "触发止损" and closed.realized < 0
    hits = svc.db.all(select(FuturesTrade).where(FuturesTrade.action == "STOP_HIT"))
    assert len(hits) == 1
    # 0.003 BTC stopped out ~2.6% below entry: about -$6.2 plus fees
    assert 9995 > svc.broker.balances()["USDT"] > 9990


def test_close_reduce_and_tighten_only(svc):
    on(svc, auto_open=True, auto_open_max_margin_usd=200)
    run(svc, long_btc(margin=150))
    (p,) = svc.fbroker.open_rows()
    v = run(svc, {"op": "SET_STOP", "symbol": "BTCUSDT", "stop_loss": 77000}).risk[0]
    assert v["decision"] == "reject" and "收紧" in v["checks"][-1]["name"]
    v = run(svc, {"op": "SET_STOP", "symbol": "BTCUSDT", "stop_loss": 79000}).risk[0]
    assert v["decision"] == "execute"
    assert svc.db.get(FuturesPosition, p.id).stop == 79000
    v = run(svc, {"op": "REDUCE", "symbol": "BTCUSDT", "fraction": 0.5}).risk[0]
    assert v["decision"] == "execute"
    assert svc.db.get(FuturesPosition, p.id).qty == pytest.approx(p.qty / 2, abs=0.001)
    svc.pause("test")
    v = run(svc, {"op": "CLOSE", "symbol": "BTCUSDT"}).risk  # paused: the model is not even called
    assert svc.fbroker.open_rows()
    ok, msg = svc.close_position(p.id, "web")
    assert ok, msg
    assert not svc.fbroker.open_rows()


def test_paused_blocks_open_but_not_close(svc):
    from binanceclaw.risk.futures import evaluate_futures
    on(svc, auto_open=True, auto_open_max_margin_usd=200)
    run(svc, long_btc(margin=100))
    prices, *_ = svc.market_snapshot(0)
    pf = svc.portfolio(prices)
    st = svc.risk_state()
    st.paused, st.pause_reason = True, "manual"
    fv = svc.futures_view(pf, st, fresh=True)
    v1, v2 = evaluate_futures([{"op": "OPEN_LONG", "symbol": "ETHUSDT", "margin_usd": 50, "leverage": 2, "stop_loss": 2450},
                               {"op": "CLOSE", "symbol": "BTCUSDT"}], fv)
    assert v1.decision == "reject" and v2.decision == "execute"


def test_no_second_position_and_futures_equity_counts(svc):
    on(svc, auto_open=True, auto_open_max_margin_usd=200)
    run(svc, long_btc(margin=100))
    v = run(svc, long_btc(margin=100)).risk[0]
    assert v["decision"] == "reject" and "已有" in v["reason"]
    ov = svc.overview()
    assert 75 < ov["values"]["FUTURES"] < 101          # ~$80 after lot rounding
    assert 9990 < ov["total"] < 10001


def test_live_stop_failure_closes_position(svc):
    on(svc)
    svc.broker.mode = svc.fbroker.mode = "live"
    fc = svc.fclient
    fc.protect_error = "server busy"
    fc.available = 0
    with pytest.raises(ExchangeError, match="已立即平仓"):
        svc.fbroker.open("BTCUSDT", "LONG", 100, 3, 78000, None, "cid1", {})
    assert fc.pos["BTCUSDT"] == 0                 # entered, then flattened reduce-only
    assert [c for c in fc.calls if c[0] == "order"][-1][4] is True     # reduce-only
    assert ("MAIN_UMFUTURE", "USDT", pytest.approx(102.37, abs=0.5)) in svc.client.transfers
    assert not svc.fbroker.open_rows()


def test_live_open_sets_isolated_leverage_and_stop_then_restores_missing_stop(svc):
    on(svc)
    svc.broker.mode = svc.fbroker.mode = "live"
    fc = svc.fclient
    row, _fill = svc.fbroker.open("BTCUSDT", "LONG", 100, 3, 78000, 84000, "cid2", {})
    assert ("marginType", "BTCUSDT", "ISOLATED") in fc.calls and ("leverage", "BTCUSDT", 3) in fc.calls
    kinds = {a["orderType"] for a in fc.algos["BTCUSDT"]}
    assert kinds == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    fc.algos["BTCUSDT"] = []                      # stop vanished on the exchange
    svc.fbroker.live_sync()
    assert any(a["orderType"] == "STOP_MARKET" for a in fc.algos["BTCUSDT"])
    fc.pos["BTCUSDT"] = 0                         # stop fired on the exchange
    svc.fbroker.live_sync()
    assert svc.db.get(FuturesPosition, row.id).status == "closed"


def test_live_hedge_mode_uses_position_side(svc):
    on(svc)
    svc.broker.mode = svc.fbroker.mode = "live"
    fc = svc.fclient
    fc.dual = True
    row, _ = svc.fbroker.open("BTCUSDT", "SHORT", 100, 2, 82000, None, "cid3", {})
    assert fc.hpos[("BTCUSDT", "SHORT")] < 0
    assert [c for c in fc.calls if c[0] == "protect"][-1][-1] == "SHORT"
    svc.fbroker.live_sync()                        # stop present -> nothing restored
    assert svc.db.get(FuturesPosition, row.id).status == "open"
    svc.fbroker.close(svc.db.get(FuturesPosition, row.id), 1.0, "test")
    last = [c for c in fc.calls if c[0] == "order"][-1]
    assert last[2] == "BUY" and last[5] == "SHORT" and fc.hpos[("BTCUSDT", "SHORT")] == 0
    assert fc.algos["BTCUSDT"] == []               # our stop was cancelled


def test_live_close_leaves_owner_algo_orders(svc):
    on(svc)
    svc.broker.mode = svc.fbroker.mode = "live"
    fc = svc.fclient
    row, _ = svc.fbroker.open("BTCUSDT", "LONG", 100, 3, 78000, None, "cid4", {})
    fc.algos["BTCUSDT"].append({"algoId": "owner1", "orderType": "TAKE_PROFIT_MARKET", "positionSide": "BOTH"})
    svc.fbroker.close(svc.db.get(FuturesPosition, row.id), 1.0, "test")
    assert [a["algoId"] for a in fc.algos["BTCUSDT"]] == ["owner1"]


def test_live_gate_requires_futures_permissions_when_enabled(svc):
    on(svc)
    chk = svc.live_check()
    assert not chk["ok"] and any("合约" in b for b in chk["blockers"])
    svc.client.restrictions.update(enableFutures=True, permitsUniversalTransfer=True)
    assert svc.live_check()["ok"]


def test_unknown_perp_rejected_in_settings(svc):
    with pytest.raises(ValueError, match="永续"):
        svc.update_settings({"futures": {"symbols": ["BTC", "NOPE"]}})


# ---------------------------------------------------------------- Simple Earn
def test_earn_sweep_and_auto_redeem_paper(svc):
    svc.update_settings({"earn": {"enabled": True, "keep_liquid_usd": 1000}})
    svc.earn.sweep()
    assert svc.broker.balances()["USDT"] == pytest.approx(1000)
    assert svc.earn.positions(0)["USDT"]["amount"] == pytest.approx(9000)
    prices, *_ = svc.market_snapshot(0)
    assert svc.portfolio(prices).balances["USDT"] == pytest.approx(10000)     # AI sees Earn as available
    use(svc, plan(("BUY", "BTCUSDT", 150)))
    c = svc.run_cycle("manual")
    assert c.risk[0]["decision"] == "execute"
    use(svc, plan(("BUY", "ETHUSDT", 180)))
    # drain spot below the next buy: move almost everything to Earn, then buy -> auto redeem
    svc.earn.subscribe("USDT", svc.broker.balances()["USDT"] - 5)
    c = svc.run_cycle("manual")
    assert c.risk[0]["decision"] == "execute", c.risk[0]
    assert "ETH" in svc.broker.balances()


def test_earn_disabled_hides_balances_from_ai(svc):
    svc.update_settings({"earn": {"enabled": False}})
    svc.earn.subscribe("USDT", 4000)
    prices, *_ = svc.market_snapshot(0)
    assert svc.portfolio(prices).balances["USDT"] == pytest.approx(6000)
    assert svc.account_portfolio(prices).balances["USDT"] == pytest.approx(10000)


def test_earn_only_stablecoins():
    from binanceclaw.config import Settings
    with pytest.raises(ValueError, match="稳定币"):
        Settings.model_validate({"earn": {"assets": ["BTC"]}})


def test_db_migration_adds_columns(tmp_path):
    import sqlite3

    from binanceclaw.db import Database
    con = sqlite3.connect(tmp_path / "old.db")
    con.execute("CREATE TABLE approval (id VARCHAR PRIMARY KEY, created FLOAT, expires FLOAT, cycle_id VARCHAR, symbol VARCHAR,"
                " side VARCHAR, quote_usd FLOAT, reason VARCHAR, invalidation VARCHAR, status VARCHAR, resolved_by VARCHAR,"
                " result VARCHAR, tg_message_id INTEGER)")
    con.execute("INSERT INTO approval VALUES ('a1', 0, 0, '', 'BTCUSDT', 'BUY', 10, '', '', 'pending', '', '', NULL)")
    con.commit()
    con.close()
    db = Database(tmp_path / "old.db")
    from binanceclaw.db import Approval
    a = db.get(Approval, "a1")
    assert a.kind == "spot" and a.payload == {}


def test_context_carries_open_interest_and_funding_history(svc):
    on(svc)
    c = run(svc)
    m = c.context["futures"]["markets"]["BTCUSDT"]
    assert m["funding_rate_8h"] == 0.0001 and m["funding_avg_3"] == 0.0001 and m["funding_positive_streak"] == 9
    assert m["oi_change_24h"] == round(1024 / 1000 - 1, 4) and m["oi_change_4h"] == round(1024 / 1020 - 1, 4)
    calls = svc.fbroker.client.oi_calls
    run(svc)                                    # hourly data: cached between rounds
    assert svc.fbroker.client.oi_calls == calls


def test_context_survives_missing_open_interest(svc):
    on(svc)

    def boom(*a, **k):
        raise ExchangeError("no data")
    svc.fbroker.client.open_interest_hist = boom
    m = run(svc).context["futures"]["markets"]["BTCUSDT"]
    assert m["funding_rate_8h"] == 0.0001 and "oi_change_24h" not in m
