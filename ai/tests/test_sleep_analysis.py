"""수면 기록 도구의 수면 코칭 분석(analysis)과 기본 조회에 붙는 두 줄 (calculation-rules.md 11장, 11.6, 11.7).

synth-11 은 골든셋 고정 데이터이고 오늘은 2026-10-22(목)이다. 결과 문장의 기대값은 계산·결과 문장 모듈 테스트
(tests/test_sleep_coaching_text.py)가 고정 데이터 전체로 만든 문장과 같다.
"""

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from coach_ai.adapters.memory import HeartDay, SleepSession, StepDay, SyncState
from coach_ai.agent.context import CoachContext
from coach_ai.agent.evidence import KindEvidenceDraft
from coach_ai.agent.executor import ExtraPeriod, ToolCall, ToolRequest
from coach_ai.agent.rules.exercise_words import mentions_exercise
from coach_ai.ports import HealthDataUnavailableError
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import CALL_LIMIT_MESSAGE, DENIED_MESSAGE, MAX_TOOL_CALLS, ToolGateway
from coach_ai.tools import sleep_coaching_text
from coach_ai.tools.handlers import get_sleep_records
from coach_ai.tools.periods import DateRange
from coach_ai.tools.results import SleepAnalysisResult, format_minutes
from coach_ai.tools.schemas import GetSleepRecords
from coach_ai.tools.sleep_coaching import factors, pattern
from coach_ai.tools.sleep_coaching_text import exercise_comparison_text, factors_text, pattern_text
from eval.golden import fixtures as gf
from eval.golden.items import load_set
from tests.fakes import KST, NOW, TODAY, D, FakeHealthData, at, request, sample_port
from tests.test_sleep_coaching import synth11_input
from tests.test_sleep_coaching_text import (
    SYNTH11_DECLINING,
    SYNTH11_FACTORS,
    SYNTH11_LAST_NIGHT,
    SYNTH11_PATTERN,
    SYNTH11_SIGNALS,
)

SYNTH11_TODAY = date(2026, 10, 22)
LINES = ("sleep_vs_baseline", "last_night_vs_week")
# 수면 기록 결과와 어젯밤 줄이 붙은 pattern 결과의 수면 시간 문장 (calculation-rules.md 7.5, 11.1)
SLEEP_TIME_NOTE = (
    "수면 시간은 잠든 시각(워치가 잠든 것으로 기록한 시각)부터 깬 시각 사이에 실제로 잔 시간이고, "
    "중간에 깬 시간과 잠들기 전·깬 뒤 누워 있던 시간은 빠져 있다. "
    "이는 집계 기준이며 사용자가 말한 시간과 다른 원인을 확인한 것은 아니다. "
    "차이의 원인은 확인할 수 없으며, 이전 답변의 동기화 지연·워치 인식 오류 추측은 근거가 없어 반복하지 않는다. "
    "사용자가 잤다고 말한 시간과 다르면 첫 문장은 해당 날짜의 실제 수면 시간으로 시작하고 이 집계 기준을 설명한다. "
    "피로의 원인이나 사용자 시간과 다른 이유를 수면 패턴만으로 추정하지 않는다."
)


def synth11(asks_exercise: bool = False) -> tuple[ToolRequest, FakeHealthData]:
    fixture = gf.load_fixture("synth-11")
    port = fixture.port()
    req = ToolRequest(user_id=fixture.user_id, today=fixture.now.date(), port=port, asks_exercise=asks_exercise)
    return req, port


async def run(*, asks_exercise: bool = False, **args):
    req, port = synth11(asks_exercise)
    return await get_sleep_records(GetSleepRecords(**args), req), port


def lookups(port: FakeHealthData) -> list[tuple]:
    return [call[2:] for call in port.calls]


def sleep_span(start: date, end: date, expression: str, display: str) -> dict:
    return {
        "period": expression,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "basis": "수면 종료일(KST)",
        "display": display,
    }


async def test_pattern_writes_judgment_first_for_default_fourteen_days():
    out, _ = await run(analysis="pattern")
    assert out.status == "ok"
    assert out.payload == {
        "metric": "sleep",
        "analysis": "pattern",
        "text": SYNTH11_PATTERN,
        "period": sleep_span(D(2026, 10, 9), SYNTH11_TODAY, "recent_days:14", "최근 14일 (10월 9일-22일)"),
        "sleep_time_note": SLEEP_TIME_NOTE,
        "last_night_vs_week": SYNTH11_LAST_NIGHT,
    }
    # 판정 문장이 결과 문장 맨 앞이고, 결과 문장이 기간보다 앞에 있다
    assert out.payload["text"].startswith(
        "최근 14일(10월 9일-22일) 수면은 짧고, 평소보다 줄어드는 중이고, "
        "수면 시간대가 불규칙하고, 깊은 잠이 평소보다 적다. "
    )
    assert list(out.payload) == ["metric", "analysis", "text", "period", "sleep_time_note", "last_night_vs_week"]


@pytest.mark.parametrize(
    ("analysis", "text", "start", "expression", "display"),
    [
        ("signals", SYNTH11_SIGNALS, D(2026, 10, 9), "recent_days:14", "최근 14일 (10월 9일-22일)"),
        ("factors", SYNTH11_FACTORS, D(2026, 9, 25), "recent_days:28", "최근 28일 (9월 25일-10월 22일)"),
    ],
    ids=["signals", "factors"],
)
async def test_signals_and_factors_use_their_default_periods(analysis, text, start, expression, display):
    out, _ = await run(analysis=analysis)
    assert out.payload["text"] == text
    assert out.payload["period"] == sleep_span(start, SYNTH11_TODAY, expression, display)


async def test_signals_start_with_low_oxygen_nights_and_factors_with_scope():
    signals, _ = await run(analysis="signals")
    factor, _ = await run(analysis="factors")
    assert signals.payload["text"].startswith(
        "최근 14일(10월 9일-22일) 수면에서 산소포화도를 기록한 14일 중 90% 미만으로 기록된 날은 7일, 기록은 8회"
    )
    assert factor.payload["text"].startswith(
        "아래 비교는 최근 28일(9월 25일-10월 22일) 수면을 잠들기 전 낮에 있었던 일로 나눠 본 것이고, "
        "원인이 아니라 함께 나타난 차이다."
    )


@pytest.mark.parametrize(
    ("analysis", "expected"),
    [
        # 본인 기준(분석 기간 앞 28일)과 줄어드는 중(기간 끝까지 14일과 그 전 28일). 기본 14일이면 두 범위가 같다
        ("pattern", [("sleep", D(2026, 10, 9), SYNTH11_TODAY), ("sleep", D(2026, 9, 11), D(2026, 10, 8))]),
        # 수면 중 심박 기준선(기간 끝까지 7일 앞의 28일, 9월 18일-10월 15일)은 분석 기간과 겹쳐 근거로 내보내지 않고,
        # 기간 앞 날짜만 계산에 쓰는 조회로 읽는다
        ("signals", [("sleep", D(2026, 10, 9), SYNTH11_TODAY), ("sleep", D(2026, 9, 18), D(2026, 10, 8))]),
        # 조건 날짜 D 는 깬 날 전날이고 전날 짧게 잔 날·연달아 늦게 잔 날은 D 와 D−1 에 깬 수면을 쓴다
        (
            "factors",
            [
                ("sleep", D(2026, 9, 25), SYNTH11_TODAY),
                ("sleep", D(2026, 9, 23), D(2026, 9, 24)),
                ("steps", D(2026, 9, 24), D(2026, 10, 21)),
                ("heart_rate", D(2026, 9, 24), D(2026, 10, 21)),
                ("exercise", D(2026, 9, 24), D(2026, 10, 21)),
            ],
        ),
    ],
)
async def test_analysis_looks_up_records_before_the_period_inside_one_call(analysis, expected):
    # 주 수면은 근거 기간마다 따로 조회하고, 계산에만 쓰는 나머지 날짜는 따로 조회한다. 조회한 날짜를 모으면 분석마다
    # 한 번에 조회하는 범위(pattern 9월 11일부터, signals 9월 18일부터, factors 9월 23일부터)와 같고 겹치지 않는다
    _, port = await run(analysis=analysis)
    assert sorted(lookups(port)) == sorted(expected)
    assert all(call[0] == "get_metric_result" for call in port.calls)


