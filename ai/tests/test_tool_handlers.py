import json
import re
from datetime import UTC, datetime

import pytest
import respx

from coach_ai.adapters.memory import HeartDay, SleepSession, StepDay, SyncState
from coach_ai.agent.context import CoachContext
from coach_ai.agent.events import ListSink
from coach_ai.agent.executor import ToolCall, ToolRequest
from coach_ai.agent.graph import run_coach
from coach_ai.llm.client import create_chat_model
from coach_ai.llm.metrics import MetricsRecorder
from coach_ai.ports import MetricResult, ResultContractError
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import ToolGateway
from coach_ai.tools.handlers import (
    PERIOD_LABELS,
    get_daily_trend,
    get_exercise_sessions,
    get_health_overview,
    get_metric_summary,
    get_sleep_records,
)
from coach_ai.tools.schemas import (
    PERIODS,
    GetDailyTrend,
    GetExerciseSessions,
    GetHealthOverview,
    GetMetricSummary,
    GetSleepRecords,
)
from eval.golden.fixtures import NOW as GOLDEN_NOW
from eval.golden.fixtures import load_fixture
from tests.fakes import (
    COMMON_RESULTS,
    KST,
    NOW,
    TODAY,
    D,
    FakeHealthData,
    at,
    exercise_port,
    night,
    request,
    sample_port,
    workout,
)
from tests.llm_fakes import CHAT_URL, text_response, tool_call_response

# 진행 중 값의 기준 시각이 오늘보다 이를 때 붙는 사실 문장
NOT_TODAY = "이 시각은 오늘이 아니고, 오늘 기록은 아직 반영되지 않았다"


async def test_steps_summary_separates_missing_and_in_progress():
    port = sample_port()
    out = await get_metric_summary(GetMetricSummary(metric="steps", period="last_7_days"), request(port))
    r = out.payload

    assert out.status == "ok"
    assert r["period"] == {"period": "last_7_days", "start": "2026-10-09", "end": "2026-10-15", "basis": "날짜(KST)"}
    assert (r["completed_days"], r["days_with_data"]) == (6, 3)
    assert r["missing_days"] == ["2026-10-10", "2026-10-12", "2026-10-14"]
    assert r["unsynced_days"] == []
    assert r["avg"] == {"value": 7000.0, "display": "7,000걸음"}
    assert r["total"] == {"value": 21000.0, "display": "21,000걸음"}  # 오늘 4,210걸음은 넣지 않는다
    # 기간 중 하루 값이 가장 낮았던 날과 가장 높았던 날. 공통 원형과 근거 snapshot 은 daily_min·daily_max 그대로다
    assert (r["lowest_day"]["date"], r["highest_day"]["date"]) == ("2026-10-11", "2026-10-09")
    assert "daily_min" not in r and "daily_max" not in r
    assert out.evidence[0].snapshot["summary"]["daily_max"] == {"date": "2026-10-09", "value": 8000}
    assert r["in_progress"]["value"] == 4210.0
    assert r["in_progress"]["display"] == "오늘 진행 중 4,210걸음 (12:05까지 기록 기준)"
    # 원형의 UTC 시각을 표시 문자열과 같은 한국 시간으로 전달한다
    assert r["in_progress"]["as_of"] == "2026-10-15T12:05:00+09:00"
    assert out.evidence[0].summary == (
        "지난 7일 걸음 수 평균 7,000걸음 (6일 중 3일 기록), 오늘 진행 중 4,210걸음 (12:05까지 기록 기준)"
    )


async def test_heart_summary_keeps_today_out_of_extremes():
    out = await get_metric_summary(GetMetricSummary(metric="heart_rate", period="this_week"), request(sample_port()))
    r = out.payload
    assert (r["period"]["start"], r["completed_days"]) == ("2026-10-12", 3)
    assert r["measured_max"]["date"] == "2026-10-14" and r["measured_max"]["display"] == "134bpm"
    assert r["avg"]["display"] == "71.3bpm"
    assert r["in_progress"]["display"] == "오늘 진행 중 71.5bpm (11:58까지 기록 기준)"


async def test_sleep_summary_reports_unsynced_today():
    out = await get_metric_summary(GetMetricSummary(metric="sleep", period="last_7_days"), request(sample_port()))
    r = out.payload
    assert r["period"]["basis"] == "수면 종료일(KST)"
    assert (r["completed_days"], r["days_with_data"]) == (7, 2)
    assert r["missing_days"] == ["2026-10-09", "2026-10-10", "2026-10-11", "2026-10-14"]
    assert r["unsynced_days"] == ["2026-10-15"]
    assert r["in_progress"] is None
    assert r["avg"]["display"] == "6시간 49분"
    assert out.evidence[0].summary == "지난 7일 수면 평균 6시간 49분 (7일 중 2일 기록, 동기화 전 1일)"


@pytest.mark.parametrize(
    ("linked", "coverage"),
    [
        (D(2026, 10, 12), "연동일 10/12 이후 4일 중 2일 기록, 동기화 전 1일"),
        (D(2026, 10, 9), "7일 중 2일 기록, 동기화 전 1일"),  # 기간 시작일과 같으면 빠진 날이 없다
    ],
)
async def test_coverage_shows_linked_from_after_period_start(linked, coverage):
    port = sample_port()
    port.linked_from = {"u1": linked}
    out = await get_metric_summary(GetMetricSummary(metric="sleep", period="last_7_days"), request(port))
    assert out.payload["coverage"] == coverage
    assert out.evidence[0].summary == f"지난 7일 수면 평균 6시간 49분 ({coverage})"

    out = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))
    assert out.payload["summary"]["coverage"] == coverage
    assert out.evidence[0].summary == f"지난 7일 평균 수면 6시간 49분 ({coverage}), 평균 취침 00:32"


async def test_sleep_records_nights_and_midnight_bedtime():
    out = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(sample_port()))
    r = out.payload

    assert [n["date"] for n in r["nights"]] == ["2026-10-12", "2026-10-13", "2026-10-14"]  # 낮잠은 빠진다
    assert [n["bedtime"] for n in r["nights"]] == ["23:50", "00:35", "01:10"]
    assert r["nights"][2]["total_min"] is None and r["nights"][2]["total"] is None
    s = r["summary"]
    assert s["bedtime"]["earliest"] == {"date": "2026-10-12", "time": "23:50"}
    assert s["bedtime"]["latest"] == {"date": "2026-10-14", "time": "01:10"}
    assert s["bedtime"]["average"] == "00:32"  # 단순 평균이면 정오 근처가 된다
    assert s["wake"]["average"] == "07:18"
    assert s["avg_total"] == {"minutes": 409, "display": "6시간 49분"}
    assert s["shortest"]["date"] == "2026-10-13" and s["longest"]["date"] == "2026-10-12"
    # 수면 시간을 모르는 14일은 결측이지만 밤 기록과 시각 범위에는 들어간다
    assert "2026-10-14" in s["missing_days"] and s["unsynced_days"] == ["2026-10-15"]
    assert s["linked_from"] == "2026-09-20"
    assert out.evidence[0].summary == "지난 7일 평균 수면 6시간 49분 (7일 중 2일 기록, 동기화 전 1일), 평균 취침 00:32"


