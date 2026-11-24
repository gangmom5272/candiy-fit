import copy
import dataclasses
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from coach_ai.adapters.memory import ExerciseSession, HeartDay, ScoreDay, SleepSession, StepDay, SyncState
from coach_ai.adapters.sample import sample_health_data
from coach_ai.agent.context import CoachContext
from coach_ai.ports import (
    HealthDataError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
    HealthDataPort,
    HealthDataUnavailableError,
    LifestyleScoreResult,
    MetricResult,
    ResultContractError,
)
from tests.fakes import COMMON_RESULTS, SCORE_IN_PROGRESS, SCORE_NO_ROW, SCORE_WITHOUT_SLEEP, FakeHealthData

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 15, 12, 10, tzinfo=KST)
D = date


def at(d: int, hh: int, mm: int) -> datetime:
    return datetime(2026, 10, d, hh, mm, tzinfo=KST)


def main_sleep(wake_day: int, minutes: int | None, spo2: float | None = None) -> SleepSession:
    return SleepSession(
        D(2026, 10, wake_day), at(wake_day - 1, 23, 40), at(wake_day, 7, 10), True, minutes,
        spo2_avg=spo2, spo2_min=None if spo2 is None else spo2 - 4, spo2_max=None if spo2 is None else spo2 + 2,
    )  # fmt: skip


RUN = (56, "달리기", "cardio")
WALK = (79, "걷기", "cardio")


def workout(d: int, start: tuple[int, int], end: tuple[int, int], kind=RUN, counted=True, **extra) -> ExerciseSession:
    """운동 세션 하나. kind 는 (코드, 기준표 이름, 분류)이고 운동 분은 시작부터 종료까지다."""
    begin, finish = at(d, *start), at(d, *end)
    minutes = int((finish - begin).total_seconds()) // 60
    return ExerciseSession(D(2026, 10, d), begin, finish, minutes, *kind, counted, **extra)


def test_context_is_frozen_with_seoul_default():
    ctx = CoachContext(user_id="u1", conversation_id="c1", request_id="r1")
    assert ctx.tz == "Asia/Seoul"
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.user_id = "u2"  # type: ignore[misc]


def test_context_requires_keywords_and_values():
    with pytest.raises(TypeError):
        CoachContext("u1", "c1", "r1")  # type: ignore[misc]
    with pytest.raises(ValueError):
        CoachContext(user_id=" ", conversation_id="c1", request_id="r1")


def test_fake_satisfies_port():
    assert isinstance(FakeHealthData(), HealthDataPort)


async def test_result_keeps_snapshot_as_copy_and_reads_typed_fields():
    fake = FakeHealthData(steps={"u1": [StepDay(D(2026, 10, 14), 6000)]}, now=NOW)
    result = await fake.get_metric_result("u1", "steps", D(2026, 10, 14), D(2026, 10, 14))
    raw = dict(result.snapshot)

    copied = MetricResult("u1", raw)
    raw["summary"]["avg"] = 1
    assert copied.snapshot["summary"]["avg"] == 6000 and copied.summary.avg == 6000
    assert (copied.metric, copied.unit, copied.period.start) == ("steps", "steps", D(2026, 10, 14))


async def test_result_ignores_unknown_fields_but_keeps_them_in_snapshot():
    fake = FakeHealthData(steps={"u1": [StepDay(D(2026, 10, 14), 6000)]}, now=NOW)
    snapshot = dict((await fake.get_metric_result("u1", "steps", D(2026, 10, 14), D(2026, 10, 14))).snapshot)
    snapshot["new_field"] = {"x": 1}
    snapshot["summary"] = {**snapshot["summary"], "median": 6000}

    result = MetricResult("u1", snapshot)
    assert result.snapshot["new_field"] == {"x": 1} and result.summary.avg == 6000


@pytest.mark.parametrize(
    "broken",
    [
        lambda s: s.pop("data_status"),
        lambda s: s["summary"].pop("days_used_for_summary"),
        lambda s: s["summary"].update(completed_days="1일"),  # 명세와 다른 타입
        lambda s: s["data_status"].update(checked_at="2026-10-15T03:10:00"),  # 시간대 없는 시각
        lambda s: s.update(user_id="u1"),  # 내부 소유자는 공개 원형에 넣지 않는다
    ],
)
async def test_result_contract_error_is_not_a_lookup_failure(broken):
    fake = FakeHealthData(heart={"u1": [HeartDay(D(2026, 10, 14), 70.0, 50, 120, sample_count=120)]}, now=NOW)
    snapshot = dict((await fake.get_metric_result("u1", "heart_rate", D(2026, 10, 14), D(2026, 10, 14))).snapshot)
    snapshot = {k: dict(v) if isinstance(v, dict) else v for k, v in snapshot.items()}
    broken(snapshot)
    with pytest.raises(ResultContractError) as exc:
        MetricResult("u1", snapshot)
    # 연동 구현의 결함이라 조회 실패(HealthDataError)로 끝나지 않는다
    assert not isinstance(exc.value, HealthDataError)


# 점수 결과 모델 (API 명세 5.3 응답 data)
@pytest.mark.parametrize("example", [SCORE_IN_PROGRESS, SCORE_WITHOUT_SLEEP, SCORE_NO_ROW])
def test_score_result_reads_spec_examples_and_keeps_them_as_snapshot(example):
    result = LifestyleScoreResult("u1", example)
    assert result.user_id == "u1" and result.snapshot == example
    assert (result.date.isoformat(), result.is_in_progress, result.reason) == (
        example["date"],
        example["is_in_progress"],
        example["reason"],
    )
    assert (result.total.score, list(result.total.missing_areas)) == (
        example["total"]["score"],
        example["total"]["missing_areas"],
    )