async def test_evidence_is_the_main_sleep_record_used_in_calculation():
    # 근거 번호를 받는 근거는 분석 기간의 주 수면이고, 결과 글이 비교한 본인 기준 28일은 근거 이벤트로만 보낸다
    out, port = await run(analysis="pattern")
    [draft] = out.evidence
    [baseline] = out.event_only_evidence
    assert not isinstance(draft, KindEvidenceDraft)
    assert (draft.tool, draft.metric, baseline.tool, baseline.metric) == (
        "get_sleep_records",
        "sleeps",
        "get_sleep_records",
        "sleeps",
    )
    # 근거 기간은 snapshot 의 기간과 같다 (agent-interface.md 4.1). 분석 기간 근거의 기간은 결과의 기간과 같다
    assert draft.period.model_dump(mode="json") == out.payload["period"]
    assert (baseline.period.start, baseline.period.end) == (D(2026, 9, 11), D(2026, 10, 8))
    user = port.calls[0][1]
    assert draft.snapshot == (await port.get_metric_result(user, "sleep", D(2026, 10, 9), SYNTH11_TODAY)).snapshot
    assert baseline.snapshot == (await port.get_metric_result(user, "sleep", D(2026, 9, 11), D(2026, 10, 8))).snapshot
    assert draft.summary == "수면 패턴 판정에 쓴 최근 14일 (10월 9일-22일) 수면 기록 (14일 중 14일 기록)"
    assert baseline.summary == "수면 패턴 판정에서 비교한 9월 11일-10월 8일 수면 기록 (28일 중 28일 기록)"


async def test_user_period_replaces_default_and_baseline_follows_it():
    out, port = await run(analysis="pattern", period="recent_days:7")
    expected = pattern_text(pattern(synth11_input(), DateRange(D(2026, 10, 16), SYNTH11_TODAY)), SYNTH11_TODAY)
    assert out.payload["text"] == expected
    assert out.payload["period"]["display"] == "최근 7일 (10월 16일-22일)"
    # 본인 기준은 기간 시작 전 28일(9월 18일부터), 줄어드는 중은 기간 끝까지 14일과 그 전 28일(9월 11일부터)이다.
    # 기간과 본인 기준은 근거라 따로 조회하고, 줄어드는 중에만 쓰는 9월 11일-17일은 계산에만 쓰는 조회다
    assert lookups(port) == [
        ("sleep", D(2026, 10, 16), SYNTH11_TODAY),
        ("sleep", D(2026, 9, 18), D(2026, 10, 15)),
        ("sleep", D(2026, 9, 11), D(2026, 9, 17)),
    ]

    # synth-11 은 최근 14일에 운동한 날이 없어 다시 계산하므로, 운동 조건 비교가 되는 최근 21일로 확인한다
    out, port = await run(analysis="factors", period="recent_days:21")
    three_weeks = DateRange(D(2026, 10, 2), SYNTH11_TODAY)
    assert out.payload["text"] == factors_text(factors(synth11_input(), three_weeks), SYNTH11_TODAY)
    assert out.payload["period"]["display"] == "최근 21일 (10월 2일-22일)"
    assert sorted(lookups(port)) == sorted(
        [
            ("sleep", D(2026, 10, 2), SYNTH11_TODAY),
            ("sleep", D(2026, 9, 30), D(2026, 10, 1)),
            ("steps", D(2026, 10, 1), D(2026, 10, 21)),
            ("heart_rate", D(2026, 10, 1), D(2026, 10, 21)),
            ("exercise", D(2026, 10, 1), D(2026, 10, 21)),
        ]
    )
    assert out.extra_period is None


# synth-11 고정 데이터의 10월 10일-22일에 깬 주 수면의 수면 시간. 기본 조회의 밤처럼 깬 날짜로 쓴다
SYNTH11_NIGHTS = {
    D(2026, 10, 10): "7시간 36분",
    D(2026, 10, 11): "6시간 40분",
    D(2026, 10, 12): "5시간 38분",
    D(2026, 10, 13): "6시간 21분",
    D(2026, 10, 14): "5시간 17분",
    D(2026, 10, 15): "6시간 13분",
    D(2026, 10, 16): "2시간 59분",
    D(2026, 10, 17): "9시간 26분",
    D(2026, 10, 18): "8시간 49분",
    D(2026, 10, 19): "5시간 8분",
    D(2026, 10, 20): "5시간 20분",
    D(2026, 10, 21): "2시간 58분",
    D(2026, 10, 22): "5시간 8분",
}
SHORT_FACTORS_NOTE = "생활 요인 비교(운동·걸음 등)는 14일 이상에서만 계산한다."
# synth-11 오늘(10월 22일)까지 28일과, 그 생활 요인 비교의 낮 기록 조회 (조건 날짜 D 는 깬 날 전날이다).
# 주 수면은 전날 수면까지 읽어 9월 23일부터 조회하고, 근거 기간 밖의 날짜는 계산에만 쓰는 조회로 읽는다
RECENT_28 = DateRange(D(2026, 9, 25), SYNTH11_TODAY)
RECENT_28_DAY_LOOKUPS = [
    ("steps", D(2026, 9, 24), D(2026, 10, 21)),
    ("heart_rate", D(2026, 9, 24), D(2026, 10, 21)),
    ("exercise", D(2026, 9, 24), D(2026, 10, 21)),
]
RECENT_28_LOOKUPS = [
    ("sleep", D(2026, 9, 25), SYNTH11_TODAY),
    ("sleep", D(2026, 9, 23), D(2026, 9, 24)),
    *RECENT_28_DAY_LOOKUPS,
]


def recent_exercise_block() -> str:
    return exercise_comparison_text(factors(synth11_input(), RECENT_28), SYNTH11_TODAY)


def asked_nights(start: date, end: date, label: str) -> str:
    nights = ", ".join(f"{d.month}월 {d.day}일 {v}" for d, v in SYNTH11_NIGHTS.items() if start <= d <= end)
    return f"{label} 날짜별 수면 시간(날짜는 깬 날 기준): {nights}."


@pytest.mark.parametrize(
    ("period", "start", "label", "display", "extremes"),
    [
        (
            "recent_days:7",
            D(2026, 10, 16),
            "최근 7일(10월 16일-22일)",
            "최근 7일 (10월 16일-22일)",
            # 10월 16일-22일에 깬 밤 179·566·529·308·320·178·308분
            "이 기간 가장 오래 잔 날은 10월 17일 수면 9시간 26분, 가장 짧게 잔 날은 10월 21일 수면 2시간 58분이다.",
        ),
        (
            "days_ago:0",
            SYNTH11_TODAY,
            "오늘(10월 22일)",
            "오늘 (10월 22일)",
            "이 기간 수면 시간 기록이 있는 날은 어젯밤 5시간 8분 하루다.",
        ),
        (
            "recent_days:13",
            D(2026, 10, 10),
            "최근 13일(10월 10일-22일)",
            "최근 13일 (10월 10일-22일)",
            "이 기간 가장 오래 잔 날은 10월 17일 수면 9시간 26분, 가장 짧게 잔 날은 10월 21일 수면 2시간 58분이다.",
        ),
    ],
    ids=["seven-days", "today", "thirteen-days"],
)
async def test_short_factors_period_has_its_own_nights_and_exercise_of_the_recent_28_days(
    period, start, label, display, extremes
):
    # 14일보다 짧은 기간은 조건별 비교를 하지 않는다. 11.4 의 기본 비교 기간은 28일이고, 14일이면 조건마다 4일이
    # 모자라는 일이 많다. 결과 글은 질문한 기간의 밤별 수면 시간, 그 기간에 가장 오래 잔 날과 가장 짧게 잔 날, 생활 요인
    # 비교를 계산하지 않은 사실이다. 질문에 운동 말이 있고 그 기간이 최근 28일 안이라 최근 28일의 운동 조건 비교를 따로
    # 덧붙인다
    out, port = await run(asks_exercise=True, analysis="factors", period=period)
    assert out.payload["text"] == " ".join([asked_nights(start, SYNTH11_TODAY, label), extremes, SHORT_FACTORS_NOTE])
    assert out.payload["period"] == sleep_span(start, SYNTH11_TODAY, period, display)
    assert out.payload["exercise_comparison"] == recent_exercise_block()
    assert list(out.payload) == ["metric", "analysis", "text", "period", "exercise_comparison"]
    assert out.extra_period == ExtraPeriod("exercise_period", RECENT_28)
    # 조회 범위는 최근 28일 계산과 같다. 질문한 기간의 주 수면은 근거라 따로 조회하고, 그 앞 날짜는 계산에만 쓴다.
    # 근거는 질문한 기간의 주 수면이고, 계산에 쓴 최근 28일 조건 날짜의 낮 기록은 근거 이벤트로만 보낸다
    rest = ("sleep", D(2026, 9, 23), start - timedelta(days=1))
    assert sorted(lookups(port)) == sorted([("sleep", start, SYNTH11_TODAY), rest, *RECENT_28_DAY_LOOKUPS])
    [draft] = out.evidence
    assert draft.period.model_dump(mode="json") == out.payload["period"]
    days = (SYNTH11_TODAY - start).days + 1
    assert draft.summary == f"조건별 비교에 쓴 {display} 수면 기록 ({days}일 중 {days}일 기록)"
    assert [(e.metric, e.period.start, e.period.end) for e in out.event_only_evidence] == [
        ("steps", D(2026, 9, 24), D(2026, 10, 21)),
        ("heart_rates", D(2026, 9, 24), D(2026, 10, 21)),
        ("exercises", D(2026, 9, 24), D(2026, 10, 21)),
    ]


