from datetime import UTC, date, datetime
from itertools import product
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.health.daily_message import (
    DailyMessageInput,
    MetricFailure,
    MetricSuccess,
    SleepScoreUnavailable,
    UsableSleepScore,
    build_daily_message,
    select_candidate,
)
from app.health.errors import HealthDataLookupError, HealthDataNotReadyError

TODAY = date(2026, 10, 29)
AS_OF = datetime(2026, 10, 29, 6, tzinfo=UTC)
SCORE_AT = datetime(2026, 10, 29, 6, 10, tzinfo=UTC)
USER_ID = UUID("00000000-0000-0000-0000-000000000197")


def success(value=None, *, reason=None, detail=False, stored=None, **kwargs):
    reason = reason or ("NO_RECORD" if value is None else None)
    return MetricSuccess(
        value=value,
        missing=reason is not None,
        reason=reason,
        has_stored_value=value is not None if stored is None else stored,
        has_detail=detail,
        as_of=kwargs.pop("as_of", AS_OF),
        sync_failed=kwargs.pop("sync_failed", None),
        **kwargs,
    )


def message_input(*, steps=None, exercises=None, sleeps=None, score=None):
    return DailyMessageInput(
        user_id=USER_ID,
        date=TODAY,
        steps=steps if steps is not None else success(),
        exercises=exercises if exercises is not None else success(),
        sleeps=sleeps if sleeps is not None else success(),
        sleep_score=score
        if score is not None
        else SleepScoreUnavailable(reason="NOT_QUERIED"),
    )


@pytest.mark.parametrize(
    "steps,exercise,score,code,area,used,title,description",
    [
        (
            8200,
            35,
            90,
            "BALANCED_RECORDS",
            None,
            ("steps", "exercises", "sleeps"),
            "오늘은 나에게 칭찬 한마디 건네볼까요?",
            "수면과 활동 기록이 함께 쌓였어요. 오늘 남긴 작은 성취들을 돌아보세요.",
        ),
        (
            7000,
            30,
            79,
            "ACTIVE_DAY",
            "activity",
            ("steps", "exercises"),
            "차곡차곡 쌓은 움직임, 뿌듯해해도 좋아요",
            "운동과 걸음 기록에 오늘의 움직임이 담겼어요.",
        ),
        (
            6999,
            30,
            80,
            "EXERCISE_MILESTONE",
            "activity",
            ("exercises",),
            "운동한 나에게 작은 칭찬을 보내주세요",
            "오늘 운동 기록을 돌아보며, 좋았던 순간 하나를 떠올려볼까요?",
        ),
        (
            7000,
            29,
            80,
            "STEPS_MILESTONE",
            "activity",
            ("steps",),
            "한 걸음씩 쌓아온 오늘, 잘하셨어요",
            "오늘 기록이 7,000보를 채웠어요. 더 채우기보다 지금까지의 걸음도 칭찬해 주세요.",
        ),
        (
            6999,
            29,
            80,
            "SLEEP_REFLECTION",
            "sleep",
            ("sleeps",),
            "잠깐, 오늘의 내 컨디션을 살펴볼까요?",
            "수면 기록과 함께, 깨어났을 때의 느낌도 돌아보세요.",
        ),
        (
            5000,
            20,
            None,
            "EXERCISE_PROGRESS",
            "activity",
            ("exercises",),
            "오늘의 움직임에도 박수를 보내요",
            "시간을 더 채워야 한다는 부담 없이, 남겨진 운동 기록을 돌아보세요.",
        ),
        (
            5000,
            0,
            None,
            "STEPS_PROGRESS",
            "activity",
            ("steps",),
            "한 걸음씩, 내 페이스대로 가볼까요?",
            "오늘 남겨진 걸음도 소중해요. 움직이고 싶은 때에 편한 만큼 이어가 보세요.",
        ),
        (
            None,
            0,
            None,
            "SLEEP_RECORDED",
            "sleep",
            ("sleeps",),
            "오늘 잠은 어땠나요?",
            "기록된 시간과 내가 느낀 잠은 다를 수 있어요. 오늘의 느낌도 함께 살펴보세요.",
        ),
    ],
)
def test_priority_and_exact_copy(
    steps, exercise, score, code, area, used, title, description
):
    facts = message_input(
        steps=success(steps),
        exercises=success(exercise),
        sleeps=success(450, detail=True),
        score=UsableSleepScore(value=score, computed_at=SCORE_AT)
        if score is not None
        else None,
    )
    before = facts.model_dump_json()
    candidate = select_candidate(facts)
    assert (candidate.code, candidate.area, candidate.used_metrics) == (
        code,
        area,
        used,
    )
    assert (candidate.title, candidate.description) == (title, description)
    assert select_candidate(facts) == candidate
    assert facts.model_dump_json() == before