def test_score_result_reads_area_values_without_recalculating():
    result = LifestyleScoreResult("u1", SCORE_IN_PROGRESS)
    activity, sleep, heart = result.areas.activity, result.areas.sleep, result.areas.heart
    assert (activity.score, activity.grade, activity.steps.value, activity.exercise.minutes) == (80, 1, 5400, 24)
    assert (sleep.score, sleep.grade, sleep.sleep_minutes) == (65, 2, 380)
    assert (heart.score, heart.grade, heart.sleep_hr_avg, heart.baseline_hr_avg) == (55, 3, 66.0, 58.0)
    assert (heart.baseline_nights, heart.check_wear) == (20, False)
    assert result.computed_at == datetime(2026, 10, 20, 12, 15, tzinfo=KST)


def test_score_result_keeps_snapshot_as_copy_and_unknown_fields():
    raw = copy.deepcopy(SCORE_IN_PROGRESS)
    raw["areas"]["sleep"]["efficiency"] = 0.9
    result = LifestyleScoreResult("u1", raw)
    raw["total"]["score"] = 1
    assert result.snapshot["total"]["score"] == 67 and result.total.score == 67
    assert result.snapshot["areas"]["sleep"]["efficiency"] == 0.9


@pytest.mark.parametrize(
    "broken",
    [
        lambda s: s.pop("areas"),
        lambda s: s["total"].pop("missing_areas"),
        lambda s: s["areas"]["heart"].pop("baseline_nights"),
        lambda s: s["areas"]["sleep"].update(grade=5),  # 등급 코드는 1~4
        lambda s: s.update(reason="LATE"),  # 행이 없는 날짜의 사유는 세 가지다
        lambda s: s.update(computed_at="2026-10-20T03:15:00"),  # 시간대 없는 시각
        lambda s: s.update(user_id="u1"),  # 내부 소유자는 응답에 넣지 않는다
    ],
)
def test_score_result_contract_error_is_not_a_lookup_failure(broken):
    snapshot = copy.deepcopy(SCORE_IN_PROGRESS)
    broken(snapshot)
    with pytest.raises(ResultContractError) as exc:
        LifestyleScoreResult("u1", snapshot)
    assert not isinstance(exc.value, HealthDataError)


async def test_fake_returns_only_requested_user_and_range():
    fake = FakeHealthData(
        steps={
            "u1": [StepDay(D(2026, 10, d), 8000 + d) for d in (8, 9, 15)],
            "u2": [StepDay(D(2026, 10, 9), 1)],
        },
        now=NOW,
    )
    result = await fake.get_metric_result("u1", "steps", D(2026, 10, 9), D(2026, 10, 14))
    assert result.user_id == "u1" and result.metric == "steps"
    assert [(d.date.day, d.value) for d in result.days] == [(9, 8009), *[(d, None) for d in range(10, 15)]]
    assert fake.calls == [("get_metric_result", "u1", "steps", D(2026, 10, 9), D(2026, 10, 14))]


async def test_fake_steps_keeps_zero_null_and_missing_rows_apart():
    fake = FakeHealthData(
        steps={"u1": [StepDay(D(2026, 10, 9), 4000), StepDay(D(2026, 10, 10), 0), StepDay(D(2026, 10, 11), None),
                      StepDay(D(2026, 10, 12), 8000)]},
        sync={"u1": {"steps": SyncState(at(15, 12, 5))}},
        now=NOW,
    )  # fmt: skip
    result = await fake.get_metric_result("u1", "steps", D(2026, 10, 9), D(2026, 10, 13))
    assert [(d.value, d.missing, d.reason) for d in result.days] == [
        (4000, False, None),
        (0, True, "ZERO_STEPS"),  # 0보는 값을 그대로 두고 결측으로 센다
        (None, True, "NULL_VALUE"),
        (8000, False, None),
        (None, True, "NO_RECORD"),
    ]
    s = result.summary
    assert (s.avg, s.total, s.days_with_data, s.days_used_for_summary, s.completed_days) == (6000, 12000, 2, 2, 5)
    assert (s.daily_min.date.day, s.daily_max.date.day) == (9, 12)
    assert s.missing_days == (D(2026, 10, 10), D(2026, 10, 11), D(2026, 10, 13)) and s.unsynced_days == ()
    assert result.in_progress is None and result.detail.measured_min is None and result.detail.daily_ranges == ()


async def test_fake_splits_missing_and_unsynced_by_synced_until_of_all_metrics():
    fake = FakeHealthData(
        sleep={"u1": [main_sleep(12, 400, spo2=96.0)]},
        # 회원의 모든 지표 기준 시각 중 가장 늦은 값이 동기화 기준이다
        sync={"u1": {"oxygen_saturation": SyncState(at(12, 7, 10)), "steps": SyncState(at(16, 9, 0))}},
        now=datetime(2026, 10, 17, 9, 0, tzinfo=KST),
    )
    result = await fake.get_metric_result("u1", "oxygen_saturation", D(2026, 10, 12), D(2026, 10, 17))
    assert result.summary.missing_days == (D(2026, 10, 13), D(2026, 10, 14), D(2026, 10, 15))
    assert result.summary.unsynced_days == (D(2026, 10, 16), D(2026, 10, 17))
    assert result.data_status.synced_until == at(16, 9, 0)
    assert result.data_status.last_data_at == at(12, 7, 10)


async def test_fake_days_before_linked_from_are_not_counted():
    fake = FakeHealthData(
        steps={"u1": [StepDay(D(2026, 10, d), 6000) for d in (14, 16, 18)]},
        sync={"u1": {"steps": SyncState(at(19, 8, 0))}},
        linked_from={"u1": D(2026, 10, 16)},
        now=datetime(2026, 10, 19, 9, 0, tzinfo=KST),
    )
    result = await fake.get_metric_result("u1", "steps", D(2026, 10, 12), D(2026, 10, 18))
    assert [d.reason for d in result.days[:4]] == ["BEFORE_LINK"] * 4
    assert result.days[2].value is None  # 연동 전 값은 쓰지 않는다
    s = result.summary
    assert s.linked_from == D(2026, 10, 16)
    assert (s.completed_days, s.days_with_data, s.missing_days) == (3, 2, (D(2026, 10, 17),))