@pytest.mark.parametrize(
    ("period", "start", "end"),
    [
        ("recent_days:7", D(2026, 10, 16), SYNTH11_TODAY),
        ("days_ago:0", SYNTH11_TODAY, SYNTH11_TODAY),
        ("weeks_ago:1", D(2026, 10, 12), D(2026, 10, 18)),
    ],
    ids=["seven-days", "today", "last-week"],
)
async def test_short_factors_period_without_exercise_words_has_only_its_own_nights(period, start, end):
    # 질문에 운동 말이 없으면 덧붙이지 않는다. 조회는 그 기간의 주 수면 하나이고, 결과와 근거는 운동 말이 있을 때의
    # 결과 글(text)·기간과 같다
    out, port = await run(analysis="factors", period=period)
    asked, _ = await run(asks_exercise=True, analysis="factors", period=period)
    assert list(out.payload) == ["metric", "analysis", "text", "period"]
    assert out.payload["text"] == asked.payload["text"] and out.payload["period"] == asked.payload["period"]
    assert out.extra_period is None
    assert lookups(port) == [("sleep", start, end)]
    [draft] = out.evidence
    assert (draft.period.start, draft.period.end) == (start, end)


async def test_short_factors_period_before_today_keeps_the_asked_week_nights():
    out, port = await run(asks_exercise=True, analysis="factors", period="weeks_ago:1")
    assert out.payload["text"] == (
        asked_nights(D(2026, 10, 12), D(2026, 10, 18), "지난주(10월 12일-18일)")
        + " 이 기간 가장 오래 잔 날은 10월 17일 수면 9시간 26분, 가장 짧게 잔 날은 10월 16일 수면 2시간 59분이다. "
        + SHORT_FACTORS_NOTE
    )
    assert out.payload["period"] == sleep_span(
        D(2026, 10, 12), D(2026, 10, 18), "weeks_ago:1", "지난주 (10월 12일-18일)"
    )
    assert out.payload["exercise_comparison"] == recent_exercise_block()
    # 질문한 주의 앞뒤 날짜는 계산에만 쓰는 조회로 읽는다
    assert sorted(lookups(port)) == sorted(
        [
            ("sleep", D(2026, 10, 12), D(2026, 10, 18)),
            ("sleep", D(2026, 9, 23), D(2026, 10, 11)),
            ("sleep", D(2026, 10, 19), SYNTH11_TODAY),
            *RECENT_28_DAY_LOOKUPS,
        ]
    )


async def test_short_factors_period_outside_the_recent_28_days_has_no_exercise_block():
    # 최근 28일 밖의 짧은 기간은 질문에 운동 말이 있어도 덧붙이지 않고 그 기간의 주 수면만 조회한다
    out, port = await run(asks_exercise=True, analysis="factors", period="dates:2026-09-01..2026-09-07")
    assert out.payload["text"].startswith("9월 1일-7일 날짜별 수면 시간(날짜는 깬 날 기준): ")
    assert "exercise_comparison" not in out.payload and out.extra_period is None
    assert lookups(port) == [("sleep", D(2026, 9, 1), D(2026, 9, 7))]


@pytest.mark.parametrize("period", ["weeks_ago:1", "days_ago:0", "recent_days:13", "last_week", "weekday:sun"])
async def test_short_factors_text_has_no_28_day_comparison(period):
    # 결과 글(text)에는 28일 비교의 숫자(평균·차이·밤 수)와 조건 문장이 없다. 최근 28일의 운동 조건 비교는 따로
    # 덧붙인 칸에만 있고, 그 칸의 문장마다 최근 28일이 있다
    out, _ = await run(asks_exercise=True, analysis="factors", period=period)
    block = out.payload["exercise_comparison"]
    assert block.startswith("운동 조건 비교(최근 28일, 9월 25일-10월 22일): ")
    assert all("최근 28일" in s for s in block.removesuffix(".").split(". "))
    text = out.payload["text"]
    whole = factors_text(factors(synth11_input()), SYNTH11_TODAY)
    for phrase in ("28일", "아래 비교는", "운동 기록", "걸음이", "평균", "한쪽이 4일보다", "원인이 아니라"):
        assert phrase not in text, phrase
    assert not re.search(r"가장 (오래|짧게) 잔 \d+밤", text)
    # 28일 비교 문장의 괄호 속 분 값(조건별 평균·차이)이 들어가지 않는다
    for minutes in set(re.findall(r"\((\d+)분[,)]", whole)):
        assert f"({minutes}분" not in text, minutes
    assert text.endswith(SHORT_FACTORS_NOTE)


@pytest.mark.parametrize(
    ("period", "label"),
    [
        ("last_week", "지난주(10월 12일-18일)"),
        ("weekday:sun", "지난 일요일(10월 18일)"),
        ("dates:2026-10-17..2026-10-18", "10월 17일-18일"),
    ],
    ids=["named", "weekday", "dates"],
)
async def test_asked_nights_line_names_the_asked_period_like_its_display(period, label):
    # 이름이 있는 기간은 이름 뒤에 날짜를 붙이고, 날짜로 지정한 기간은 날짜만 쓴다
    out, _ = await run(analysis="factors", period=period)
    assert out.payload["text"].startswith(f"{label} 날짜별 수면 시간(날짜는 깬 날 기준): ")


@pytest.mark.parametrize(
    "args", [{}, {"period": "recent_days:14"}, {"period": "recent_days:21"}, {"period": "recent_days:28"}], ids=str
)
async def test_factors_of_14_days_or_more_has_no_asked_nights(args):
    out, _ = await run(analysis="factors", **args)
    assert "밤별 수면 시간" not in out.payload["text"]
    assert SHORT_FACTORS_NOTE not in out.payload["text"]
    assert "exercise_comparison" not in out.payload
    # 최근 14일은 운동한 날이 없어 다시 계산한 이유가 맨 앞에 있고, 그 뒤는 같은 형식이다
    assert out.payload["text"].removeprefix(RECALCULATED_14).startswith("아래 비교는 ")


# synth-11 최근 14일(10월 9일-22일)은 운동한 날이 0일이라 운동 조건 비교가 하나도 안 된다
RECALCULATED_14 = (
    "요청한 기간인 최근 14일(10월 9일-22일)에는 운동한 날이 없어 생활 요인 비교를 최근 28일(9월 25일-10월 22일)로 "
    "다시 계산했다. "
)


async def test_factors_without_exercise_comparison_is_recalculated_with_the_recent_28_days():
    # 14일 이상인 요청 기간에 운동 조건(운동한 날, 늦은 운동) 비교가 하나도 안 되면 최근 28일로 다시 계산한다. 첫 문장에
    # 요청한 기간과 이유를 쓰고, 그 뒤는 기간 없이 부른 생활 요인 비교와 같다
    out, port = await run(analysis="factors", period="recent_days:14")
    assert out.payload["text"] == RECALCULATED_14 + SYNTH11_FACTORS
    assert out.payload["period"] == sleep_span(
        D(2026, 9, 25), SYNTH11_TODAY, "recent_days:28", "최근 28일 (9월 25일-10월 22일)"
    )
    assert out.extra_period == ExtraPeriod("extended_period", RECENT_28)
    # 요청한 기간으로 먼저 계산하고 최근 28일을 한 번 더 조회한다. 근거는 결과에 쓴 최근 28일의 주 수면과 그 계산의
    # 낮 기록이고, 요청한 기간으로 한 첫 계산의 조회는 근거가 아니다
    asked = [
        ("sleep", D(2026, 10, 9), SYNTH11_TODAY),
        ("sleep", D(2026, 10, 7), D(2026, 10, 8)),
        ("steps", D(2026, 10, 8), D(2026, 10, 21)),
        ("heart_rate", D(2026, 10, 8), D(2026, 10, 21)),
        ("exercise", D(2026, 10, 8), D(2026, 10, 21)),
    ]
    assert sorted(lookups(port)) == sorted(asked + RECENT_28_LOOKUPS)
    [draft] = out.evidence
    assert draft.period.model_dump(mode="json") == out.payload["period"]
    assert [(e.metric, e.period.start, e.period.end) for e in out.event_only_evidence] == [
        ("steps", D(2026, 9, 24), D(2026, 10, 21)),
        ("heart_rates", D(2026, 9, 24), D(2026, 10, 21)),
        ("exercises", D(2026, 9, 24), D(2026, 10, 21)),
    ]


