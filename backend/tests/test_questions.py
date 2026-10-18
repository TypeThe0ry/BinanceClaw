"""The AI asks the owner before high-risk decisions; answers wake a round, timeouts follow the owner's setting."""
import time

import pytest

from binanceclaw.brain.schema import PlanError, parse_plan
from binanceclaw.config import ConfirmationRequired
from binanceclaw.db import AiQuestion
from binanceclaw.notify.telegram import TelegramBot

from .conftest import plan, use


def asking(*actions, topic="TRX", recommended=1):
    p = plan(*actions)
    p["questions"] = [{"topic": topic, "text": "发现 1000 TRX 闲置，我不看好 TRON，要卖出换成 BTC 吗？",
                       "options": [{"label": "保留"}, {"label": "卖出换 BTC", "detail": "卖出全部 TRX，买入 BTC"}],
                       "recommended": recommended}]
    return p


def provider(svc, *replies):
    return use(svc, *replies)


def test_schema_limits_options_and_recommended():
    assert parse_plan('{"summary":"s","confidence":0.5}').questions == []
    with pytest.raises(PlanError):         # one option is not a choice
        parse_plan('{"summary":"s","confidence":0.5,"questions":[{"topic":"t","text":"q","options":[{"label":"a"}],"recommended":0}]}')
    with pytest.raises(PlanError):         # recommended must point at an option
        parse_plan('{"summary":"s","confidence":0.5,"questions":[{"topic":"t","text":"q",'
                   '"options":[{"label":"a"},{"label":"b"}],"recommended":2}]}')


def test_question_is_stored_and_not_asked_twice(svc):
    provider(svc, asking(), asking(), asking(topic="TON"))
    c = svc.run_cycle("manual")
    qs = svc.db.pending_questions()
    assert len(qs) == 1 and qs[0].topic == "TRX" and qs[0].cycle_id == c.id
    assert c.plan["questions"][0]["id"] == qs[0].id
    c2 = svc.run_cycle("manual")
    assert c2.context["questions"]["pending"][0]["topic"] == "TRX"
    assert len(svc.db.pending_questions()) == 1          # same topic still waiting: not asked again
    svc.run_cycle("manual")
    assert [q.topic for q in svc.db.pending_questions()] == ["TRX", "TON"]


def test_answer_wakes_a_round_with_the_answer(svc):
    p = provider(svc, asking(), plan(("BUY", "BTCUSDT", 50)))
    svc.run_cycle("manual")
    q = svc.db.pending_questions()[0]
    assert svc.question_due() is None
    ok, _ = svc.answer_question(q.id, 1, "web")
    assert ok and not svc.answer_question(q.id, 0, "web")[0]      # first answer wins
    trig, why = svc.question_due()
    assert trig == "answer" and "卖出换 BTC" in why
    c = svc.run_cycle(trig, why)
    ans = c.context["questions"]["answers"][0]
    assert ans["answer"] == "卖出换 BTC" and ans["by"] == "主人" and ans["new"] is True
    assert "卖出换 BTC" in p.calls[1][1][0]["content"]
    assert c.risk[0]["decision"] == "execute"                     # the follow-up action went through the risk engine
    assert svc.question_due() is None
    c3 = svc.run_cycle("manual")                                  # later rounds still remember it, no longer new
    assert c3.context["questions"]["answers"][0]["new"] is False


def test_bad_option_is_refused(svc):
    provider(svc, asking())
    svc.run_cycle("manual")
    q = svc.db.pending_questions()[0]
    assert svc.answer_question(q.id, 5, "web") == (False, "没有这个选项")


def test_timeout_skip_does_nothing_about_it(svc):
    provider(svc, asking(), plan())
    svc.run_cycle("manual")
    q = svc.db.pending_questions()[0]
    svc.db.update(AiQuestion, q.id, expires=time.time() - 1)
    svc.expire_questions()
    row = svc.db.get(AiQuestion, q.id)
    assert row.status == "expired" and row.answer is None
    assert svc.question_due() is None                             # skipping needs no extra round
    c = svc.run_cycle("manual")
    ans = c.context["questions"]["answers"][0]
    assert ans["answer"] is None and "不操作" in ans["by"]


def test_timeout_auto_takes_the_recommendation(svc):
    with pytest.raises(ConfirmationRequired) as e:                # dangerous: needs a second confirmation
        svc.update_settings({"questions": {"on_timeout": "auto"}}, guard=True)
    tok = e.value.token
    svc.update_settings({"questions": {"on_timeout": "auto"}}, guard=True, confirm=tok)
    provider(svc, asking(recommended=1), plan())
    svc.run_cycle("manual")
    q = svc.db.pending_questions()[0]
    svc.db.update(AiQuestion, q.id, expires=time.time() - 1)
    svc.expire_questions()
    assert svc.db.get(AiQuestion, q.id).answer == 1
    trig, why = svc.question_due()
    c = svc.run_cycle(trig, why)
    assert c.context["questions"]["answers"][0]["answer"] == "卖出换 BTC"


def test_disabled_questions_are_dropped(svc):
    svc.update_settings({"questions": {"enabled": False}})
    provider(svc, asking())
    c = svc.run_cycle("manual")
    assert svc.db.pending_questions() == [] and c.plan["questions"] == []
    assert c.context["questions"]["enabled"] is False


def test_paused_account_is_not_woken_and_kill_cancels(svc):
    provider(svc, asking())
    svc.run_cycle("manual")
    q = svc.db.pending_questions()[0]
    svc.answer_question(q.id, 0, "web")
    svc.pause("test")
    assert svc.question_due() is None
    svc.resume()
    provider(svc, asking(topic="TON"))
    svc.run_cycle("manual")
    svc.kill_switch("web")
    assert svc.db.pending_questions() == []


def test_telegram_buttons_answer(svc):
    bot = TelegramBot.__new__(TelegramBot)
    bot.svc, sent = svc, []
    bot.send = lambda text, buttons=None: sent.append((text, buttons)) or 7
    provider(svc, asking())
    svc.run_cycle("manual")
    q = svc.db.pending_questions()[0]
    bot._send_question(q.id)
    text, buttons = sent[-1]
    assert "TRX" in text and "AI 推荐" in text and "不操作" in text
    assert buttons[1][0][1] == f"qa:{q.id}:1"
    assert svc.db.get(AiQuestion, q.id).tg_message_id == 7
    svc.answer_question(q.id, 1, "telegram")
    assert "卖出换 BTC" in TelegramBot._question_outcome(svc.db.get(AiQuestion, q.id))
