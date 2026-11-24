import json
from dataclasses import replace

import pytest
import respx
from langchain_core.messages import ToolMessage

from coach_ai.agent.rules.sleep_grounding import normalize_calls, sleep_plan_followup, sleep_results
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import DENIED_MESSAGE
from tests.test_agent_graph import (
    CHAT_URL,
    CTX,
    SleepAnalysisHandler,
    history_turn,
    run,
    text_response,
    tool_call_response,
)
from tests.test_agent_graph import model as model
from tests.test_agent_graph import recorder as recorder
from tests.test_sleep_grounding import CLAIM, message


@pytest.mark.parametrize(
    "content", [DENIED_MESSAGE, "조회 한도입니다", "{", "null", "[]", "1", '{"result":null}', '{"result":[]}']
)
def test_non_result_messages_do_not_break_sleep_results(content):
    valid = {"analysis": "pattern", "text": "현재 기록"}
    messages = [
        ToolMessage(content, tool_call_id="bad", name="get_sleep_records"),
        ToolMessage(json.dumps({"result": valid}), tool_call_id="ok", name="get_sleep_records"),
    ]
    assert sleep_results(messages, "pattern") == [valid]
    assert sleep_results(messages, "signals") == []


@pytest.mark.parametrize(
    "question,expected",
    [
        ("그럼 오늘부터 뭘 하면 돼?", True),
        ("그러면 어떻게 개선하면 좋아?", True),
        ("그럼 일반적인 수면 조언만 해줘", False),
        ("고마워, 알려준 대로 해볼게", False),
        ("그럼 최근 7일 기준으로 뭘 하면 돼?", False),
        ("그럼 그 기간은 어떻게 개선해?", False),
        ("그럼 10월 12일 기록으로 뭘 하면 돼?", False),
        ("그럼 운동은 뭘 하면 돼?", False),
        ("지난 주말처럼 몰아서 늦잠 자도 돼?", False),
    ],
)
def test_personal_plan_boundary(question, expected):
    assert sleep_plan_followup(question, "어젯밤 수면 기록은 짧습니다") is expected
    assert not sleep_plan_followup(question, "걸음 기록이 적습니다")


def test_normalized_sleep_calls_deduplicate_equivalent_periods():
    from datetime import date

    original = message(
        ("get_metric_summary", {"metric": "sleep", "period": "days_ago:0"}),
        ("get_sleep_records", {"period": "date:10-22", "analysis": "pattern"}),
        ("get_sleep_records", {"period": "weeks_ago:1", "analysis": "pattern"}),
    )
    normalized = normalize_calls(original, CLAIM, today=date(2026, 10, 22))
    assert len(normalized.tool_calls) == 2 and len(original.tool_calls) == 3
    assert normalized.tool_calls[-1]["args"]["period"] == "weeks_ago:1"


@respx.mock
async def test_normalized_duplicate_does_not_crash(model, recorder):
    audit = MemoryAuditSink()
    respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(
                ("get_metric_summary", {"metric": "sleep", "period": "days_ago:0"}),
                ("get_sleep_records", {"period": "days_ago:0", "analysis": "pattern"}),
            ),
            text_response("조회했습니다."),
        ]
    )
    result, _ = await run(model, CLAIM, handlers={"get_sleep_records": SleepAnalysisHandler()}, audit=audit)
    assert result.error_code is None
    assert len(audit.records()) == 1 and audit.records()[0].decision == "allow"


@respx.mock
async def test_number_free_personal_plan_queries_before_answer(model, recorder):
    handler = SleepAnalysisHandler()
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            text_response("일찍 잠자리에 들어보세요."),
            text_response("조회한 취침 시각 01:37보다 일찍 준비해 보세요."),
        ]
    )
    ctx = replace(CTX, turns=(history_turn(1, CLAIM, 2, "어젯밤 수면 기록은 짧습니다."),))
    result, _ = await run(model, "그럼 오늘부터 뭘 하면 돼?", ctx=ctx, handlers={"get_sleep_records": handler})
    assert result.error_code is None and route.call_count == 2
    assert handler.calls == [(None, "pattern")]
    assert result.answer == "조회한 취침 시각 01:37보다 일찍 준비해 보세요."