async def test_recalculation_reason_counts_the_exercise_nights_of_the_asked_period():
    # 10월 5일-18일은 운동한 날 2일, 운동하지 않은 날 12일이다
    out, _ = await run(analysis="factors", period="weeks_ago:2..weeks_ago:1")
    assert (
        "은 운동한 날 2일, 운동하지 않은 날 12일로 운동 조건마다 양쪽 4일이 되지 않아 생활 요인 비교를 최근 "
        "28일(9월 25일-10월 22일)로 다시 계산했다. 아래 비교는 최근 28일(9월 25일-10월 22일) 수면을"
    ) in out.payload["text"]
    assert out.payload["text"].startswith("요청한 기간인 ") and out.extra_period == ExtraPeriod(
        "extended_period", RECENT_28
    )


@pytest.mark.parametrize(
    ("period", "start", "end", "label"),
    [
        ("recent_days:21", D(2026, 10, 2), SYNTH11_TODAY, "최근 21일(10월 2일-22일)"),
        ("this_month", D(2026, 10, 1), SYNTH11_TODAY, "이번 달(10월 1일-22일)"),
        ("months_ago:1", D(2026, 9, 1), D(2026, 9, 30), "지난달(9월 1일-30일)"),
    ],
)
async def test_asked_period_with_exercise_comparison_is_kept(period, start, end, label):
    # 운동 조건 비교가 되는 기간은 사용자가 말한 기간 그대로 계산하고, 첫 문장은 그 기간의 이름으로 부른다
    # (운동한 날 4·5·11일)
    out, port = await run(analysis="factors", period=period)
    text = factors_text(factors(synth11_input(), DateRange(start, end)), SYNTH11_TODAY, label)
    assert out.payload["text"] == text and text.startswith(f"아래 비교는 {label} 수면을")
    # 주 수면은 기간과 그 앞 이틀을 따로, 낮 기록은 세 지표를 조회한다. 다시 계산하지 않아 한 번씩이다
    assert out.extra_period is None and len(port.calls) == 5


async def test_call_log_records_the_asked_period_with_the_extra_28_days():
    # 호출 로그의 인자는 모델이 낸 그대로이고, 처리 함수가 인자 밖으로 계산에 쓴 최근 28일은 period_dates 에 이름을
    # 붙여 요청한 기간과 함께 기록한다. 도구 노드에 전달하는 결과에도 같은 기간이 있다
    fixture = gf.load_fixture("synth-11")
    audit = MemoryAuditSink()
    gateway = ToolGateway(fixture.port(), SYNTH11_TODAY, audit=audit, evidence_enabled=frozenset())
    ctx = CoachContext(user_id=fixture.user_id, conversation_id="c-389", request_id="r-389")
    calls = [
        ToolCall(f"c{i}", "get_sleep_records", {"analysis": "factors", "period": period})
        for i, period in enumerate(["recent_days:7", "recent_days:14", "recent_days:21"])
    ]
    replies = await gateway.execute(calls, ctx, asks_exercise=True)
    assert [r.extra_period for r in replies] == [
        ExtraPeriod("exercise_period", RECENT_28),
        ExtraPeriod("extended_period", RECENT_28),
        None,
    ]
    records = audit.records()
    assert [r.args for r in records] == [c.args for c in calls]
    assert [r.period_dates for r in records] == [
        {"period": DateRange(D(2026, 10, 16), SYNTH11_TODAY), "exercise_period": RECENT_28},
        {"period": DateRange(D(2026, 10, 9), SYNTH11_TODAY), "extended_period": RECENT_28},
        {"period": DateRange(D(2026, 10, 2), SYNTH11_TODAY)},
    ]
    assert json.loads(records[0].to_json())["period_dates"] == {
        "period": {"start": "2026-10-16", "end": "2026-10-22"},
        "exercise_period": {"start": "2026-09-25", "end": "2026-10-22"},
    }


# 시연 날짜(2026-11-18 21:00) 고정 데이터. 데이터 파이프라인의 synthetic·golden 을 기준일 11-18 로 실행해 내보낸
# 합성 데이터다 (지수 synth-11, 민준 synth-12)
DEMO_FIXTURES = Path(__file__).parent / "data" / "demo-1118"
DEMO_NOW = datetime(2026, 11, 18, 21, 0, tzinfo=KST)


async def demo(persona: str, *, asks_exercise: bool = False, **args):
    fixture = gf.load_fixture(persona, DEMO_FIXTURES, now=DEMO_NOW)
    req = ToolRequest(user_id=fixture.user_id, today=DEMO_NOW.date(), port=fixture.port(), asks_exercise=asks_exercise)
    return await get_sleep_records(GetSleepRecords(**args), req)


async def test_demo_jisu_without_exercise_in_two_weeks_is_compared_over_the_recent_28_days():
    # 지수는 최근 2주 야근 동안 운동하지 않았다 (마지막 운동이 16일 전). 최근 14일로 물어도 최근 28일로 다시 계산하고,
    # 운동을 물으며 최근 7일로 부르면 그 7일의 밤과 최근 28일의 운동 조건 비교를 따로 덧붙인다
    out = await demo("synth-11", analysis="factors", period="recent_days:14")
    text = out.payload["text"]
    assert text.startswith(
        "요청한 기간인 최근 14일(11월 5일-18일)에는 운동한 날이 없어 생활 요인 비교를 최근 28일(10월 22일-11월 18일)로 "
        "다시 계산했다. 아래 비교는 최근 28일(10월 22일-11월 18일) 수면을"
    )
    assert (
        "운동한 날(운동 기록이 있는 날, 느린 걷기 제외) 7일 평균: 수면 시간 6시간 57분(417분), 깊은 잠 1시간 15분(75분)"
    ) in text
    assert "운동한 날이 운동하지 않은 날보다 수면 시간이 40분 길고, 깊은 잠이 24분 길고" in text
    week = await demo("synth-11", asks_exercise=True, analysis="factors", period="recent_days:7")
    assert week.payload["text"].startswith("최근 7일(11월 12일-18일) 날짜별 수면 시간(날짜는 깬 날 기준): ")
    block = week.payload["exercise_comparison"]
    assert block.startswith("운동 조건 비교(최근 28일, 10월 22일-11월 18일): ")
    assert "최근 28일에서는 운동한 날이 운동하지 않은 날보다 수면 시간이 40분 길고, 깊은 잠이 24분 길고" in block


async def test_demo_minjun_two_weeks_compares_late_and_early_exercise_as_asked():
    # 민준은 2주 전부터 평일 운동을 밤으로 옮겼다. 최근 14일은 늦게 운동한 날 8일 대 일찍 운동한 날 5일이라 비교가
    # 되고, 요청한 기간 그대로 두 무리를 사용자가 쓰는 말과 뜻으로 쓴다
    out = await demo("synth-12", analysis="factors", period="recent_days:14")
    assert out.extra_period is None
    text = out.payload["text"]
    assert text.startswith("아래 비교는 최근 14일(11월 5일-18일) 수면을")
    assert (
        "늦게 운동한 날(운동을 끝내고 3시간이 지나기 전에 잠든 날, 느린 걷기 제외) 8일 평균: 수면 시간 "
        "7시간 24분(444분), 깊은 잠 30분"
    ) in text
    assert (
        " / 일찍 운동한 날(운동을 끝내고 3시간 이상 지나 잠든 날) 5일 평균: 수면 시간 7시간 8분(428분), 깊은 잠 59분"
        in text
    )
    assert "늦게 운동한 날이 일찍 운동한 날보다 수면 시간이 16분 길고, 깊은 잠이 29분 짧고" in text
    assert "잠들기 3시간" not in text