async def test_fake_without_synced_until_counts_gaps_as_unsynced():
    # 기준일을 알 수 없으면 연동 함수처럼 결측을 모두 동기화 전으로 간주한다
    fake = FakeHealthData(steps={"u1": [StepDay(D(2026, 10, d), 100) for d in (12, 13)]}, now=NOW)
    result = await fake.get_metric_result("u1", "steps", D(2026, 10, 12), D(2026, 10, 15))
    assert result.data_status.synced_until is None
    assert [d.reason for d in result.days] == [None, None, "UNSYNCED", "UNSYNCED"]
    assert (result.summary.missing_days, result.summary.unsynced_days) == ((), (D(2026, 10, 14), D(2026, 10, 15)))


async def test_fake_member_without_records_has_every_day_before_link():
    result = await FakeHealthData(now=NOW).get_metric_result("u9", "sleep", D(2026, 10, 13), D(2026, 10, 14))
    assert result.summary.linked_from is None and result.data_status.synced_until is None
    assert [d.reason for d in result.days] == ["BEFORE_LINK", "BEFORE_LINK"]
    assert (result.summary.completed_days, result.summary.missing_days, result.summary.avg) == (0, (), None)


async def test_fake_today_steps_is_in_progress_but_today_sleep_is_counted():
    fake = FakeHealthData(
        steps={"u1": [StepDay(D(2026, 10, d), 1000 * d) for d in (13, 14, 15)]},
        sleep={"u1": [main_sleep(d, 400) for d in (13, 14, 15)]},
        sync={"u1": {"steps": SyncState(at(15, 12, 5), "success", at(15, 12, 7))}},
        now=NOW,
    )
    steps = await fake.get_metric_result("u1", "steps", D(2026, 10, 13), D(2026, 10, 15))
    s = steps.summary
    assert (s.completed_days, s.days_with_data, s.total) == (2, 2, 27000)
    assert [d.is_in_progress for d in steps.days] == [False, False, True]
    assert (steps.in_progress.value, steps.in_progress.missing, steps.in_progress.as_of) == (
        15000,
        False,
        at(15, 12, 5),
    )
    status = steps.data_status
    assert (status.last_sync_status, status.last_success_at, status.checked_at) == ("success", at(15, 12, 7), NOW)

    sleep = await fake.get_metric_result("u1", "sleep", D(2026, 10, 13), D(2026, 10, 15))
    assert (sleep.summary.completed_days, sleep.summary.days_with_data, sleep.in_progress) == (3, 3, None)
    assert not any(d.is_in_progress for d in sleep.days)


async def test_fake_today_zero_steps_is_unsynced_and_in_progress_zero():
    fake = FakeHealthData(
        steps={"u1": [StepDay(D(2026, 10, 14), 5000), StepDay(D(2026, 10, 15), 0)]},
        sync={"u1": {"steps": SyncState(at(15, 12, 5))}},
        now=NOW,
    )
    result = await fake.get_metric_result("u1", "steps", D(2026, 10, 15), D(2026, 10, 15))
    s = result.summary
    assert (s.avg, s.total, s.days_with_data, s.completed_days, s.missing_days) == (None, None, 0, 0, ())
    assert s.unsynced_days == (D(2026, 10, 15),)
    assert (result.in_progress.value, result.in_progress.missing) == (0, True)
    assert (result.days[0].value, result.days[0].reason) == (0, "UNSYNCED")


async def test_fake_heart_average_is_weighted_by_valid_hours():
    # backend/docs/health-query-contract.md 7.3의 가중 평균 사례
    days = [
        HeartDay(D(2026, 10, 19), 70, 50, 110, hours=14, sample_count=140),
        HeartDay(D(2026, 10, 20), 76, 52, 140, hours=15, sample_count=300),
        HeartDay(D(2026, 10, 21), 95, 60, 130, hours=1, sample_count=5000),
    ]
    fake = FakeHealthData(heart={"u1": days}, now=datetime(2026, 10, 22, 14, 0, tzinfo=KST))
    result = await fake.get_metric_result("u1", "heart_rate", D(2026, 10, 19), D(2026, 10, 21))
    s, detail = result.summary, result.detail
    assert s.avg == 73.83  # (70×14 + 76×15 + 95×1) / 30, ROUND_HALF_UP
    assert ((s.daily_min.date.day, s.daily_min.value), (s.daily_max.date.day, s.daily_max.value)) == (
        (19, 70),
        (21, 95),
    )
    assert (detail.measured_min.date.day, detail.measured_min.value) == (19, 50)
    assert (detail.measured_max.date.day, detail.measured_max.value) == (20, 140)
    assert (s.days_with_data, s.completed_days, s.total, result.in_progress) == (3, 3, None, None)
    assert [(r.date.day, r.min, r.max) for r in detail.daily_ranges] == [(19, 50, 110), (20, 52, 140), (21, 60, 130)]


async def test_fake_sleep_uses_main_sleep_and_keeps_night_without_minutes():
    nap = SleepSession(D(2026, 10, 13), at(13, 14, 0), at(13, 14, 40), False, 40)
    fake = FakeHealthData(
        sleep={"u1": [main_sleep(13, 368), nap, main_sleep(14, None)]},
        sync={"u1": {"sleep": SyncState(at(15, 7, 10))}},
        now=NOW,
    )
    result = await fake.get_metric_result("u1", "sleep", D(2026, 10, 13), D(2026, 10, 14))
    assert [(n.date.day, n.sleep_minutes) for n in result.detail.nights] == [(13, 368), (14, None)]
    assert result.detail.nights[0].start_at == at(12, 23, 40)
    assert (result.summary.avg, result.summary.days_with_data, result.days[1].reason) == (368, 1, "NULL_VALUE")