async def test_sleep_records_include_daytime_primary_sleep_but_not_extra_nap():
    port = sample_port()
    port.sleep["u1"] = [
        night(14, (14, 9, 19), (16, 29), 407),
        night(14, (14, 18, 0), (18, 40), 38, main=False),
    ]
    out = await get_sleep_records(GetSleepRecords(period="days_ago:1"), request(port))
    nights = out.payload["nights"]
    assert len(nights) == 1
    assert (nights[0]["bedtime"], nights[0]["wake"], nights[0]["total_min"]) == ("09:19", "16:29", 407)
    assert out.payload["summary"]["avg_total"]["display"] == "6시간 47분"
    assert out.payload["summary"]["unsynced_days"] == []


async def test_empty_records():
    port = FakeHealthData(now=NOW)
    out = await get_sleep_records(GetSleepRecords(period="yesterday"), ToolRequest("u9", TODAY, port))
    assert out.payload["nights"] == [] and out.payload["summary"]["bedtime"] is None
    # 기록이 하나도 없는 회원은 linked_from 이 없어 모든 날짜가 연동 전이다
    assert out.payload["summary"]["completed_days"] == 0 and out.payload["summary"]["linked_from"] is None
    assert out.evidence[0].summary == "어제 수면 기록 없음 (연동 후 받은 기록 없음)"


async def test_evidence_holds_public_metric_and_result_snapshot():
    port = sample_port()
    out = await get_metric_summary(GetMetricSummary(metric="heart_rate", period="this_week"), request(port))
    expected = await port.get_metric_result("u1", "heart_rate", D(2026, 10, 12), TODAY)
    assert out.evidence[0].metric == "heart_rates"
    assert out.evidence[0].snapshot == expected.snapshot and "user_id" not in out.evidence[0].snapshot
    # LLM 에 전달하는 결과는 근거 snapshot 과 따로 둔다
    assert out.payload["metric"] == "heart_rate" and "days" not in out.payload

    out = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))
    assert (out.evidence[0].metric, out.evidence[0].snapshot["metric"]) == ("sleeps", "sleeps")
    assert len(out.evidence[0].snapshot["detail"]["nights"]) == 3


async def test_today_zero_steps_is_in_progress_without_record():
    port = sample_port()
    port.steps["u1"] = [*port.steps["u1"][:-1], StepDay(TODAY, 0)]
    out = await get_metric_summary(GetMetricSummary(metric="steps", period="this_week"), request(port))
    r = out.payload
    # 원형은 오늘을 unsynced_days 에 두지만 오늘은 통계 대상이 아니라 LLM 결과에서는 진행 중 값으로만 알린다
    assert out.evidence[0].snapshot["summary"]["unsynced_days"] == ["2026-10-15"]
    assert out.evidence[0].snapshot["in_progress"] == {
        "date": "2026-10-15", "value": 0, "missing": True, "as_of": "2026-10-15T03:05:00Z",
    }  # fmt: skip
    assert (r["completed_days"], r["days_with_data"], r["missing_days"], r["unsynced_days"]) == (
        3,
        1,
        ["2026-10-12", "2026-10-14"],
        [],
    )
    assert r["in_progress"]["value"] is None
    assert r["in_progress"]["display"] == "오늘 진행 중 기록 아직 없음 (12:05까지 기록 기준)"
    assert out.evidence[0].summary == (
        "이번 주 걸음 수 평균 7,000걸음 (3일 중 1일 기록), 오늘 진행 중 기록 아직 없음 (12:05까지 기록 기준)"
    )


@pytest.mark.parametrize(
    ("today", "value", "progress"),
    [
        ((), 0.0, "오늘 진행 중 0분 (12:05까지 기록 기준)"),
        ((workout(15, (7, 50), 50),), 50.0, "오늘 진행 중 50분 (12:05까지 기록 기준)"),
    ],
)
async def test_exercise_in_progress_stays_out_of_week_stats(today, value, progress):
    req = request(exercise_port(*today))
    out = await get_metric_summary(GetMetricSummary(metric="exercise", period="today"), req)
    assert (out.payload["in_progress"]["value"], out.payload["in_progress"]["display"]) == (value, progress)
    assert out.evidence[0].summary == f"운동 시간 {progress}"

    week = await get_metric_summary(GetMetricSummary(metric="exercise", period="this_week"), req)
    r = week.payload
    # 오늘 운동은 진행 중 값으로만 전달하고 이번 주 평균·합계에는 넣지 않는다
    assert (r["avg"]["display"], r["total"]["display"], r["missing_days"]) == ("15분", "30분", ["2026-10-14"])
    assert r["coverage"] == "3일 중 활동 기록이 있는 날 2일"
    assert week.evidence[0].summary == f"이번 주 운동 시간 평균 15분 (3일 중 활동 기록이 있는 날 2일), {progress}"


# 13일 느린 걷기가 끝난 시각. 운동 상태의 기준 시각이고 공통 결과의 in_progress.as_of 가 된다
LAST_EXERCISE = SyncState(at(13, 18, 20))


@pytest.mark.parametrize(
    ("sync", "as_of", "progress"),
    [
        (
            {"steps": SyncState(at(15, 12, 5)), "exercise": LAST_EXERCISE},
            "2026-10-15T12:05:00+09:00",
            "오늘 진행 중 0분 (12:05까지 기록 기준)",
        ),
        # 동기화 기준 시각이 오늘이 아니면 오늘 시각으로 읽히지 않게 날짜와 오늘이 아니라는 문장을 함께 적는다
        (
            {"steps": SyncState(at(14, 23, 50)), "exercise": LAST_EXERCISE},
            "2026-10-14T23:50:00+09:00",
            f"오늘 진행 중 0분. 10월 14일 23:50까지 기록 기준이다. {NOT_TODAY}",
        ),
        ({}, None, "오늘 진행 중 0분"),
    ],
)
async def test_exercise_in_progress_uses_sync_time_instead_of_last_exercise(sync, as_of, progress):
    port = exercise_port()
    port.sync = {"u1": sync}
    req = request(port)
    out = await get_metric_summary(GetMetricSummary(metric="exercise", period="today"), req)
    r = out.payload
    assert (r["in_progress"]["display"], r["in_progress"]["as_of"]) == (progress, as_of)
    assert out.evidence[0].summary == f"운동 시간 {progress}"
    # 마지막 운동 기록 시각(13일 18:20)은 근거 snapshot 에만 있고 LLM 결과에는 넣지 않는다
    text = json.dumps(r, ensure_ascii=False)
    assert "18:20" not in text and "마지막 운동 기록" not in text
    assert out.evidence[0].snapshot["in_progress"]["as_of"] == (None if not sync else "2026-10-13T09:20:00Z")

    trend = await get_daily_trend(GetDailyTrend(metric="exercise", period="this_week"), req)
    assert (trend.payload["in_progress"]["display"], trend.payload["in_progress"].get("as_of")) == (progress, as_of)
    assert trend.evidence[0].summary.endswith(f", {progress}")