@pytest.mark.parametrize(
    "exercise_detail,sleep_detail,used",
    [
        (True, False, ("exercises",)),
        (False, True, ("sleeps",)),
        (True, True, ("exercises", "sleeps")),
    ],
)
def test_detail_only_uses_every_available_detail(exercise_detail, sleep_detail, used):
    candidate = select_candidate(
        message_input(
            exercises=success(0, detail=exercise_detail),
            sleeps=success(reason="NULL_VALUE", detail=sleep_detail),
        )
    )
    assert (candidate.code, candidate.area, candidate.used_metrics) == (
        "DETAIL_RECORDS",
        None,
        used,
    )
    assert candidate.title == "숫자로 다 담기지 않은 기록도 살펴보세요"
    assert candidate.description == "남겨진 기록을 보며 오늘 하루를 가볍게 돌아볼까요?"


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, None),
        (1, "STEPS_PROGRESS"),
        (6999, "STEPS_PROGRESS"),
        (7000, "STEPS_MILESTONE"),
        (7001, "STEPS_MILESTONE"),
    ],
)
def test_steps_boundaries(value, expected):
    candidate = select_candidate(
        message_input(steps=success(value, reason="ZERO_STEPS" if value == 0 else None))
    )
    assert (candidate.code if candidate else None) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, None),
        (1, "EXERCISE_PROGRESS"),
        (29, "EXERCISE_PROGRESS"),
        (30, "EXERCISE_MILESTONE"),
        (31, "EXERCISE_MILESTONE"),
    ],
)
def test_exercise_boundaries(value, expected):
    candidate = select_candidate(message_input(exercises=success(value)))
    assert (candidate.code if candidate else None) == expected


@pytest.mark.parametrize(
    "score,expected",
    [
        (0, "SLEEP_RECORDED"),
        (79, "SLEEP_RECORDED"),
        (80, "SLEEP_REFLECTION"),
        (100, "SLEEP_REFLECTION"),
    ],
)
def test_zero_sleep_is_valid_without_stages(score, expected):
    candidate = select_candidate(
        message_input(
            sleeps=success(0, detail=True),
            score=UsableSleepScore(value=score, computed_at=SCORE_AT),
        )
    )
    assert candidate.code == expected


@pytest.mark.parametrize("value", [True, "30", -1, 1.5])
def test_metric_values_reject_invalid_types(value):
    with pytest.raises(ValidationError):
        success(value)


@pytest.mark.parametrize("value", [True, "80", -1, 101, 80.5])
def test_score_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        UsableSleepScore(value=value, computed_at=SCORE_AT)


@pytest.mark.parametrize(
    "patch",
    [
        {"missing": True},
        {"reason": "UNSYNCED"},
        {"has_stored_value": False},
        {"value": None},
        {"missing": "false"},
        {"sync_failed": "false"},
        {"as_of": "2026-10-29T06:00:00"},
    ],
)
def test_success_rejects_contradictory_or_untyped_facts(patch):
    payload = success(30).model_dump() | patch
    with pytest.raises(ValidationError):
        MetricSuccess.model_validate(payload)


@pytest.mark.parametrize(
    "field,value", [("value", 100), ("has_detail", True), ("as_of", AS_OF)]
)
def test_failure_cannot_carry_candidate_data(field, value):
    with pytest.raises(ValidationError):
        MetricFailure.model_validate(
            {"state": "LOOKUP_FAILED", "sync_failed": None, field: value}
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"has_detail": True},
        {"value": 0},
        {"value": 1, "missing": True, "reason": "UNSYNCED"},
    ],
)
def test_steps_reject_inconsistent_metric_specific_facts(patch):
    payload = message_input(steps=success(30)).model_dump()
    payload["steps"].update(patch)
    with pytest.raises(ValidationError):
        DailyMessageInput.model_validate(payload)


def test_usable_score_requires_valid_sleep_and_aware_computed_at():
    with pytest.raises(ValidationError):
        message_input(score=UsableSleepScore(value=80, computed_at=SCORE_AT))
    with pytest.raises(ValidationError):
        UsableSleepScore(value=80, computed_at="2026-10-29T06:00:00")


def test_all_three_metrics_required_and_no_candidate_for_absence():
    facts = message_input()
    assert select_candidate(facts) is None
    for metric in ("steps", "exercises", "sleeps"):
        payload = facts.model_dump()
        del payload[metric]
        with pytest.raises(ValidationError):
            DailyMessageInput.model_validate(payload)


