import json

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlmodel import select

from binanceclaw.api.app import create_app
from binanceclaw.brain.providers import Completion, Provider, ProviderError
from binanceclaw.config import Secrets
from binanceclaw.core.arena import BENCHMARK, ArenaConfig
from binanceclaw.core.service import Service
from binanceclaw.db import ArenaEquity, ArenaRound, PaperBalance

from .conftest import FakeFutures, FakeSpot


class Scripted(Provider):
    """One fake model per contestant: replies are consumed in order; ``seen`` keeps every prompt."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.model = "scripted"
        self.seen = []

    def available(self):
        return True, ""

    def complete(self, system, messages, json_mode=False):
        self.seen.append(messages[-1]["content"])
        r = self.replies.pop(0) if self.replies else {"summary": "观望", "actions": [], "confidence": 0.3}
        if isinstance(r, Exception):
            raise r
        return Completion(r if isinstance(r, str) else json.dumps(r), "scripted", {"input_tokens": 100, "output_tokens": 20})


def act(*xs, summary="s", confidence=0.6):
    return {"summary": summary, "confidence": confidence,
            "actions": [{"side": s, "symbol": t, "quote_usd": u, "reason": "r"} for s, t, u in xs]}


@pytest.fixture
def arena(svc):
    a = svc.arena
    a.update_config({"tokens": ["BTC", "ETH", "SOL"], "initial_usd": 10000})
    a.models = {}
    a.make_provider = lambda cfg, secrets: a.models[cfg.model]
    return a


def add(arena, name, *replies):
    c = arena.add(name, "openai", model=name)
    arena.models[name] = Scripted(replies)
    return c


def data(prompt: str) -> dict:
    return json.loads(prompt.split("<data>\n", 1)[1].rsplit("\n</data>", 1)[0])


def test_config_validation(arena):
    assert arena.config().tokens == ["BTC", "ETH", "SOL"]
    assert arena.update_config({"tokens": ["btcusdt", "eth", "ETH", "USDT"]}).tokens == ["BTC", "ETH"]
    with pytest.raises(ValueError):
        arena.update_config({"tokens": []})
    with pytest.raises(ValueError):
        arena.update_config({"interval_minutes": 1})
    with pytest.raises(ValueError):
        arena.add("x", "nope")
    with pytest.raises(ValueError):
        arena.add("x", "openai_compat")          # needs base_url
    assert ArenaConfig().enabled is False


def test_round_trades_virtual_accounts_on_one_snapshot(arena, svc):
    a = add(arena, "alpha", act(("BUY", "BTC", 3000), ("BUY", "ETHUSDT", 1000)))
    b = add(arena, "beta", act(("BUY", "SOL", 2000)))
    res = arena.run_round("manual")
    assert res["ok"] and len(res["results"]) == 2
    # both models were handed the identical market snapshot
    ma, mb = data(arena.models["alpha"].seen[0]), data(arena.models["beta"].seen[0])
    assert ma["market"] == mb["market"] and ma["time"] == mb["time"]
    assert ma["account"]["cash_usd"] == 10000
    board = arena.leaderboard()
    rows = {r["name"]: r for r in board["contestants"]}
    assert rows["alpha"]["trades"] == 2 and rows["beta"]["trades"] == 1
    assert rows["alpha"]["cash"] == pytest.approx(10000 - 4000, abs=5)
    assert set(rows["alpha"]["positions"]) == {"BTC", "ETH"}
    # fees and slippage make a fresh position worth slightly less than the cash it cost
    assert rows["alpha"]["equity"] < 10000 and rows["alpha"]["return"] < 0
    assert [r["rank"] for r in board["contestants"]] == [1, 2]
    assert rows["alpha"]["tokens_in"] == 100
    assert board["benchmark"]["return"] == pytest.approx(0, abs=1e-9)
    # one equity point per contestant + the BTC benchmark
    ids = {e.contestant_id for e in svc.db.all(select(ArenaEquity))}
    assert ids == {a.id, b.id, BENCHMARK}
    batches = arena.rounds()
    assert len(batches) == 1 and len(batches[0]["entries"]) == 2


def test_never_touches_real_or_paper_account(arena, svc):
    svc.settings_store.update({"mode": "live"}, allow_mode_change=True)
    svc._rebuild()
    add(arena, "alpha", act(("BUY", "BTC", 3000)), act(("SELL", "BTC", 99999)))
    arena.run_round()
    arena.run_round()
    assert svc.client.live_orders == []
    assert svc.db.all(select(PaperBalance)) == []
    row = arena.leaderboard()["contestants"][0]
    assert row["positions"] == {} and row["trades"] == 2 and row["closed_trades"] == 1


def test_rules_are_enforced(arena):
    arena.update_config({"max_position_pct": 0.3})
    add(arena, "alpha", act(("BUY", "BTC", 5000), ("BUY", "DOGE", 100), ("SELL", "ETH", 100), ("BUY", "ETH", 5),
                             ("BUY", "SOL", 50000)))
    res = arena.run_round()
    acts = arena.rounds()[0]["entries"][0]["actions"]
    by = {(x["side"], x["symbol"], x["quote_usd"]): x for x in acts}
    assert by[("BUY", "BTC", 5000)]["result"] == "filled" and by[("BUY", "BTC", 5000)]["usd"] <= 3001   # capped at 30%
    assert by[("BUY", "DOGE", 100)]["result"] == "rejected"      # not a contest token
    assert by[("SELL", "ETH", 100)]["note"] == "没有持仓"
    assert by[("BUY", "ETH", 5)]["result"] == "rejected"          # below the minimum order
    assert by[("BUY", "SOL", 50000)]["result"] == "filled" and by[("BUY", "SOL", 50000)]["usd"] <= 3001
    assert res["ok"]


def test_sell_books_realised_pnl_and_win_rate(arena, svc):
    add(arena, "alpha", act(("BUY", "BTC", 4000)), act(("SELL", "BTC", 1e9)))
    arena.run_round()
    svc.client.prices_map["BTCUSDT"] *= 1.10
    arena.run_round()
    row = arena.leaderboard()["contestants"][0]
    assert row["win_rate"] == 1 and row["realized"] > 300 and row["positions"] == {}
    assert row["return"] > 0.03
    assert row["vs_btc"] < 0              # BTC itself rose 10% while the account held 40% of it


def test_bad_output_and_provider_error_are_recorded(arena):
    add(arena, "alpha", "not json")
    add(arena, "beta", ProviderError("boom"))
    add(arena, "gamma", act(("BUY", "ETH", 1000)))
    res = arena.run_round()
    st = {r["contestant"]: r["status"] for r in res["results"]}
    assert st == {"alpha": "error", "beta": "error", "gamma": "done"}
    rows = {r["name"]: r for r in arena.leaderboard()["contestants"]}
    assert rows["alpha"]["errors"] == 1 and rows["beta"]["errors"] == 1 and rows["gamma"]["errors"] == 0


def test_recent_rounds_fed_back(arena):
    add(arena, "alpha", act(("BUY", "ETH", 1000), summary="先买 ETH"), act())
    arena.run_round()
    arena.run_round()
    ctx = data(arena.models["alpha"].seen[1])
    assert ctx["your_recent_rounds"][0]["summary"] == "先买 ETH"
    assert "ETH" in ctx["account"]["positions"]


def test_disabled_contestant_sits_out_and_reset(arena, svc):
    a = add(arena, "alpha", act(("BUY", "ETH", 1000)))
    b = add(arena, "beta")
    arena.edit(b.id, enabled=False)
    arena.run_round()
    assert arena.models["beta"].seen == []
    arena.update_config({"initial_usd": 5000})
    arena.reset()
    rows = {r["name"]: r for r in arena.leaderboard()["contestants"]}
    assert rows["alpha"]["equity"] == 5000 and rows["alpha"]["trades"] == 0
    assert svc.db.all(select(ArenaRound)) == [] and svc.db.all(select(ArenaEquity)) == []
    assert arena.remove(a.id) and not arena.remove(a.id)


def test_schedule(arena, svc):
    add(arena, "alpha")
    assert not arena.due()                       # off by default
    arena.update_config({"enabled": True, "max_rounds_per_day": 1})
    assert arena.due()
    arena.run_round()
    assert not arena.due()                       # interval not reached
    svc.db.kv_set(arena.KV_LAST, 0)
    assert not arena.due()                       # daily cap


def test_drawdown_and_sharpe_on_leaderboard(arena, svc):
    add(arena, "alpha", act(("BUY", "BTC", 5000)))
    arena.run_round()
    svc.client.prices_map["BTCUSDT"] *= 0.8
    arena.update_config({"enabled": True})
    arena.mark()
    row = arena.leaderboard()["contestants"][0]
    assert row["max_drawdown"] == pytest.approx(0.1, abs=0.01)
    assert "sharpe" in row


def test_api(tmp_path):
    sec = Secrets(admin_password_hash=PasswordHasher().hash("correct-horse-1"), session_secret="x" * 64,
                  binance_api_key="k", binance_api_secret="s")
    svc = Service(data_dir=tmp_path, secrets=sec, client=FakeSpot(), fclient=FakeFutures())
    models = {}
    svc.arena.make_provider = lambda cfg, secrets: models[cfg.model]
    c = TestClient(create_app(svc))
    assert c.get("/api/arena").status_code == 401
    c.post("/api/auth/login", json={"password": "correct-horse-1"})
    r = c.post("/api/arena/contestants", json={"name": "alpha", "provider": "openai", "model": "alpha"})
    assert r.status_code == 200
    cid = r.json()["id"]
    models["alpha"] = Scripted([act(("BUY", "BTC", 1000))])
    assert c.post("/api/arena/contestants", json={"name": "x", "provider": "bad"}).status_code == 400
    assert c.put("/api/arena/config", json={"interval_minutes": 2}).status_code == 400
    assert c.put("/api/arena/config", json={"interval_minutes": 30}).json()["interval_minutes"] == 30
    assert c.patch(f"/api/arena/contestants/{cid}", json={"name": "阿尔法"}).json()["name"] == "阿尔法"
    svc.arena.run_round()
    body = c.get("/api/arena").json()
    assert body["contestants"][0]["name"] == "阿尔法" and body["contestants"][0]["trades"] == 1
    assert cid in c.get("/api/arena/curves").json()
    assert c.get(f"/api/arena/rounds?contestant={cid}").json()[0]["status"] == "done"
    assert c.post("/api/arena/reset").json() == {"ok": True}
    assert c.delete(f"/api/arena/contestants/{cid}").json() == {"ok": True}
    assert c.post("/api/arena/run").status_code == 400
