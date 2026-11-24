"""인증된 회원의 지표·준비 상태를 DB 읽기 스냅샷에서 반환한다."""

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from typing import get_args
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import TemporaryStorageError
from app.health import repository
from app.health.calculations import (
    build_timeline,
    calculate_body_metric,
    calculate_exercise_record_comparison,
    calculate_metric,
    comparison_metric_query,
    timeline_exercises,
    timeline_hearts,
    timeline_sleeps,
)
from app.health.errors import (
    ExerciseRecordNotFoundError,
    ExerciseSampleLimitError,
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
    InvalidHealthData,
    StepRecordNotFoundError,
    UnknownExerciseTypeError,
)
from app.health.lookup import lookup_boundary
from app.health.schemas import (
    BODY_UNITS,
    EXERCISE_SAMPLE_HOURS_LIMIT,
    KST,
    MEASUREMENT_FIELDS,
    ActivityDailyResult,
    ActivityDay,
    ActivitySourceMetric,
    ActivitySourceStatus,
    BodyDailyQuery,
    BodyDailyResult,
    BodyDataStatus,
    BodyDay,
    BodyMetric,
    ComparisonMetric,
    DataStatus,
    ExerciseRecordComparison,
    ExerciseRecordComparisonQuery,
    ExerciseSampleMetric,
    ExerciseSamplesQuery,
    ExerciseSamplesResult,
    ExerciseSampleStatus,
    ExerciseTypeItem,
    HealthDataStatusResult,
    HealthMetric,
    HealthMetricResult,
    HealthQuery,
    HealthTimelineResult,
    HourlyStepsDay,
    HourlyStepsQuery,
    HourlyStepsResult,
    LegacyHealthMetric,
    LifestyleScoreRecord,
    MeasurementDataStatus,
    MeasurementMetric,
    MeasurementRecordsQuery,
    MeasurementRecordsResult,
    MetricReadiness,
    MissingLifestyleScore,
    Period,
    SleepDataStatus,
    SleepSessionsQuery,
    SleepSessionsResult,
    StepRecordDataStatus,
    StepRecordHistoryQuery,
    StepRecordHistoryResult,
    StepRecordsQuery,
    StepRecordsResult,
    Vo2MaxDataStatus,
    Vo2MaxQuery,
    Vo2MaxResult,
)
from app.users import service as user_service

LOOKUP_TIMEOUT_SECONDS = 1.0
STATUS_METRICS: tuple[HealthMetric, ...] = get_args(LegacyHealthMetric)
SCORE_INPUT_METRICS = ("steps", "exercise_sessions", "sleeps", "heart_rates")
TIMELINE_METRICS: tuple[HealthMetric, ...] = (
    "steps",
    "heart_rates",
    "sleeps",
    "exercises",
)


async def get_hourly_steps_range(
    user_id: UUID,
    start: date,
    end: date,
    *,
    start_hour: int,
    end_hour: int,
    session_factory: async_sessionmaker[AsyncSession],
) -> HourlyStepsResult:
    """날짜별 KST [start_hour, end_hour) 저장 걸음. 결측이 있으면 합계도 null."""
    today = _now().astimezone(KST).date()
    query = HourlyStepsQuery.model_validate(
        dict(
            user_id=user_id,
            start=start,
            end=end,
            start_hour=start_hour,
            end_hour=end_hour,
        ),
        context={"today": today},
    )
    async with _lookup_boundary(("steps",)):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            states = await repository.list_sync_states(db_session, query.user_id)
            check_candiy_link(states, user_id=query.user_id, metrics=("steps",))
            selected = next((s for s in states if s.metric == "steps"), None)
            if selected is None:
                raise HealthDataLookupError(("steps",))
            if not selected.initial_sync_done:
                raise HealthDataNotReadyError(("steps",))
            if selected.last_success_at is None:
                raise InvalidHealthData("ready state without a success time")
            records = await repository.list_hourly_steps(
                db_session, query.user_id, query.start, query.end
            )
            _, status = _data_status(states, selected, _now())
        by_date = {record.date: record for record in records}
        if len(by_date) != len(records) or any(
            not query.start <= day <= query.end for day in by_date
        ):
            raise InvalidHealthData("invalid hourly step dates")
        days = []
        for index in range((query.end - query.start).days + 1):
            day = query.start + timedelta(days=index)
            record = by_date.get(day)
            values = (
                record.hourly_steps[query.start_hour : query.end_hour]
                if record is not None and record.hourly_steps is not None
                else None
            )
            days.append(
                HourlyStepsDay(
                    date=day,
                    is_in_progress=day == today,
                    values=values,
                    total=sum(values) if values is not None else None,
                    reason=None
                    if values is not None
                    else "NO_RECORD"
                    if record is None
                    else "NULL_VALUE",
                )
            )
        result = HourlyStepsResult(
            user_id=query.user_id,
            period=Period(start=query.start, end=query.end),
            start_hour=query.start_hour,
            end_hour=query.end_hour,
            days=days,
            total=sum(day.total for day in days)
            if all(day.total is not None for day in days)
            else None,
            data_status=status,
        )
        result.model_dump_json()
        return result


