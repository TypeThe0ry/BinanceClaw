"""多空辩论: bull, bear and risk argue before the decision model; the plan still passes the risk engine."""
import json
import threading

import pytest

from binanceclaw.brain import debate as deb
from binanceclaw.brain.providers import Completion, ProviderError
from binanceclaw.brain.schema import PlanError
from binanceclaw.config import Settings
from binanceclaw.db import Cycle

from .conftest import FakeProvider, plan


def arg(stance="bullish", conviction=0.7, symbol="BTC", side="BUY"):
    return {"stance": stance, "conviction": conviction, "thesis": f"{stance} view", "points": ["p1"],
            "proposals": [{"symbol": symbol, "side": side, "note": "n"}], "rebuttal": ""}


class Seats(FakeProvider):
    """Answers by who is asking: each seat's system prompt names its role; the judge gets the plan."""
    name = "claude_code"

    def __init__(self, by_role, judge_reply, fail=()):
        super().__init__([])
        self.by_role, self.judge_reply, self.fail = by_role, judge_reply, set(fail)
        self.lock = threading.Lock()

    def complete(self, system, messages, json_mode=False):
        with self.lock:
            self.calls.append((system, messages, json_mode))
        for role, label in deb.ROLES.items():
            if f"「{label}」角色" in system:
                if role in self.fail:
                    raise ProviderError(f"{role} down")
                return Completion(json.dumps(self.by_role[role]), f"m-{role}", {})
        return Completion(json.dumps(self.judge_reply), self.model, {})


def debate_on(svc, **kw):
    svc.settings_store.update({"debate": {"enabled": True, **kw}})


def test_off_by_default_and_rounds_bounded():
    assert Settings().debate.enabled is False
    with pytest.raises(ValueError):
        Settings(debate={"rounds": 4})


def test_off_means_a_single_decision_call(svc):
    p = Seats({}, plan(("BUY", "BTC", 50)))
    svc.provider = p
    c = svc.run_cycle("manual")
    assert c.status == "done" and len(p.calls) == 1 and not c.debate


def test_seats_argue_then_the_judge_decides_and_risk_still_applies(svc):
    p = Seats({"bull": arg(), "bear": arg("bearish", 0.4, side="AVOID"), "risk": arg("neutral", 0.5, side="HOLD")},
              plan(("BUY", "BTC", 5000)))
    svc.provider = p
    debate_on(svc)
    c = svc.run_cycle("manual")
    assert c.status == "done"
    assert len(p.calls) == 4
    judge_system, judge_msgs, _ = p.calls[-1]
    assert "多空辩论" in judge_system and "<debate>" in judge_msgs[0]["content"]
    assert [t["role"] for t in c.debate["turns"]] == ["bull", "bear", "risk"]
    assert c.debate["consensus"]["votes"]["BTC"] == {"BUY": 1, "AVOID": 1, "HOLD": 1}
    assert "debate_ms" in c.timing
    # the judge asked for 5000; the risk engine still cuts it to the per-order cap
    r = c.risk[0]
    assert r["requested_usd"] == 5000 and r["approved_usd"] <= svc.settings.limits.max_order_usd
    assert svc.db.get(Cycle, c.id).prompt.endswith("只输出 JSON。")


def test_second_round_sees_the_first(svc):
    p = Seats({"bull": arg(), "bear": arg("bearish"), "risk": arg("neutral")}, plan())
    svc.provider = p
    debate_on(svc, rounds=2)
    c = svc.run_cycle("manual")
    assert len(c.debate["turns"]) == 6 and len(p.calls) == 7
    second = [m for s, m, _ in p.calls if "第 2 轮" in m[0]["content"]]
    assert len(second) == 3 and all("bearish view" in m[0]["content"] for m in second)


def test_a_failing_seat_is_recorded_and_the_round_goes_on(svc):
    p = Seats({"bull": arg(), "risk": arg("neutral")}, plan(), fail={"bear"})
    svc.provider = p
    debate_on(svc)
    c = svc.run_cycle("manual")
    assert c.status == "done"
    bear = next(t for t in c.debate["turns"] if t["role"] == "bear")
    assert "bear down" in bear["error"]