async def test_steps_and_heart_in_progress_keep_their_own_basis_time():
    # 걸음 수·심박은 동기화 기준 시각(12:05)이 아니라 지표 기준 시각을 쓰고, 그 시각이 이전 날이면 날짜를 함께 적는다
    port = sample_port()
    port.sync = {
        "u1": {
            "steps": SyncState(at(14, 22, 0)),
            "heart_rate": SyncState(at(15, 11, 58)),
            "sleep": SyncState(at(15, 12, 5)),
        }
    }
    steps = await get_metric_summary(GetMetricSummary(metric="steps", period="today"), request(port))
    heart = await get_daily_trend(GetDailyTrend(metric="heart_rate", period="this_week"), request(port))
    steps_progress = f"오늘 진행 중 4,210걸음. 10월 14일 22:00까지 기록 기준이다. {NOT_TODAY}"
    assert steps.payload["in_progress"]["display"] == steps_progress
    assert steps.payload["in_progress"]["as_of"] == "2026-10-14T22:00:00+09:00"
    assert heart.payload["in_progress"]["display"] == "오늘 진행 중 71.5bpm (11:58까지 기록 기준)"
    assert steps.evidence[0].summary == f"걸음 수 {steps_progress}"


def stale_port(as_of: datetime) -> FakeHealthData:
    """오늘 기록이 아직 없고 모든 지표의 기준 시각이 as_of 인 회원. 동기화가 늦은 골든셋 synth-09 와 같은 경우다."""
    days = [D(2026, 10, d) for d in (12, 13, 14)]
    return FakeHealthData(
        steps={"u1": [StepDay(d, 6000) for d in days]},
        heart={"u1": [HeartDay(d, 70.0, sample_count=120) for d in days]},
        exercise={"u1": [workout(14, (7, 0), 30)]},
        sync={"u1": {metric: SyncState(as_of) for metric in ("steps", "heart_rate", "exercise")}},
        linked_from={"u1": D(2026, 9, 20)},
        now=NOW,
    )


@pytest.mark.parametrize(
    ("as_of", "basis", "before_today"),
    [
        (at(14, 23, 30), "10월 14일 23:30", True),
        (at(11, 9, 0), "10월 11일 09:00", True),
        (at(14, 23, 59), "10월 14일 23:59", True),
        (at(15, 0, 1), "00:01", False),
    ],
    ids=["yesterday", "days_ago", "before_midnight", "after_midnight"],
)
async def test_today_notices_add_date_when_basis_time_is_not_today(as_of, basis, before_today):
    port = stale_port(as_of)
    # 오늘보다 이른 기준 시각은 "오늘"과 다른 문장에 두고, 그 시각이 오늘이 아니라는 문장을 뒤에 붙인다
    if before_today:
        progress = f"오늘 진행 중 기록 아직 없음. {basis}까지 기록 기준이다. {NOT_TODAY}"
        session_note = f"오늘은 진행 중이다. {basis}까지 기록 기준이다. {NOT_TODAY}"
        session_line = f"오늘 운동 기록 없음. {basis}까지 기록 기준이다. {NOT_TODAY}"
    else:
        progress = f"오늘 진행 중 기록 아직 없음 ({basis}까지 기록 기준)"
        session_note = f"오늘은 진행 중이라 {basis}까지 기록 기준이다"
        session_line = f"오늘 운동 기록 없음, 오늘 {basis}까지 기록 기준"
    for metric, label in (("steps", "걸음 수"), ("heart_rate", "심박"), ("exercise", "운동 시간")):
        out = await get_metric_summary(GetMetricSummary(metric=metric, period="today"), request(port))
        assert out.payload["in_progress"]["display"] == progress, metric
        assert out.evidence[0].summary == f"{label} {progress}", metric
    overview = await get_health_overview(GetHealthOverview(period="this_week"), request(port))
    for metric in ("steps", "exercise", "heart_rate"):
        assert overview.payload[metric]["in_progress"]["display"] == progress, metric
    sessions = await get_exercise_sessions(GetExerciseSessions(period="today"), request(port))
    assert sessions.payload["in_progress_note"] == session_note
    assert sessions.evidence[0].summary == session_line


def _strings(value) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) else []


_PAST_TIME = re.compile(r"(\d{1,2})월 (\d{1,2})일 \d{2}:\d{2}")


def _today_with_past_time(texts: list[str], today) -> list[str]:
    """문장 중 "오늘"과 오늘보다 이른 날짜의 시각("10월 20일 23:30")이 함께 든 문장."""
    sentences = [s for text in texts for s in re.split(r"(?<=\.)\s+", text)]
    return [
        s
        for s in sentences
        if "오늘" in s
        and any((int(m.group(1)), int(m.group(2))) < (today.month, today.day) for m in _PAST_TIME.finditer(s))
    ]


def _synth_09_request() -> ToolRequest:
    """동기화가 10월 20일 23:30 에 멈춘 골든셋 synth-09 고정 데이터로 만든 요청."""
    fixture = load_fixture("synth-09")
    return ToolRequest(user_id=fixture.user_id, today=GOLDEN_NOW.date(), port=fixture.port(), request_id="r-synth-09")


async def test_golden_09_018_result_keeps_today_apart_from_past_basis_time():
    """골든셋 synth-09 고정 데이터로 09-018 이 받는 결과(오늘 걸음 요약)를 만든다.

    결과 문장 중 "오늘"과 오늘보다 이른 날짜의 시각이 한 문장에 든 곳이 없다.
    """
    out = await get_metric_summary(GetMetricSummary(metric="steps", period="today"), _synth_09_request())

    display = out.payload["in_progress"]["display"]
    assert display == f"오늘 진행 중 기록 아직 없음. 10월 20일 23:30까지 기록 기준이다. {NOT_TODAY}"
    assert _today_with_past_time(_strings(out.to_message(["E1"])), GOLDEN_NOW.date()) == []