async def test_fake_exercise_is_zero_on_activity_days_and_missing_without_activity_row():
    fake = FakeHealthData(
        # 13일은 걸음 값이 없어도 활동 행이 있다. 14일은 활동 행이 없다
        steps={"u1": [StepDay(D(2026, 10, 11), 6000), StepDay(D(2026, 10, 12), 0), StepDay(D(2026, 10, 13), None)]},
        exercise={"u1": [workout(11, (19, 0), (19, 30)), workout(12, (8, 0), (8, 20), WALK, counted=False)]},
        sync={"u1": {"exercise": SyncState(at(12, 8, 20)), "steps": SyncState(at(15, 12, 5))}},
        now=NOW,
    )
    result = await fake.get_metric_result("u1", "exercise", D(2026, 10, 11), D(2026, 10, 14))
    assert [(d.value, d.missing, d.reason) for d in result.days] == [
        (30, False, None),
        (0, False, None),  # 운동 시간에 들어가지 않는 세션만 있다
        (0, False, None),  # 걸음 값이 없는 활동 행도 운동 0분인 날이다
        (None, True, "NO_RECORD"),
    ]
    s = result.summary
    assert (s.avg, s.total, s.days_with_data, s.completed_days, s.missing_days) == (10, 30, 3, 4, (D(2026, 10, 14),))
    assert (result.unit, result.in_progress) == ("min", None)
    # 운동 시간에 들어가지 않는 세션도 상세에 들어간다
    assert [x["counts_as_exercise"] for x in result.detail.sessions] == [True, False]


async def test_fake_exercise_today_is_in_progress_with_last_exercise_time():
    fake = FakeHealthData(
        steps={"u1": [StepDay(D(2026, 10, d), 5000) for d in (14, 15)]},
        exercise={"u1": [workout(14, (19, 0), (19, 45)), workout(15, (0, 30), (1, 0))]},
        sync={"u1": {"exercise": SyncState(at(15, 1, 0)), "steps": SyncState(at(15, 12, 5))}},
        now=NOW,
    )
    result = await fake.get_metric_result("u1", "exercise", D(2026, 10, 14), D(2026, 10, 15))
    assert [d.is_in_progress for d in result.days] == [False, True]
    assert (result.in_progress.value, result.in_progress.missing, result.in_progress.as_of) == (30, False, at(15, 1, 0))
    assert (result.summary.total, result.summary.completed_days) == (45, 1)
    # 새벽에 시작한 세션은 UTC 로는 전날이지만 시작 시각의 KST 날짜로 묶인다
    assert (result.detail.sessions[1]["date"], result.detail.sessions[1]["start_at"]) == (
        "2026-10-15",
        "2026-10-14T15:30:00Z",
    )


async def test_fake_exercise_session_without_activity_row_fails_like_common_query():
    fake = FakeHealthData(
        steps={"u1": [StepDay(D(2026, 10, 12), 5000)]},
        exercise={"u1": [workout(13, (19, 0), (19, 30))]},
        now=NOW,
    )
    with pytest.raises(HealthDataUnavailableError) as exc:
        await fake.get_metric_result("u1", "exercise", D(2026, 10, 12), D(2026, 10, 14))
    assert exc.value.metrics == ("exercise",)
    # 기간 밖의 세션은 조회하지 않아 검사하지 않는다
    result = await fake.get_metric_result("u1", "exercise", D(2026, 10, 12), D(2026, 10, 12))
    assert (result.days[0].value, result.detail.sessions) == (0, ())


async def test_fake_exercise_code_outside_code_table_is_other_exercise():
    fake = FakeHealthData(
        steps={"u1": [StepDay(D(2026, 10, 14), 5000)]},
        exercise={"u1": [workout(14, (10, 0), (10, 50), (84, None, None))]},
        now=NOW,
    )
    result = await fake.get_metric_result("u1", "exercise", D(2026, 10, 14), D(2026, 10, 14))
    [session] = result.detail.sessions
    # 원본 코드는 그대로 두고 이름·분류만 API 명세의 기본값으로 채운다
    assert (session["exercise_type"], session["type_name"], session["category"]) == (84, "기타 운동", "other")


@pytest.mark.parametrize(
    "changes",
    [
        {"date": D(2026, 10, 14)},  # 시작 시각의 KST 날짜와 다르다
        {"end_at": at(13, 19, 0)},  # 시작과 종료가 같다
        {"duration_minutes": 0},
        {"distance_m": float("nan")},
        {"distance_m": float("inf")},
        {"distance_m": -0.1},  # 저장 거리는 음수일 수 없다
    ],
)
def test_exercise_session_follows_common_session_rules(changes):
    with pytest.raises(ValueError):
        dataclasses.replace(workout(13, (19, 0), (19, 30)), **changes)


async def test_fake_rejects_range_past_today_or_over_366_days():
    fake = FakeHealthData(now=NOW)
    with pytest.raises(ValueError):
        await fake.get_metric_result("u1", "steps", D(2026, 10, 15), D(2026, 10, 16))
    with pytest.raises(ValueError):
        await fake.get_metric_result("u1", "steps", D(2025, 10, 14), D(2026, 10, 15))
    result = await fake.get_metric_result("u1", "steps", D(2025, 10, 15), D(2026, 10, 15))
    assert len(result.days) == 366


async def test_fake_not_linked_member_fails_but_member_without_records_gets_result():
    fake = FakeHealthData(not_linked={"u9"}, now=NOW)
    with pytest.raises(HealthDataNotLinkedError) as exc:
        await fake.get_metric_result("u9", "steps", D(2026, 10, 9), D(2026, 10, 15))
    assert exc.value.code == "user_not_linked" and exc.value.retryable is True
    result = await fake.get_metric_result("u1", "steps", D(2026, 10, 9), D(2026, 10, 15))
    assert result.summary.days_with_data == 0


# 메모리 어댑터: 하루 점수 (API 명세 5.3)
SCORE_NOW = datetime(2026, 10, 20, 14, 0, tzinfo=KST)
# 점수 행이 없을 때 준비 여부를 확인하는 입력 지표 (연동 함수의 steps, exercise_sessions, sleeps, heart_rates)
SCORE_INPUTS = ("steps", "exercise", "sleep", "heart_rate")


