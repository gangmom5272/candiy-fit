import json
from datetime import date

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from coach_ai.agent.rules.sleep_grounding import (
    NO_SLEEP_RECORD,
    grounded_answer,
    normalize_calls,
    pattern_result,
    sleep_closing,
    unqueried_sleep_clock,
)

CLAIM = "7시간 30분 잤는데 왜 피곤할까?"
SIGNALS = "내 산소포화도는 왜 들쭉날쭉해?"


@pytest.mark.parametrize(
    "answer,question,expected",
    [
        ("평균 취침 시각인 오후 11시 45분에 준비하세요.", "그럼 오늘부터 뭘 하면 돼?", True),
        ("기록상 잠든 시각은 01:37입니다.", "그럼 오늘은?", True),
        ("평균 기상 시각은 08:30이에요.", "아침에는?", True),
        ("밤 11시 45분부터 준비해 보세요.", "일반적인 방법을 알려줘.", False),
        ("성인의 평균 취침 시각은 23:45입니다.", "일반적인 통계야?", False),
        ("평균 수면 시간은 7시간 30분입니다.", "그럼 오늘은?", False),
        ("평균 취침 시각 23:45에 맞춰 보세요.", "내가 말한 취침 시각은 23시 45분이야.", False),
    ],
)
def test_unqueried_sleep_clock_distinguishes_record_claims_from_advice(answer, question, expected):
    assert unqueried_sleep_clock(answer, question) is expected


@pytest.mark.parametrize(
    "question,expected",
    [
        ("알겠어, 오늘은 일찍 자 볼게", True),
        ("고마워, 알려준 대로 해볼게.", True),
        ("알겠어, 그럼 오늘부터 뭘 하면 돼?", False),
        ("고마워, 내 수면 기록도 조회해 줘.", False),
        ("그 기간의 평균 취침 시각을 알려줘", False),
        ("고마워, 몇 시에 잠들면 좋을지 궁금해", False),
        ("알겠어, 내 수면은 어때", False),
        ("내 취침 시각을 알려줘. 고마워", False),
    ],
)
def test_sleep_closing_does_not_swallow_new_requests(question, expected):
    assert sleep_closing(question) is expected


def message(*calls):
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": str(i)} for i, (n, a) in enumerate(calls)])


def test_compare_keeps_its_period_and_adds_required_pattern():
    original = message(("compare_periods", {"metric": "sleep", "current": "weeks_ago:1", "baseline": "weeks_ago:2"}))
    result = normalize_calls(original, CLAIM)
    assert result.tool_calls[0] == original.tool_calls[0]
    assert result.tool_calls[1]["args"] == {"analysis": "pattern", "period": "weeks_ago:1"}
    assert len(original.tool_calls) == 1


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_metric_summary", {"metric": "sleep", "period": "weeks_ago:1"}),
        ("get_sleep_records", {"period": "weeks_ago:1", "include_non_main": True}),
    ],
)
def test_general_sleep_lookup_becomes_pattern_without_losing_period(name, args):
    c = normalize_calls(message((name, args)), CLAIM).tool_calls[0]
    assert c["name"] == "get_sleep_records"
    assert c["args"]["analysis"] == "pattern" and c["args"]["period"] == "weeks_ago:1"
    if name == "get_sleep_records":
        assert c["args"]["include_non_main"] is True


@pytest.mark.parametrize(
    "question",
    [
        "지난주보다 수면 시간이 줄었어?",
        "조회하지 말고 일반적인 피로 조언을 줘",
        "운동한 날은 7시간 잤는데 피곤해. 운동 안 한 날과 비교해줘",
        "다른 사람은 7시간 잤는데 피곤하대",
    ],
)
def test_other_intents_are_not_forced_to_pattern(question):
    m = message(("compare_periods", {"metric": "sleep", "current": "weeks_ago:1", "baseline": "weeks_ago:2"}))
    assert normalize_calls(m, question) is m


