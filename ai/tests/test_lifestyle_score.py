import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import get_args

import pytest

from coach_ai.adapters.memory import ScoreDay, SyncState
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.agent.executor import ToolCall, ToolRequest
from coach_ai.ports import (
    HealthDataForbiddenError,
    HealthDataNotReadyError,
    HealthDataUnavailableError,
    LifestyleScoreResult,
    ResultContractError,
    ScoreGrade,
)
from coach_ai.security.audit import MemoryAuditSink
from coach_ai.security.gateway import DENIED_MESSAGE, ToolGateway
from coach_ai.tools.handlers import get_lifestyle_score
from coach_ai.tools.results import SCORE_GRADE_LABELS, SleepScoreAreaResult
from coach_ai.tools.schemas import GetLifestyleScore
from coach_ai.tools.sleep_score import sleep_points
from tests.fakes import SCORE_IN_PROGRESS, SCORE_NO_ROW, FakeHealthData

KST = timezone(timedelta(hours=9))
TODAY = date(2026, 10, 20)
NOW = datetime(2026, 10, 20, 14, 0, tzinfo=KST)
CTX = CoachContext(user_id="u1", conversation_id="c-1", request_id="r-1")
INPUTS = ("steps", "exercise", "sleep", "heart_rate")
REASON_NOTE = (
    "각 영역 점수의 이유는 그 영역의 used 값이다. 영역을 말하지 않은 질문이면 등급이 가장 낮은 영역이 주된 이유다"
)
CARE_NOTE = "관리 등급은 질병이 있거나 치료가 필요하다는 뜻이 아니다"
REST_DAY_NOTE = "운동하지 않은 날이 있는 것은 정상이다"
LOWER_HEART_NOTE = (
    "평소보다 낮은 수면 중 심박은 감점 대상이 아니고, 낮아진 값은 건강이 좋아지거나 나빠졌다는 근거가 아니다"
)
CHECK_WEAR_NOTE = "수면 중 심박이 평소보다 많이 낮아 감점 대상이 아니고, 워치 착용 상태 확인 안내 대상이다"
# 하루가 아니거나 조회할 수 없는 날짜의 안내. 점수가 하루 단위라는 것과 조회할 수 있는 날짜 범위만 적는다
DAY_ONLY = "생활 습관 점수는 날짜 하루 단위로만 있다. 조회할 수 있는 날짜는 2025년 10월 20일-2026년 10월 20일(오늘)이다"
# LLM 에 지시하는 문장 끝. 지시 문장은 답에 그대로 나올 수 있어 점수 결과에는 사실만 적는다
INSTRUCTION_ENDING = re.compile(r"(주세요|한다|않는다)$")
IN_PROGRESS_NOTE = "하루가 끝나지 않아 동기화될 때마다 다시 계산되고, 걷거나 운동하면 활동 점수가 오를 수 있다"
# 점수 계산 시각이 오늘보다 이를 때 붙는 사실 문장
NOT_TODAY = "이 시각은 오늘이 아니고, 오늘 기록은 아직 반영되지 않았다"
# 수면 영역에 넣는 점수 기준 (data/docs/score-rules.md 3장)
SLEEP_RULE = (
    "수면 점수는 그날 깬 주 수면의 수면 시간만으로 매긴다. 근거는 Life's Essential 8(LE8) 수면 점수표다. "
    "기준점은 8시간 100점, 6시간 30분 70점, 5시간 30분 40점, 4시간 30분 20점, 3시간 30분 0점이고, "
    "기준점 사이는 시간에 비례해 계산된다. 3시간 30분 이하는 0점, 8시간 이상 9시간 미만은 100점, "
    "9시간 이상 10시간 미만은 90점, 10시간 이상은 40점이다. "
    "깊은 잠과 규칙성은 수면 점수에 들어가지 않는 값이다"
)


def at(d: int, hh: int, mm: int) -> datetime:
    return datetime(2026, 10, d, hh, mm, tzinfo=KST)


def row(**changes) -> ScoreDay:
    """API 명세 5.3 의 오늘 진행 중 예시와 같은 저장 행."""
    values = dict(
        date=TODAY, total_score=67, activity_score=80, activity_grade="excellent", steps_score=48,
        exercise_score=32, sleep_score=65, sleep_grade="stable", heart_score=55, heart_grade="attention", steps=5400,
        exercise_minutes=24, sleep_minutes=380, sleep_hr_avg=66.0, hr_baseline=58.0, hr_baseline_nights=20,
        computed_at=at(20, 12, 15),
    )  # fmt: skip
    return ScoreDay(**{**values, **changes})