async def get_activity_daily(
    user_id: UUID,
    start: date,
    end: date,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ActivityDailyResult:
    """일별 운동 저장값과 원천별 상태. reason은 활동 행의 유무만 나타낸다."""
    today = _now().astimezone(KST).date()
    query = HealthQuery.model_validate(
        dict(user_id=user_id, metric="exercises", start=start, end=end),
        context={"today": today},
    )
    async with _lookup_boundary(("exercises",)):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            states = await repository.list_sync_states(db_session, query.user_id)
            check_candiy_link(states, user_id=query.user_id, metrics=("exercises",))
            await check_initial_collection(
                db_session, states, user_id=query.user_id, metrics=("exercises",)
            )
            by_metric = {state.metric: state for state in states}
            exercise_state = by_metric.get("exercise_sessions")
            if exercise_state is None:
                raise HealthDataLookupError(("exercises",))
            if not exercise_state.initial_sync_done:
                raise HealthDataNotReadyError(("exercises",))
            source_statuses = {}
            for metric in get_args(ActivitySourceMetric):
                state = by_metric.get(metric)
                if (
                    state is not None
                    and state.initial_sync_done
                    and state.last_success_at is None
                ):
                    raise InvalidHealthData("ready state without a success time")
                source_statuses[metric] = ActivitySourceStatus(
                    ready=state.initial_sync_done if state else None,
                    last_sync_status=state.last_status if state else None,
                    last_data_at=state.last_data_at if state else None,
                    last_success_at=state.last_success_at if state else None,
                )
            checked_at = _now()
            records = await repository.list_activity_daily(
                db_session, query.user_id, query.start, query.end
            )
        by_date = {record.date: record for record in records}
        if len(by_date) != len(records) or any(
            not query.start <= day <= query.end for day in by_date
        ):
            raise InvalidHealthData("invalid activity dates")
        days = []
        for offset in range((query.end - query.start).days + 1):
            day = query.start + timedelta(days=offset)
            record = by_date.get(day)
            values = (
                record.model_dump()
                if record
                else dict(
                    date=day,
                    exercise_minutes=None,
                    exercise_count=None,
                    exercise_calories_kcal=None,
                    exercise_distance_m=None,
                    computed_at=None,
                )
            )
            days.append(
                ActivityDay(
                    **values,
                    is_in_progress=day == today,
                    reason=None if record else "NO_RECORD",
                )
            )
        result = ActivityDailyResult(
            user_id=query.user_id,
            period=Period(start=query.start, end=query.end),
            days=days,
            source_statuses=source_statuses,
            checked_at=checked_at,
        )
        result.model_dump_json()
        return result


async def get_body_daily(
    user_id: UUID,
    metric: BodyMetric,
    start: date,
    end: date,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> BodyDailyResult:
    """선택한 신체 지표의 일별 저장값. 빈 날짜를 다른 측정·프로필로 채우지 않는다."""
    return await _get_body_daily(
        user_id,
        metric,
        start,
        end,
        session_factory=session_factory,
        today=_now().astimezone(KST).date(),
    )


async def _get_body_daily(
    user_id: UUID,
    metric: BodyMetric,
    start: date,
    end: date,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    today: date,
    error_metrics: tuple[HealthMetric, ...] = (),
) -> BodyDailyResult:
    # 기존 내부 조회는 보존하고 공통 지표 진입점에서만 290 오류 계약을 적용한다.
    query = BodyDailyQuery.model_validate(
        dict(user_id=user_id, metric=metric, start=start, end=end),
        context={"today": today},
    )
    async with _lookup_boundary(error_metrics):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            states = await repository.list_sync_states(db_session, query.user_id)
            check_candiy_link(states, user_id=query.user_id, metrics=error_metrics)
            if error_metrics:
                await check_initial_collection(
                    db_session, states, user_id=query.user_id, metrics=error_metrics
                )
            selected = next(
                (state for state in states if state.metric == query.metric), None
            )
            if selected is None:
                raise HealthDataLookupError(error_metrics)
            if not selected.initial_sync_done:
                raise HealthDataNotReadyError(error_metrics)
            if selected.last_success_at is None:
                raise InvalidHealthData("ready state without a success time")
            status = BodyDataStatus(
                last_sync_status=selected.last_status,
                last_data_at=selected.last_data_at,
                last_success_at=selected.last_success_at,
                checked_at=_now(),
            )
            records = await repository.list_body_daily(db_session, query)
            wear = await _read_wear_values(db_session, query, states, today)
        by_date = {record.date: record for record in records}
        if len(by_date) != len(records) or any(
            not query.start <= day <= query.end for day in by_date
        ):
            raise InvalidHealthData("invalid body dates")
        days = []
        for offset in range((query.end - query.start).days + 1):
            day = query.start + timedelta(days=offset)
            record = by_date.get(day)
            values = (
                record.model_dump()
                if record
                else dict(date=day, value=None, measured_at=None, computed_at=None)
            )
            days.append(
                BodyDay(
                    **values,
                    is_in_progress=day == today,
                    reason=None if record else "NO_RECORD",
                    wear_hours=wear[day][0],
                    is_well_worn=wear[day][1],
                )
            )
        result = BodyDailyResult(
            user_id=query.user_id,
            metric=query.metric,
            unit=BODY_UNITS[query.metric],
            period=Period(start=query.start, end=query.end),
            days=days,
            data_status=status,
        )
        result.model_dump_json()
        return result


async def get_sleep_sessions(
    user_id: UUID,
    start: date,
    end: date,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> SleepSessionsResult:
    """KST 종료일 기준의 모든 저장 세션. 주 수면 통계나 원본 조각을 재계산하지 않는다."""
    query = SleepSessionsQuery.model_validate(
        dict(user_id=user_id, start=start, end=end),
        context={"today": _now().astimezone(KST).date()},
    )
    async with _lookup_boundary(("sleeps",)):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            states = await repository.list_sync_states(db_session, query.user_id)
            check_candiy_link(states, user_id=query.user_id, metrics=("sleeps",))
            selected = next(
                (state for state in states if state.metric == "sleeps"), None
            )
            if selected is None:
                raise HealthDataLookupError(("sleeps",))
            if not selected.initial_sync_done:
                raise HealthDataNotReadyError(("sleeps",))
            if selected.last_success_at is None:
                raise InvalidHealthData("ready state without a success time")
            status = SleepDataStatus(
                last_sync_status=selected.last_status,
                last_data_at=selected.last_data_at,
                last_success_at=selected.last_success_at,
                checked_at=_now(),
            )
            sessions = await repository.list_sleep_sessions(db_session, query)
        result = SleepSessionsResult(
            user_id=query.user_id,
            period=Period(start=query.start, end=query.end),
            sessions=sessions,
            data_status=status,
        )
        result.model_dump_json()
        return result


async def get_vo2_max_records(
    user_id: UUID,
    start: date,
    end: date,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> Vo2MaxResult:
    """현재 저장된 VO₂max 원본. 수집 성공 시점의 버전으로 되돌리지 않는다."""
    query = Vo2MaxQuery.model_validate(
        dict(user_id=user_id, start=start, end=end),
        context={"today": _now().astimezone(KST).date()},
    )
    async with _lookup_boundary(()):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            states = await repository.list_sync_states(db_session, query.user_id)
            check_candiy_link(states, user_id=query.user_id)
            selected = next(
                (state for state in states if state.metric == query.metric), None
            )
            if selected is None:
                raise HealthDataLookupError()
            if not selected.initial_sync_done:
                raise HealthDataNotReadyError()
            if selected.last_success_at is None:
                raise InvalidHealthData("ready state without a success time")
            status = Vo2MaxDataStatus(
                last_sync_status=selected.last_status,
                last_data_at=selected.last_data_at,
                last_success_at=selected.last_success_at,
                checked_at=_now(),
            )
            records = await repository.list_vo2_max_records(db_session, query)
        result = Vo2MaxResult(
            user_id=query.user_id,
            period=Period(start=query.start, end=query.end),
            unit="mL/kg/min",
            records=records,
            data_status=status,
        )
        result.model_dump_json()
        return result


async def get_measurement_records(
    user_id: UUID,
    metric: MeasurementMetric,
    start: date,
    end: date,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> MeasurementRecordsResult:
    """5종 개별 측정의 현재 원본. 집계 필터·대표값 선택·반올림을 적용하지 않는다."""
    query = MeasurementRecordsQuery.model_validate(
        dict(user_id=user_id, metric=metric, start=start, end=end),
        context={"today": _now().astimezone(KST).date()},
    )
    async with _lookup_boundary(query.error_metrics):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            states = await repository.list_sync_states(db_session, query.user_id)
            check_candiy_link(
                states, user_id=query.user_id, metrics=query.error_metrics
            )
            selected = next((s for s in states if s.metric == query.metric), None)
            if selected is None:
                raise HealthDataLookupError(query.error_metrics)
            if not selected.initial_sync_done:
                raise HealthDataNotReadyError(query.error_metrics)
            if selected.last_success_at is None:
                raise InvalidHealthData("ready state without a success time")
            status = MeasurementDataStatus(
                last_sync_status=selected.last_status,
                last_data_at=selected.last_data_at,
                last_success_at=selected.last_success_at,
                checked_at=_now(),
            )
            records = await repository.list_measurement_records(db_session, query)
        result = MeasurementRecordsResult(
            user_id=query.user_id,
            metric=query.metric,
            unit=MEASUREMENT_FIELDS[query.metric][1],
            period=Period(start=query.start, end=query.end),
            records=records,
            data_status=status,
        )
        result.model_dump_json()
        return result


def _step_record_status(
    states: Sequence[repository.SyncRecord], user_id: UUID
) -> StepRecordDataStatus:
    check_candiy_link(states, user_id=user_id, metrics=("steps",))
    selected = next((s for s in states if s.metric == "steps"), None)
    if selected is None:
        raise HealthDataLookupError(("steps",))
    if not selected.initial_sync_done:
        raise HealthDataNotReadyError(("steps",))
    if selected.last_success_at is None:
        raise InvalidHealthData("ready state without a success time")
    return StepRecordDataStatus(
        last_sync_status=selected.last_status,
        last_data_at=selected.last_data_at,
        last_success_at=selected.last_success_at,
        checked_at=_now(),
    )


async def get_step_records(
    user_id: UUID,
    start: date,
    end: date,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> StepRecordsResult:
    """시작일 기준 현재 걸음 원본 목록. 출처를 고르거나 합산하지 않는다."""
    query = StepRecordsQuery.model_validate(
        dict(user_id=user_id, start=start, end=end),
        context={"today": _now().astimezone(KST).date()},
    )
    async with _lookup_boundary(("steps",)):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            status = _step_record_status(
                await repository.list_sync_states(db_session, query.user_id),
                query.user_id,
            )
            records = await repository.list_step_records(db_session, query)
        result = StepRecordsResult(
            user_id=query.user_id,
            metric="steps",
            unit="steps",
            period=Period(start=query.start, end=query.end),
            records=records,
            data_status=status,
        )
        result.model_dump_json()
        return result


async def get_step_record_history(
    user_id: UUID,
    record_id: str,
    start: date,
    end: date,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> StepRecordHistoryResult:
    """수정일 기준 저장된 걸음 버전. 현재 원본과 과거 출처·구간을 혼동하지 않는다."""
    query = StepRecordHistoryQuery.model_validate(
        dict(user_id=user_id, record_id=record_id, start=start, end=end),
        context={"today": _now().astimezone(KST).date()},
    )
    async with _lookup_boundary(("steps",)):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            status = _step_record_status(
                await repository.list_sync_states(db_session, query.user_id),
                query.user_id,
            )
            record = await repository.find_step_record(db_session, query)
            if record is None:
                raise StepRecordNotFoundError()
            versions = await repository.list_step_record_versions(db_session, query)
        result = StepRecordHistoryResult(
            user_id=query.user_id,
            metric="steps",
            unit="steps",
            period=Period(start=query.start, end=query.end),
            record=record,
            versions=versions,
            data_status=status,
        )
        result.model_dump_json()
        return result


async def get_exercise_samples(
    user_id: UUID,
    record_id: str,
    metric: ExerciseSampleMetric,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ExerciseSamplesResult:
    """저장 운동 구간의 현재 원본 샘플. 집계 필터·통계는 다시 적용하지 않는다."""
    query = ExerciseSamplesQuery(user_id=user_id, record_id=record_id, metric=metric)
    error_metrics: tuple[HealthMetric, ...] = (
        ("exercises", "heart_rates") if metric == "heart_rates" else ("exercises",)
    )
    async with _lookup_boundary(error_metrics):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            states = await repository.list_sync_states(db_session, query.user_id)
            check_candiy_link(states, user_id=query.user_id, metrics=error_metrics)
            statuses = {}
            for required in ("exercise_sessions", query.metric):
                selected = next((s for s in states if s.metric == required), None)
                if selected is None:
                    raise HealthDataLookupError(error_metrics)
                if not selected.initial_sync_done:
                    raise HealthDataNotReadyError(error_metrics)
                if selected.last_success_at is None:
                    raise InvalidHealthData("ready state without a success time")
                statuses[required] = ExerciseSampleStatus(
                    last_sync_status=selected.last_status,
                    last_data_at=selected.last_data_at,
                    last_success_at=selected.last_success_at,
                )
            window = await repository.find_exercise_sample_window(db_session, query)
            if window is None:
                raise ExerciseRecordNotFoundError()
            if window.end_at - window.start_at > timedelta(
                hours=EXERCISE_SAMPLE_HOURS_LIMIT
            ):
                raise ExerciseSampleLimitError()
            records = await repository.list_exercise_samples(db_session, query, window)
        result = ExerciseSamplesResult(
            user_id=query.user_id,
            metric=query.metric,
            unit="bpm" if query.metric == "heart_rates" else "m/s",
            exercise=window,
            samples=records,
            exercise_status=statuses["exercise_sessions"],
            source_status=statuses[query.metric],
            checked_at=_now(),
        )
        result.model_dump_json()
        return result


def _now() -> datetime:
    return datetime.now(UTC)


def check_candiy_link(
    states: Sequence[repository.SyncRecord],
    *,
    user_id: UUID,
    metrics: tuple[HealthMetric, ...] = (),
) -> None:
    """본인 상태 전체가 http_404일 때만 거절한다. 통과가 연동 확인을 뜻하지는 않는다."""
    if any(state.end_user_id != user_id for state in states):
        raise HealthDataForbiddenError(metrics)
    if states and all(state.last_error == "http_404" for state in states):
        raise HealthDataNotLinkedError(metrics)


async def check_initial_collection(
    db_session: AsyncSession,
    states: Sequence[repository.SyncRecord],
    *,
    user_id: UUID,
    metrics: tuple[HealthMetric, ...] = (),
) -> None:
    """전체 상태와 저장 기록 모두 없을 때만 준비 중. DB 오류는 조회 경계에 전달한다."""
    if states:
        return
    if await repository.has_health_history(db_session, user_id):
        raise HealthDataLookupError(metrics)
    raise HealthDataNotReadyError(metrics)


def _data_status(
    states: Sequence[repository.SyncRecord],
    selected: repository.SyncRecord,
    checked_at: datetime,
) -> tuple[date | None, DataStatus]:
    first_data = min(
        (state.first_data_at for state in states if state.first_data_at is not None),
        default=None,
    )
    return (
        first_data.astimezone(KST).date() if first_data else None,
        DataStatus(
            last_data_at=selected.last_data_at,
            synced_until=max(
                (
                    state.last_data_at
                    for state in states
                    if state.last_data_at is not None
                ),
                default=None,
            ),
            last_sync_status=selected.last_status,
            last_success_at=selected.last_success_at,
            checked_at=checked_at,
        ),
    )


async def list_exercise_types(
    *, session_factory: async_sessionmaker[AsyncSession]
) -> list[ExerciseTypeItem]:
    """회원 상태와 무관한 등록 운동 목록. 빈 기준표와 조회 실패를 구분한다."""
    async with (
        _lookup_boundary(("exercises",)),
        session_factory() as db_session,
        db_session.begin(),
    ):
        await db_session.execute(text("SET TRANSACTION READ ONLY"))
        return await repository.list_exercise_types(db_session)


async def get_exercise_record_comparison(
    user_id: UUID,
    metric: ComparisonMetric,
    start: date,
    end: date,
    *,
    exercise_type: int | None = None,
    session_factory: async_sessionmaker[AsyncSession],
) -> ExerciseRecordComparison:
    """인증된 회원의 운동 기준일 그룹을 비교한다. 읽기 실패는 빈 그룹이 아니다."""
    today = _now().astimezone(KST).date()
    query = ExerciseRecordComparisonQuery.model_validate(
        dict(
            user_id=user_id,
            metric=metric,
            start=start,
            end=end,
            exercise_type=exercise_type,
        ),
        context={"today": today},
    )
    exercise_query = HealthQuery.model_validate(
        dict(
            user_id=query.user_id, metric="exercises", start=query.start, end=query.end
        ),
        context={"today": today},
    )
    metric_query = comparison_metric_query(query, today)
    metrics = ("exercises", query.metric)
    async with _lookup_boundary(metrics):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            states = await repository.list_sync_states(db_session, query.user_id)
            check_candiy_link(states, user_id=query.user_id, metrics=metrics)
            by_metric = {state.metric: state for state in states}
            selected = {
                "exercises": by_metric.get("exercise_sessions"),
                query.metric: by_metric.get(query.metric),
            }
            missing = tuple(name for name, state in selected.items() if state is None)
            if missing:
                raise HealthDataLookupError(missing)
            unready = tuple(
                name for name, state in selected.items() if not state.initial_sync_done
            )
            if unready:
                raise HealthDataNotReadyError(unready)
            invalid = tuple(
                name
                for name, state in selected.items()
                if state.last_success_at is None
            )
            if invalid:
                raise HealthDataLookupError(invalid)
            checked_at = _now()
            linked_from, exercise_status = _data_status(
                states, selected["exercises"], checked_at
            )
            _, metric_status = _data_status(states, selected[query.metric], checked_at)
            async with _lookup_boundary(("exercises",)):
                registered_codes = await repository.list_exercise_codes(db_session)
                if (
                    query.exercise_type is not None
                    and query.exercise_type not in registered_codes
                ):
                    raise UnknownExerciseTypeError()
                exercises = await repository.list_daily_records(
                    db_session, exercise_query
                )
            records = []
            if metric_query is not None:
                async with _lookup_boundary((query.metric,)):
                    records = await repository.list_daily_records(
                        db_session, metric_query
                    )
        result = calculate_exercise_record_comparison(
            query,
            exercises,
            records,
            linked_from=linked_from,
            exercise_status=exercise_status,
            metric_status=metric_status,
            today=today,
            registered_codes=registered_codes,
        )
        result.to_public().model_dump_json()
        return result


async def get_timeline(
    user_id: UUID,
    start: date,
    end: date,
    *,
    today: date,
    session_factory: async_sessionmaker[AsyncSession],
) -> HealthTimelineResult:
    """검증된 본인·1~31일 기간을 한 스냅샷에서 읽는다. today는 요청 경계에서 고정한다."""
    async with _lookup_boundary(TIMELINE_METRICS):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            states = await repository.list_sync_states(db_session, user_id)
            check_candiy_link(states, user_id=user_id, metrics=TIMELINE_METRICS)
            await check_initial_collection(
                db_session, states, user_id=user_id, metrics=TIMELINE_METRICS
            )
            by_metric = {state.metric: state for state in states}
            selected = {
                metric: by_metric.get(
                    "exercise_sessions" if metric == "exercises" else metric
                )
                for metric in TIMELINE_METRICS
            }
            missing = tuple(
                metric for metric, state in selected.items() if state is None
            )
            if missing:
                raise HealthDataLookupError(missing)
            unready = tuple(
                metric
                for metric, state in selected.items()
                if not state.initial_sync_done
            )
            if unready:
                raise HealthDataNotReadyError(unready)
            invalid = tuple(
                metric
                for metric, state in selected.items()
                if state.last_success_at is None
            )
            if invalid:
                raise HealthDataLookupError(invalid)

            async with _lookup_boundary(("steps",)):
                steps = {
                    record.date: record.hourly_steps
                    for record in await repository.list_hourly_steps(
                        db_session, user_id, start, end
                    )
                }
            async with _lookup_boundary(("heart_rates",)):
                hearts = timeline_hearts(
                    await repository.list_hourly_hearts(db_session, user_id, start, end)
                )
            async with _lookup_boundary(("sleeps",)):
                sleeps = timeline_sleeps(
                    await repository.list_main_sleeps(db_session, user_id, start, end)
                )
            async with _lookup_boundary(("exercises",)):
                exercises = timeline_exercises(
                    await repository.list_exercise_sessions(
                        db_session, user_id, start, end
                    )
                )
        result = build_timeline(
            user_id,
            start,
            end,
            today=today,
            steps=steps,
            hearts=hearts,
            sleeps=sleeps,
            exercises=exercises,
        )
        result.model_dump_json()
        return result


async def get_lifestyle_score(
    user_id: UUID,
    score_date: date,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> LifestyleScoreRecord | MissingLifestyleScore:
    """검증된 회원·날짜의 저장 점수와 미연동 신호를 한 스냅샷에서 읽는다."""
    async with _lookup_boundary(()):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            states = await repository.list_sync_states(db_session, user_id)
            check_candiy_link(states, user_id=user_id)
            result = await repository.find_lifestyle_score(
                db_session, user_id, score_date
            )
            if result is None:
                await check_initial_collection(db_session, states, user_id=user_id)
                result = _missing_lifestyle_score(states, user_id, score_date)
        result.model_dump_json()
        return result


def _missing_lifestyle_score(
    states: Sequence[repository.SyncRecord], user_id: UUID, score_date: date
) -> MissingLifestyleScore:
    by_metric = {state.metric: state for state in states}
    for metric in SCORE_INPUT_METRICS:
        state = by_metric.get(metric)
        if state is None or not state.initial_sync_done:
            raise HealthDataNotReadyError()
        if state.last_success_at is None:
            raise InvalidHealthData("ready state without a success time")

    # 준비 여부는 입력 4종, 기록 경계는 지표 API와 동일하게 회원의 모든 지표다.
    first_data = min(
        (state.first_data_at for state in states if state.first_data_at is not None),
        default=None,
    )
    last_data = max(
        (state.last_data_at for state in states if state.last_data_at is not None),
        default=None,
    )
    if first_data is None or score_date < first_data.astimezone(KST).date():
        reason = "BEFORE_LINK"
    elif last_data is None or score_date >= last_data.astimezone(KST).date():
        reason = "UNSYNCED"
    else:
        reason = "NO_RECORD"
    return MissingLifestyleScore(end_user_id=user_id, date=score_date, reason=reason)


async def get_metric_result(
    user_id: UUID,
    metric: HealthMetric,
    start: date,
    end: date,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> HealthMetricResult:
    """인증·인가 후 호출한다. 자체 읽기 트랜잭션을 종료한 결과만 반환한다.

    입력 오류는 Pydantic ValidationError, 알려진 건강 조회 실패는 HealthDataError다.
    본인 상태 전체의 http_404 판정은 집계 준비 여부보다 먼저 적용한다.
    """
    today = _now().astimezone(KST).date()
    query = HealthQuery.model_validate(
        dict(user_id=user_id, metric=metric, start=start, end=end),
        context={"today": today},
    )
    if query.metric in BODY_UNITS:
        async with _lookup_boundary((query.metric,)):
            body = await _get_body_daily(
                user_id,
                query.metric,
                start,
                end,
                session_factory=session_factory,
                today=today,
                error_metrics=(query.metric,),
            )
            result = calculate_body_metric(body)
            result.to_public().model_dump_json()
            return result
    async with _lookup_boundary((query.metric,)):
        async with session_factory() as db_session, db_session.begin():
            # 첫 SELECT 전에 적용하며 풀의 기본 격리 수준은 바꾸지 않는다.
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            states = await repository.list_sync_states(db_session, query.user_id)
            checked_at = _now()
            check_candiy_link(states, user_id=query.user_id, metrics=(query.metric,))
            await check_initial_collection(
                db_session, states, user_id=query.user_id, metrics=(query.metric,)
            )
            state_metric = (
                "exercise_sessions" if query.metric == "exercises" else query.metric
            )
            selected = next(
                (state for state in states if state.metric == state_metric),
                None,
            )
            if selected is None:
                raise HealthDataLookupError((query.metric,))
            if not selected.initial_sync_done:
                raise HealthDataNotReadyError((query.metric,))
            if selected.last_success_at is None:
                raise InvalidHealthData("ready state without a success time")
            records = await repository.list_daily_records(db_session, query)
            linked_from, status = _data_status(states, selected, checked_at)
            wear = await _read_wear_values(db_session, query, states, today)
        # 지연 조회 없이 구체화한 값으로 DB 읽기를 끝낸 뒤 계산한다.
        result = calculate_metric(
            query,
            records,
            linked_from=linked_from,
            status=status,
            today=today,
            activity_source_statuses={
                metric: ActivitySourceStatus(
                    ready=state.initial_sync_done if state else None,
                    last_sync_status=state.last_status if state else None,
                    last_data_at=state.last_data_at if state else None,
                    last_success_at=state.last_success_at if state else None,
                )
                for metric in get_args(ActivitySourceMetric)
                for state in [next((s for s in states if s.metric == metric), None)]
            }
            if query.metric == "exercises"
            else None,
        )
        result = result.model_copy(
            update={
                "days": [
                    day.model_copy(
                        update={
                            "wear_hours": wear[day.date][0],
                            "is_well_worn": wear[day.date][1],
                        }
                    )
                    for day in result.days
                ]
            }
        )
        result.to_public().model_dump_json()
        return result


async def _read_wear_values(
    db_session: AsyncSession,
    query: HealthQuery | BodyDailyQuery,
    states: Sequence[repository.SyncRecord],
    today: date,
) -> dict[date, tuple[int | None, bool | None]]:
    heart = next((state for state in states if state.metric == "heart_rates"), None)
    hours = {}
    if heart is not None and heart.initial_sync_done:
        rows = await repository.list_hourly_hearts(
            db_session, query.user_id, query.start, query.end
        )
        for row in rows:
            if row.date in hours or not query.start <= row.date <= query.end:
                raise InvalidHealthData("invalid wear dates")
            hourly_hr = row.hourly_hr or {}
            if any(key not in {str(hour) for hour in range(24)} for key in hourly_hr):
                raise InvalidHealthData("invalid wear hours")
            hours[row.date] = len(hourly_hr)
    linked_from, status = _data_status(states, heart or states[0], _now())
    return {
        day: _wear_values(day, hours, heart, linked_from, status.synced_until, today)
        for offset in range((query.end - query.start).days + 1)
        for day in [query.start + timedelta(days=offset)]
    }


def _wear_values(
    day: date,
    hours: dict[date, int],
    heart: repository.SyncRecord | None,
    linked_from: date | None,
    synced_until: datetime | None,
    today: date,
) -> tuple[int | None, bool | None]:
    """calculation-rules 9.1의 순서대로 판단하며 수집 실패를 미착용으로 바꾸지 않는다."""
    if linked_from is None or day < linked_from:
        return None, None
    if (
        heart is None
        or not heart.initial_sync_done
        or (
            heart.last_status == "fail"
            and (
                heart.last_success_at is None
                or day >= heart.last_success_at.astimezone(KST).date()
            )
        )
    ):
        return None, None
    incomplete = (
        day == today
        or synced_until is None
        or day >= synced_until.astimezone(KST).date()
    )
    if incomplete:
        count = hours.get(day)
        return count, True if count is not None and count >= 10 else None
    count = hours.get(day, 0)
    return count, count >= 10


def _lookup_boundary(
    metrics: tuple[HealthMetric, ...],
) -> AbstractAsyncContextManager[None]:
    return lookup_boundary(metrics, seconds=LOOKUP_TIMEOUT_SECONDS, clock=monotonic)


async def get_data_status(
    user_id: UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> HealthDataStatusResult:
    """앱 설정 완료·최초 도착·5개 지표 상태를 같은 읽기 스냅샷에서 반환한다."""
    async with _lookup_boundary(STATUS_METRICS):
        async with session_factory() as db_session, db_session.begin():
            await db_session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            states = await repository.list_sync_states(db_session, user_id)
            first_arrived_at = await repository.find_first_arrival(db_session, user_id)
            try:
                connection = await user_service.get_app_connection(db_session, user_id)
            except TemporaryStorageError:
                raise HealthDataLookupError(STATUS_METRICS) from None
            checked_at = _now()
            if any(state.end_user_id != user_id for state in states):
                raise HealthDataForbiddenError(STATUS_METRICS)
            by_metric = {state.metric: state for state in states}
            metrics = []
            for metric in STATUS_METRICS:
                state_metric = "exercise_sessions" if metric == "exercises" else metric
                selected = by_metric.get(state_metric)
                if (
                    selected
                    and selected.initial_sync_done
                    and selected.last_success_at is None
                ):
                    raise InvalidHealthData("ready state without a success time")
                metrics.append(
                    MetricReadiness(
                        metric=metric,
                        ready=selected.initial_sync_done if selected else None,
                        last_sync_status=selected.last_status if selected else None,
                        last_data_at=selected.last_data_at if selected else None,
                        last_success_at=selected.last_success_at if selected else None,
                    )
                )
        result = HealthDataStatusResult(
            user_id=user_id,
            app_connection=connection,
            first_arrived_at=first_arrived_at,
            metrics=metrics,
            checked_at=checked_at,
        )
        result.model_dump_json()
        return result
