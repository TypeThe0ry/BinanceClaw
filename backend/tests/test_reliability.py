import time

from binanceclaw.brain.providers import ProviderError
from binanceclaw.db import Cycle

from .conftest import FakeProvider, plan, use


def test_fallback_model_is_used_when_primary_fails(svc):
    use(svc, ProviderError("primary down"))
    svc.fallback = FakeProvider([plan(("BUY", "BTCUSDT", 50))])
    c = svc.run_cycle("manual")
    assert c.status == "done" and c.usage.get("fallback") is True
    assert "primary down" in c.usage.get("primary_error", "")


def test_both_models_fail_means_no_trade(svc):
    use(svc, ProviderError("a"))
    svc.fallback = FakeProvider([ProviderError("b")])
    c = svc.run_cycle("manual")
    assert c.status == "error" and "备用模型也失败" in c.error
    assert svc.db.orders_for_day(svc.today()) == []


def test_watchdog_alerts_once_and_rearms(svc):
    alerts = []
    svc.events.on(lambda k, d: alerts.append(d) if k == "alert" else None)
    svc.db.kv_set("started_at", time.time() - 13 * 3600)
    svc.watchdog()
    svc.watchdog()
    assert len(alerts) == 1
    svc.db.add(Cycle(mode="paper", status="done"))
    svc.watchdog()
    assert svc.db.kv_get("watchdog_alerted") is False


def test_watchdog_silent_while_paused(svc):
    alerts = []
    svc.events.on(lambda k, d: alerts.append(d) if k == "alert" else None)
    svc.db.kv_set("started_at", time.time() - 13 * 3600)
    svc.pause("test")
    alerts.clear()
    svc.watchdog()
    assert alerts == []


def test_daily_backup(svc):
    svc.backup_db()
    files = list((svc.data_dir / "backups").glob("binanceclaw-*.db"))
    assert len(files) == 1 and files[0].stat().st_size > 0
    assert oct(files[0].stat().st_mode & 0o777) == "0o600"
    svc.backup_db()      # idempotent per day
    assert len(list((svc.data_dir / "backups").glob("*.db"))) == 1


def test_health(svc):
    h = svc.health()
    assert h["heartbeat_age_s"] is None and h["mode"] == "paper"
    svc.db.kv_set("heartbeat", time.time())
    assert svc.health()["scheduler_alive"] is True