@pytest.mark.parametrize("item_id", ["golden2-c11", "golden2-c16"])
async def test_asked_nights_carry_the_answers_of_last_weekend_questions(item_id):
    # "지난 주말처럼 …" 질문(골든셋 v2.1 개발용)의 정답은 지난주 날짜별 수면 시간의 가장 짧은 날·긴 날이다. factors 를
    # 지난주로 불러도 그 값은 날짜별 수면 시간 줄과 가장 오래·짧게 잔 날 문장에 있고, 평균과 28일 비교 숫자는 없다
    [item] = [i for i in load_set("v2.1")[0] if i.id == item_id]
    fixture = gf.load_fixture(item.persona)
    req = ToolRequest(user_id=fixture.user_id, today=fixture.now.date(), port=fixture.port())
    out = await get_sleep_records(GetSleepRecords(analysis="factors", period="weeks_ago:1"), req)
    text = out.payload["text"]
    line, *_ = text.split(". ")
    assert line.startswith("지난주(10월 12일-18일) 날짜별 수면 시간(날짜는 깬 날 기준): ")
    assert line.count("일 ") == 7
    for fact in item.facts:
        assert (fact.metric, fact.period, fact.field) in {
            ("sleep", "last_week", "daily_min"),
            ("sleep", "last_week", "daily_max"),
        }
        day, minutes = f"{fact.date.month}월 {fact.date.day}일", format_minutes(int(fact.value))
        assert f"{day} {minutes}" in line, fact
        # 가장 짧은 날·긴 날은 그 기간의 가장 짧게·오래 잔 날 문장에도 깬 날로 부른 잠으로 있다
        longest_or_shortest = "가장 짧게 잔 날은" if fact.field == "daily_min" else "가장 오래 잔 날은"
        assert f"{longest_or_shortest} {day} 수면 {minutes}" in text, fact
    assert "평균" not in text and "28일" not in text and "아래 비교는" not in text and text.endswith(SHORT_FACTORS_NOTE)
    # 이 질문에는 운동 말이 없어 최근 28일 운동 조건 비교를 덧붙이지 않는다
    assert not mentions_exercise([item.question]) and "exercise_comparison" not in out.payload
    # 28일 비교의 토·일·평일 평균은 지난주 값으로 옮겨 쓰일 수 있다. 앞 질문의 운동 말로 덧붙이더라도
    # 덧붙인 운동 조건 비교에는 운동 조건만 있고 문장마다 최근 28일이 있어, 주말·평일 평균과 지난주 밤의 값이 없다
    asked = ToolRequest(user_id=fixture.user_id, today=fixture.now.date(), port=fixture.port(), asks_exercise=True)
    block = (await get_sleep_records(GetSleepRecords(analysis="factors", period="weeks_ago:1"), asked)).payload[
        "exercise_comparison"
    ]
    assert all("최근 28일" in s for s in block.removesuffix(".").split(". "))
    for word in ("토·일", "주말", "평일", "지난주", "가장 오래", "가장 짧게"):
        assert word not in block, word


async def test_short_factors_period_still_has_the_lookup_limit():
    # 질문한 기간이 12개월 전보다 앞서면 짧은 기간이어도 분석할 수 없다. 넓히지 않아 바로 앞 기간은 분석한다
    out, port = await run(analysis="factors", period="dates:2025-10-15..2025-10-21")
    assert out.status == "unavailable_period"
    assert out.payload["period"] == "dates:2025-10-15..2025-10-21"
    assert port.calls == []
    inside, _ = await run(analysis="factors", period="dates:2025-11-05..2025-11-11")
    assert inside.status == "ok"


async def test_factors_compares_every_condition_and_condition_argument_is_denied():
    # 조건을 고르는 칸이 없어 factors 는 11.4 의 조건 일곱 개와 잘 잔 밤·못 잔 밤을 늘 함께 비교한다
    out, _ = await run(analysis="factors")
    assert out.payload["text"] == factors_text(factors(synth11_input()), SYNTH11_TODAY)
    assert "가장 오래 잔 9일" in out.payload["text"]
    fixture = gf.load_fixture("synth-11")
    audit = MemoryAuditSink()
    gateway = ToolGateway(fixture.port(), SYNTH11_TODAY, audit=audit, evidence_enabled=frozenset())
    ctx = CoachContext(user_id=fixture.user_id, conversation_id="c-336", request_id="r-336")
    args = {"analysis": "factors", "condition": "weekend"}
    [reply] = await gateway.execute([ToolCall("c1", "get_sleep_records", args)], ctx)
    assert reply.content == DENIED_MESSAGE
    [record] = audit.records()
    assert (record.decision, record.reason) == ("deny", "extra_field")


async def test_texts_have_thresholds_and_nights_for_every_judgment():
    out, _ = await run(analysis="pattern")
    text = out.payload["text"]
    for threshold in (
        "7시간 미만이라 짧다",
        "평소보다 60분 이상 짧아",
        "날마다 보통 60분 이상 달라지거나 주말·평일 자는 시간대가 60분 이상",
        "자다가 깬 시간이 평균 30분 이하",
        "수면 효율이 85% 이상",
        "평소보다 20% 이상 낮아",
    ):
        assert threshold in text, threshold
    assert "(14일)" in text and "(28일)" in text


async def test_too_few_nights_are_not_judged_and_status_stays_ok():
    # sample_port: 10-02~15 에 깬 주 수면은 12·13·14일 세 밤이다
    port = sample_port()
    outs = {
        kind: await get_sleep_records(GetSleepRecords(analysis=kind), request(port))
        for kind in ("pattern", "signals", "factors")
    }
    assert {kind: out.status for kind, out in outs.items()} == dict.fromkeys(outs, "ok")
    assert (
        outs["pattern"]
        .payload["text"]
        .startswith("최근 14일(10월 2일-15일, 기록 3일) 수면은 기록이 4일보다 적어 판단하지 않았다.")
    )
    assert (
        outs["signals"]
        .payload["text"]
        .startswith("최근 14일(10월 2일-15일, 기록 3일) 수면은 기록이 4일보다 적다. 산소포화도를 기록한 날이 없다.")
    )
    factor = outs["factors"].payload["text"]
    assert factor.startswith(
        "아래 비교는 최근 28일(9월 18일-10월 15일, 기록 3일) 수면을 잠들기 전 낮에 있었던 일로 나눠 본 것이고, "
        "원인이 아니라 함께 나타난 차이다."
    )
    assert "한쪽이 4일보다 적어 비교하지 않은 것:" in factor
    assert "가장 오래 잔 날과 가장 짧게 잔 날은 각각 0일로 4일보다 적어 비교하지 않았다" in factor


# 착용 값: 심박 수집이 10월 8일부터 실패 중이면 그날부터는 착용을 판단하지 않는다 (calculation-rules.md 9.1 2번 줄)
HIGH_HR_DAYS = {D(2026, 9, 20), D(2026, 9, 24), D(2026, 9, 28), D(2026, 10, 2), D(2026, 10, 6)}
UNKNOWN_FROM = D(2026, 10, 8)


def wear_port(heart_status: str) -> FakeHealthData:
    """9-15~10-15 매일 같은 잠과 걸음. 낮 심박은 70, 다섯 날만 80 이고 10월 8일부터는 95 다."""
    days = [D(2026, 9, 15) + timedelta(days=i) for i in range((TODAY - D(2026, 9, 15)).days + 1)]

    def hr(d: date) -> float:
        return 95.0 if d >= UNKNOWN_FROM else 80.0 if d in HIGH_HR_DAYS else 70.0

    def sleep(d: date) -> SleepSession:
        wake = datetime(d.year, d.month, d.day, 7, 0, tzinfo=KST)
        return SleepSession(
            d, wake - timedelta(hours=8), wake, True, 450, deep_minutes=60, awake_minutes=30, avg_heart_rate=60.0
        )

    success = SyncState(at(15, 11, 58), "success", at(15, 11, 58))
    heart = success if heart_status == "success" else SyncState(at(15, 11, 58), "fail", at(8, 9, 0))
    return FakeHealthData(
        steps={"u1": [StepDay(d, 6000) for d in days]},
        heart={"u1": [HeartDay(d, hr(d), hours=20, sample_count=200) for d in days]},
        sleep={"u1": [sleep(d) for d in days]},
        sync={"u1": {"steps": SyncState(at(15, 12, 5), "success", at(15, 12, 5)), "heart_rate": heart}},
        linked_from={"u1": D(2026, 9, 1)},
        now=NOW,
    )


