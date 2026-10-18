"""Model routing by task: decisions on the strong model, summaries on the fast one, user-configurable."""
import json

from binanceclaw.brain.providers import ClaudeCodeProvider, OpenAIProvider
from binanceclaw.config import ModelRouting, ProviderSettings, Secrets, Settings, SettingsStore, decision_changes

from .conftest import FakeProvider, plan, use


def test_defaults_send_decisions_to_opus_and_reports_to_haiku():
    r = ModelRouting()
    assert r.model_for("claude_code", "", "decision") == "claude-opus-5-5"
    assert r.model_for("claude_code", "", "allocation") == "claude-opus-5-5"
    assert r.model_for("anthropic_api", "claude-opus-5-5", "report") == "claude-haiku-5-5"


def test_providers_without_tier_models_use_their_own_model():
    r = ModelRouting()
    assert r.model_for("openai", "gpt-5.5", "decision") == "gpt-5.5"
    assert r.model_for("openai_compat", "", "report") == ""


def test_user_overrides_tier_models_and_task_tiers():
    r = ModelRouting(tasks={"chat": "fast", "report": "main", "bogus": "fast"},
                     models={"openai": {"fast": "gpt-5.5-mini", "strong": ""}, "nope": {"fast": "x"}})
    assert "bogus" not in r.tasks and "nope" not in r.models
    assert r.tasks["decision"] == "strong"
    assert r.model_for("openai", "gpt-5.5", "chat") == "gpt-5.5-mini"
    assert r.model_for("openai", "gpt-5.5", "decision") == "gpt-5.5"      # empty override = main model
    assert r.model_for("claude_code", "", "report") == ""                    # main = CLI default


def test_disabled_routing_uses_one_model_for_everything():
    r = ModelRouting(enabled=False)
    assert all(r.model_for("claude_code", "sonnet", t) == "sonnet" for t in ("decision", "report", "chat"))


def test_settings_saved_with_a_hand_picked_model_keep_it(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"provider": {"name": "claude_code", "model": "claude-sonnet-5-5"}}))
    assert SettingsStore(p).get().model_routing.enabled is False
    p.write_text(json.dumps({"provider": {"name": "claude_code", "model": ""}}))
    assert SettingsStore(p).get().model_routing.enabled is True


def test_with_model_copies_without_touching_the_original():
    base = ClaudeCodeProvider(ProviderSettings(), Secrets())
    p = base.with_model("claude-haiku-5-5")
    assert p is not base and p.model == "claude-haiku-5-5" and base.model == ""
    assert base.with_model("") is base
    o = OpenAIProvider(ProviderSettings(name="openai"), Secrets())
    assert o.with_model("gpt-5.5") is o


def test_each_call_uses_its_tasks_model(svc):
    seen = []

    class Spy(FakeProvider):
        name = "claude_code"

        def complete(self, system, messages, json_mode=False):
            seen.append(self.model)
            return super().complete(system, messages, json_mode)

    svc.provider = Spy([plan(("BUY", "BTCUSDT", 50)), "hi", "点评"])
    c = svc.run_cycle("manual")
    assert c.status == "done" and c.model == "fake-model"   # Completion reports the fake's own name
    svc.chat("现在怎么样")
    svc.daily_report()
    assert seen == ["claude-opus-5-5", "claude-opus-5-5", "claude-haiku-5-5"]
    assert svc.overview()["provider"]["models"]["report"] == "claude-haiku-5-5"
    svc.settings_store.update({"model_routing": {"tasks": {"chat": "fast"}, "models": {"claude_code": {"fast": "h2"}}}})
    svc.provider.replies.append("hi")
    svc.chat("再问一次")
    assert seen[-1] == "h2"


def test_fallback_is_routed_too(svc):
    from binanceclaw.brain.providers import ProviderError
    svc.settings_store.update({"model_routing": {"models": {"openai": {"strong": "big"}}}})
    use(svc, ProviderError("down"))
    fb = FakeProvider([plan(("BUY", "BTCUSDT", 50))])
    fb.name = "openai"
    svc.fallback = fb
    assert svc.routed("decision", fb).model == "big"
    assert svc.run_cycle("manual").status == "done"


def test_routing_change_offers_a_rerun():
    a = Settings()
    b = a.model_copy(deep=True)
    b.model_routing.tasks["decision"] = "fast"
    assert "模型分配" in decision_changes(a, b)
