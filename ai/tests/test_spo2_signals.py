"""산소포화도 질문의 생활 요인 비교에 같은 기간 이상 신호를 더하는 규칙(agent/rules/spo2_signals.py) 테스트."""

import pytest

from coach_ai.agent.executor import ToolCall
from coach_ai.agent.rules.spo2_signals import added_signals, asks_spo2

# 수면 골든셋 sleep1-05 개선용 질문
SPO2_QUESTION = "내 산소포화도는 왜 이렇게 들쭉날쭉해?"
FACTORS = {"analysis": "factors"}


def sleep(args, call_id: str = "c1") -> ToolCall:
    return ToolCall(call_id, "get_sleep_records", args)


def signals(index: int, **period) -> ToolCall:
    return ToolCall(f"added-{index}", "get_sleep_records", {"analysis": "signals", **period})


@pytest.mark.parametrize(
    "question",
    [
        SPO2_QUESTION,
        "산소 포화도가 밤마다 달라",
        "자는 동안 산소가 모자란 걸까?",
        "SpO2 가 낮게 찍혀",
        "spo2 기록 봐 줘",
        "SPO2",
    ],
)
def test_spo2_words_are_found(question):
    assert asks_spo2(question)


@pytest.mark.parametrize("question", ["수면 중 맥박이 빠른 까닭이 궁금해", "요즘 왜 이렇게 못 자지?", "포화도"])
def test_questions_without_spo2_words(question):
    assert not asks_spo2(question)


def test_factors_call_without_period_gets_signals_without_period():
    assert added_signals([sleep(FACTORS)], SPO2_QUESTION) == [signals(0)]


@pytest.mark.parametrize("period", ["recent_days:14", "weeks_ago:0", "dates:2026-10-01..2026-10-21"])
def test_signals_takes_the_period_of_the_factors_call(period):
    # 수면 기록의 다른 인자(include_non_main)는 넘기지 않는다
    factors = sleep({"analysis": "factors", "period": period, "include_non_main": True})
    assert added_signals([factors], SPO2_QUESTION) == [signals(0, period=period)]


def test_null_period_is_the_same_as_no_period():
    assert added_signals([sleep({"analysis": "factors", "period": None})], SPO2_QUESTION) == [signals(0)]


def test_each_factors_call_gets_its_own_signals_call_in_place():
    summary = ToolCall("c1", "get_metric_summary", {"metric": "oxygen_saturation", "period": "weeks_ago:0"})
    calls = [summary, sleep({"analysis": "factors", "period": "weeks_ago:0"}, "c2"), sleep(FACTORS, "c3")]
    assert added_signals(calls, "SpO2 가 금주에 제일 낮던 날 뭐 했지") == [
        None,
        signals(1, period="weeks_ago:0"),
        signals(2),
    ]


@pytest.mark.parametrize("period", [None, "recent_days:7"])
def test_response_that_already_has_signals_is_left_alone(period):
    already = sleep({"analysis": "signals"} if period is None else {"analysis": "signals", "period": period}, "c2")
    assert added_signals([sleep(FACTORS), already], SPO2_QUESTION) == [None, None]


@pytest.mark.parametrize(
    "call",
    [
        sleep({"analysis": "pattern"}),
        sleep({"period": "recent_days:7"}),
        sleep({"analysis": "factor"}),
        sleep(None),
        ToolCall("c1", "get_metric_summary", {"metric": "oxygen_saturation", "period": "last_7_days"}),
        ToolCall("c1", "get_daily_trend", {"metric": "oxygen_saturation", "period": "last_7_days"}),
        ToolCall("c1", "", None),
    ],
)
def test_calls_other_than_sleep_factors_are_left_alone(call):
    assert added_signals([call], SPO2_QUESTION) == [None]


def test_heart_rate_question_is_left_alone():
    assert added_signals([sleep(FACTORS)], "수면 중 맥박이 빠른 까닭이 궁금해") == [None]


def test_empty_response_has_nothing_to_add():
    assert added_signals([], SPO2_QUESTION) == []