HIGH_HR_SENTENCE = (
    "워치를 충분히 찬 날 중 낮 심박이 이 기간 보통보다 5bpm 이상 높았던 날(75.0bpm 이상) 다음 잠은 수면 시간이 "
    "평균 7시간 30분(450분), 그렇지 않은 날 다음 잠은 7시간 30분(450분)으로 차이가 없고, 깊은 잠은 차이가 없고, "
    "수면 효율은 차이가 없고, 자다가 깬 시간은 차이가 없고, 수면 중 심박은 차이가 없었다({true}일 대 16일)."
)


@pytest.mark.parametrize(("heart_status", "true_nights"), [("fail", 5), ("success", 12)])
async def test_days_with_unknown_wear_are_left_out_of_day_heart_rate(heart_status, true_nights):
    # 착용을 모르는 10월 8~14일(D)은 낮 심박 조건의 그룹과 중앙값에서 빠진다.
    # 수집이 정상이면 그날도 충분히 착용한 날이다
    out = await get_sleep_records(GetSleepRecords(analysis="factors"), request(wear_port(heart_status)))
    assert HIGH_HR_SENTENCE.format(true=true_nights) in out.payload["text"]


async def test_include_non_main_with_analysis_uses_main_sleep_only():
    port = sample_port()
    out = await get_sleep_records(GetSleepRecords(analysis="pattern", include_non_main=True), request(port))
    assert out.payload["non_main_note"] == "분석에는 낮잠 같은 짧은 잠을 넣지 않았다."
    # 전체 수면 세션은 조회하지 않는다
    assert {call[0] for call in port.calls} == {"get_metric_result"}
    plain = await get_sleep_records(GetSleepRecords(analysis="pattern"), request(sample_port()))
    assert "non_main_note" not in plain.payload
    assert {k: v for k, v in out.payload.items() if k != "non_main_note"} == plain.payload


@pytest.mark.parametrize(
    ("analysis", "period"),
    [
        # 오늘(10-22) 기준 조회할 수 있는 가장 이른 날짜는 2025-10-22 다. 기간은 그 안이지만 앞선 기록이 그 전이다
        ("pattern", "dates:2025-11-01..2025-11-14"),
        ("signals", "dates:2025-10-25..2025-11-24"),
        ("factors", "dates:2025-10-23..2025-11-19"),
    ],
)
async def test_period_needing_records_before_lookup_limit_is_not_analyzed(analysis, period):
    out, port = await run(analysis=analysis, period=period)
    assert out.status == "unavailable_period"
    assert out.payload["tool"] == "get_sleep_records" and out.payload["period"] == period
    assert out.payload["message"] == (
        "수면 분석은 기간 앞의 평소와 비교할 기록까지 조회해 2025년 10월 22일(12개월 전)보다 앞선 기록이 필요한 기간은 "
        "분석할 수 없어요. 이 기간은 분석할 수 없다고 말하고 더 최근 기간을 제안해 주세요"
    )
    assert out.evidence == () and port.calls == []


async def test_period_whose_records_start_on_lookup_limit_is_analyzed():
    out, port = await run(analysis="factors", period="dates:2025-10-24..2025-11-20")
    assert out.status == "ok"
    assert min(start for metric, start, _ in lookups(port) if metric == "sleep") == D(2025, 10, 22)


async def test_unavailable_period_of_analysis_uses_period_rules():
    out, port = await run(analysis="signals", period="dates:2027-01-01..2027-01-05")
    assert out.status == "unavailable_period" and port.calls == []


async def test_gateway_logs_analysis_args_and_every_lookup():
    fixture = gf.load_fixture("synth-11")
    port = fixture.port()
    audit = MemoryAuditSink()
    gateway = ToolGateway(port, SYNTH11_TODAY, audit=audit, evidence_enabled=frozenset())
    ctx = CoachContext(user_id=fixture.user_id, conversation_id="c-336", request_id="r-336")
    args = {"analysis": "factors"}
    [reply] = await gateway.execute([ToolCall("c1", "get_sleep_records", args)], ctx)
    message = json.loads(reply.content)
    assert message["evidence_ids"] == ["E1"]
    assert message["result"]["text"] == SYNTH11_FACTORS
    [record] = audit.records()
    assert (record.args, record.period_dates) == (args, {})
    # 수면 30일(28일과 앞 이틀)과 걸음·심박·운동 28일씩. 계산에 쓴 조회를 모두 센다
    assert (record.result_status, record.result_count) == ("ok", 30 + 3 * 28)
    # 근거 이벤트로만 보내는 낮 기록 근거도 이 호출이 만든 근거라 호출 로그에 순서대로 들어간다
    assert record.evidence_ids == tuple(e.id for e in reply.evidence)
    assert [(e.ref, e.metric) for e in reply.evidence] == [
        ("E1", "sleeps"),
        (None, "steps"),
        (None, "heart_rates"),
        (None, "exercises"),
    ]


# 분석 근거의 기간과 지표. 근거 번호를 받는 근거는 결과 글 기간의 주 수면 하나이고, 비교한 앞선 기간의 주 수면과
# 생활 요인 비교의 낮 기록은 근거 이벤트로만 보낸다. 근거 카드의 주 기간은 결과 글의 기간이 된다
def evidence_rows(drafts) -> list[tuple]:
    return [(d.metric, d.period.start, d.period.end) for d in drafts]


@pytest.mark.parametrize(
    ("period", "main", "baseline"),
    [
        (None, (D(2026, 10, 9), SYNTH11_TODAY), (D(2026, 9, 11), D(2026, 10, 8))),
        ("recent_days:7", (D(2026, 10, 16), SYNTH11_TODAY), (D(2026, 9, 18), D(2026, 10, 15))),
        ("weeks_ago:1", (D(2026, 10, 12), D(2026, 10, 18)), (D(2026, 9, 14), D(2026, 10, 11))),
        ("recent_days:28", (D(2026, 9, 25), SYNTH11_TODAY), (D(2026, 8, 28), D(2026, 9, 24))),
    ],
    ids=["default", "seven-days", "last-week", "28-days"],
)
async def test_pattern_evidence_is_the_period_and_the_28_days_before_it(period, main, baseline):
    out, port = await run(analysis="pattern", period=period)
    assert evidence_rows(out.evidence) == [("sleeps", *main)]
    assert evidence_rows(out.event_only_evidence) == [("sleeps", *baseline)]
    user = port.calls[0][1]
    for draft, (start, end) in [(out.evidence[0], main), (out.event_only_evidence[0], baseline)]:
        assert draft.snapshot == (await port.get_metric_result(user, "sleep", start, end)).snapshot


@pytest.mark.parametrize(
    ("period", "main", "baseline", "rest"),
    [
        # 수면 중 심박 기준선(9월 18일-10월 15일)이 분석 기간과 겹쳐 근거로 내보내지 않고 계산에만 쓴다
        (None, (D(2026, 10, 9), SYNTH11_TODAY), None, [(D(2026, 9, 18), D(2026, 10, 8))]),
        ("recent_days:28", (D(2026, 9, 25), SYNTH11_TODAY), None, [(D(2026, 9, 18), D(2026, 9, 24))]),
        # 기간이 7일 이하면 기준선이 기간 앞에서 끝나 근거 카드에 비교로 보인다
        ("weeks_ago:1", (D(2026, 10, 12), D(2026, 10, 18)), (D(2026, 9, 14), D(2026, 10, 11)), []),
        ("recent_days:7", (D(2026, 10, 16), SYNTH11_TODAY), (D(2026, 9, 18), D(2026, 10, 15)), []),
        # 기간이 7일보다 짧으면 최근 7일 중 기간 밖 날짜는 계산에만 쓴다
        (
            "days_ago:0",
            (SYNTH11_TODAY, SYNTH11_TODAY),
            (D(2026, 9, 18), D(2026, 10, 15)),
            [(D(2026, 10, 16), D(2026, 10, 21))],
        ),
    ],
    ids=["default", "28-days", "last-week", "seven-days", "today"],
)
async def test_signals_sends_the_heart_rate_baseline_only_when_it_ends_before_the_period(period, main, baseline, rest):
    out, port = await run(analysis="signals", period=period)
    assert evidence_rows(out.evidence) == [("sleeps", *main)]
    assert evidence_rows(out.event_only_evidence) == ([] if baseline is None else [("sleeps", *baseline)])
    expected = [main, *([] if baseline is None else [baseline]), *rest]
    assert sorted(lookups(port)) == sorted(("sleep", start, end) for start, end in expected)


