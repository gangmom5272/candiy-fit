"""회원·기간을 제한한 집계 조회. 트랜잭션은 서비스가 소유한다."""

import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import (
    Integer,
    Numeric,
    RowMapping,
    Select,
    Text,
    case,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.health.calculations import (
    DailyRecord,
    exercise_records,
    heart_record,
    oxygen_record,
    sleep_record,
)
from app.health.errors import (
    ExerciseSampleLimitError,
    HealthDataForbiddenError,
    InvalidHealthData,
    MeasurementRecordLimitError,
    SleepSessionLimitError,
    StepHistoryLimitError,
    StepRecordLimitError,
    Vo2MaxRecordLimitError,
)
from app.health.models import (
    ActivityDaily,
    BodyDaily,
    ExerciseCode,
    ExerciseSession,
    HeartDaily,
    LifestyleScore,
    RawHealthRecord,
    SleepSession,
    StepSnapshot,
    SyncState,
)
from app.health.schemas import (
    BODY_UNITS,
    EXERCISE_RAW_LIMIT,
    EXERCISE_SAMPLE_BYTES_LIMIT,
    EXERCISE_SAMPLE_LIMIT,
    KST,
    MEASUREMENT_FIELDS,
    MEASUREMENT_RECORD_LIMIT,
    SLEEP_SESSION_LIMIT,
    STEP_HISTORY_LIMIT,
    STEP_RECORD_COUNT_MAX,
    STEP_RECORD_LIMIT,
    VO2_MAX_RECORD_LIMIT,
    ActivityDailyRecord,
    BodyDailyQuery,
    BodyDailyRecord,
    ExerciseSample,
    ExerciseSampleSource,
    ExerciseSamplesQuery,
    ExerciseSampleWindow,
    ExerciseSessionDetail,
    ExerciseTypeItem,
    HealthQuery,
    HourlyHeartRecord,
    HourlyStepsRecord,
    LifestyleScoreRecord,
    MainSleepRecord,
    MeasurementRecord,
    MeasurementRecordsQuery,
    Night,
    SleepSessionDetail,
    SleepSessionsQuery,
    StepRawRecord,
    StepRecordHistoryQuery,
    StepRecordsQuery,
    StepRecordVersion,
    Vo2MaxQuery,
    Vo2MaxRecord,
)


@dataclass(frozen=True)
class SyncRecord:
    end_user_id: UUID
    metric: str
    initial_sync_done: bool
    last_data_at: datetime | None
    first_data_at: datetime | None
    last_success_at: datetime | None
    last_status: str | None
    last_error: str | None


async def has_health_history(db_session: AsyncSession, user_id: UUID) -> bool:
    """상태 유실을 최초 수집 대기로 오인하지 않도록 값 없이 존재 여부만 읽는다."""
    models = (
        RawHealthRecord,
        StepSnapshot,
        ActivityDaily,
        BodyDaily,
        ExerciseSession,
        HeartDaily,
        SleepSession,
        LifestyleScore,
    )
    result = await db_session.scalar(
        select(
            or_(
                *(
                    select(model.end_user_id)
                    .where(model.end_user_id == user_id)
                    .exists()
                    for model in models
                )
            )
        )
    )
    if type(result) is not bool:
        raise InvalidHealthData("invalid health history existence result")
    return result


@dataclass(frozen=True)
class SleepScoreRow:
    end_user_id: UUID
    date: date
    sleep_score: int | None
    sleep_minutes: int | None
    computed_at: datetime


async def find_sleep_score(
    db_session: AsyncSession, user_id: UUID, score_date: date
) -> SleepScoreRow | None:
    """수면 근거만 구체화한다. 필드 유효성·근거 대조는 한마디 서비스 책임이다."""
    result = await db_session.execute(
        select(
            LifestyleScore.end_user_id,
            LifestyleScore.date,
            LifestyleScore.sleep_score,
            LifestyleScore.sleep_minutes,
            LifestyleScore.computed_at,
        ).where(
            LifestyleScore.end_user_id == user_id, LifestyleScore.date == score_date
        )
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    _check_owner(row["end_user_id"], user_id)
    return SleepScoreRow(**row)


def _check_owner(owner: UUID, user_id: UUID) -> None:
    if owner != user_id:
        raise HealthDataForbiddenError()


async def list_hourly_steps(
    db_session: AsyncSession, user_id: UUID, start: date, end: date
) -> list[HourlyStepsRecord]:
    result = await db_session.execute(
        select(
            ActivityDaily.end_user_id, ActivityDaily.date, ActivityDaily.hourly_steps
        )
        .where(
            ActivityDaily.end_user_id == user_id,
            ActivityDaily.date >= start,
            ActivityDaily.date <= end,
        )
        .order_by(ActivityDaily.date)
    )
    records = []
    for row in result.mappings():
        _check_owner(row["end_user_id"], user_id)
        records.append(
            HourlyStepsRecord(date=row["date"], hourly_steps=row["hourly_steps"])
        )
    return records


async def list_activity_daily(
    db_session: AsyncSession, user_id: UUID, start: date, end: date
) -> list[ActivityDailyRecord]:
    result = await db_session.execute(
        select(
            ActivityDaily.end_user_id,
            ActivityDaily.date,
            ActivityDaily.exercise_minutes,
            ActivityDaily.exercise_count,
            ActivityDaily.exercise_calories_kcal,
            ActivityDaily.exercise_distance_m,
            ActivityDaily.computed_at,
        )
        .where(
            ActivityDaily.end_user_id == user_id,
            ActivityDaily.date >= start,
            ActivityDaily.date <= end,
        )
        .order_by(ActivityDaily.date)
    )
    records = []
    for row in result.mappings():
        _check_owner(row["end_user_id"], user_id)
        records.append(
            ActivityDailyRecord.model_validate(
                {name: row[name] for name in ActivityDailyRecord.model_fields}
            )
        )

    # 전체 세션 상세를 펼치지 않고 날짜별 인정 시간·횟수만 대조한다.
    sessions = await db_session.execute(
        select(
            ExerciseSession.end_user_id,
            ExerciseSession.date,
            func.count().filter(ExerciseSession.counts_as_exercise).label("count"),
            func.coalesce(
                func.sum(ExerciseSession.duration_minutes).filter(
                    ExerciseSession.counts_as_exercise
                ),
                0,
            ).label("minutes"),
            func.bool_or(ExerciseSession.duration_minutes < 1).label(
                "invalid_duration"
            ),
        )
        .where(
            ExerciseSession.end_user_id == user_id,
            ExerciseSession.date >= start,
            ExerciseSession.date <= end,
        )
        .group_by(ExerciseSession.end_user_id, ExerciseSession.date)
    )
    by_date = {}
    for row in sessions.mappings():
        _check_owner(row["end_user_id"], user_id)
        if row["invalid_duration"]:
            raise InvalidHealthData("invalid exercise duration")
        by_date[row["date"]] = (row["count"], row["minutes"])
    for record in records:
        if (record.exercise_count, record.exercise_minutes) != by_date.pop(
            record.date, (0, 0)
        ):
            raise InvalidHealthData("inconsistent exercise aggregate")
    if by_date:
        raise InvalidHealthData("exercise session without daily aggregate")
    return records


async def list_body_daily(
    db_session: AsyncSession, query: BodyDailyQuery
) -> list[BodyDailyRecord]:
    result = await db_session.execute(
        select(
            BodyDaily.end_user_id,
            BodyDaily.metric,
            BodyDaily.unit,
            BodyDaily.date,
            BodyDaily.value,
            BodyDaily.measured_at,
            BodyDaily.computed_at,
        )
        .where(
            BodyDaily.end_user_id == query.user_id,
            BodyDaily.metric == query.metric,
            BodyDaily.date >= query.start,
            BodyDaily.date <= query.end,
        )
        .order_by(BodyDaily.date)
    )
    records = []
    for row in result.mappings():
        _check_owner(row["end_user_id"], query.user_id)
        if row["metric"] != query.metric or row["unit"] != BODY_UNITS[query.metric]:
            raise InvalidHealthData("invalid body metric or unit")
        records.append(
            BodyDailyRecord.model_validate(
                {name: row[name] for name in BodyDailyRecord.model_fields}
            )
        )
    return records


async def list_hourly_hearts(
    db_session: AsyncSession, user_id: UUID, start: date, end: date
) -> list[HourlyHeartRecord]:
    result = await db_session.execute(
        select(HeartDaily.end_user_id, HeartDaily.date, HeartDaily.hourly_hr)
        .where(
            HeartDaily.end_user_id == user_id,
            HeartDaily.date >= start,
            HeartDaily.date <= end,
        )
        .order_by(HeartDaily.date)
    )
    records = []
    for row in result.mappings():
        _check_owner(row["end_user_id"], user_id)
        records.append(HourlyHeartRecord(date=row["date"], hourly_hr=row["hourly_hr"]))
    return records


async def list_sleep_sessions(
    db_session: AsyncSession, query: SleepSessionsQuery
) -> list[SleepSessionDetail]:
    result = await db_session.execute(
        select(
            SleepSession.end_user_id,
            SleepSession.data_id.label("session_id"),
            SleepSession.date,
            SleepSession.start_at,
            SleepSession.end_at,
            SleepSession.is_main,
            SleepSession.computed_at,
            SleepSession.session_minutes,
            SleepSession.sleep_minutes,
            SleepSession.awake_minutes,
            SleepSession.light_minutes,
            SleepSession.deep_minutes,
            SleepSession.rem_minutes,
            SleepSession.unspecified_minutes,
            SleepSession.unknown_minutes,
            SleepSession.hr_avg.label("avg_heart_rate"),
            SleepSession.hr_min.label("min_heart_rate"),
            SleepSession.hr_max.label("max_heart_rate"),
            SleepSession.spo2_avg,
            SleepSession.spo2_min,
            SleepSession.spo2_max,
            SleepSession.spo2_count,
            SleepSession.spo2_low_count,
            SleepSession.spo2_low_times,
        )
        .where(
            SleepSession.end_user_id == query.user_id,
            SleepSession.date >= query.start,
            SleepSession.date <= query.end,
        )
        .order_by(
            SleepSession.date,
            SleepSession.start_at,
            SleepSession.end_at,
            SleepSession.data_id,
        )
        .limit(SLEEP_SESSION_LIMIT + 1)
    )
    rows = list(result.mappings())
    for row in rows:
        _check_owner(row["end_user_id"], query.user_id)
    # 잘린 목록으로 주 수면 누락이나 상세 오류를 판정하지 않는다.
    if len(rows) > SLEEP_SESSION_LIMIT:
        raise SleepSessionLimitError()
    records = []
    main_counts: dict[date, int] = {}
    for row in rows:
        record = SleepSessionDetail.model_validate(
            {key: value for key, value in row.items() if key != "end_user_id"}
        )
        if not query.start <= record.date <= query.end:
            raise InvalidHealthData("unexpected sleep session date")
        main_counts[record.date] = main_counts.get(record.date, 0) + int(record.is_main)
        records.append(record)
    if any(count != 1 for count in main_counts.values()):
        raise InvalidHealthData("expected one main sleep per recorded day")
    return records


async def list_vo2_max_records(
    db_session: AsyncSession, query: Vo2MaxQuery
) -> list[Vo2MaxRecord]:
    start = datetime.combine(query.start, time.min, tzinfo=KST)
    end = datetime.combine(query.end + timedelta(days=1), time.min, tzinfo=KST)
    table = RawHealthRecord
    raw_value = table.payload["vo2_ml_per_min_kg"]
    raw_method = table.payload["measurement_method"]
    # 타입 확인 전에 cast하지 않는다. 거대한 배열·객체도 앱으로 보내지 않는다.
    numeric_value = case(
        (func.jsonb_typeof(raw_value) == "number", raw_value.astext.cast(Numeric()))
    )
    value = case((numeric_value.between(0, 100), numeric_value))
    method = case(
        (
            func.jsonb_typeof(raw_method) == "number",
            case(
                (
                    raw_method.astext.in_([str(i) for i in range(6)]),
                    raw_method.astext.cast(Integer()),
                )
            ),
        )
    )
    result = await db_session.execute(
        select(
            table.end_user_id,
            table.data_id.label("record_id"),
            table.start_at.label("measured_at"),
            table.end_at,
            value.label("value"),
            method.label("measurement_method"),
            table.app.label("source_app"),
            table.last_modified_at,
            table.ingested_at,
        )
        .where(
            table.end_user_id == query.user_id,
            table.metric == query.metric,
            table.start_at >= start,
            table.start_at < end,
        )
        .order_by(table.start_at, table.data_id)
        .limit(VO2_MAX_RECORD_LIMIT + 1)
    )
    rows = list(result.mappings())
    for row in rows:
        _check_owner(row["end_user_id"], query.user_id)
    if len(rows) > VO2_MAX_RECORD_LIMIT:
        raise Vo2MaxRecordLimitError()
    records = []
    for row in rows:
        if row["end_at"] is not None:
            raise InvalidHealthData("VO2 max must be a point measurement")
        record = Vo2MaxRecord.model_validate(
            dict(date=row["measured_at"].astimezone(KST).date())
            | {
                key: value
                for key, value in row.items()
                if key not in ("end_user_id", "end_at")
            }
        )
        if not query.start <= record.date <= query.end:
            raise InvalidHealthData("unexpected VO2 max date")
        records.append(record)
    return records


async def list_measurement_records(
    db_session: AsyncSession, query: MeasurementRecordsQuery
) -> list[MeasurementRecord]:
    start = datetime.combine(query.start, time.min, tzinfo=KST).astimezone(UTC)
    end = datetime.combine(
        query.end + timedelta(days=1), time.min, tzinfo=KST
    ).astimezone(UTC)
    key, _, maximum = MEASUREMENT_FIELDS[query.metric]
    table = RawHealthRecord
    raw_value = table.payload[key]
    # 큰 배열·객체·범위 밖 숫자는 앱으로 옮기지 않고 필수값 오류로 남긴다.
    numeric_value = case(
        (func.jsonb_typeof(raw_value) == "number", raw_value.astext.cast(Numeric()))
    )
    value = case((numeric_value.between(0, maximum), numeric_value))
    statement = (
        select(
            table.end_user_id,
            table.data_id.label("record_id"),
            table.start_at.label("measured_at"),
            table.end_at,
            value.label("value"),
            table.app.label("source_app"),
            table.last_modified_at,
            table.ingested_at,
        )
        .where(
            table.end_user_id == query.user_id,
            table.metric == query.metric,
            table.start_at >= start,
            table.start_at < end,
        )
        .order_by(table.start_at, table.data_id)
        .limit(MEASUREMENT_RECORD_LIMIT + 1)
    )
    try:
        result = await db_session.execute(statement)
        rows = list(result.mappings())
    except OverflowError:
        # PostgreSQL 시각은 Python datetime 범위보다 넓어 디코딩부터 실패할 수 있다.
        raise InvalidHealthData("measurement timestamp is out of range") from None
    for row in rows:
        if row["end_user_id"] != query.user_id:
            raise HealthDataForbiddenError(query.error_metrics)
    if len(rows) > MEASUREMENT_RECORD_LIMIT:
        raise MeasurementRecordLimitError()
    records = []
    for row in rows:
        if row["end_at"] is not None:
            raise InvalidHealthData("measurement must be a point record")
        try:
            record = MeasurementRecord.model_validate(
                dict(date=row["measured_at"].astimezone(KST).date())
                | {
                    key: value
                    for key, value in row.items()
                    if key not in ("end_user_id", "end_at")
                }
            )
        except OverflowError:
            raise InvalidHealthData("measurement timestamp is out of range") from None
        if not query.start <= record.date <= query.end:
            raise InvalidHealthData("unexpected measurement date")
        records.append(record)
    return records


def _step_records_statement(user_id: UUID) -> Select:
    table = RawHealthRecord
    raw_count = table.payload["count"]
    numeric_count = case(
        (func.jsonb_typeof(raw_count) == "number", raw_count.astext.cast(Numeric()))
    )
    # Integer cast의 소수 반올림을 막고 1200.0 같은 정수값은 허용한다.
    count = case(
        (
            numeric_count.between(0, STEP_RECORD_COUNT_MAX)
            & (numeric_count == func.trunc(numeric_count)),
            numeric_count.cast(Integer()),
        )
    )
    return select(
        table.end_user_id,
        table.data_id.label("record_id"),
        table.start_at,
        table.end_at,
        count.label("step_count"),
        table.app.label("source_app"),
        table.last_modified_at,
        table.ingested_at,
    ).where(table.end_user_id == user_id, table.metric == "steps")


def _step_raw_record(row: RowMapping, user_id: UUID) -> StepRawRecord:
    if row["end_user_id"] != user_id:
        raise HealthDataForbiddenError(("steps",))
    try:
        return StepRawRecord.model_validate(
            dict(date=row["start_at"].astimezone(KST).date())
            | {key: value for key, value in row.items() if key != "end_user_id"}
        )
    except OverflowError:
        raise InvalidHealthData("step record timestamp is out of range") from None


async def list_step_records(
    db_session: AsyncSession, query: StepRecordsQuery
) -> list[StepRawRecord]:
    start = datetime.combine(query.start, time.min, tzinfo=KST).astimezone(UTC)
    end = datetime.combine(
        query.end + timedelta(days=1), time.min, tzinfo=KST
    ).astimezone(UTC)
    statement = (
        _step_records_statement(query.user_id)
        .where(RawHealthRecord.start_at >= start, RawHealthRecord.start_at < end)
        .order_by(RawHealthRecord.start_at, RawHealthRecord.data_id)
        .limit(STEP_RECORD_LIMIT + 1)
    )
    try:
        rows = list((await db_session.execute(statement)).mappings())
    except OverflowError:
        raise InvalidHealthData("step record timestamp is out of range") from None
    for row in rows:
        if row["end_user_id"] != query.user_id:
            raise HealthDataForbiddenError(("steps",))
    if len(rows) > STEP_RECORD_LIMIT:
        raise StepRecordLimitError()
    records = [_step_raw_record(row, query.user_id) for row in rows]
    if any(not query.start <= record.date <= query.end for record in records):
        raise InvalidHealthData("unexpected step record date")
    return records


async def find_step_record(
    db_session: AsyncSession, query: StepRecordHistoryQuery
) -> StepRawRecord | None:
    # 이력 기간은 수정 시각 기준이므로 현재 원본의 시작일로 제한하지 않는다.
    statement = _step_records_statement(query.user_id).where(
        RawHealthRecord.data_id == query.record_id
    )
    try:
        row = (await db_session.execute(statement)).mappings().one_or_none()
    except OverflowError:
        raise InvalidHealthData("step record timestamp is out of range") from None
    if row is None:
        return None
    if row["record_id"] != query.record_id:
        raise InvalidHealthData("unexpected step record identifier")
    return _step_raw_record(row, query.user_id)


async def list_step_record_versions(
    db_session: AsyncSession, query: StepRecordHistoryQuery
) -> list[StepRecordVersion]:
    table = StepSnapshot
    start = datetime.combine(query.start, time.min, tzinfo=KST).astimezone(UTC)
    end = datetime.combine(
        query.end + timedelta(days=1), time.min, tzinfo=KST
    ).astimezone(UTC)
    statement = (
        select(
            table.end_user_id,
            table.data_id,
            table.modified_at,
            table.step_count,
            table.ingested_at,
        )
        .where(
            table.end_user_id == query.user_id,
            table.data_id == query.record_id,
            table.modified_at >= start,
            table.modified_at < end,
        )
        .order_by(table.modified_at)
        .limit(STEP_HISTORY_LIMIT + 1)
    )
    try:
        rows = list((await db_session.execute(statement)).mappings())
    except OverflowError:
        raise InvalidHealthData("step version timestamp is out of range") from None
    for row in rows:
        if row["end_user_id"] != query.user_id:
            raise HealthDataForbiddenError(("steps",))
        if row["data_id"] != query.record_id:
            raise InvalidHealthData("unexpected step version identifier")
    if len(rows) > STEP_HISTORY_LIMIT:
        raise StepHistoryLimitError()
    versions = []
    for row in rows:
        try:
            version = StepRecordVersion.model_validate(
                {
                    key: value
                    for key, value in row.items()
                    if key not in ("end_user_id", "data_id")
                }
            )
        except OverflowError:
            raise InvalidHealthData("step version timestamp is out of range") from None
        if not start <= version.modified_at < end:
            raise InvalidHealthData("unexpected step version time")
        versions.append(version)
    return versions


async def list_main_sleeps(
    db_session: AsyncSession, user_id: UUID, start: date, end: date
) -> list[MainSleepRecord]:
    result = await db_session.execute(
        select(
            SleepSession.end_user_id,
            SleepSession.date,
            SleepSession.start_at,
            SleepSession.end_at,
            SleepSession.sleep_timeline,
        )
        .where(
            SleepSession.end_user_id == user_id,
            SleepSession.date >= start,
            SleepSession.date <= end,
            SleepSession.is_main.is_(True),
        )
        .order_by(SleepSession.date, SleepSession.start_at, SleepSession.data_id)
    )
    records = []
    dates = set()
    for row in result.mappings():
        _check_owner(row["end_user_id"], user_id)
        if row["date"] in dates:
            raise InvalidHealthData("multiple main sleeps on one day")
        dates.add(row["date"])
        records.append(
            MainSleepRecord.model_validate(
                {key: value for key, value in row.items() if key != "end_user_id"}
            )
        )
    return records


async def find_lifestyle_score(
    db_session: AsyncSession, user_id: UUID, score_date: date
) -> LifestyleScoreRecord | None:
    """본인·날짜의 저장 점수만 읽는다. 행 없음·미연동 처리는 서비스 책임이다."""
    row = await db_session.scalar(
        select(LifestyleScore).where(
            LifestyleScore.end_user_id == user_id,
            LifestyleScore.date == score_date,
        )
    )
    if row is None:
        return None
    _check_owner(row.end_user_id, user_id)
    if row.date != score_date:
        raise InvalidHealthData("unexpected score date")
    return LifestyleScoreRecord.model_validate(row, from_attributes=True)


async def list_sync_states(db_session: AsyncSession, user_id: UUID) -> list[SyncRecord]:
    result = await db_session.execute(
        select(
            SyncState.end_user_id,
            SyncState.metric,
            SyncState.initial_sync_done,
            SyncState.last_data_at,
            SyncState.first_data_at,
            SyncState.last_success_at,
            SyncState.last_status,
            SyncState.last_error,
        ).where(SyncState.end_user_id == user_id)
    )
    records = []
    for row in result.mappings():
        _check_owner(row["end_user_id"], user_id)
        records.append(SyncRecord(**row))
    return records


async def find_first_arrival(
    db_session: AsyncSession, user_id: UUID
) -> datetime | None:
    """집계 유효성·준비 여부와 무관한 CANDiY 최초 도착 시각."""
    return await db_session.scalar(
        select(func.min(RawHealthRecord.source_created_at)).where(
            RawHealthRecord.end_user_id == user_id,
            RawHealthRecord.metric.in_(
                ("steps", "heart_rates", "oxygen_saturations", "sleeps")
            ),
        )
    )


async def list_exercise_sessions(
    db_session: AsyncSession, user_id: UUID, start: date, end: date
) -> list[ExerciseSessionDetail]:
    """검증된 회원·기간의 집계 세션을 읽는다. 준비 상태·결측 판정은 하지 않는다."""
    result = await db_session.execute(
        select(
            ExerciseSession.end_user_id,
            ExerciseSession.data_id.label("record_id"),
            ExerciseSession.date,
            ExerciseSession.start_at,
            ExerciseSession.end_at,
            ExerciseSession.duration_minutes,
            ExerciseSession.code.label("exercise_type"),
            ExerciseSession.title,
            ExerciseSession.calories_kcal,
            ExerciseSession.distance_m,
            ExerciseSession.hr_avg.label("avg_heart_rate"),
            ExerciseSession.hr_max.label("max_heart_rate"),
            ExerciseSession.counts_as_exercise,
            ExerciseSession.avg_speed_mps,
            ExerciseCode.name_ko.label("code_name"),
            ExerciseCode.category.label("code_category"),
        )
        .outerjoin(ExerciseCode, ExerciseSession.code == ExerciseCode.code)
        .where(
            ExerciseSession.end_user_id == user_id,
            ExerciseSession.date >= start,
            ExerciseSession.date <= end,
        )
        .order_by(ExerciseSession.start_at, ExerciseSession.data_id)
    )
    sessions = []
    for row in result.mappings():
        _check_owner(row["end_user_id"], user_id)
        values = {
            name: row[name]
            for name in ExerciseSessionDetail.model_fields
            if name not in ("type_name", "category")
        }
        sessions.append(
            ExerciseSessionDetail(
                **values,
                type_name=row["code_name"]
                if row["code_name"] is not None
                else "기타 운동",
                category=row["code_category"]
                if row["code_category"] is not None
                else "other",
            )
        )
    return sessions


async def find_exercise_sample_window(
    db_session: AsyncSession, query: ExerciseSamplesQuery
) -> ExerciseSampleWindow | None:
    result = await db_session.execute(
        select(
            ExerciseSession.end_user_id,
            ExerciseSession.data_id.label("record_id"),
            ExerciseSession.start_at,
            ExerciseSession.end_at,
            ExerciseSession.computed_at,
        ).where(
            ExerciseSession.end_user_id == query.user_id,
            ExerciseSession.data_id == query.record_id,
        )
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    _check_owner(row["end_user_id"], query.user_id)
    return ExerciseSampleWindow.model_validate(
        {key: value for key, value in row.items() if key != "end_user_id"}
    )


async def list_exercise_samples(
    db_session: AsyncSession, query: ExerciseSamplesQuery, window: ExerciseSampleWindow
) -> list[ExerciseSample]:
    table = RawHealthRecord
    samples = table.payload["samples"]
    is_array = func.jsonb_typeof(samples) == "array"
    result = await db_session.execute(
        select(
            table.end_user_id,
            table.data_id.label("record_id"),
            table.start_at,
            table.end_at,
            table.app.label("source_app"),
            table.last_modified_at,
            table.ingested_at,
            case((is_array, func.jsonb_array_length(samples))).label("sample_count"),
            case((is_array, func.octet_length(samples.cast(Text)))).label(
                "sample_bytes"
            ),
        )
        .where(
            table.end_user_id == query.user_id,
            table.metric == query.metric,
            table.start_at <= window.end_at,
            # 구간 안에서 시작한 불량 원본도 검증하되 무관한 과거 행은 읽지 않는다.
            or_(
                table.end_at >= window.start_at,
                table.start_at >= window.start_at,
            ),
        )
        .order_by(table.start_at, table.data_id)
        .limit(EXERCISE_RAW_LIMIT + 1)
    )
    rows = list(result.mappings())
    for row in rows:
        _check_owner(row["end_user_id"], query.user_id)
    if (
        len(rows) > EXERCISE_RAW_LIMIT
        or sum(row["sample_count"] or 0 for row in rows) > EXERCISE_SAMPLE_LIMIT
        or sum(row["sample_bytes"] or 0 for row in rows) > EXERCISE_SAMPLE_BYTES_LIMIT
    ):
        raise ExerciseSampleLimitError()
    sources = {}
    for row in rows:
        if row["sample_count"] is None:
            raise InvalidHealthData("invalid sample array")
        source = ExerciseSampleSource.model_validate(
            {
                key: value
                for key, value in row.items()
                if key not in ("end_user_id", "sample_count", "sample_bytes")
            }
        )
        sources[source.record_id] = source
    if not sources:
        return []
    # 같은 읽기 스냅샷에서 예산을 확인한 배열만 가져온다.
    result = await db_session.execute(
        select(
            table.end_user_id, table.data_id, samples.cast(Text).label("sample_array")
        ).where(
            table.end_user_id == query.user_id,
            table.metric == query.metric,
            table.data_id.in_(sources),
        )
    )
    records = []
    value_key = "beatsPerMinute" if query.metric == "heart_rates" else "speed_mps"
    for row in result.mappings():
        _check_owner(row["end_user_id"], query.user_id)
        source = sources[row["data_id"]]
        metadata = source.model_dump(exclude={"start_at", "end_at"})
        try:
            items = json.loads(row["sample_array"])
        except ValueError:
            raise InvalidHealthData("invalid sample JSON") from None
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not isinstance(item.get("time"), str):
                raise InvalidHealthData("invalid sample timestamp")
            try:
                instant = datetime.fromisoformat(item["time"].replace("Z", "+00:00"))
                if instant.tzinfo is None:
                    raise ValueError("sample timestamp requires an offset")
                instant = instant.astimezone(UTC)
            except (ValueError, OverflowError):
                raise InvalidHealthData("invalid sample timestamp") from None
            if query.metric == "heart_rates" and type(item.get(value_key)) is not int:
                raise InvalidHealthData("heart sample must be an integer")
            record = ExerciseSample(
                **metadata,
                sample_index=index,
                measured_at=instant,
                value=item.get(value_key),
            )
            if not source.start_at <= record.measured_at <= source.end_at:
                raise InvalidHealthData("sample outside source interval")
            if window.start_at <= record.measured_at <= window.end_at:
                records.append(record)
    return sorted(
        records,
        key=lambda record: (record.measured_at, record.record_id, record.sample_index),
    )


async def list_exercise_codes(db_session: AsyncSession) -> set[int]:
    return set(await db_session.scalars(select(ExerciseCode.code)))


async def list_exercise_types(db_session: AsyncSession) -> list[ExerciseTypeItem]:
    result = await db_session.execute(
        select(ExerciseCode.code, ExerciseCode.name_ko, ExerciseCode.category).order_by(
            ExerciseCode.code
        )
    )
    return [ExerciseTypeItem.model_validate(row) for row in result.mappings()]


async def list_daily_records(
    db_session: AsyncSession, query: HealthQuery
) -> list[DailyRecord]:
    if query.metric == "exercises":
        return await _list_exercise_daily_records(db_session, query)
    if query.metric == "steps":
        table = ActivityDaily
        columns = (table.end_user_id, table.date, table.steps)
    elif query.metric == "heart_rates":
        table = HeartDaily
        columns = (
            table.end_user_id,
            table.date,
            table.hr_avg,
            table.sample_count,
            table.hourly_hr,
            table.hr_min,
            table.hr_max,
        )
    else:
        table = SleepSession
        columns = (
            table.end_user_id,
            table.date,
            table.is_main,
            table.start_at,
            table.end_at,
            table.spo2_avg,
            table.spo2_min,
            table.spo2_max,
        )
        if query.metric == "sleeps":
            columns += (
                table.sleep_minutes,
                table.deep_minutes,
                table.rem_minutes,
                table.light_minutes,
                table.awake_minutes,
                table.session_minutes,
                table.unspecified_minutes,
                table.unknown_minutes,
                table.hr_avg.label("avg_heart_rate"),
                table.hr_min.label("min_heart_rate"),
                table.hr_max.label("max_heart_rate"),
                table.spo2_count,
                table.spo2_low_count,
                table.spo2_low_times,
            )
    result = await db_session.execute(
        select(*columns)
        .where(
            table.end_user_id == query.user_id,
            table.date >= query.start,
            table.date <= query.end,
        )
        .order_by(table.date)
    )
    records = []
    sleep_dates = set()
    main_dates = set()
    for row in result.mappings():
        _check_owner(row["end_user_id"], query.user_id)
        if query.metric == "steps":
            records.append(DailyRecord(row["date"], row["steps"]))
        elif query.metric == "heart_rates":
            records.append(
                heart_record(
                    row["date"],
                    row["hr_avg"],
                    row["sample_count"],
                    row["hourly_hr"],
                    row["hr_min"],
                    row["hr_max"],
                )
            )
        else:
            sleep_dates.add(row["date"])
            if not row["is_main"]:
                continue
            if row["date"] in main_dates:
                raise InvalidHealthData("multiple main sleeps")
            main_dates.add(row["date"])
            if query.metric == "oxygen_saturations":
                records.append(
                    oxygen_record(
                        row["date"],
                        row["start_at"],
                        row["end_at"],
                        spo2_avg=row["spo2_avg"],
                        spo2_min=row["spo2_min"],
                        spo2_max=row["spo2_max"],
                    )
                )
            else:
                night = Night.model_validate(
                    {name: row[name] for name in Night.model_fields}
                )
                records.append(sleep_record(night))
    if sleep_dates != main_dates:
        raise InvalidHealthData("missing main sleep")
    return records


async def _list_exercise_daily_records(
    db_session: AsyncSession, query: HealthQuery
) -> list[DailyRecord]:
    result = await db_session.execute(
        select(
            ActivityDaily.end_user_id,
            ActivityDaily.date,
            ActivityDaily.exercise_count,
            ActivityDaily.exercise_minutes,
            ActivityDaily.exercise_calories_kcal,
            ActivityDaily.exercise_distance_m,
            ActivityDaily.computed_at,
        )
        .where(
            ActivityDaily.end_user_id == query.user_id,
            ActivityDaily.date >= query.start,
            ActivityDaily.date <= query.end,
        )
        .order_by(ActivityDaily.date)
    )
    daily_rows = list(result.mappings())
    for row in daily_rows:
        _check_owner(row["end_user_id"], query.user_id)
    sessions = await list_exercise_sessions(
        db_session, query.user_id, query.start, query.end
    )
    by_date = {record.date: record for record in exercise_records(sessions)}
    records = []
    for row in daily_rows:
        record = by_date.pop(row["date"], DailyRecord(row["date"], 0))
        counted_count = sum(item.counts_as_exercise for item in record.sessions)
        if (
            row["exercise_minutes"] != record.value
            or row["exercise_count"] != counted_count
        ):
            raise InvalidHealthData("inconsistent exercise aggregate")
        records.append(
            replace(
                record,
                activity={name: row[name] for name in ActivityDailyRecord.model_fields},
            )
        )
    if by_date:
        raise InvalidHealthData("exercise session without daily aggregate")
    return records