def score_row(**changes) -> ScoreDay:
    """API 명세 5.3 의 오늘 진행 중 예시와 같은 저장 행."""
    row = dict(
        date=D(2026, 10, 20), total_score=67, activity_score=80, activity_grade="excellent", steps_score=48,
        exercise_score=32, sleep_score=65, sleep_grade="stable", heart_score=55, heart_grade="attention", steps=5400,
        exercise_minutes=24, sleep_minutes=380, sleep_hr_avg=66.0, hr_baseline=58.0, hr_baseline_nights=20,
        computed_at=at(20, 12, 15),
    )  # fmt: skip
    return ScoreDay(**{**row, **changes})


def score_port(*rows: ScoreDay, inputs: tuple[str, ...] = SCORE_INPUTS) -> FakeHealthData:
    """9월 20일부터 기록이 있고 동기화 기준 시각이 10월 20일 12:05인 회원."""
    return FakeHealthData(
        scores={"u1": list(rows)},
        sync={"u1": {metric: SyncState(at(20, 12, 5)) for metric in inputs}},
        linked_from={"u1": D(2026, 9, 20)},
        now=SCORE_NOW,
    )


async def test_fake_score_row_builds_spec_example():
    port = score_port(score_row())
    result = await port.get_lifestyle_score("u1", D(2026, 10, 20))
    assert result.user_id == "u1" and result.snapshot == SCORE_IN_PROGRESS
    assert port.calls == [("get_lifestyle_score", "u1", D(2026, 10, 20))]


async def test_fake_score_row_without_sleep_builds_spec_example():
    row = score_row(
        total_score=80, sleep_score=None, sleep_grade=None, heart_score=None, heart_grade=None, sleep_minutes=None,
        sleep_hr_avg=None,
    )  # fmt: skip
    result = await score_port(row).get_lifestyle_score("u1", D(2026, 10, 20))
    assert result.snapshot == SCORE_WITHOUT_SLEEP


async def test_fake_score_without_row_builds_spec_example():
    # 기록 시작일(9월 20일) 이후이고 동기화 기준일(10월 20일) 전이라 기록 없음이다
    result = await score_port(score_row()).get_lifestyle_score("u1", D(2026, 10, 15))
    assert result.snapshot == SCORE_NO_ROW


@pytest.mark.parametrize(
    ("day", "reason"),
    [(D(2026, 9, 19), "BEFORE_LINK"), (D(2026, 9, 20), "NO_RECORD"), (D(2026, 10, 20), "UNSYNCED")],
)
async def test_fake_score_without_row_reason_follows_link_and_sync_dates(day, reason):
    result = await score_port().get_lifestyle_score("u1", day)
    assert (result.reason, result.total.score, result.computed_at) == (reason, None, None)
    assert result.is_in_progress == (day == D(2026, 10, 20))


async def test_fake_score_of_member_without_records_is_before_link():
    port = FakeHealthData(sync={"u2": {metric: SyncState() for metric in SCORE_INPUTS}}, now=SCORE_NOW)
    assert (await port.get_lifestyle_score("u2", D(2026, 10, 20))).reason == "BEFORE_LINK"


async def test_fake_score_without_row_is_not_ready_until_every_input_state_exists():
    port = score_port(score_row(), inputs=("steps", "exercise", "sleep"))
    with pytest.raises(HealthDataNotReadyError) as exc:
        await port.get_lifestyle_score("u1", D(2026, 10, 19))
    # 점수 조회 실패는 지표 하나의 실패가 아니라 지표 목록을 비운다
    assert exc.value.metrics == ()
    # 저장된 행은 준비 상태와 관계없이 반환한다
    assert (await port.get_lifestyle_score("u1", D(2026, 10, 20))).total.score == 67


async def test_fake_score_not_linked_member_fails_and_future_day_is_rejected():
    port = score_port(score_row())
    port.not_linked.add("u1")
    with pytest.raises(HealthDataNotLinkedError):
        await port.get_lifestyle_score("u1", D(2026, 10, 20))
    with pytest.raises(ValueError):
        await score_port().get_lifestyle_score("u1", D(2026, 10, 21))


@pytest.mark.parametrize(
    "changes",
    [
        {"sleep_grade": None},  # 영역 점수가 있으면 등급도 있다
        {"exercise_minutes": None},  # 활동 점수가 있으면 운동 분도 있다
        {"total_score": None},  # 영역 점수가 하나라도 있으면 오늘의 점수가 있다
        {"computed_at": datetime(2026, 10, 20, 12, 15)},  # 시간대 없는 시각
    ],
)
def test_score_day_follows_storage_contract(changes):
    with pytest.raises(ValueError):
        score_row(**changes)


# 합성 데이터: backend/docs/health-query-contract.md 7장의 기대값
USER = "00000000-0000-4000-8000-000000000001"
SAMPLE_NOW = datetime(2026, 10, 22, 14, 0, tzinfo=KST)
STATUS = {
    "synced_until": "2026-10-22T04:53:00Z",
    "last_sync_status": "success",
    "last_success_at": "2026-10-22T04:55:00Z",
    "checked_at": "2026-10-22T05:00:00Z",
}
EMPTY_DETAIL = {"measured_min": None, "measured_max": None, "nights": [], "daily_ranges": [], "sessions": []}


async def test_sample_today_steps_matches_contract_example():
    result = await sample_health_data(USER, SAMPLE_NOW).get_metric_result(
        USER, "steps", D(2026, 10, 22), D(2026, 10, 22)
    )
    assert result.user_id == USER
    assert result.snapshot == {
        "metric": "steps",
        "period": {"start": "2026-10-22", "end": "2026-10-22"},
        "unit": "steps",
        "summary": {
            "avg": None, "daily_min": None, "daily_max": None, "total": None, "days_with_data": 0,
            "days_used_for_summary": 0, "completed_days": 0, "missing_days": [], "unsynced_days": [],
            "linked_from": "2026-10-09",
        },
        # 착용 값은 문서 예시 뒤에 생긴 칸이다 (API 명세 v43). 오늘 심박 9시간이라 9.1 의 3번 줄로 판단을 보류한다
        "days": [
            {
                "date": "2026-10-22", "value": 4210, "missing": False, "reason": None, "is_in_progress": True,
                "wear_hours": 9, "is_well_worn": None,
            }
        ],
        "in_progress": {"date": "2026-10-22", "value": 4210, "missing": False, "as_of": "2026-10-22T04:53:00Z"},
        "data_status": {"last_data_at": "2026-10-22T04:53:00Z", **STATUS},
        "detail": EMPTY_DETAIL,
    }  # fmt: skip


