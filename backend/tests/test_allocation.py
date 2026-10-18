"""All Binance spot assets (incl. suspended ones like TON) and AI-drafted target allocations."""
import pytest

from .conftest import plan, use


def _cats(svc):
    return {k: v.model_dump() for k, v in svc.settings.categories.items()}


def test_suspended_pair_is_listed_and_selectable(svc):
    rows = {r["token"]: r for r in svc.markets("", 500)}
    assert rows["TON"]["status"] == "BREAK" and rows["TON"]["selectable"] and not rows["TON"]["trading"]
    assert rows["XYZ"]["selectable"] is False and rows["XYZ"]["quotes"] == ["BTC"]   # no USDT pair, still listed
    assert "USDT" not in rows                                                        # stables aren't assets to pick
    svc.update_settings({"allocation": {"enabled": ["BTC", "TON"], "mode": "ai"}})
    assert svc.settings.tradable_tokens() == ["BTC", "TON"]


def test_risk_engine_refuses_suspended_pair(svc):
    svc.update_settings({"allocation": {"enabled": ["BTC", "TON"], "mode": "ai"}})
    p = use(svc, plan(("BUY", "TONUSDT", 50), ("BUY", "BTCUSDT", 50)))
    c = svc.run_cycle("manual")
    assert [r["decision"] for r in c.risk] == ["reject", "execute"]
    assert "暂停" in c.risk[0]["reason"]
    assert '"halted"' in p.calls[0][1][0]["content"]          # the model is told, too


def test_rules_cache_picks_up_status_changes(svc, monkeypatch):
    from tests import conftest
    sym = "TONUSDT"
    assert svc.broker.rules([sym])[sym].status == "BREAK"
    monkeypatch.setitem(conftest.HALTED, sym, "TRADING")
    assert svc.broker.rules([sym])[sym].status == "BREAK"     # cached
    svc.broker._rules_ts[sym] -= svc.broker.RULES_TTL + 1
    assert svc.broker.rules([sym])[sym].status == "TRADING"


PROPOSAL = {"summary": "加入 SOL 和 TON，BTC 降到 50%",
            "categories": {"BTC": {"tokens": {"BTC": 0.5}, "band": [0.4, 0.6]},
                           "ALTS": {"tokens": {"SOLUSDT": 0.1, "TON": 0.05}},
                           "STABLE": {"tokens": {"USDT": 0.35}, "band": [0.2, 0.3]}}}


def test_ai_drafts_allocation_and_owner_applies_it(svc):
    before = _cats(svc)
    p = use(svc, PROPOSAL)
    prop = svc.propose_allocation("加 10% SOL 和 5% TON", "web")
    assert "加 10% SOL" in p.calls[0][1][0]["content"]
    assert _cats(svc) == before                                # nothing changes until applied
    assert prop["proposed"] == {"BTC": 0.5, "SOL": 0.1, "TON": 0.05, "USDT": 0.35}
    assert prop["categories"]["ALTS"]["band"] == [0.12, 0.2]   # band generated around 15%
    assert prop["categories"]["STABLE"]["band"] == [0.2, 0.35]   # widened to contain the target
    assert any("TON" in n and "暂停" in n for n in prop["notes"])
    svc.resolve_allocation(prop["id"], True)
    s = svc.settings
    assert s.allocation.mode == "targets"
    assert {"SOL", "TON"} <= set(s.allocation.enabled)
    assert s.tradable_tokens() == ["BTC", "SOL", "TON"]
    assert "ETH" not in s.categories
    with pytest.raises(ValueError):
        svc.resolve_allocation(prop["id"], True)              # one-shot


def test_proposal_without_instruction_and_discard(svc):
    p = use(svc, PROPOSAL)
    prop = svc.propose_allocation(None)
    assert "生成一份完整" in p.calls[0][1][0]["content"]
    before = _cats(svc)
    assert svc.resolve_allocation(prop["id"], False)["status"] == "discarded"
    assert _cats(svc) == before


@pytest.mark.parametrize("bad, msg", [
    ({**PROPOSAL, "categories": {"BTC": {"tokens": {"BTC": 0.9}}, "STABLE": {"tokens": {"USDT": 0.3}}}}, "超过 100%"),
    ({**PROPOSAL, "categories": {"BTC": {"tokens": {"BTC": 0.5}}}}, "STABLE"),
    ({**PROPOSAL, "categories": {"BTC": {"tokens": {"BTC": 0.3}}, "MORE": {"tokens": {"BTC": 0.1}},
                                     "STABLE": {"tokens": {"USDT": 0.3}}}}, "多个类别"),
    ({**PROPOSAL, "categories": {"BTC": {"tokens": {"USDT": 0.5}}, "STABLE": {"tokens": {"USDT": 0.3}}}}, "只能放在 STABLE"),
    ({**PROPOSAL, "categories": {"X": {"tokens": {"NOTACOIN": 0.1}}, "STABLE": {"tokens": {"USDT": 0.3}}}}, "NOTACOIN"),
    ({"summary": "x", "categories": {}, "actions": []}, "不符合结构"),
])
def test_invalid_proposals_are_rejected(svc, bad, msg):
    use(svc, bad)
    with pytest.raises(ValueError, match=msg):
        svc.propose_allocation("x")
    assert svc.allocation_proposal() is None


def test_allocation_api(tmp_path):
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    from binanceclaw.api.app import create_app
    from binanceclaw.config import Secrets
    from binanceclaw.core.service import Service

    from .conftest import FakeFutures, FakeSpot
    sec = Secrets(admin_password_hash=PasswordHasher().hash("correct-horse-1"), session_secret="x" * 64,
                  binance_api_key="k", binance_api_secret="s")
    svc = Service(data_dir=tmp_path, secrets=sec, client=FakeSpot(), fclient=FakeFutures())
    c = TestClient(create_app(svc))
    assert c.post("/api/allocation/propose", json={"instruction": "x"}).status_code == 401
    c.post("/api/auth/login", json={"password": "correct-horse-1"})
    use(svc, PROPOSAL)
    prop = c.post("/api/allocation/propose", json={"instruction": "加 TON"}).json()
    assert c.get("/api/allocation/proposal").json()["id"] == prop["id"]
    assert c.post("/api/allocation/resolve", json={"id": "nope", "apply": True}).status_code == 400
    assert c.post("/api/allocation/resolve", json={"id": prop["id"], "apply": True}).json()["status"] == "applied"
    assert "TON" in svc.settings.tradable_tokens()
    use(svc, "not json")
    r = c.post("/api/allocation/propose", json={"instruction": ""})
    assert r.status_code == 400 and "不合规" in r.json()["detail"]
