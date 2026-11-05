"""Daily report: the total is the whole account, a read failure is never shown as 0, and the
AI commentary gets the targets and the pause conditions it is asked to compare against."""
from .conftest import use


def test_report_shows_account_total_targets_and_pause_conditions(svc, monkeypatch):
    use(svc, "点评")
    seen = []
    real = svc.complete
    monkeypatch.setattr(svc, "complete", lambda system, msgs, **kw: (seen.append(msgs[0]["content"]), real(system, msgs, **kw))[1])
    # USDT locked: the AI manages nothing, but the account still holds 10000
    svc.update_settings({"capital": {"locked": {"USDT": 10000}}})
    rep = svc.daily_report()
    assert "总资产 $10,000.00" in rep.text
    assert "其中 AI 管理 $0.00" in rep.text
    assert "配置：" in rep.text
    assert "暂停条件：" in rep.text and "跌到 -25% 暂停" in rep.text
    assert "连续异常 0/3 次暂停" in rep.text
    prompt = seen[0]
    assert "配置：" in prompt and "暂停条件：" in prompt


def test_targets_mode_lists_each_target(svc):
    use(svc, "点评")
    svc.update_settings({"allocation": {"mode": "targets"}})
    rep = svc.daily_report()
    for cat, spec in svc.overview()["targets"].items():
        assert f"· {cat} 当前" in rep.text
        lo, hi = spec["band"]
        assert f"区间 {lo:.0%}–{hi:.0%}" in rep.text


def test_transient_error_is_retried(svc, monkeypatch):
    use(svc, "点评")
    monkeypatch.setattr(svc, "REPORT_RETRIES", 3)
    monkeypatch.setattr("binanceclaw.core.service.time.sleep", lambda s: None)
    real = svc._market_snapshot
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            from binanceclaw.exchange.binance_spot import ExchangeError
            raise ExchangeError("timeout")
        return real()

    svc._mkt_cache = None
    monkeypatch.setattr(svc, "_market_snapshot", flaky)
    rep = svc.daily_report()
    assert "总资产 $10,000.00" in rep.text and "读取失败" not in rep.text


def test_market_failure_values_account_from_all_prices(svc, monkeypatch):
    use(svc, "点评")
    monkeypatch.setattr("binanceclaw.core.service.time.sleep", lambda s: None)
    from binanceclaw.exchange.binance_spot import ExchangeError

    def down():
        raise ExchangeError("klines down")
    svc._mkt_cache = None
    monkeypatch.setattr(svc, "_market_snapshot", down)
    rep = svc.daily_report()
    assert "总资产 $10,000.00" in rep.text
    assert "读取失败：klines down" in rep.text


def test_total_failure_never_shows_zero(svc, monkeypatch):
    use(svc, "点评")
    monkeypatch.setattr("binanceclaw.core.service.time.sleep", lambda s: None)
    svc.client.fail = True
    svc._mkt_cache = None
    from binanceclaw.exchange.binance_spot import ExchangeError

    def down():
        raise ExchangeError("network down")
    monkeypatch.setattr(svc.broker, "balances", down)
    rep = svc.daily_report()
    assert "$0.00" not in rep.text.split("\n")[1]
    assert "总资产 读取失败" in rep.text