@pytest.mark.parametrize("period", ["today", "this_week", "last_7_days", "days_ago:1..days_ago:0"])
async def test_golden_synth_09_exercise_sessions_keep_today_apart_from_past_basis_time(period):
    """같은 고정 데이터의 운동 세션 결과도 "오늘"과 오늘보다 이른 날짜의 시각을 다른 문장에 둔다."""
    out = await get_exercise_sessions(GetExerciseSessions(period=period), _synth_09_request())

    assert out.status == "ok"
    texts = [*_strings(out.to_message(["E1"])), out.evidence[0].summary]
    assert _today_with_past_time(texts, GOLDEN_NOW.date()) == []
    assert "10월 20일 23:30까지 기록 기준이다." in json.dumps(out.payload, ensure_ascii=False)


@pytest.mark.parametrize(
    ("values", "metric", "display"),
    [
        ({12: 72.2, 13: 72.3}, "heart_rate", "72.3bpm"),  # 공개 평균 72.25. 부동소수 round 면 72.2bpm
        ({12: 396, 13: 397}, "sleep", "6시간 37분"),  # 공개 평균 396.5분
    ],
)
async def test_display_rounds_public_average_half_up(values, metric, display):
    if metric == "heart_rate":
        port = FakeHealthData(
            heart={"u1": [HeartDay(D(2026, 10, d), v, 55, 120, sample_count=120) for d, v in values.items()]}, now=NOW
        )
    else:
        port = FakeHealthData(sleep={"u1": [night(d, (d - 1, 23, 30), (7, 0), v) for d, v in values.items()]}, now=NOW)
    out = await get_metric_summary(GetMetricSummary(metric=metric, period="last_7_days"), request(port))
    assert out.payload["avg"]["display"] == display


def test_every_period_has_a_distinct_label():
    assert set(PERIOD_LABELS) == set(PERIODS)
    assert len(set(PERIOD_LABELS.values())) == len(PERIOD_LABELS)
    assert PERIOD_LABELS["week_before_last"] == "지지난주"


def week_before_last_port() -> FakeHealthData:
    """TODAY(10-15 목) 기준 지지난주(9-28~10-04)에 걸음·수면 기록이 있다.

    10-01 은 0보, 9-30·10-03 은 걸음 기록이 없다. 10-04(일) 밤에 자고 10-05(월) 아침에 깬 잠도 있다.
    """
    steps = [(9, 28, 6000), (9, 29, 7000), (10, 1, 0), (10, 2, 8000), (10, 4, 5000)]
    nights = [
        ((9, 27, 23, 30), (9, 28, 7, 0), 420),
        ((10, 1, 1, 0), (10, 1, 7, 0), 360),
        ((10, 4, 23, 40), (10, 5, 7, 10), 400),
    ]
    sessions = [
        SleepSession(
            D(2026, *wake[:2]), datetime(2026, *bed, tzinfo=KST), datetime(2026, *wake, tzinfo=KST), True, minutes
        )
        for bed, wake, minutes in nights
    ]
    return FakeHealthData(
        steps={"u1": [StepDay(D(2026, m, d), v) for m, d, v in steps]},
        sleep={"u1": sessions},
        sync={"u1": {"steps": SyncState(at(15, 12, 5)), "sleep": SyncState(at(15, 7, 30))}},
        linked_from={"u1": D(2026, 9, 20)},
        now=NOW,
    )


async def test_week_before_last_is_labeled_in_evidence():
    req = request(week_before_last_port())
    summary = await get_metric_summary(GetMetricSummary(metric="steps", period="week_before_last"), req)
    trend = await get_daily_trend(GetDailyTrend(metric="steps", period="week_before_last"), req)
    records = await get_sleep_records(GetSleepRecords(period="week_before_last"), req)
    overview = await get_health_overview(GetHealthOverview(period="week_before_last"), req)
    assert summary.evidence[0].summary == "지지난주 걸음 수 평균 6,500걸음 (7일 중 4일 기록)"
    assert trend.evidence[0].summary == "지지난주 걸음 수 날짜별 기록 (7일 중 4일 기록)"
    assert records.evidence[0].summary == "지지난주 평균 수면 6시간 30분 (7일 중 2일 기록), 평균 취침 00:15"
    assert len(overview.evidence) == 4 and all(e.summary.startswith("지지난주 ") for e in overview.evidence)


async def test_week_before_last_sleep_uses_same_dates_by_wake_day():
    port = week_before_last_port()
    req = request(port)
    steps = await get_metric_summary(GetMetricSummary(metric="steps", period="week_before_last"), req)
    sleep = await get_metric_summary(GetMetricSummary(metric="sleep", period="week_before_last"), req)
    oxygen = await get_metric_summary(GetMetricSummary(metric="oxygen_saturation", period="week_before_last"), req)
    # 수면·산소포화도도 같은 월~일을 조회하고, 그 날짜를 깬 날로 읽는다
    assert [call[3:] for call in port.calls] == [(D(2026, 9, 28), D(2026, 10, 4))] * 3
    assert steps.payload["period"]["basis"] == "날짜(KST)"
    assert sleep.payload["period"]["basis"] == oxygen.payload["period"]["basis"] == "수면 종료일(KST)"
    # 9-27(일) 밤에 자고 9-28(월) 아침에 깬 잠은 지지난주, 10-04(일) 밤에 자고 10-05(월) 아침에 깬 잠은 지난주 수면이다
    records = await get_sleep_records(GetSleepRecords(period="week_before_last"), req)
    last = await get_sleep_records(GetSleepRecords(period="last_week"), req)
    assert [n["date"] for n in records.payload["nights"]] == ["2026-09-28", "2026-10-01"]
    assert [n["date"] for n in last.payload["nights"]] == ["2026-10-05"]


class Rewritten(FakeHealthData):
    """원형을 바꿔 반환하는 잘못된 구현."""

    def __init__(self, change, **kw) -> None:
        super().__init__(**kw)
        self.change = change

    async def get_metric_result(self, user_id, metric, start, end):
        result = await super().get_metric_result(user_id, metric, start, end)
        snapshot = {k: dict(v) if isinstance(v, dict) else v for k, v in result.snapshot.items()}
        self.change(snapshot)
        return MetricResult(result.user_id, snapshot)


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.update(metric="steps"),  # 다른 지표
        lambda s: s.update(period={"start": "2026-10-08", "end": "2026-10-15"}),  # 다른 기간
        lambda s: s.update(days=s["days"][1:]),  # 빠진 날짜
    ],
)
async def test_result_that_disagrees_with_request_is_contract_error(change):
    port = Rewritten(change, **vars(sample_port()))
    with pytest.raises(ResultContractError):
        await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))