@pytest.mark.parametrize("failure", ["LOOKUP_FAILED", "DATA_NOT_READY"])
def test_partial_active_day_preserves_success_times(failure):
    result = build_daily_message(
        message_input(
            steps=success(8200),
            exercises=success(35),
            sleeps=MetricFailure(state=failure, sync_failed=None),
        )
    )
    assert (result.status, result.code) == ("PARTIAL", "ACTIVE_DAY")
    assert result.data_as_of.model_dump() == {
        "steps": AS_OF,
        "exercises": AS_OF,
        "sleeps": None,
    }
    assert result.score_computed_at is None
    assert [(n.metric, n.code) for n in result.notices] == [("sleeps", failure)]
    assert result.notices[0].message == (
        "수면 기록은 불러오지 못했어요."
        if failure == "LOOKUP_FAILED"
        else "수면 기록은 아직 준비 중이에요. 잠시 후 다시 확인해 주세요."
    )


def test_partial_detail_uses_only_successful_detail():
    result = build_daily_message(
        message_input(
            exercises=success(0, detail=True),
            sleeps=MetricFailure(state="LOOKUP_FAILED", sync_failed=True),
        )
    )
    assert (result.status, result.code, result.used_metrics) == (
        "PARTIAL",
        "DETAIL_RECORDS",
        ("exercises",),
    )
    assert (
        result.notices[-1].message
        == "최근 동기화에 실패했어요. 앱의 동기화 상태를 확인해 주세요."
    )


@pytest.mark.parametrize(
    "states", list(product(("SUCCESS", "LOOKUP_FAILED", "DATA_NOT_READY"), repeat=3))
)
def test_no_candidate_failure_precedence_and_all_failed_metrics(states):
    metrics = ("steps", "exercises", "sleeps")
    facts = message_input(
        **{
            metric: success()
            if state == "SUCCESS"
            else MetricFailure(state=state, sync_failed=None)
            for metric, state in zip(metrics, states, strict=True)
        }
    )
    if states == ("SUCCESS",) * 3:
        assert build_daily_message(facts).status == "EMPTY"
        return
    error = (
        HealthDataLookupError if "LOOKUP_FAILED" in states else HealthDataNotReadyError
    )
    with pytest.raises(error) as caught:
        build_daily_message(facts)
    assert caught.value.metrics == tuple(
        metric
        for metric, state in zip(metrics, states, strict=True)
        if state != "SUCCESS"
    )


@pytest.mark.parametrize(
    "reason",
    [
        "NOT_QUERIED",
        "NO_ROW",
        "NULL_SCORE",
        "MISMATCH",
        "INVALID_VALUE",
        "LOOKUP_FAILED",
    ],
)
def test_score_unavailable_alone_does_not_fail_sleep(reason):
    result = build_daily_message(
        message_input(
            sleeps=success(450, detail=True),
            score=SleepScoreUnavailable(reason=reason),
        )
    )
    assert (result.status, result.code) == ("READY", "SLEEP_RECORDED")
    assert result.notices == ()
    assert result.data_as_of.sleeps == AS_OF
    assert result.score_computed_at is None


@pytest.mark.parametrize(
    "steps,exercise,expected",
    [(7000, 30, SCORE_AT), (None, 30, None), (None, 0, SCORE_AT)],
)
def test_score_time_only_when_selected(steps, exercise, expected):
    result = build_daily_message(
        message_input(
            steps=success(steps),
            exercises=success(exercise),
            sleeps=success(450, detail=True),
            score=UsableSleepScore(value=80, computed_at=SCORE_AT),
        )
    )
    assert result.status == "READY"
    assert result.score_computed_at == expected


@pytest.mark.parametrize("reason", ["NO_RECORD", "NULL_VALUE", "BEFORE_LINK"])
def test_empty_without_stored_values_uses_absence_copy(reason):
    result = build_daily_message(message_input(steps=success(reason=reason)))
    assert (result.status, result.code, result.area) == ("EMPTY", "NO_RECORDS", None)
    assert result.title == "오늘은 기록 없이도 내 하루를 돌아볼까요?"
    assert result.description == "지금 확인할 수 있는 활동·수면 기록은 없어요."
    assert result.used_metrics == ()


@pytest.mark.parametrize("metric", ["steps", "exercises"])
def test_empty_stored_zero_is_not_absence(metric):
    result = build_daily_message(
        message_input(
            **{metric: success(0, reason="ZERO_STEPS" if metric == "steps" else None)}
        )
    )
    assert (result.status, result.code, result.area) == ("EMPTY", "NO_RECORDS", None)
    assert result.title == "오늘은 내 하루를 가볍게 돌아볼까요?"
    assert result.description == "오늘의 한마디로 소개할 활동·수면 기록이 아직 없어요."


@pytest.mark.parametrize("stored_zero", [False, True])
def test_unsynced_without_candidate_waits_even_with_zero(stored_zero):
    result = build_daily_message(
        message_input(steps=success(0 if stored_zero else None, reason="UNSYNCED"))
    )
    assert (result.status, result.code, result.area) == (
        "WAITING",
        "PENDING_RECORDS",
        None,
    )
    assert result.title == "오늘의 기록을 아직 확인하지 못했어요"
    assert result.description == "괜찮다면 앱의 동기화 상태를 확인해 주세요."
    assert result.notices == ()