def port(*rows: ScoreDay) -> FakeHealthData:
    """9월 20일부터 기록이 있고 동기화 기준 시각이 10월 20일 12:05인 회원."""
    return FakeHealthData(
        scores={"u1": list(rows)},
        sync={"u1": {metric: SyncState(at(20, 12, 5)) for metric in INPUTS}},
        linked_from={"u1": date(2026, 9, 20)},
        now=NOW,
    )


async def run(health: FakeHealthData, period: str = "days_ago:0", today: date = TODAY):
    return await get_lifestyle_score(GetLifestyleScore(period=period), ToolRequest("u1", today, health))


def test_grade_labels_follow_api_spec_grade_table():
    # API 명세 5.3 의 영역 등급 표. 홈 화면도 같은 이름을 쓴다
    assert SCORE_GRADE_LABELS == {1: "훌륭", 2: "안정", 3: "관심", 4: "관리"}
    assert set(SCORE_GRADE_LABELS) == set(get_args(ScoreGrade))


async def test_score_in_progress_explains_each_area_with_used_values_and_sends_no_evidence():
    outcome = await run(port(row()))
    assert outcome.status == "ok" and outcome.evidence == ()
    assert outcome.payload == {
        "period": {
            "period": "days_ago:0", "start": "2026-10-20", "end": "2026-10-20", "basis": "날짜(KST)",
            "display": "오늘 (10월 20일)",
        },
        "in_progress": {
            "date": "2026-10-20",
            "display": "오늘 진행 중인 점수 (12:15 계산 기준). "
            "하루가 끝나지 않아 동기화될 때마다 다시 계산되고, 걷거나 운동하면 활동 점수가 오를 수 있다",
            "as_of": "2026-10-20T12:15:00+09:00",
        },
        "total": {"score": 67, "display": "67점", "note": "점수가 있는 영역의 평균이며 등급이 없다"},
        "activity": {
            "score": 80, "grade": "훌륭",
            "used": "5,400걸음으로 걸음 점수 48점(60점 만점), 인정 운동 24분으로 운동 점수 32점(40점 만점)",
        },
        "sleep": {
            "score": 65, "grade": "안정", "used": "이날 깬 주 수면의 실제 수면 6시간 20분", "rule": SLEEP_RULE,
            "targets": "하루 수면 시간별 수면 점수: 이날 6시간 20분 65점, 30분 더 자면 6시간 50분 77점, "
            "1시간 더 자면 7시간 20분 87점, 7시간이면 80점, 8시간이면 100점",
        },
        "heart": {"score": 55, "grade": "관심", "used": "수면 중 평균 심박 66bpm, 평소 58bpm(20일 평균)보다 8bpm 높음"},
        "notes": [REASON_NOTE],
    }  # fmt: skip
    assert outcome.to_message([]) == {"evidence_ids": [], "result": outcome.payload}


# score-rules.md 3장의 기준점·예시·9시간 이상 값 (분, 점수). 308분은 3장의 비례 계산과 1.2 반올림으로 32.67 → 33 이다
@pytest.mark.parametrize(
    ("minutes", "points"),
    [
        (180, 0), (210, 0), (240, 10), (270, 20), (300, 30), (308, 33), (330, 40), (360, 55), (390, 70), (420, 80),
        (480, 100), (539, 100), (540, 90), (599, 90), (600, 40), (660, 40),
    ],
)  # fmt: skip
def test_sleep_points_follow_score_rules_chapter_3(minutes, points):
    assert sleep_points(minutes) == points


@pytest.mark.parametrize(
    ("minutes", "score", "targets"),
    [
        (308, 33, "이날 5시간 8분 33점, 30분 더 자면 5시간 38분 44점, 1시간 더 자면 6시간 8분 59점, "
                  "7시간이면 80점, 8시간이면 100점"),
        # 30분 더 잔 시간이 7시간이면 7시간 항목을 다시 적지 않는다
        (390, 70, "이날 6시간 30분 70점, 30분 더 자면 7시간 80점, 1시간 더 자면 7시간 30분 90점, 8시간이면 100점"),
        # 9시간 이상은 오래 잘수록 낮아진다
        (560, 90, "이날 9시간 20분 90점, 30분 더 자면 9시간 50분 90점, 1시간 더 자면 10시간 20분 40점, "
                  "7시간이면 80점, 8시간이면 100점"),
    ],
)  # fmt: skip
async def test_sleep_area_has_rule_and_scores_by_sleep_time(minutes, score, targets):
    grade = {33: "care", 70: "stable", 90: "excellent"}[score]
    payload = (await run(port(row(sleep_minutes=minutes, sleep_score=score, sleep_grade=grade)))).payload
    assert payload["sleep"]["score"] == score
    assert payload["sleep"]["rule"] == SLEEP_RULE
    assert payload["sleep"]["targets"] == f"하루 수면 시간별 수면 점수: {targets}"