async def test_sample_week_sleep_matches_contract_example():
    port = sample_health_data(USER, SAMPLE_NOW)
    snapshot = (await port.get_metric_result(USER, "sleep", D(2026, 10, 16), D(2026, 10, 22))).snapshot
    assert snapshot["metric"] == "sleeps" and snapshot["unit"] == "min"
    assert snapshot["summary"] == {
        "avg": 395.8,
        "daily_min": {"date": "2026-10-20", "value": 340},
        "daily_max": {"date": "2026-10-17", "value": 441},
        "total": None, "days_with_data": 5, "days_used_for_summary": 5, "completed_days": 7,
        "missing_days": ["2026-10-19"], "unsynced_days": ["2026-10-22"], "linked_from": "2026-10-09",
    }  # fmt: skip
    assert [(d["date"][-2:], d["reason"]) for d in snapshot["days"] if d["missing"]] == [
        ("19", "NO_RECORD"),
        ("22", "UNSYNCED"),
    ]
    assert snapshot["data_status"] == {"last_data_at": "2026-10-20T22:25:00Z", **STATUS}
    assert [n["date"] for n in snapshot["detail"]["nights"]] == [f"2026-10-{d}" for d in (16, 17, 18, 20, 21)]
    assert snapshot["detail"]["nights"][0] == {
        "date": "2026-10-16", "start_at": "2026-10-15T15:10:00Z", "end_at": "2026-10-15T22:40:00Z",
        "sleep_minutes": 418, "deep_minutes": 70, "rem_minutes": 85, "light_minutes": 263, "awake_minutes": 18,
        "session_minutes": 450, "unspecified_minutes": None, "unknown_minutes": None,
        "avg_heart_rate": 58.5, "min_heart_rate": 50, "max_heart_rate": 76,
        "spo2_avg": 96.3, "spo2_min": 92.1, "spo2_max": 98.4, "spo2_count": 450, "spo2_low_count": 0,
        # 연동 쪽 nights 의 90% 미만 기록 시각. 90% 미만 기록이 없어 빈 목록이다
        "spo2_low_times": [],
    }  # fmt: skip
    assert snapshot["in_progress"] is None and snapshot["detail"]["daily_ranges"] == []


@pytest.mark.parametrize(
    ("metric", "start", "end", "avg", "total", "completed", "valid", "missing", "unsynced"),
    [
        ("steps", 19, 22, 7715, 15430, 3, 2, [20], []),
        ("steps", 16, 22, 7465, 29860, 6, 4, [18, 20], []),
        ("heart_rate", 21, 21, 72.3, None, 1, 1, [], []),
        ("oxygen_saturation", 16, 22, 95.73, None, 7, 4, [18, 19], [22]),
    ],
)
async def test_sample_summaries_match_contract_cases(
    metric, start, end, avg, total, completed, valid, missing, unsynced
):
    port = sample_health_data(USER, SAMPLE_NOW)
    s = (await port.get_metric_result(USER, metric, D(2026, 10, start), D(2026, 10, end))).summary
    assert (s.avg, s.total, s.completed_days, s.days_with_data) == (avg, total, completed, valid)
    assert [d.day for d in s.missing_days] == missing and [d.day for d in s.unsynced_days] == unsynced


async def test_sample_last_week_steps_keeps_stored_zero():
    port = sample_health_data(USER, SAMPLE_NOW)
    result = await port.get_metric_result(USER, "steps", D(2026, 10, 16), D(2026, 10, 22))
    zero = next(d for d in result.days if d.date == D(2026, 10, 18))
    assert (zero.value, zero.missing, zero.reason) == (0, True, "ZERO_STEPS")
    assert (result.in_progress.value, result.in_progress.missing) == (4210, False)


async def test_sample_other_user_has_no_records():
    port = sample_health_data(USER, SAMPLE_NOW)
    result = await port.get_metric_result("someone-else", "steps", D(2026, 10, 16), D(2026, 10, 22))
    assert result.summary.linked_from is None and result.summary.days_with_data == 0


async def test_sample_exercise_has_zero_minutes_uncounted_other_code_and_today_without_exercise():
    port = sample_health_data(USER, SAMPLE_NOW)
    result = await port.get_metric_result(USER, "exercise", D(2026, 10, 9), D(2026, 10, 22))
    days = {d.date.day: (d.value, d.reason) for d in result.days}
    assert days[20] == (None, "NO_RECORD")  # 활동 기록이 없는 날
    # 12일은 느린 걷기만 있어 0분이다
    assert [day for day, (value, _) in days.items() if value == 0] == [11, 12, 15, 19, 22]
    sessions = result.detail.sessions
    assert [s["date"] for s in sessions if not s["counts_as_exercise"]] == ["2026-10-12", "2026-10-17"]
    assert [(s["exercise_type"], s["type_name"]) for s in sessions if s["category"] == "other"] == [(84, "기타 운동")]
    # 오늘은 운동이 없어 진행 중 0분이고, 기준 시각은 어제 마지막 운동이 끝난 시각이다
    assert (result.in_progress.value, result.in_progress.missing) == (0, False)
    assert result.in_progress.as_of == at(21, 19, 40)

    week = (await port.get_metric_result(USER, "exercise", D(2026, 10, 19), D(2026, 10, 22))).summary
    assert (week.avg, week.total, week.days_with_data, week.completed_days) == (17.5, 35, 2, 3)
    assert week.missing_days == (D(2026, 10, 20),)