async def test_missing_days_follow_prototype_without_reclassifying():
    # 날짜별 사유는 15일을 동기화 전으로 두지만 요약 목록이 기록 없음으로 적었으면 요약 목록을 그대로 전달한다
    def move(s):
        s["summary"].update(missing_days=[*s["summary"]["missing_days"], "2026-10-15"], unsynced_days=[])

    port = Rewritten(move, **vars(sample_port()))
    out = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))
    s = out.payload["summary"]
    assert s["missing_days"] == ["2026-10-09", "2026-10-10", "2026-10-11", "2026-10-14", "2026-10-15"]
    assert (s["unsynced_days"], s["coverage"]) == ([], "7일 중 2일 기록")


async def test_summary_counts_follow_prototype_even_when_they_do_not_add_up():
    # 원형 값끼리의 불일치는 검사하지 않고 원형의 일수를 그대로 전달한다
    port = Rewritten(lambda s: s["summary"].update(completed_days=6), **vars(sample_port()))
    out = await get_sleep_records(GetSleepRecords(period="last_7_days"), request(port))
    s = out.payload["summary"]
    assert (s["completed_days"], s["days_with_data"], s["coverage"]) == (6, 2, "6일 중 2일 기록, 동기화 전 1일")


class CommonResult:
    """연동 모듈의 공통 조회 함수가 만든 원형을 그대로 반환하는 포트."""

    def __init__(self, case: str) -> None:
        self.case = COMMON_RESULTS[case]

    async def get_metric_result(self, user_id, metric, start, end):
        return MetricResult(self.case["user_id"], self.case["snapshot"])


def common_request(case: str) -> ToolRequest:
    # 원형은 2026-10-22 13:53(KST)까지 동기화된 회원 기준이다
    return ToolRequest(COMMON_RESULTS[case]["user_id"], D(2026, 10, 22), CommonResult(case))


async def test_common_today_zero_steps_is_kept_in_snapshot_only():
    snapshot = COMMON_RESULTS["today_zero_steps"]["snapshot"]
    # 공통 원형은 오늘 0보를 저장된 0과 동기화 전 상태로 보존한다
    assert snapshot["days"] == [
        {"date": "2026-10-22", "value": 0, "missing": True, "reason": "UNSYNCED", "is_in_progress": True}
    ]
    out = await get_metric_summary(GetMetricSummary(metric="steps", period="today"), common_request("today_zero_steps"))
    r = out.payload
    assert (r["completed_days"], r["days_with_data"], r["missing_days"], r["unsynced_days"]) == (0, 0, [], [])
    assert (r["avg"], r["total"], r["in_progress"]["value"]) == (None, None, None)
    assert out.evidence[0].summary == "걸음 수 오늘 진행 중 기록 아직 없음 (13:53까지 기록 기준)"
    assert out.evidence[0].snapshot == snapshot


async def test_common_week_leaves_today_zero_steps_and_days_before_link_out_of_counts():
    out = await get_metric_summary(
        GetMetricSummary(metric="steps", period="last_7_days"), common_request("week_today_zero_steps")
    )
    r = out.payload
    # 원형의 unsynced_days 에 있는 오늘은 통계 대상이 아니다. 16일은 연동 전이라 완료 일수에서 빠진다
    assert out.evidence[0].snapshot["summary"]["unsynced_days"] == ["2026-10-22"]
    assert (r["completed_days"], r["days_with_data"]) == (5, 3)
    assert (r["missing_days"], r["unsynced_days"]) == (["2026-10-18", "2026-10-20"], [])
    assert (r["avg"]["display"], r["total"]["display"]) == ("7,293걸음", "21,880걸음")
    assert out.evidence[0].summary == (
        "지난 7일 걸음 수 평균 7,293걸음 (연동일 10/17 이후 5일 중 3일 기록), "
        "오늘 진행 중 기록 아직 없음 (13:53까지 기록 기준)"
    )


async def test_common_result_after_failed_sync_is_normal_result():
    # 최근 처리가 실패해도 이전에 성공한 집계로 만든 결과는 조회 실패가 아니다
    sink = MemoryAuditSink()
    gateway = ToolGateway(CommonResult("recent_failure"), D(2026, 10, 22), audit=sink)
    ctx = CoachContext(user_id=COMMON_RESULTS["recent_failure"]["user_id"], conversation_id="c", request_id="r")
    call = ToolCall("call_1", "get_metric_summary", {"metric": "steps", "period": "last_7_days"})
    [reply] = await gateway.execute([call], ctx)

    result = json.loads(reply.content)["result"]
    assert (result["avg"]["display"], result["completed_days"], result["days_with_data"]) == ("7,465걸음", 6, 4)
    assert (result["missing_days"], result["unsynced_days"]) == (["2026-10-18", "2026-10-20"], [])
    assert reply.evidence[0].snapshot["data_status"]["last_sync_status"] == "fail"
    [record] = sink.records()
    assert (record.decision, record.reason, record.result_count) == ("allow", None, 7)


async def test_common_result_without_synced_until_counts_gaps_as_unsynced():
    out = await get_metric_summary(
        GetMetricSummary(metric="steps", period="last_7_days"), common_request("synced_none")
    )
    r = out.payload
    assert (r["missing_days"], r["unsynced_days"]) == ([], ["2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21"])
    assert r["coverage"] == "6일 중 2일 기록, 동기화 전 4일"
    # 기준 시각이 없으면 진행 중 값에 시각을 붙이지 않는다
    assert r["in_progress"]["display"] == "오늘 진행 중 기록 아직 없음"


# 원형의 in_progress.as_of 는 마지막 운동 기록 시각(21일 19:40)이고, 안내는 동기화 기준 시각(22일 13:53)을 쓴다
EXERCISE_PROGRESS = "오늘 진행 중 0분 (13:53까지 기록 기준)"


async def test_common_exercise_summary_counts_zero_minute_days_as_activity_days():
    request = common_request("week_exercise")
    out = await get_metric_summary(GetMetricSummary(metric="exercise", period="last_7_days"), request)
    r = out.payload
    assert (r["unit"], r["period"]["basis"]) == ("min", "날짜(KST)")
    # 0분인 18·19일도 평균과 가장 낮은 날에 들어가고, 활동 기록이 없는 20일만 빠진다
    assert (r["avg"]["display"], r["total"]["display"]) == ("24분", "2시간 2분")
    assert r["lowest_day"] == {"value": 0.0, "display": "0분", "date": "2026-10-18"}
    assert (r["days_with_data"], r["completed_days"], r["missing_days"]) == (5, 6, ["2026-10-20"])
    assert r["coverage"] == "6일 중 활동 기록이 있는 날 5일"
    assert r["in_progress"] == {
        "date": "2026-10-22", "value": 0.0, "display": EXERCISE_PROGRESS, "as_of": "2026-10-22T13:53:00+09:00",
    }  # fmt: skip
    assert r["measured_min"] is None and "sessions" not in json.dumps(r)
    assert out.evidence[0].metric == "exercises"
    assert out.evidence[0].snapshot == COMMON_RESULTS["week_exercise"]["snapshot"]
    [evidence] = out.evidence
    assert evidence.summary == f"지난 7일 운동 시간 평균 24분 (6일 중 활동 기록이 있는 날 5일), {EXERCISE_PROGRESS}"