async def test_sleep_without_score_keeps_rule_without_scores_by_sleep_time():
    health = port(
        row(total_score=80, sleep_score=None, sleep_grade=None, heart_score=None, heart_grade=None, sleep_minutes=None,
            sleep_hr_avg=None)
    )  # fmt: skip
    payload = (await run(health)).payload
    assert payload["sleep"] == {"missing": "기록 없음", "rule": SLEEP_RULE}


def test_sleep_area_needs_rule():
    with pytest.raises(ValueError):
        SleepScoreAreaResult(score=65, grade="안정")


async def test_score_without_sleep_leaves_sleep_and_heart_missing_and_says_average_skips_them():
    health = port(
        row(total_score=80, sleep_score=None, sleep_grade=None, heart_score=None, heart_grade=None, sleep_minutes=None,
            sleep_hr_avg=None)
    )  # fmt: skip
    payload = (await run(health)).payload
    assert payload["total"] == {
        "score": 80,
        "display": "80점",
        "note": "점수가 있는 영역의 평균이며 등급이 없다. 기록이 없는 영역은 평균에서 빠졌다",
    }
    assert (payload["sleep"], payload["heart"]) == (
        {"missing": "기록 없음", "rule": SLEEP_RULE},
        {"missing": "기록 없음"},
    )


async def test_heart_without_baseline_keeps_sleep_heart_rate_for_resting_heart_card():
    # 평소 값을 만들 이전 밤이 없으면 심박 점수가 없어도 홈 화면 안정 시 심박수와 같은 값은 있다
    health = port(row(total_score=73, heart_score=None, heart_grade=None, sleep_hr_avg=61.0, hr_baseline=None,
                      hr_baseline_nights=0))  # fmt: skip
    payload = (await run(health)).payload
    assert payload["heart"] == {"missing": "평소 값 없음", "used": "수면 중 평균 심박 61bpm"}


@pytest.mark.parametrize(
    ("day", "reason", "label"),
    [
        ("date:09-19", "BEFORE_LINK", "연동 전"),
        ("date:10-15", "NO_RECORD", "기록 없음"),
        ("days_ago:0", "UNSYNCED", "동기화 전"),
    ],
)
async def test_score_without_row_says_missing_reason_without_areas(day, reason, label):
    health = port()
    payload = (await run(health, day)).payload
    assert (payload["total"], payload["missing"]) == ({"display": "점수 없음"}, label)
    assert set(payload) == {"period", "total", "missing"}


async def test_score_of_spec_example_without_row():
    class SpecPort(FakeHealthData):
        async def get_lifestyle_score(self, user_id, day):
            return LifestyleScoreResult(user_id, SCORE_NO_ROW)

    payload = (await run(SpecPort(now=NOW), "date:10-15")).payload
    assert payload == {
        "period": {"period": "date:10-15", "start": "2026-10-15", "end": "2026-10-15", "basis": "날짜(KST)",
                   "display": "10월 15일"},
        "total": {"display": "점수 없음"},
        "missing": "기록 없음",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("changes", "used", "notes"),
    [
        # 관리 등급이 있고 운동하지 않은 날
        (
            dict(total_score=60, activity_score=30, activity_grade="care", steps_score=30, exercise_score=0,
                 steps=3105, exercise_minutes=0, heart_score=100, heart_grade="excellent", sleep_hr_avg=59.0,
                 sleep_score=50, sleep_grade="attention"),
            "수면 중 평균 심박 59bpm, 평소 58bpm(20일 평균)보다 1bpm 높음",
            [REASON_NOTE, CARE_NOTE, REST_DAY_NOTE],
        ),
        # 평소보다 낮은 수면 중 심박은 감점하지 않고 해석하지 않는다
        (
            dict(total_score=82, heart_score=100, heart_grade="excellent", sleep_hr_avg=55.5),
            "수면 중 평균 심박 55.5bpm, 평소 58bpm(20일 평균)보다 2.5bpm 낮음",
            [REASON_NOTE, LOWER_HEART_NOTE],
        ),
        # 평소보다 많이 낮으면 착용 확인 안내만 넣는다
        (
            dict(total_score=82, heart_score=100, heart_grade="excellent", sleep_hr_avg=45.0, check_wear=True),
            "수면 중 평균 심박 45bpm, 평소 58bpm(20일 평균)보다 13bpm 낮음",
            [REASON_NOTE, CHECK_WEAR_NOTE],
        ),
        (
            dict(total_score=82, heart_score=100, heart_grade="excellent", sleep_hr_avg=58.0),
            "수면 중 평균 심박 58bpm, 평소 58bpm(20일 평균)과 같음",
            [REASON_NOTE],
        ),
    ],
)  # fmt: skip
async def test_score_notes_follow_grades_exercise_and_heart(changes, used, notes):
    payload = (await run(port(row(**changes)))).payload
    assert payload["heart"]["used"] == used
    assert payload["notes"] == notes