@pytest.mark.parametrize(
    ("args", "asks_exercise", "main", "days"),
    [
        ({}, False, (D(2026, 9, 25), SYNTH11_TODAY), (D(2026, 9, 24), D(2026, 10, 21))),
        ({"period": "recent_days:21"}, False, (D(2026, 10, 2), SYNTH11_TODAY), (D(2026, 10, 1), D(2026, 10, 21))),
        # 최근 14일은 운동한 날이 없어 최근 28일로 다시 계산하므로 근거도 최근 28일 계산의 것이다
        ({"period": "recent_days:14"}, False, (D(2026, 9, 25), SYNTH11_TODAY), (D(2026, 9, 24), D(2026, 10, 21))),
        # 짧은 기간에 운동 비교를 덧붙이면 주 수면 근거는 질문한 기간이고 낮 기록은 최근 28일의 조건 날짜다
        ({"period": "recent_days:7"}, True, (D(2026, 10, 16), SYNTH11_TODAY), (D(2026, 9, 24), D(2026, 10, 21))),
        ({"period": "recent_days:7"}, False, (D(2026, 10, 16), SYNTH11_TODAY), None),
    ],
    ids=["default", "21-days", "recalculated", "short-with-exercise", "short"],
)
async def test_factors_evidence_adds_the_day_records_used_in_the_calculation(args, asks_exercise, main, days):
    out, port = await run(asks_exercise=asks_exercise, analysis="factors", **args)
    assert evidence_rows(out.evidence) == [("sleeps", *main)]
    if days is None:
        assert out.event_only_evidence == ()
        return
    assert evidence_rows(out.event_only_evidence) == [(m, *days) for m in ("steps", "heart_rates", "exercises")]
    user = port.calls[0][1]
    for draft, metric in zip(out.event_only_evidence, ("steps", "heart_rate", "exercise"), strict=True):
        assert draft.snapshot == (await port.get_metric_result(user, metric, *days)).snapshot


async def test_split_lookups_do_not_use_up_the_six_calls_of_one_answer():
    # 한 답의 조회 한도(6번)는 도구 호출을 센다. 분석이 포트를 여러 번 조회해도(생활 요인 비교를 다시 계산하면 10번)
    # 호출 하나로 세고, 도구 메시지의 근거 번호는 근거 번호를 받는 근거만 차례로 센다
    fixture = gf.load_fixture("synth-11")
    port = fixture.port()
    audit = MemoryAuditSink()
    gateway = ToolGateway(port, SYNTH11_TODAY, audit=audit, evidence_enabled=frozenset())
    ctx = CoachContext(user_id=fixture.user_id, conversation_id="c-429", request_id="r-429")
    calls = [
        ToolCall("c1", "get_sleep_records", {"analysis": "pattern"}),
        ToolCall("c2", "get_sleep_records", {"analysis": "signals"}),
        ToolCall("c3", "get_sleep_records", {"analysis": "factors", "period": "recent_days:14"}),
        ToolCall("c4", "get_metric_summary", {"metric": "steps", "period": "last_7_days"}),
        ToolCall("c5", "compare_periods", {"metric": "sleep", "current": "this_week", "baseline": "last_week"}),
        ToolCall("c6", "get_daily_trend", {"metric": "heart_rate", "period": "last_7_days"}),
    ]
    replies = await gateway.execute(calls, ctx)
    [late] = await gateway.execute([ToolCall("c7", "get_sleep_records", {"period": "last_7_days"})], ctx)

    assert [json.loads(r.content)["evidence_ids"] for r in replies] == [
        ["E1"],
        ["E2"],
        ["E3"],
        ["E4"],
        ["E5", "E6"],
        ["E7"],
    ]
    evidence = [e for r in replies for e in r.evidence]
    assert [e.position for e in evidence] == list(range(1, len(evidence) + 1))
    assert [e.ref for e in evidence if e.ref is not None] == [f"E{i}" for i in range(1, 8)]
    assert late.content == CALL_LIMIT_MESSAGE
    assert [(r.decision, r.reason) for r in audit.records()] == [("allow", None)] * 6 + [("deny", "call_limit")]
    assert len(port.calls) > MAX_TOOL_CALLS


# 기본 조회의 두 줄 (줄어드는 중 판정 값, 어젯밤 대 그 전 7일)
async def test_basic_records_of_recent_days_get_two_lines_at_the_end():
    out, port = await run(period="recent_days:7")
    assert out.payload["sleep_vs_baseline"] == SYNTH11_DECLINING
    assert out.payload["last_night_vs_week"] == SYNTH11_LAST_NIGHT
    assert list(out.payload)[-2:] == list(LINES)
    # 두 줄은 오늘까지 42일(최근 14일과 그 전 28일) 수면으로 계산한다. 근거는 지금처럼 기간의 기록 하나다
    assert ("sleep", D(2026, 9, 11), SYNTH11_TODAY) in lookups(port)
    [draft] = out.evidence
    assert (draft.period.start, draft.period.end) == (D(2026, 10, 16), SYNTH11_TODAY)


@pytest.mark.parametrize(
    ("period", "shown"),
    [
        ("days_ago:0", True),
        ("days_ago:1", True),
        ("recent_days:14", True),
        ("dates:10-08..10-21", True),
        ("recent_days:15", False),
        ("dates:10-07..10-21", False),
        ("days_ago:2", False),
        ("weeks_ago:1", False),
        ("recent_days:31", False),
    ],
)
async def test_two_lines_only_for_periods_ending_today_or_yesterday_within_fourteen_days(period, shown):
    out, port = await run(period=period)
    assert [name in out.payload for name in LINES] == [shown, shown]
    assert (("sleep", D(2026, 9, 11), SYNTH11_TODAY) in lookups(port)) == shown


async def test_two_lines_are_about_today_even_when_period_ends_yesterday():
    out, _ = await run(period="days_ago:1")
    assert (out.payload["sleep_vs_baseline"], out.payload["last_night_vs_week"]) == (
        SYNTH11_DECLINING,
        SYNTH11_LAST_NIGHT,
    )


async def test_missing_last_night_is_stated_as_no_record():
    # sample_port 에는 10-15 에 깬 수면이 없다
    out = await get_sleep_records(GetSleepRecords(period="days_ago:0"), request(sample_port()))
    assert out.payload["last_night_vs_week"].startswith("어젯밤(10월 15일에 깬 수면)은 기록이 없다.")


class FailingPort(FakeHealthData):
    async def get_metric_result(self, user_id, metric, start, end):
        raise HealthDataUnavailableError(metrics=(metric,))


async def test_other_fields_stay_as_they_were_and_lines_drop_when_their_lookup_fails():
    req, _ = synth11()
    with_lines = await get_sleep_records(GetSleepRecords(period="recent_days:7"), req)
    failing = ToolRequest(
        req.user_id, req.today, req.port, aux_port=FailingPort(**vars(gf.load_fixture("synth-11").port()))
    )
    without = await get_sleep_records(GetSleepRecords(period="recent_days:7"), failing)
    assert not any(name in without.payload for name in LINES)
    assert {k: v for k, v in with_lines.payload.items() if k not in LINES} == without.payload
    assert without.evidence[0].snapshot == with_lines.evidence[0].snapshot


async def test_gateway_counts_only_the_period_of_basic_records():
    fixture = gf.load_fixture("synth-11")
    port = fixture.port()
    audit = MemoryAuditSink()
    gateway = ToolGateway(port, SYNTH11_TODAY, audit=audit, evidence_enabled=frozenset())
    ctx = CoachContext(user_id=fixture.user_id, conversation_id="c-336", request_id="r-336")
    [reply] = await gateway.execute([ToolCall("c1", "get_sleep_records", {"period": "recent_days:7"})], ctx)
    assert json.loads(reply.content)["result"]["last_night_vs_week"] == SYNTH11_LAST_NIGHT
    [record] = audit.records()
    # 두 줄 계산에 쓴 42일 조회는 조회한 날짜 수와 결과 상태에 넣지 않는다
    assert (record.result_status, record.result_count, len(record.evidence_ids)) == ("ok", 7, 1)
    assert ("sleep", D(2026, 9, 11), SYNTH11_TODAY) in lookups(port)