def test_signals_drops_only_same_period_oxygen_summary():
    m = message(
        ("get_sleep_records", {"analysis": "signals"}),
        ("get_metric_summary", {"metric": "oxygen_saturation", "period": "recent_days:14"}),
        ("get_metric_summary", {"metric": "oxygen_saturation", "period": "weeks_ago:1"}),
        ("get_metric_summary", {"metric": "heart_rate", "period": "recent_days:14"}),
    )
    assert [c["id"] for c in normalize_calls(m, SIGNALS).tool_calls] == ["0", "2", "3"]
    assert normalize_calls(m, SIGNALS + " 평균도 알려줘") is m


RESULT = {
    "analysis": "pattern",
    "text": "지난주 평균 수면 시간은 6시간 23분이다.",
    "sleep_time_note": "실제 잔 시간이며 누워 있던 시간은 제외된다. 이는 집계 기준이며 원인은 알 수 없다.",
    "last_night_vs_week": "어젯밤은 수면 5시간 8분(308분)이다.",
}


def test_answer_contains_only_current_tool_facts():
    fixed = grounded_answer(RESULT)
    assert fixed.startswith("어젯밤은 수면 5시간 8분(308분)입니다.")
    assert "누워" in fixed and "원인은 이 기록만으로 확인할 수 없습니다" in fixed
    assert "동기화" not in fixed and "인식" not in fixed


@pytest.mark.parametrize("record", ["어젯밤은 기록이 없다.", "어젯밤은 수면 5시간 8분이고, 수면 중 심박 기록은 없다."])
def test_missing_last_night_measurement_keeps_unavailable_fact(record):
    answer = grounded_answer({**RESULT, "last_night_vs_week": record + " 지난 7일은 수면 7시간이다."})
    assert answer.startswith(record.replace("없다.", "없습니다."))
    assert "없다.입니다" not in answer and "7시간" not in answer


def test_past_result_never_gets_last_night():
    past = {"analysis": "pattern", "text": RESULT["text"]}
    assert "어젯밤" not in grounded_answer(past)
    m = ToolMessage(json.dumps({"result": past}), name="get_sleep_records", tool_call_id="1")
    assert pattern_result([m], CLAIM) == past
    assert pattern_result([m], "평균 취침 시각은?") is None
    denied = ToolMessage('{"error":"denied"}', name="get_sleep_records", tool_call_id="2")
    assert pattern_result([denied], CLAIM) is None


@pytest.mark.parametrize(
    "call",
    [
        ("get_metric_summary", {"metric": "sleep"}),
        ("compare_periods", {"metric": "sleep", "baseline": "weeks_ago:1"}),
        ("get_sleep_records", {"period": "days_ago:0", "user_id": "someone-else"}),
    ],
)
def test_invalid_call_is_left_for_gateway_validation(call):
    m = message(call)
    assert normalize_calls(m, CLAIM) is m


def test_unavailable_pattern_never_uses_history():
    assert grounded_answer(None) == NO_SLEEP_RECORD


@pytest.mark.parametrize("period", ["recent_days:14", "dates:10-09..10-22"])
def test_completed_signals_deduplicates_later_round_with_equivalent_period(period):
    result = {
        "analysis": "signals",
        "period": {"period": "recent_days:14", "start": "2026-10-09", "end": "2026-10-22"},
        "text": "합성 기록",
    }
    done = ToolMessage(json.dumps({"result": result}), name="get_sleep_records", tool_call_id="previous")
    m = message(("get_metric_summary", {"metric": "oxygen_saturation", "period": period}))
    assert not normalize_calls(m, SIGNALS, messages=[done], today=date(2026, 10, 22)).tool_calls
    assert normalize_calls(m, SIGNALS + " 평균도 알려줘", messages=[done], today=date(2026, 10, 22)) is m


def test_failed_or_different_period_signals_does_not_remove_summary():
    m = message(("get_metric_summary", {"metric": "oxygen_saturation", "period": "weeks_ago:1"}))
    for body in [
        {"error": "unavailable_period"},
        {
            "result": {
                "analysis": "signals",
                "period": {"period": "recent_days:14", "start": "2026-10-09", "end": "2026-10-22"},
                "text": "합성 기록",
            }
        },
    ]:
        done = ToolMessage(json.dumps(body), name="get_sleep_records", tool_call_id="previous")
        assert normalize_calls(m, SIGNALS, messages=[done], today=date(2026, 10, 22)) is m