TREND_CASES = [
    ("week_sleep", "sleep", "last_7_days", 22),
    ("week_oxygen", "oxygen_saturation", "last_7_days", 22),
    ("heart_weighted", "heart_rate", "this_week", 21),
    ("week_today_zero_steps", "steps", "last_7_days", 22),
    ("recent_failure", "steps", "last_7_days", 22),
    ("synced_none", "steps", "last_7_days", 22),
    ("week_exercise", "exercise", "last_7_days", 22),
]


async def common_trend(case: str, metric: str, period: str, today: int):
    request = ToolRequest(COMMON_RESULTS[case]["user_id"], D(2026, 10, today), CommonResult(case))
    return await get_daily_trend(GetDailyTrend(metric=metric, period=period), request)


def empty_items(value, path: str = "result") -> list[str]:
    """null·false 인 항목의 경로."""
    if value is None or value is False:
        return [path]
    if isinstance(value, dict):
        return [p for key, item in value.items() for p in empty_items(item, f"{path}.{key}")]
    if isinstance(value, list):
        return [p for i, item in enumerate(value) for p in empty_items(item, f"{path}[{i}]")]
    return []


@pytest.mark.parametrize(
    ("case", "metric", "period", "today", "expected"),
    [
        # 16일 연동 전, 18일 실제 수면 시간 없음(NULL_VALUE), 19일 기록 없음(NO_RECORD), 22일 동기화 전
        ("week_sleep", "sleep", "last_7_days", 22, [
            {"date": "2026-10-16", "missing": "연동 전"},
            {"date": "2026-10-17", "value": 441.0, "display": "7시간 21분"},
            {"date": "2026-10-18", "missing": "기록 없음"},
            {"date": "2026-10-19", "missing": "기록 없음"},
            {"date": "2026-10-20", "value": 340.0, "display": "5시간 40분"},
            {"date": "2026-10-21", "value": 412.0, "display": "6시간 52분"},
            {"date": "2026-10-22", "missing": "동기화 전"},
        ]),
        # 20일은 수면은 있지만 산소포화도 측정이 없다(NULL_VALUE)
        ("week_oxygen", "oxygen_saturation", "last_7_days", 22, [
            {"date": "2026-10-16", "missing": "연동 전"},
            {"date": "2026-10-17", "value": 95.5, "display": "95.5%"},
            {"date": "2026-10-18", "value": 96.0, "display": "96%"},
            {"date": "2026-10-19", "missing": "기록 없음"},
            {"date": "2026-10-20", "missing": "기록 없음"},
            {"date": "2026-10-21", "value": 95.1, "display": "95.1%"},
            {"date": "2026-10-22", "missing": "동기화 전"},
        ]),
        ("heart_weighted", "heart_rate", "this_week", 21, [
            {"date": "2026-10-19", "value": 70.0, "display": "70bpm"},
            {"date": "2026-10-20", "value": 76.0, "display": "76bpm"},
            {"date": "2026-10-21", "value": 95.0, "display": "95bpm"},
        ]),
        # 18일 0보(ZERO_STEPS)와 오늘 0보(UNSYNCED)는 저장된 0을 값으로 전달하지 않는다
        ("week_today_zero_steps", "steps", "last_7_days", 22, [
            {"date": "2026-10-16", "missing": "연동 전"},
            {"date": "2026-10-17", "value": 6450.0, "display": "6,450걸음"},
            {"date": "2026-10-18", "missing": "기록 없음"},
            {"date": "2026-10-19", "value": 8120.0, "display": "8,120걸음"},
            {"date": "2026-10-20", "missing": "기록 없음"},
            {"date": "2026-10-21", "value": 7310.0, "display": "7,310걸음"},
            {"date": "2026-10-22", "missing": "동기화 전", "in_progress": True},
        ]),
        # 18일은 운동이 없고 19일은 느린 걷기만 있어 0분이다. 20일은 활동 기록이 없고 오늘은 아직 0분이다
        ("week_exercise", "exercise", "last_7_days", 22, [
            {"date": "2026-10-16", "value": 32.0, "display": "32분"},
            {"date": "2026-10-17", "value": 50.0, "display": "50분"},
            {"date": "2026-10-18", "value": 0.0, "display": "0분"},
            {"date": "2026-10-19", "value": 0.0, "display": "0분"},
            {"date": "2026-10-20", "missing": "기록 없음"},
            {"date": "2026-10-21", "value": 40.0, "display": "40분"},
            {"date": "2026-10-22", "value": 0.0, "display": "0분", "in_progress": True},
        ]),
    ],
)  # fmt: skip
async def test_common_trend_shows_day_values_and_missing_reasons(case, metric, period, today, expected):
    out = await common_trend(case, metric, period, today)
    r = out.payload
    snapshot = COMMON_RESULTS[case]["snapshot"]

    assert out.status == "ok" and r["metric"] == metric
    assert (r["period"]["start"], r["period"]["end"]) == (snapshot["period"]["start"], snapshot["period"]["end"])
    assert r["points"] == expected
    assert r["days_with_data"] == snapshot["summary"]["days_with_data"]
    assert out.evidence[0].snapshot == snapshot


@pytest.mark.parametrize(("case", "metric", "period", "today"), TREND_CASES)
async def test_common_trend_has_no_null_or_false_items(case, metric, period, today):
    out = await common_trend(case, metric, period, today)
    assert empty_items(out.payload) == []
    # 결측 사유는 원형의 결측인 날에만, 진행 중 표시는 원형의 진행 중인 날에만 들어간다
    days = COMMON_RESULTS[case]["snapshot"]["days"]
    assert [("missing" in p, p.get("in_progress", False)) for p in out.payload["points"]] == [
        (d["missing"], d["is_in_progress"]) for d in days
    ]