# 산소포화도 90% 미만 시각 표시 (수면 기록과 이상 신호가 같은 함수를 쓴다)
def test_low_times_text_groups_times_by_the_sleep_named_by_wake_date():
    # 오늘은 10월 14일이다. 13일에 깬 잠은 깬 날로, 오늘 0시 10분에 잠들어 오늘 깬 밤잠은 "어젯밤"으로 부른다
    first, second = datetime(2026, 10, 12, 23, 30, tzinfo=KST), datetime(2026, 10, 14, 0, 10, tzinfo=KST)
    times = [
        datetime(2026, 10, 13, 1, 0, tzinfo=KST),
        datetime(2026, 10, 13, 1, 30, tzinfo=KST),
        datetime(2026, 10, 13, 1, 30, tzinfo=KST),
        datetime(2026, 10, 14, 1, 20, tzinfo=KST),
    ]
    slept, woke = [first, first, first, second], [date(2026, 10, 13)] * 3 + [date(2026, 10, 14)]
    today = date(2026, 10, 14)
    assert (
        sleep_coaching_text.low_times_text(times, slept, 2, woke, today)
        == "깬 날 기준 10월 13일 수면 새벽 1시·1시 30분·1시 30분, 어젯밤 새벽 1시 20분 외 2회"
    )
    assert sleep_coaching_text.low_times_text(times[:1], slept[:1], 0, woke[:1], today) == (
        "깬 날 기준 10월 13일 수면 새벽 1시"
    )


def test_low_times_text_calls_the_sleep_from_yesterday_last_night():
    # 어제 밤 11시 30분에 잠들어 오늘 깬 잠은 "어젯밤"이다. "어젯밤" 바로 뒤의 "밤"은 겹쳐 빼고, 같은 잠 안에서
    # 시간대 말이 바뀌면 다시 쓴다
    slept = datetime(2026, 10, 13, 23, 30, tzinfo=KST)
    times = [datetime(2026, 10, 13, 23, 50, tzinfo=KST), datetime(2026, 10, 14, 2, 0, tzinfo=KST)]
    text = sleep_coaching_text.low_times_text(times, [slept, slept], 0, [date(2026, 10, 14)] * 2, date(2026, 10, 14))
    assert text == "깬 날 기준 어젯밤 11시 50분·새벽 2시"


async def test_records_and_signals_write_the_same_low_times():
    listed = (
        "(깬 날 기준 10월 13일 수면 새벽 3시 20분, 10월 16일 수면 새벽 4시 43분, 10월 17일 수면 새벽 3시 12분, "
        "10월 18일 수면 새벽 4시 42분, 10월 19일 수면 새벽 3시 46분 외 3회)"
    )
    records, _ = await run(period="recent_days:14")
    signals, _ = await run(analysis="signals")
    assert records.payload["spo2_low"] == (f"산소포화도를 기록한 14일 중 90% 미만으로 기록된 날 7일, 기록 8회 {listed}")
    assert f"기록은 8회{listed}다." in signals.payload["text"]


# pattern 의 어젯밤 대 그 전 7일 줄 (기본 조회의 줄과 같은 조건·같은 문장)과 함께 붙는 수면 시간 문장
async def test_pattern_ending_today_or_yesterday_gets_last_night_line():
    out, _ = await run(analysis="pattern", period="recent_days:28")
    assert out.payload["last_night_vs_week"] == SYNTH11_LAST_NIGHT
    assert list(out.payload)[-2:] == ["sleep_time_note", "last_night_vs_week"]
    assert out.payload["sleep_time_note"] == SLEEP_TIME_NOTE
    # 기간이 어제로 끝나도 기준일은 오늘이라 오늘 깬 수면까지 같은 호출에서 조회한다. 판정은 기간 안의 수면으로만
    # 한다. 오늘 하루는 어젯밤 줄에만 쓰여 근거 없이 계산에만 쓰는 조회다
    out, port = await run(analysis="pattern", period="dates:10-08..10-21")
    assert out.payload["last_night_vs_week"] == SYNTH11_LAST_NIGHT
    assert out.payload["sleep_time_note"] == SLEEP_TIME_NOTE
    period = DateRange(D(2026, 10, 8), D(2026, 10, 21))
    assert out.payload["text"] == pattern_text(pattern(synth11_input(), period), SYNTH11_TODAY)
    assert lookups(port) == [
        ("sleep", D(2026, 10, 8), D(2026, 10, 21)),
        ("sleep", D(2026, 9, 10), D(2026, 10, 7)),
        ("sleep", SYNTH11_TODAY, SYNTH11_TODAY),
    ]
    assert (out.evidence[0].period.start, out.evidence[0].period.end) == (D(2026, 10, 8), D(2026, 10, 21))
    [baseline] = out.event_only_evidence
    assert (baseline.period.start, baseline.period.end) == (D(2026, 9, 10), D(2026, 10, 7))


@pytest.mark.parametrize(
    ("args", "lookup_end"),
    [
        ({"analysis": "pattern", "period": "weeks_ago:1"}, D(2026, 10, 18)),
        ({"analysis": "pattern", "period": "days_ago:2"}, D(2026, 10, 20)),
        ({"analysis": "signals"}, SYNTH11_TODAY),
        ({"analysis": "factors"}, SYNTH11_TODAY),
    ],
)
async def test_last_night_line_and_sleep_time_note_only_in_pattern_ending_today_or_yesterday(args, lookup_end):
    out, port = await run(**args)
    assert "last_night_vs_week" not in out.payload and "sleep_time_note" not in out.payload
    assert lookups(port)[0][0] == "sleep" and lookups(port)[0][2] == lookup_end


async def test_pattern_without_last_night_states_no_record():
    # sample_port 에는 10-15 에 깬 수면이 없다
    out = await get_sleep_records(GetSleepRecords(analysis="pattern"), request(sample_port()))
    assert out.payload["last_night_vs_week"].startswith("어젯밤(10월 15일에 깬 수면)은 기록이 없다.")
    # 어젯밤 줄이 붙으면 수면 시간 문장도 함께 붙는다
    assert out.payload["sleep_time_note"] == SLEEP_TIME_NOTE


@pytest.mark.parametrize("fields", [{"sleep_time_note": SLEEP_TIME_NOTE}, {"last_night_vs_week": SYNTH11_LAST_NIGHT}])
async def test_analysis_result_fills_sleep_time_note_and_last_night_line_together(fields):
    out, _ = await run(analysis="signals")
    base = {k: v for k, v in out.payload.items() if k not in ("sleep_time_note", "last_night_vs_week")}
    with pytest.raises(ValidationError):
        SleepAnalysisResult.model_validate({**base, **fields})
    assert SleepAnalysisResult.model_validate(base).model_dump(mode="json", exclude_none=True) == out.payload


# 수면 기록 결과의 수면 시간 문장 (calculation-rules.md 7.5 sleep_minutes, 11.1 잠든 시각)
@pytest.mark.parametrize("args", [{"period": "recent_days:7"}, {"period": "recent_days:31", "include_non_main": True}])
async def test_sleep_records_state_how_sleep_time_is_counted_before_nights(args):
    out, _ = await run(**args)
    assert out.payload["sleep_time_note"] == SLEEP_TIME_NOTE
    keys = list(out.payload)
    assert keys.index("sleep_time_note") == keys.index("nights") - 1


async def test_sleep_time_note_only_in_records_with_nights():
    empty = FakeHealthData(now=datetime(2026, 10, 15, 12, 0, tzinfo=KST))
    out = await get_sleep_records(GetSleepRecords(period="days_ago:0"), ToolRequest("u9", TODAY, empty))
    assert out.payload["nights"] == [] and "sleep_time_note" not in out.payload


async def test_sleep_time_matches_records_without_awake_time():
    # 수면 시간은 깬 시간이 빠진 값이라 잠든 시각부터 깬 시각까지의 길이보다 짧다 (synth-11 최근 7일)
    out, port = await run(period="recent_days:7")
    result = await port.get_metric_result(port.calls[0][1], "sleep", D(2026, 10, 16), SYNTH11_TODAY)
    for night, raw in zip(out.payload["nights"], result.detail.nights, strict=True):
        session = round((raw.end_at - raw.start_at).total_seconds() / 60)
        assert night["total_min"] == raw.sleep_minutes and night["awake_min"] == raw.awake_minutes
        assert night["total_min"] + night["awake_min"] <= session
        assert night["awake_min"] == 0 or night["total_min"] < session


async def test_same_period_has_one_name_in_signals_pattern_and_factors():
    # 세 분석 모두 질문한 기간을 같은 이름으로 부르고, 이상 신호도 이번 주를 "최근 4일"로 부르지 않는다
    texts = [
        (await run(analysis=kind, period="weeks_ago:0"))[0].payload["text"]
        for kind in ("signals", "pattern", "factors")
    ]
    assert all(text.startswith("이번 주(10월 19일-22일) ") for text in texts)
    assert all("최근 4일" not in text for text in texts)