async def test_sample_has_score_row_on_every_day_from_link_date():
    # 활동 행이나 주 수면이 있는 날마다 행이 있다. 오늘의 점수는 합성 기록으로 파이프라인 점수 함수를 실행한 값이다
    port = sample_health_data(USER, SAMPLE_NOW)
    days = [D(2026, 10, 9) + timedelta(days=i) for i in range(14)]
    results = [await port.get_lifestyle_score(USER, day) for day in days]
    assert [r.reason for r in results] == [None] * 14
    assert [r.total.score for r in results] == [91, 89, 68, 78, 83, 100, 57, 92, 92, 80, 60, 50, 92, 30]
    assert (await port.get_lifestyle_score(USER, D(2026, 10, 8))).reason == "BEFORE_LINK"


async def test_sample_score_today_has_only_activity_and_is_in_progress():
    # 오늘 아침 수면은 동기화 전이라 수면·심박 점수가 없고, 걸음 4,210걸음·운동 0분으로 활동이 관리다
    today = await sample_health_data(USER, SAMPLE_NOW).get_lifestyle_score(USER, D(2026, 10, 22))
    assert (today.is_in_progress, today.total.score, today.total.missing_areas) == (True, 30, ("sleep", "heart"))
    activity = today.areas.activity
    assert (activity.score, activity.grade, activity.steps.score, activity.steps.value) == (30, 4, 30, 4210)
    assert (activity.exercise.score, activity.exercise.minutes) == (0, 0)
    assert (today.areas.heart.baseline_hr_avg, today.areas.heart.baseline_nights) == (59.5, 11)
    assert today.computed_at == at(22, 13, 55)


async def test_sample_score_keeps_heart_values_with_and_without_baseline():
    port = sample_health_data(USER, SAMPLE_NOW)
    # 2일 전은 활동 기록이 없고, 수면 340분이 관리, 수면 중 심박이 평소보다 7.5 높아 관심이다
    before = await port.get_lifestyle_score(USER, D(2026, 10, 20))
    assert (before.total.score, before.areas.activity.score, before.areas.activity.steps.value) == (50, None, None)
    assert (before.areas.sleep.score, before.areas.sleep.grade, before.areas.sleep.sleep_minutes) == (45, 4, 340)
    heart = before.areas.heart
    assert (heart.score, heart.grade, heart.sleep_hr_avg, heart.baseline_hr_avg, heart.baseline_nights) == (
        55,
        3,
        66.3,
        58.8,
        9,
    )
    # 연동일에는 평소 값을 만들 이전 밤이 없어 심박 점수가 없어도 수면 중 평균 심박은 있다
    first = (await port.get_lifestyle_score(USER, D(2026, 10, 9))).areas.heart
    assert (first.score, first.sleep_hr_avg, first.baseline_hr_avg, first.baseline_nights) == (None, 58.2, None, 0)


SYNCED = SyncState(at(22, 13, 53), "success", at(22, 13, 55))
HEART_WEIGHTED = [
    HeartDay(D(2026, 10, 19), 70, 50, 110, hours=14, sample_count=140),
    HeartDay(D(2026, 10, 20), 76, 52, 140, hours=15, sample_count=300),
    HeartDay(D(2026, 10, 21), 95, 60, 130, hours=1, sample_count=5000),
]
# 18일은 실제 수면 시간이 없고 20일은 산소포화도 측정이 없다. 19일은 기록이 없고 22일은 동기화 전이다
SLEEP_WEEK = {
    "sleep": {
        USER: [main_sleep(17, 441, 95.5), main_sleep(18, None, 96.0), main_sleep(20, 340), main_sleep(21, 412, 95.1)]
    },
    "linked_from": {USER: D(2026, 10, 17)},
}


def steps_days(values: dict[int, int]) -> dict[str, list[StepDay]]:
    return {USER: [StepDay(D(2026, 10, d), v) for d, v in values.items()]}


# 18일은 운동이 없고 19일은 느린 걷기만 있다. 20일은 활동 기록이 없고 오늘(22일)은 운동이 없다.
# 거리는 16일 달리기·19일 걷기가 소수 값, 17일 느린 걷기가 0이고 나머지 세션은 기록이 없다
EXERCISE_WEEK = {
    "steps": steps_days({16: 7980, 17: 6450, 18: 0, 19: 8120, 21: 7310, 22: 4210}),
    "exercise": {
        USER: [
            workout(16, (7, 10), (7, 42), calories_kcal=310.5, distance_m=5571.4, avg_heart_rate=152.0,
                    max_heart_rate=171, avg_speed_mps=2.9),
            workout(17, (10, 0), (10, 50), (84, None, None)),
            workout(17, (18, 20), (18, 42), WALK, False, calories_kcal=65.0, distance_m=0.0, avg_heart_rate=96.0,
                    max_heart_rate=108, avg_speed_mps=0.89),
            workout(19, (8, 5), (8, 25), WALK, False, calories_kcal=58.0, distance_m=1103.6, avg_heart_rate=94.0,
                    max_heart_rate=104, avg_speed_mps=0.92),
            workout(21, (19, 0), (19, 40), (70, "근력 운동", "strength"), title="하체 운동", calories_kcal=180.0,
                    avg_heart_rate=118.5, max_heart_rate=142),
        ]
    },
    # 운동 기준 시각은 마지막 운동 기록 시각이라 오늘이 아니다
    "sync": {USER: {"exercise": SyncState(at(21, 19, 40), "success", at(22, 13, 55)), "steps": SYNCED}},
}  # fmt: skip