async def test_common_trend_marks_sleep_basis_and_measured_during():
    sleep = await get_daily_trend(GetDailyTrend(metric="sleep", period="last_7_days"), common_request("week_sleep"))
    oxygen = await get_daily_trend(
        GetDailyTrend(metric="oxygen_saturation", period="last_7_days"), common_request("week_oxygen")
    )
    assert sleep.payload["unit"] == "min" and "measured_during" not in sleep.payload
    assert (oxygen.payload["unit"], oxygen.payload["measured_during"]) == ("%", "주 수면 중")
    assert sleep.payload["period"]["basis"] == oxygen.payload["period"]["basis"] == "수면 종료일(KST)"
    # 수면·산소포화도는 오늘도 완성된 날이라 진행 중인 날이 없다
    assert not any("in_progress" in p for p in sleep.payload["points"] + oxygen.payload["points"])
    assert "in_progress" not in sleep.payload and "in_progress" not in oxygen.payload
    assert sleep.evidence[0].summary == "지난 7일 수면 날짜별 기록 (연동일 10/17 이후 6일 중 3일 기록, 동기화 전 1일)"
    assert oxygen.evidence[0].summary == (
        "지난 7일 수면 중 산소포화도 날짜별 기록 (연동일 10/17 이후 6일 중 3일 기록, 동기화 전 1일)"
    )


@pytest.mark.parametrize(
    ("case", "today_point", "progress", "summary"),
    [
        (
            "recent_failure",
            {"date": "2026-10-22", "value": 4210.0, "display": "4,210걸음", "in_progress": True},
            {"date": "2026-10-22", "value": 4210.0, "display": "오늘 진행 중 4,210걸음 (13:53까지 기록 기준)",
             "as_of": "2026-10-22T13:53:00+09:00"},
            "지난 7일 걸음 수 날짜별 기록 (6일 중 4일 기록), 오늘 진행 중 4,210걸음 (13:53까지 기록 기준)",
        ),
        (
            "week_today_zero_steps",
            {"date": "2026-10-22", "missing": "동기화 전", "in_progress": True},
            {"date": "2026-10-22", "display": "오늘 진행 중 기록 아직 없음 (13:53까지 기록 기준)",
             "as_of": "2026-10-22T13:53:00+09:00"},
            "지난 7일 걸음 수 날짜별 기록 (연동일 10/17 이후 5일 중 3일 기록), "
            "오늘 진행 중 기록 아직 없음 (13:53까지 기록 기준)",
        ),
    ],
)  # fmt: skip
async def test_common_trend_marks_today_in_progress(case, today_point, progress, summary):
    out = await get_daily_trend(GetDailyTrend(metric="steps", period="last_7_days"), common_request(case))
    r = out.payload
    assert r["points"][-1] == today_point
    assert not any("in_progress" in p for p in r["points"][:-1])
    # 기준 시각이 담긴 오늘 값은 기간 요약과 같은 형식으로 함께 전달한다
    assert r["in_progress"] == progress
    assert out.evidence[0].summary == summary


async def test_common_exercise_trend_marks_today_and_activity_days_like_summary():
    out = await get_daily_trend(GetDailyTrend(metric="exercise", period="last_7_days"), common_request("week_exercise"))
    assert out.payload["in_progress"]["display"] == EXERCISE_PROGRESS
    assert out.evidence[0].summary == (
        f"지난 7일 운동 시간 날짜별 기록 (6일 중 활동 기록이 있는 날 5일), {EXERCISE_PROGRESS}"
    )


async def test_common_trend_keeps_stored_zero_steps_in_snapshot_only():
    out = await get_daily_trend(
        GetDailyTrend(metric="steps", period="last_7_days"), common_request("week_today_zero_steps")
    )
    stored = {d["date"]: d["value"] for d in out.evidence[0].snapshot["days"]}
    assert (stored["2026-10-18"], stored["2026-10-22"]) == (0, 0)
    points = {p["date"]: p for p in out.payload["points"]}
    assert points["2026-10-18"] == {"date": "2026-10-18", "missing": "기록 없음"}
    assert points["2026-10-22"] == {"date": "2026-10-22", "missing": "동기화 전", "in_progress": True}


def monday_port() -> FakeHealthData:
    """10월 12일(월) 09:40 기준. 이번 주가 오늘 하루뿐이다."""
    return FakeHealthData(
        steps={"u1": [StepDay(D(2026, 10, 11), 6000), StepDay(D(2026, 10, 12), 3120)]},
        sync={"u1": {"steps": SyncState(at(12, 9, 30))}},
        linked_from={"u1": D(2026, 9, 20)},
        now=at(12, 9, 40),
    )


@pytest.mark.parametrize(
    ("port", "today", "metric", "period", "line"),
    [
        (sample_port, TODAY, "steps", "today", "걸음 수 오늘 진행 중 4,210걸음 (12:05까지 기록 기준)"),
        (sample_port, TODAY, "heart_rate", "today", "심박 오늘 진행 중 71.5bpm (11:58까지 기록 기준)"),
        (monday_port, D(2026, 10, 12), "steps", "this_week", "걸음 수 오늘 진행 중 3,120걸음 (09:30까지 기록 기준)"),
    ],
)
async def test_trend_evidence_without_completed_days_shows_only_in_progress(port, today, metric, period, line):
    req = ToolRequest("u1", today, port())
    trend = await get_daily_trend(GetDailyTrend(metric=metric, period=period), req)
    summary = await get_metric_summary(GetMetricSummary(metric=metric, period=period), req)
    assert trend.evidence[0].snapshot["summary"]["completed_days"] == 0
    assert trend.evidence[0].summary == summary.evidence[0].summary == line


async def test_trend_of_member_without_received_records_has_same_coverage_as_summary():
    # 받은 기록이 없는 회원은 linked_from 이 없어 원형의 모든 날이 연동 전이다
    req = ToolRequest("u9", TODAY, FakeHealthData(now=NOW))
    trend = await get_daily_trend(GetDailyTrend(metric="sleep", period="last_7_days"), req)
    summary = await get_metric_summary(GetMetricSummary(metric="sleep", period="last_7_days"), req)
    assert trend.evidence[0].snapshot["summary"]["linked_from"] is None
    # 결측 사유는 원형 그대로 두고 기간 요약과 같은 기록 일수 문구를 함께 전달한다
    assert [p["missing"] for p in trend.payload["points"]] == ["연동 전"] * 7
    assert trend.payload["coverage"] == summary.payload["coverage"] == "연동 후 받은 기록 없음"
    assert trend.evidence[0].summary == "지난 7일 수면 날짜별 기록 (연동 후 받은 기록 없음)"
    assert empty_items(trend.payload) == []

    # 연동일이 있는 회원은 결과에 넣지 않는다
    linked = await get_daily_trend(GetDailyTrend(metric="sleep", period="last_7_days"), request(sample_port()))
    assert "coverage" not in linked.payload