def test_when_every_seat_fails_the_decision_runs_without_debate(svc):
    p = Seats({}, plan(), fail=set(deb.ROLES))
    svc.provider = p
    debate_on(svc, rounds=3)
    c = svc.run_cycle("manual")
    assert c.status == "done"
    assert len(c.debate["turns"]) == 3          # stopped after the first round
    assert "<debate>" not in p.calls[-1][1][0]["content"]


def test_each_seat_can_use_its_own_model(svc):
    p = Seats({"bull": arg(), "bear": arg("bearish"), "risk": arg("neutral")}, plan())
    svc.provider = p
    debate_on(svc, bull={"model": "bull-model"}, judge={"model": "judge-model"})
    assert svc.seat_provider(svc.settings.debate.bull, "debate").model == "bull-model"
    assert svc.seat_provider(svc.settings.debate.bear, "debate").model == svc.routed("debate").model
    assert svc.seat_provider(svc.settings.debate.judge, "decision").model == "judge-model"


def test_a_seat_on_another_provider(svc, monkeypatch):
    from binanceclaw.core import service as service_mod
    made = []

    def fake_make(cfg, secrets):
        made.append(cfg)
        return FakeProvider([json.dumps(arg("bearish"))])

    monkeypatch.setattr(service_mod, "make_provider", fake_make)
    svc.provider = Seats({"bull": arg(), "risk": arg("neutral")}, plan())
    debate_on(svc, bear={"provider": "openai_compat", "model": "deepseek-chat", "base_url": "https://x.test/v1"})
    c = svc.run_cycle("manual")
    assert made[-1].name == "openai_compat" and made[-1].base_url == "https://x.test/v1"
    assert next(t for t in c.debate["turns"] if t["role"] == "bear")["stance"] == "bearish"


def test_judge_failure_falls_back_to_the_decision_model(svc, monkeypatch):
    p = Seats({"bull": arg(), "bear": arg("bearish"), "risk": arg("neutral")}, plan(summary="fallback judge"))
    svc.provider = p
    debate_on(svc, judge={"provider": "openai", "model": "gpt-x"})
    from binanceclaw.core import service as service_mod
    monkeypatch.setattr(service_mod, "make_provider", lambda cfg, sec: FakeProvider([ProviderError("judge down")]))
    c = svc.run_cycle("manual")
    assert c.status == "done" and c.summary == "fallback judge"
    assert "judge down" in c.usage["judge_error"]


def test_parse_argument_cleans_the_reply():
    a = deb.parse_argument('```json\n{"stance":"BULLISH","conviction":3,"thesis":"t","points":["a","",' + '"b"],'
                           '"proposals":[{"symbol":"eth","side":"long"},{"x":1}]}\n```')
    assert a["stance"] == "bullish" and a["conviction"] == 1.0 and a["points"] == ["a", "b"]
    assert a["proposals"] == [{"symbol": "ETH", "side": "HOLD", "note": ""}]
    with pytest.raises(PlanError):
        deb.parse_argument('{"stance":"bullish"}')
    with pytest.raises(PlanError):
        deb.parse_argument("no json")


def test_consensus_uses_the_last_round():
    turns = [{"round": 1, "role": "bull", "stance": "bullish", "conviction": 1.0, "proposals": []},
             {"round": 2, "role": "bull", "stance": "bearish", "conviction": 0.6, "proposals": []},
             {"round": 2, "role": "bear", "stance": "bearish", "conviction": 0.6, "proposals": []},
             {"round": 2, "role": "risk", "error": "x"}]
    c = deb.consensus(turns)
    assert c["score"] == -0.6 and c["lean"] == "bearish"
    assert deb.consensus([]) == {}


def test_debate_settings_offer_a_rerun(svc):
    from binanceclaw.config import decision_changes
    before = svc.settings
    after = Settings.model_validate({**before.model_dump(), "debate": {"enabled": True}})
    assert "多空辩论" in decision_changes(before, after)
