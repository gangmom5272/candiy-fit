"""한마디의 고정 날짜·읽기 조합. 자원 종료 판정은 198번 읽기 블록에 맡긴다."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.health import repository
from app.health.calculations import calculate_metric
from app.health.daily_message import (
    DailyMessageInput,
    DailyMessageMetric,
    DailyMessageResult,
    MetricFailure,
    MetricSuccess,
    SleepScoreUnavailable,
    UsableSleepScore,
    build_daily_message,
)
from app.health.daily_message_read import read_daily_message_block
from app.health.diagnostics import emit_health_event
from app.health.errors import (
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotReadyError,
    InvalidHealthData,
)
from app.health.schemas import KST, HealthQuery, MetricReadiness
from app.health.service import _data_status, check_candiy_link, check_initial_collection

MESSAGE_METRICS: tuple[DailyMessageMetric, ...] = ("steps", "exercises", "sleeps")


def classify_sleep_score(
    row: repository.SleepScoreRow | None,
    *,
    user_id: UUID,
    today: date,
    sleep_minutes: int,
) -> UsableSleepScore | SleepScoreUnavailable:
    if row is None:
        return SleepScoreUnavailable(reason="NO_ROW")
    if row.end_user_id != user_id:
        raise HealthDataForbiddenError()
    if (
        type(row.date) is not date
        or (
            row.sleep_minutes is not None
            and (type(row.sleep_minutes) is not int or row.sleep_minutes < 0)
        )
        or (
            row.sleep_score is not None
            and (type(row.sleep_score) is not int or not 0 <= row.sleep_score <= 100)
        )
        or not isinstance(row.computed_at, datetime)
        or row.computed_at.tzinfo is None
        or row.computed_at.utcoffset() is None
    ):
        return SleepScoreUnavailable(reason="INVALID_VALUE")
    if row.date != today or (
        row.sleep_minutes is not None and row.sleep_minutes != sleep_minutes
    ):
        return SleepScoreUnavailable(reason="MISMATCH")
    if row.sleep_score is None:
        return SleepScoreUnavailable(reason="NULL_SCORE")
    if row.sleep_minutes is None:
        return SleepScoreUnavailable(reason="MISMATCH")
    return UsableSleepScore(value=row.sleep_score, computed_at=row.computed_at)


async def _read_metric(
    session: AsyncSession,
    user_id: UUID,
    today: date,
    metric: DailyMessageMetric,
    observe_sync: Callable[[bool | None], None],
) -> MetricSuccess:
    states = await repository.list_sync_states(session, user_id)
    check_candiy_link(states, user_id=user_id, metrics=(metric,))
    await check_initial_collection(session, states, user_id=user_id, metrics=(metric,))
    state_name = "exercise_sessions" if metric == "exercises" else metric
    selected = next((state for state in states if state.metric == state_name), None)
    if selected is None:
        raise HealthDataLookupError((metric,))
    readiness = MetricReadiness(
        metric=metric,
        ready=selected.initial_sync_done,
        last_sync_status=selected.last_status,
        last_data_at=selected.last_data_at,
        last_success_at=selected.last_success_at,
    )
    if readiness.ready is None:
        raise InvalidHealthData("existing state without readiness")
    sync_failed = (
        None
        if readiness.last_sync_status is None
        else readiness.last_sync_status == "fail"
    )
    observe_sync(sync_failed)
    if readiness.ready is False:
        raise HealthDataNotReadyError((metric,))
    if readiness.last_success_at is None:
        raise InvalidHealthData("ready state without success time")
    query = HealthQuery.model_validate(
        dict(user_id=user_id, metric=metric, start=today, end=today),
        context={"today": today},
    )
    records = await repository.list_daily_records(session, query)
    for record in records:
        if record.date != today or (
            record.value is not None
            and (type(record.value) is not int or record.value < 0)
        ):
            raise InvalidHealthData("invalid daily message record")
        if metric == "sleeps":
            night = record.night
            if (
                night is None
                or night.date != today
                or night.end_at.astimezone(KST).date() != today
                or night.start_at >= night.end_at
                or night.sleep_minutes != record.value
                or (
                    record.value is not None
                    and record.value * 60
                    > (night.end_at - night.start_at).total_seconds()
                )
            ):
                raise InvalidHealthData("invalid daily message sleep")
    linked_from, status = _data_status(states, selected, datetime.now(UTC))
    calculated = calculate_metric(
        query,
        records,
        linked_from=linked_from,
        status=status,
        today=today,
    )
    day = calculated.days[0]
    return MetricSuccess(
        value=day.value,
        missing=day.missing,
        reason=day.reason,
        has_stored_value=any(record.value is not None for record in records),
        has_detail=bool(calculated.detail.nights)
        if metric == "sleeps"
        else (
            any(not item.counts_as_exercise for item in calculated.detail.sessions)
            if metric == "exercises"
            else False
        ),
        as_of=status.last_data_at,
        sync_failed=sync_failed,
    )


async def get_daily_message(
    user_id: UUID, *, today: date, request_id: str, engine: AsyncEngine
) -> DailyMessageResult:
    async def common(session: AsyncSession) -> None:
        states = await repository.list_sync_states(session, user_id)
        check_candiy_link(states, user_id=user_id)

    await read_daily_message_block(engine, common, request_id=request_id)
    outcomes: dict[DailyMessageMetric, MetricSuccess | MetricFailure] = {}
    sleep_score: UsableSleepScore | SleepScoreUnavailable = SleepScoreUnavailable(
        reason="NOT_QUERIED"
    )
    for metric in MESSAGE_METRICS:
        sync_failed = None
        score_queried = False

        def observe_sync(signal: bool | None) -> None:
            nonlocal sync_failed
            sync_failed = signal

        async def read(
            session: AsyncSession, metric: DailyMessageMetric = metric
        ) -> MetricSuccess:
            return await _read_metric(session, user_id, today, metric, observe_sync)

        async def score_read(
            session: AsyncSession, sleep: MetricSuccess
        ) -> UsableSleepScore | SleepScoreUnavailable:
            nonlocal score_queried
            if sleep.missing or sleep.value is None:
                return SleepScoreUnavailable(reason="NOT_QUERIED")
            score_queried = True
            row = await repository.find_sleep_score(session, user_id, today)
            score = classify_sleep_score(
                row, user_id=user_id, today=today, sleep_minutes=sleep.value
            )
            if isinstance(score, SleepScoreUnavailable) and score.reason in (
                "MISMATCH",
                "INVALID_VALUE",
            ):
                emit_health_event(
                    "daily_message_score_unavailable",
                    **{
                        "request_id": request_id,
                        "reason": score.reason,
                    },
                )
            return score

        try:
            block = await read_daily_message_block(
                engine,
                read,
                request_id=request_id,
                metric=metric,
                score_read=score_read if metric == "sleeps" else None,
            )
        except (HealthDataLookupError, HealthDataNotReadyError) as error:
            outcomes[metric] = MetricFailure(
                state=error.reason, sync_failed=sync_failed
            )
            if metric == "sleeps" and score_queried:
                sleep_score = SleepScoreUnavailable(reason="LOOKUP_FAILED")
        else:
            outcomes[metric] = block.value
            if metric == "sleeps":
                sleep_score = (
                    SleepScoreUnavailable(reason="LOOKUP_FAILED")
                    if block.score_error is not None
                    else block.score
                )
    return build_daily_message(
        DailyMessageInput(
            user_id=user_id, date=today, sleep_score=sleep_score, **outcomes
        )
    )