async def test_heart_trend_has_measured_extremes_like_summary():
    # 날짜별 조회를 골라도 측정 최고·최저를 묻는 질문에 답할 수 있게 기간 요약과 같은 값을 전달한다
    req = request(sample_port())
    trend = await get_daily_trend(GetDailyTrend(metric="heart_rate", period="this_week"), req)
    summary = await get_metric_summary(GetMetricSummary(metric="heart_rate", period="this_week"), req)
    assert trend.payload["measured_min"] == summary.payload["measured_min"]
    assert trend.payload["measured_max"] == summary.payload["measured_max"]
    assert trend.payload["measured_max"] == {"value": 134.0, "display": "134bpm", "date": "2026-10-14"}

    common = await common_trend("heart_weighted", "heart_rate", "this_week", 21)
    detail = common.evidence[0].snapshot["detail"]
    assert (detail["measured_min"], detail["measured_max"]) == (
        {"date": "2026-10-19", "value": 50},
        {"date": "2026-10-20", "value": 140},
    )
    assert common.payload["measured_min"] == {"value": 50.0, "display": "50bpm", "date": "2026-10-19"}
    assert common.payload["measured_max"] == {"value": 140.0, "display": "140bpm", "date": "2026-10-20"}

    # 측정값이 없으면 빈 항목이라 결과에서 빠진다
    empty = await get_daily_trend(
        GetDailyTrend(metric="heart_rate", period="last_7_days"), ToolRequest("u9", TODAY, FakeHealthData(now=NOW))
    )
    assert "measured_min" not in empty.payload and "measured_max" not in empty.payload


@pytest.mark.parametrize(
    ("case", "metric"),
    [("week_oxygen", "oxygen_saturation"), ("week_sleep", "sleep"), ("week_today_zero_steps", "steps")],
)
async def test_trend_of_other_metrics_leaves_measured_extremes_out(case, metric):
    out = await common_trend(case, metric, "last_7_days", 22)
    # 원형에 산소포화도 측정 최저·최고가 있어도 기간 요약과 같이 심박에만 넣는다
    assert "measured_min" not in out.payload and "measured_max" not in out.payload


@respx.mock
async def test_trend_evidence_goes_through_gateway_like_other_tools(settings):
    port = sample_port()
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(
                ("get_metric_summary", {"metric": "sleep", "period": "last_7_days"}),
                ("get_daily_trend", {"metric": "sleep", "period": "last_7_days"}),
            ),  # fmt: skip
            text_response("10월 13일 수면 시간은 6시간 8분이에요."),
        ]
    )
    sink, audit = ListSink(), MemoryAuditSink()
    ctx = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
    model = create_chat_model(settings, recorder=MetricsRecorder(), retry_base_delay=0.0)
    result = await run_coach(ctx, "10월 13일 잠 얼마나 잤어?", port=port, model=model, sink=sink, audit=audit,
                             now=datetime(2026, 10, 15, 3, 10, tzinfo=UTC))  # fmt: skip

    summary_item, trend_item = next(e for e in sink.events if e["type"] == "evidence")["items"]
    assert set(trend_item) == set(summary_item)
    assert (trend_item["position"], trend_item["tool"], trend_item["metric"]) == (2, "get_daily_trend", "sleeps")
    assert trend_item["period"] == summary_item["period"]
    assert trend_item["snapshot"] == summary_item["snapshot"] == result.evidence[1].snapshot
    assert trend_item["summary"] == "지난 7일 수면 날짜별 기록 (7일 중 2일 기록, 동기화 전 1일)"
    trend_record = audit.records()[1]
    assert (trend_record.tool_name, trend_record.decision, trend_record.reason) == ("get_daily_trend", "allow", None)
    assert (trend_record.result_count, trend_record.evidence_ids) == (7, (trend_item["id"],))
    tool_msgs = [m for m in json.loads(route.calls[1].request.content)["messages"] if m["role"] == "tool"]
    trend_msg = json.loads(tool_msgs[1]["content"])
    assert trend_msg["evidence_ids"] == ["E2"]
    assert {p["date"]: p.get("display") for p in trend_msg["result"]["points"]}["2026-10-13"] == "6시간 8분"
    # LLM 에는 공통 원형이 아니라 처리 함수가 만든 결과만, 빈 항목 없이 전달된다
    assert "data_status" not in tool_msgs[1]["content"]
    assert empty_items(trend_msg) == []


async def test_tool_without_handler_is_not_supported():
    gateway = ToolGateway(sample_port(), TODAY, handlers={})
    ctx = CoachContext(user_id="u1", conversation_id="c", request_id="r")
    [reply] = await gateway.execute([ToolCall("call_1", "get_health_overview", {"period": "last_7_days"})], ctx)
    message = json.loads(reply.content)
    assert message["error"] == "not_supported" and reply.evidence == ()
    assert "아직 지원하지 않는" in message["message"]
    # 지원하지 않는다는 결과를 기록이 없다는 답으로 옮기지 않게 한다
    assert "기록이 없다는 뜻이 아니다" in message["message"]


@respx.mock
async def test_graph_queries_only_context_user(settings):
    port = sample_port()
    route = respx.post(CHAT_URL).mock(
        side_effect=[
            tool_call_response(
                ("get_sleep_records", {"period": "last_7_days"}),
                ("get_metric_summary", {"metric": "steps", "period": "today"}),
            ),  # fmt: skip
            text_response("지난 7일 평균 수면은 6시간 49분이에요."),
        ]
    )
    sink = ListSink()
    ctx = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
    model = create_chat_model(settings, recorder=MetricsRecorder(), retry_base_delay=0.0)
    result = await run_coach(ctx, "지난주 잠 어땠어?", port=port, model=model, sink=sink,
                             now=datetime(2026, 10, 15, 3, 10, tzinfo=UTC))  # fmt: skip

    assert {call[1] for call in port.calls} == {"u1"}
    assert [e.ref for e in result.evidence] == ["E1", "E2"]
    assert [e.tool for e in result.evidence] == ["get_sleep_records", "get_metric_summary"]
    assert [e.metric for e in result.evidence] == ["sleeps", "steps"]
    today_steps = result.evidence[1].snapshot
    assert today_steps["summary"]["completed_days"] == 0 and today_steps["in_progress"]["value"] == 4210
    tool_msgs = [m for m in json.loads(route.calls[1].request.content)["messages"] if m["role"] == "tool"]
    assert [json.loads(m["content"])["evidence_ids"] for m in tool_msgs] == [["E1"], ["E2"]]
    # LLM 에는 공통 원형이 아니라 처리 함수가 만든 요약만 전달된다
    assert all("data_status" not in m["content"] for m in tool_msgs)
    evidence = next(e for e in sink.events if e["type"] == "evidence")
    assert evidence["items"][1]["summary"] == "걸음 수 오늘 진행 중 4,210걸음 (12:05까지 기록 기준)"
    assert [item["snapshot"] for item in evidence["items"]] == [e.snapshot for e in result.evidence]