@pytest.mark.parametrize(
    ("case", "metric", "start", "end", "records"),
    [
        ("today_zero_steps", "steps", 22, 22, {"steps": steps_days({22: 0})}),
        (
            "week_today_zero_steps", "steps", 16, 22,
            {"steps": steps_days({17: 6450, 18: 0, 19: 8120, 21: 7310, 22: 0}), "linked_from": {USER: D(2026, 10, 17)}},
        ),
        (
            "recent_failure", "steps", 16, 22,
            {
                "steps": steps_days({16: 7980, 17: 6450, 18: 0, 19: 8120, 21: 7310, 22: 4210}),
                "sync": {USER: {"steps": dataclasses.replace(SYNCED, last_status="fail")}},
            },
        ),
        (
            "synced_none", "steps", 16, 22,
            {
                "steps": steps_days({16: 100, 17: 100}),
                "sync": {USER: {"steps": dataclasses.replace(SYNCED, last_data_at=None)}},
            },
        ),
        ("heart_weighted", "heart_rate", 19, 21, {"heart": {USER: HEART_WEIGHTED}}),
        ("week_sleep", "sleep", 16, 22, SLEEP_WEEK),
        ("week_oxygen", "oxygen_saturation", 16, 22, SLEEP_WEEK),
        ("week_exercise", "exercise", 16, 22, EXERCISE_WEEK),
    ],
)  # fmt: skip
async def test_fake_builds_same_prototype_as_common_query(case, metric, start, end, records):
    inputs = {"sync": {USER: {metric: SYNCED}}, "linked_from": {USER: D(2026, 10, 9)}, **records}
    fake = FakeHealthData(**inputs, now=SAMPLE_NOW)
    result = await fake.get_metric_result(USER, metric, D(2026, 10, start), D(2026, 10, end))
    assert result.user_id == COMMON_RESULTS[case]["user_id"]
    # 기대값은 착용 값이 생기기 전에 계산 함수로 만든 원형이라 days 에 두 칸이 없다.
    # 착용 값은 아래 9.1 테스트에서 확인한다
    mine = copy.deepcopy(dict(result.snapshot))
    for day in mine["days"]:
        del day["wear_hours"], day["is_well_worn"]
    # 공개 원형은 JSON 으로 전달되므로 int 와 float 구분까지 같아야 한다
    assert json.dumps(mine, sort_keys=True) == json.dumps(COMMON_RESULTS[case]["snapshot"], sort_keys=True)


# 착용 값 (calculation-rules.md 9.1, API 명세 v43 days[].wear_hours·is_well_worn)
WEAR_NOW = datetime(2026, 10, 22, 14, 0, tzinfo=KST)
# 16일 연동. 심박 마지막 기록이 20일 20:00 이라 동기화 기준일은 20일이고 오늘은 22일이다. 18일·22일은 심박 행이 없다
WEAR_HEART = [
    HeartDay(D(2026, 10, d), 70.0, hours=h, sample_count=100) for d, h in {16: 14, 17: 9, 19: 10, 20: 11, 21: 7}.items()
]
HEART_SYNCED = SyncState(at(20, 20, 0), "success", at(22, 13, 0))


def wear_port(heart_state: SyncState | None = HEART_SYNCED) -> FakeHealthData:
    return FakeHealthData(
        heart={USER: WEAR_HEART},
        sync={USER: {} if heart_state is None else {"heart_rate": heart_state}},
        linked_from={USER: D(2026, 10, 16)},
        now=WEAR_NOW,
    )


async def test_fake_days_carry_wear_values_in_rule_order():
    result = await wear_port().get_metric_result(USER, "steps", D(2026, 10, 15), D(2026, 10, 22))
    assert [(d.date.day, d.wear_hours, d.is_well_worn) for d in result.days] == [
        (15, None, None),  # 1 연동 전
        (16, 14, True),  # 5 완성된 날, 10시간 이상
        (17, 9, False),  # 5 완성된 날, 10시간 미만
        (18, 0, False),  # 6 완성된 날, 심박 행 없음
        (19, 10, True),  # 5 경계 10시간
        (20, 11, True),  # 3 아직 안 들어옴(동기화 기준일), 10시간 이상
        (21, 7, None),  # 3 아직 안 들어옴, 10시간 미만은 판단 보류
        (22, None, None),  # 4 오늘, 심박 행 없음
    ]


@pytest.mark.parametrize("metric", ["heart_rate", "sleep", "oxygen_saturation", "exercise"])
async def test_fake_wear_values_are_the_same_for_every_metric(metric):
    port = wear_port()
    steps = await port.get_metric_result(USER, "steps", D(2026, 10, 15), D(2026, 10, 22))
    other = await port.get_metric_result(USER, metric, D(2026, 10, 15), D(2026, 10, 22))
    assert [(d.wear_hours, d.is_well_worn) for d in other.days] == [(d.wear_hours, d.is_well_worn) for d in steps.days]


@pytest.mark.parametrize(
    ("heart_state", "unknown"),
    [
        (None, list(range(15, 23))),  # 2 심박 동기화 상태 없음
        (SyncState(at(20, 20, 0), "fail", at(18, 9, 0)), [15, *range(18, 23)]),  # 2 실패 중, 마지막 성공일부터
        (SyncState(at(20, 20, 0), "fail", None), list(range(15, 23))),  # 2 실패 중이고 성공한 적이 없다
    ],
    ids=["no_heart_state", "failing_since_18", "failing_without_success"],
)
async def test_fake_wear_is_unknown_while_heart_cannot_be_checked(heart_state, unknown):
    result = await wear_port(heart_state).get_metric_result(USER, "steps", D(2026, 10, 15), D(2026, 10, 22))
    assert [d.date.day for d in result.days if (d.wear_hours, d.is_well_worn) == (None, None)] == unknown


async def test_result_day_reads_wear_values_and_keeps_null():
    result = await wear_port().get_metric_result(USER, "steps", D(2026, 10, 15), D(2026, 10, 22))
    # null 은 모름 그대로 두고 false 로 바꾸지 않는다
    assert [(d.wear_hours, d.is_well_worn) for d in result.days[:3]] == [(None, None), (14, True), (9, False)]
    # 착용 값이 생기기 전에 저장한 원형은 두 칸이 없고, 모름으로 읽는다
    snapshot = copy.deepcopy(dict(result.snapshot))
    for day in snapshot["days"]:
        del day["wear_hours"], day["is_well_worn"]
    assert {(d.wear_hours, d.is_well_worn) for d in MetricResult(USER, snapshot).days} == {(None, None)}
    snapshot["days"][1]["wear_hours"] = "10시간"
    with pytest.raises(ResultContractError):
        MetricResult(USER, snapshot)