async def test_past_score_has_no_in_progress():
    payload = (await run(port(row(date=date(2026, 10, 19))), "days_ago:1")).payload
    assert "in_progress" not in payload and payload["total"]["score"] == 67


@pytest.mark.parametrize(
    "period",
    [
        "recent_days:7", "weeks_ago:0", "dates:10-18..10-19", "months_ago:1",  # 여러 날
        "date:2026-10-25", "months_ago:13", "date:02-30",  # 미래, 12개월 전, 없는 날짜
        "weeks_ago:1..days_ago:1",  # 상대 날짜 범위
    ],
)  # fmt: skip
async def test_period_other_than_one_queryable_day_is_not_looked_up_and_gives_facts(period):
    """하루가 아니거나 조회할 수 없는 기간 → 조회 없이 하루 단위 안내."""
    health = port(row())
    outcome = await run(health, period)
    assert (outcome.status, outcome.evidence) == ("unavailable_period", ())
    assert outcome.payload == {"tool": "get_lifestyle_score", "period": period, "message": DAY_ONLY}
    assert health.calls == []


@pytest.mark.parametrize(
    ("period", "day"),
    [
        ("today", 20), ("yesterday", 19), ("weekday:sun", 18), ("date:10-16", 16),
        ("weeks_ago:1.wed", 14),  # 지난주 수요일(10-14)은 하루라 통과
    ],
)  # fmt: skip
async def test_one_day_periods_look_up_that_day(period, day):
    """하루로 계산되는 기간 → 그날의 점수만 조회."""
    health = port(row())
    await run(health, period)
    assert health.calls == [("get_lifestyle_score", "u1", date(2026, 10, day))]


async def test_result_for_another_day_is_contract_error():
    class OtherDayPort(FakeHealthData):
        async def get_lifestyle_score(self, user_id, day):
            return LifestyleScoreResult(user_id, SCORE_IN_PROGRESS)

    with pytest.raises(ResultContractError):
        await run(OtherDayPort(now=NOW), "days_ago:1")


async def test_score_computed_before_today_says_time_is_not_today():
    payload = (await run(port(row(computed_at=at(19, 23, 50))))).payload
    assert payload["in_progress"] == {
        "date": "2026-10-20",
        "display": f"오늘 진행 중인 점수 (10월 19일 23:50 계산 기준). {NOT_TODAY}. {IN_PROGRESS_NOTE}",
        "as_of": "2026-10-19T23:50:00+09:00",
    }


async def test_sample_today_score_is_activity_only_and_in_care():
    sample = sample_health_data("u1", datetime(2026, 10, 22, 14, 0, tzinfo=KST))
    payload = (await run(sample, today=date(2026, 10, 22))).payload
    assert payload["total"]["score"] == 30
    assert payload["in_progress"]["display"].startswith("오늘 진행 중인 점수 (13:55 계산 기준).")
    assert payload["activity"] == {
        "score": 30,
        "grade": "관리",
        "used": "4,210걸음으로 걸음 점수 30점(60점 만점), 인정 운동 0분으로 운동 점수 0점(40점 만점)",
    }
    assert (payload["sleep"], payload["heart"]) == (
        {"missing": "기록 없음", "rule": SLEEP_RULE},
        {"missing": "기록 없음"},
    )
    assert payload["notes"] == [REASON_NOTE, CARE_NOTE, REST_DAY_NOTE]


