import time

import pytest

from binanceclaw.core.valuation import DAY
from binanceclaw.db import EquitySnapshot


def _add(svc, ts, total, mode="paper", source="recorder"):
    svc.db.add(EquitySnapshot(ts=ts, mode=mode, total=total, source=source))


def test_record_stores_account_total(svc):
    snap = svc.valuation.record()
    ov = svc.overview()
    assert snap is not None and snap.mode == "paper"
    assert abs(snap.total - ov["account_total"]) < 0.01


def test_record_skips_when_exchange_down(svc):
    svc.client.fail = True
    assert svc.valuation.record() is None


def test_history_bases_and_insufficient_ranges(svc):
    now = time.time()
    for h in range(0, 10 * 24, 6):                     # ten days of 6-hourly snapshots, rising 1 per step
        _add(svc, now - h * 3600, 1000 - h / 6)
    out = svc.valuation.history("7d", now=now)
    b = out["bases"]
    assert b["1d"]["full"] and b["7d"]["full"]
    assert b["1d"]["total"] == pytest.approx(996)       # 24 h ago
    assert b["7d"]["total"] == pytest.approx(972)
    assert not b["30d"]["full"] and b["30d"]["total"] == pytest.approx(961)   # only ten days of history
    assert out["points"][0]["total"] == pytest.approx(972)
    assert out["points"][-1]["total"] == pytest.approx(1000)
    assert all(p["t"] >= now - 7 * DAY for p in out["points"])


def test_history_empty_and_other_mode_ignored(svc):
    _add(svc, time.time() - 3600, 5000, mode="live")
    out = svc.valuation.history("1d")
    assert out["points"] == [] and all(v is None for v in out["bases"].values())


def test_history_rejects_unknown_range(svc):
    with pytest.raises(ValueError):
        svc.valuation.history("2w")


def test_history_downsamples(svc):
    now = time.time()
    for m in range(0, 7 * 24 * 60, 5):                 # a week of 5-minute points
        _add(svc, now - m * 60, 1000)
    assert len(svc.valuation.history("7d", now=now)["points"]) <= 241


def test_backfill_from_binance_snapshots_live_only(svc):
    now = time.time()
    svc.client.snapshots = [(now - d * DAY, 0.01 + d * 0.001) for d in range(1, 29)]
    assert svc.valuation.backfill() == 0                # paper mode: nothing from the real account
    svc.settings_store.update({"mode": "live"}, allow_mode_change=True)
    _add(svc, now - 3 * DAY + 600, 999, mode="live")    # our own history starts three days ago
    n = svc.valuation.backfill()
    rows = svc.db.all(__import__("sqlmodel").select(EquitySnapshot).where(EquitySnapshot.source == "binance"))
    assert n == len(rows) and 20 <= n <= 26
    assert all(r.ts < now - 3 * DAY + 600 for r in rows)
    assert rows[0].total == pytest.approx((0.01 + round((now - rows[0].ts) / DAY) * 0.001) * 80000, rel=1e-6)
    assert svc.valuation.backfill() == 0                # throttled, and never duplicates days
    svc.db.kv_set("equity_backfill_at", 0)
    assert svc.valuation.backfill() == 0
    assert svc.valuation.history("30d", now=now)["bases"]["30d"] is not None


def test_import_cycles_once_and_reset_clears_paper(svc):
    from .conftest import plan, use
    use(svc, plan(), plan())
    svc.run_cycle("manual")
    svc.run_cycle("manual")
    svc.valuation.import_cycles()
    svc.valuation.import_cycles()
    rows = svc.db.all(__import__("sqlmodel").select(EquitySnapshot))
    assert len(rows) == 2 and {r.source for r in rows} == {"cycle"}
    svc.reset_paper()
    assert svc.valuation.history("1d")["points"] == []