def test_normal_missing_does_not_make_existing_candidate_partial():
    result = build_daily_message(
        message_input(steps=success(reason="UNSYNCED"), exercises=success(35))
    )
    assert (result.status, result.code) == ("READY", "EXERCISE_MILESTONE")
    assert result.notices == ()
    assert result.data_as_of.sleeps == AS_OF  # 선택하지 않은 정상 지표도 보존.


def test_notice_order_and_copy_track_used_metrics():
    result = build_daily_message(
        message_input(
            steps=MetricFailure(state="DATA_NOT_READY", sync_failed=True),
            exercises=success(35, sync_failed=True),
            sleeps=MetricFailure(state="LOOKUP_FAILED", sync_failed=True),
        )
    )
    assert (result.status, result.code) == ("PARTIAL", "EXERCISE_MILESTONE")
    assert [(n.metric, n.code) for n in result.notices] == [
        ("steps", "DATA_NOT_READY"),
        ("steps", "SYNC_FAILED"),
        ("exercises", "SYNC_FAILED"),
        ("sleeps", "LOOKUP_FAILED"),
        ("sleeps", "SYNC_FAILED"),
    ]
    assert (
        result.notices[2].message
        == "최근 동기화에 실패해 이전에 저장된 기록을 보여 드려요."
    )
    assert (
        result.notices[1].message
        == result.notices[4].message
        == "최근 동기화에 실패했어요. 앱의 동기화 상태를 확인해 주세요."
    )


@pytest.mark.parametrize("state", ["READY", "EMPTY", "WAITING"])
def test_sync_failure_also_appears_without_partial(state):
    result = build_daily_message(
        message_input(
            steps=success(
                sync_failed=True,
                reason="UNSYNCED" if state == "WAITING" else "NO_RECORD",
            ),
            exercises=success(35 if state == "READY" else None),
        )
    )
    assert result.status == state
    assert [(n.metric, n.code) for n in result.notices] == [("steps", "SYNC_FAILED")]
    assert (
        result.notices[0].message
        == "최근 동기화에 실패했어요. 앱의 동기화 상태를 확인해 주세요."
    )


@pytest.mark.parametrize("sync_failed", [False, None])
def test_sync_failure_is_not_inferred_from_missing_time(sync_failed):
    result = build_daily_message(
        message_input(steps=success(7000, as_of=None, sync_failed=sync_failed))
    )
    assert result.status == "READY"
    assert result.data_as_of.steps is None
    assert result.notices == ()


@pytest.mark.parametrize("reason", ["NO_RECORD", "NULL_VALUE", "UNSYNCED"])
def test_absent_value_cannot_claim_stored_value_after_missing_calculation(reason):
    with pytest.raises(ValidationError):
        success(reason=reason, stored=True)


def test_before_link_preserves_presence_of_suppressed_stored_value():
    result = build_daily_message(
        message_input(steps=success(reason="BEFORE_LINK", stored=True))
    )
    assert (result.status, result.code) == ("EMPTY", "NO_RECORDS")
    assert result.title == "오늘은 내 하루를 가볍게 돌아볼까요?"


@pytest.mark.parametrize(
    "failed_metric,code,used",
    [
        ("steps", "EXERCISE_MILESTONE", ("exercises",)),
        ("exercises", "STEPS_MILESTONE", ("steps",)),
    ],
)
@pytest.mark.parametrize("failure", ["LOOKUP_FAILED", "DATA_NOT_READY"])
def test_failed_activity_excludes_composite_candidates(
    failed_metric, code, used, failure
):
    inputs = {"steps": success(7000), "exercises": success(30)}
    inputs[failed_metric] = MetricFailure(state=failure, sync_failed=None)
    result = build_daily_message(
        message_input(
            **inputs,
            sleeps=success(450, detail=True),
            score=UsableSleepScore(value=90, computed_at=SCORE_AT),
        )
    )
    assert (result.status, result.code, result.used_metrics) == ("PARTIAL", code, used)
    assert result.score_computed_at is None
    assert getattr(result.data_as_of, failed_metric) is None


@pytest.mark.parametrize("metric", ["exercises", "sleeps"])
def test_detail_sync_notice_only_describes_stored_records_when_used(metric):
    result = build_daily_message(
        message_input(
            **{
                metric: success(detail=True, sync_failed=True, reason="NULL_VALUE"),
            }
        )
    )
    assert result.code == "DETAIL_RECORDS"
    assert (
        result.notices[0].message
        == "최근 동기화에 실패해 이전에 저장된 기록을 보여 드려요."
    )