def score_call(period: str = "days_ago:0") -> ToolCall:
    return ToolCall(f"call-score-{period}", "get_lifestyle_score", {"period": period})


async def test_gateway_reply_has_no_evidence_and_counts_one_day():
    audit = MemoryAuditSink()
    [reply] = await ToolGateway(port(row()), TODAY, audit=audit).execute([score_call()], CTX)
    assert reply.evidence == ()
    content = json.loads(reply.content)
    assert content["evidence_ids"] == [] and content["result"]["total"]["score"] == 67
    [record] = audit.records()
    assert (record.tool_name, record.reason, record.result_count, record.evidence_ids) == (
        "get_lifestyle_score",
        None,
        1,
        (),
    )


async def test_gateway_score_failures_have_no_metrics():
    class SlowScore(FakeHealthData):
        async def get_lifestyle_score(self, user_id, day):
            await asyncio.sleep(0.2)

    class OtherOwner(FakeHealthData):
        async def get_lifestyle_score(self, user_id, day):
            return LifestyleScoreResult("u2", SCORE_IN_PROGRESS)

    with pytest.raises(HealthDataUnavailableError) as slow:
        await ToolGateway(SlowScore(now=NOW), TODAY, timeout=0.05).execute([score_call()], CTX)
    with pytest.raises(HealthDataForbiddenError) as other:
        await ToolGateway(OtherOwner(now=NOW), TODAY).execute([score_call()], CTX)
    not_ready = port(row(date=date(2026, 10, 19)))
    not_ready.sync["u1"].pop("heart_rate")
    with pytest.raises(HealthDataNotReadyError) as missing:
        await ToolGateway(not_ready, TODAY).execute([score_call()], CTX)
    assert slow.value.metrics == other.value.metrics == missing.value.metrics == ()


async def test_gateway_does_not_repeat_same_score_call():
    calls = [score_call(), score_call(), score_call("days_ago:1")]
    replies = await ToolGateway(port(row()), TODAY).execute(calls, CTX)
    assert [r.content == DENIED_MESSAGE for r in replies] == [False, True, False]


def _sentences(value: object) -> list[str]:
    """결과 안의 모든 문자열을 문장으로 나눈다. 소수점은 뒤에 빈칸이 없어 나누지 않는다."""
    if isinstance(value, dict):
        return [s for item in value.values() for s in _sentences(item)]
    if isinstance(value, list):
        return [s for item in value for s in _sentences(item)]
    if isinstance(value, str):
        return [part.strip(" .") for part in re.split(r"\.\s", value) if part.strip(" .")]
    return []


async def test_score_results_state_facts_without_instructions_to_the_model():
    no_sleep = dict(
        total_score=80,
        sleep_score=None,
        sleep_grade=None,
        heart_score=None,
        heart_grade=None,
        sleep_minutes=None,
        sleep_hr_avg=None,
    )
    cases = [
        (port(row()), "days_ago:0"),
        (port(row(date=date(2026, 10, 19))), "days_ago:1"),
        (port(row(**no_sleep)), "days_ago:0"),
        (port(row(total_score=73, heart_score=None, heart_grade=None, sleep_hr_avg=61.0, hr_baseline=None,
                  hr_baseline_nights=0)), "days_ago:0"),
        (port(row(total_score=60, activity_score=30, activity_grade="care", steps_score=30, exercise_score=0,
                  steps=3105, exercise_minutes=0)), "days_ago:0"),
        (port(row(total_score=82, heart_score=100, heart_grade="excellent", sleep_hr_avg=55.5)), "days_ago:0"),
        (port(row(total_score=82, heart_score=100, heart_grade="excellent", sleep_hr_avg=45.0, check_wear=True)),
         "days_ago:0"),
        (port(row(computed_at=at(19, 23, 50))), "days_ago:0"),
        (port(), "date:09-19"),
        (port(), "date:10-15"),
        (port(), "days_ago:0"),
        (port(row()), "recent_days:7"),
        (port(row()), "date:2026-10-25"),
    ]  # fmt: skip
    found = []
    for health, period in cases:
        outcome = await run(health, period)
        found += _sentences(outcome.payload)
    notes = {REASON_NOTE.split(". ")[1], CARE_NOTE, REST_DAY_NOTE, LOWER_HEART_NOTE, CHECK_WEAR_NOTE, NOT_TODAY}
    assert notes <= set(found)
    assert [s for s in found if INSTRUCTION_ENDING.search(s)] == []
