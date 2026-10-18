"""After a change that affects decisions, the owner is offered a fresh manual round."""
import time

from binanceclaw.notify.telegram import TelegramBot

from .test_api import client  # noqa: F401  (fixture)


def _bot(svc):
    bot = TelegramBot.__new__(TelegramBot)
    bot.svc, bot._confirms, sent = svc, {}, []
    bot.send = lambda text, buttons=None: sent.append((text, buttons))
    return bot, sent


def test_offer_lists_decision_changes_only(svc):
    before = svc.settings_store.get()
    svc.update_settings({"schedule": {"cycle_minutes": 30}, "paper": {"slippage": 0.001}})
    assert svc.rerun_offer(before) == []                               # timing and paper fills don't change a decision
    svc.update_settings({"investor_notes": "多配 ETH", "allocation": {"max_asset_weight": 0.5}})
    assert svc.rerun_offer(before) == ["投资备注", "资产分配"]


def test_no_offer_while_paused(svc):
    before = svc.settings_store.get()
    svc.update_settings({"investor_notes": "别动"})
    svc.pause("test")
    assert svc.rerun_offer(before) == []


def test_api_reports_rerun(client):  # noqa: F811
    client.post("/api/auth/login", json={"password": "correct-horse-1"})
    r = client.put("/api/settings", json={"investor_notes": "只买 BTC"})
    assert r.status_code == 200 and r.json()["rerun"] == ["投资备注"]
    assert r.json()["investor_notes"] == "只买 BTC"
    assert client.put("/api/settings", json={"schedule": {"cycle_minutes": 45}}).json()["rerun"] == []


def test_telegram_offers_and_runs_a_manual_round(svc, monkeypatch):
    bot, sent = _bot(svc)
    runs = []
    monkeypatch.setattr(svc, "run_cycle", lambda trigger="schedule", why="": runs.append(trigger) or True)
    bot._guarded(lambda tok: svc.update_settings({"capital": {"locked": {}}}, guard=True, confirm=tok), "✅ 无变化")
    assert sent[-1][1] is None                                         # nothing changed, no offer
    bot._guarded(lambda tok: svc.update_settings({"allocation": {"ai_candidates": 20}}, guard=True, confirm=tok), "✅ 候选")
    text, buttons = sent[-1]
    assert "资产分配" in text and buttons[0][0][0] == "🧠 立即重跑"
    bot._rerun(buttons[0][0][1].split(":")[1])
    for _ in range(50):
        if runs:
            break
        time.sleep(0.01)
    assert runs == ["manual"]

    bot._rerun(str(int(time.time()) - 3600))
    assert "过期" in sent[-1][0]
    svc.pause("test")
    bot._rerun(str(int(time.time())))
    assert "暂停" in sent[-1][0] and runs == ["manual"]
