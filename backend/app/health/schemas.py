"""API v5 2.2의 공통 원형. REST 외곽 키 변환은 호출자의 책임이다."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    SerializerFunctionWrapHandler,
    StrictBool,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

from app.users.schemas import AppConnection

KST = ZoneInfo("Asia/Seoul")
LegacyHealthMetric = Literal[
    "steps", "heart_rates", "oxygen_saturations", "sleeps", "exercises"
]
BodyMetric = Literal["weights", "body_fats", "basal_metabolic_rates", "heights"]
HealthMetric = Literal[LegacyHealthMetric, BodyMetric]
HealthReason = Literal["DATA_NOT_READY", "LOOKUP_FAILED"]
MissingReason = Literal[
    "BEFORE_LINK", "UNSYNCED", "NO_RECORD", "NULL_VALUE", "ZERO_STEPS"
]
Number = int | Annotated[Decimal, PlainSerializer(float, when_used="json")]
UtcDatetime = Annotated[
    AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))
]


class HealthSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class HealthQuery(HealthSchema):
    user_id: UUID
    metric: HealthMetric
    start: date
    end: date

    @model_validator(mode="after")
    def validate_period(self, info: ValidationInfo) -> "HealthQuery":
        today = (info.context or {}).get("today") or datetime.now(KST).date()
        if not self.start <= self.end <= today:
            raise ValueError("start <= end <= KST today is required")
        if (self.end - self.start).days >= 366:
            raise ValueError("inclusive period must be at most 366 days")
        return self


class Period(HealthSchema):
    start: date
    end: date


ComparisonMetric = Literal["steps", "sleeps"]


class ExerciseRecordComparisonQuery(HealthQuery):
    metric: ComparisonMetric
    exercise_type: int | None = Field(default=None, strict=True)


class TimelineSpan(HealthSchema):
    start_at: UtcDatetime
    end_at: UtcDatetime

    @model_validator(mode="after")
    def validate_span(self) -> "TimelineSpan":
        if self.start_at >= self.end_at:
            raise ValueError("start must precede end")
        return self


class SleepSegment(TimelineSpan):
    stage: Literal["awake", "light", "deep", "rem", "unspecified", "unknown"]


class ExerciseSegment(TimelineSpan):
    exercise_type: int = Field(strict=True)
    type_name: str
    title: str | None


class HourlyHeartRate(HealthSchema):
    avg: float = Field(strict=True)
    min: float = Field(strict=True)
    max: float = Field(strict=True)
    sample_count: int | None = Field(strict=True, ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "HourlyHeartRate":
        # 극값은 정수, 평균은 소수 첫째 자리로 따로 반올림된 저장값이다.
        if self.min > self.max or not self.min - 0.5 <= self.avg <= self.max + 0.5:
            raise ValueError("invalid hourly heart range")
        return self


HourlySteps = Annotated[
    list[Annotated[int, Field(strict=True, ge=0)]], Field(min_length=24, max_length=24)
]
HourlyHeartRates = Annotated[
    list[HourlyHeartRate | None], Field(min_length=24, max_length=24)
]


class TimelineDay(HealthSchema):
    date: date
    is_in_progress: StrictBool
    hourly_steps: HourlySteps | None
    hourly_heart_rates: HourlyHeartRates | None
    main_sleep: TimelineSpan | None
    sleep_segments: list[SleepSegment]
    exercise_segments: list[ExerciseSegment]


class HealthTimelineResult(HealthSchema):
    user_id: UUID
    period: Period
    days: list[TimelineDay]


class HourlyStepsRecord(HealthSchema):
    date: date
    hourly_steps: HourlySteps | None


class HourlyHeartRecord(HealthSchema):
    date: date
    hourly_hr: dict[str, dict[str, object]] | None


class MainSleepRecord(TimelineSpan):
    date: date
    sleep_timeline: list[dict[str, object]] | None

    @model_validator(mode="after")
    def validate_wake_date(self) -> "MainSleepRecord":
        if self.end_at.astimezone(KST).date() != self.date:
            raise ValueError("sleep date must be the KST wake date")
        return self


class DatedValue(HealthSchema):
    date: date
    value: Number


class Summary(HealthSchema):
    avg: Number | None
    daily_min: DatedValue | None
    daily_max: DatedValue | None
    total: Number | None
    days_with_data: int = Field(ge=0)
    days_used_for_summary: int = Field(ge=0)
    completed_days: int = Field(ge=0)
    missing_days: list[date]
    unsynced_days: list[date]
    linked_from: date | None


class Day(HealthSchema):
    date: date
    value: Number | None
    missing: bool
    reason: MissingReason | None
    is_in_progress: bool
    wear_hours: int | None = Field(default=None, strict=True, ge=0, le=24)
    is_well_worn: StrictBool | None = None


class InProgress(HealthSchema):
    date: date
    value: Number | None
    missing: bool
    as_of: UtcDatetime | None


class DataStatus(HealthSchema):
    last_data_at: UtcDatetime | None
    synced_until: UtcDatetime | None
    last_sync_status: Literal["success", "fail"] | None
    last_success_at: UtcDatetime | None
    checked_at: UtcDatetime


class MetricReadiness(HealthSchema):
    metric: HealthMetric
    ready: StrictBool | None
    last_sync_status: Literal["success", "fail"] | None
    last_data_at: UtcDatetime | None
    last_success_at: UtcDatetime | None


class HealthDataStatusResult(HealthSchema):
    user_id: UUID
    app_connection: AppConnection
    first_arrived_at: UtcDatetime | None
    metrics: list[MetricReadiness]
    checked_at: UtcDatetime


class Night(HealthSchema):
    date: date
    start_at: UtcDatetime
    end_at: UtcDatetime
    sleep_minutes: int | None
    deep_minutes: int | None
    rem_minutes: int | None
    light_minutes: int | None
    awake_minutes: int | None
    session_minutes: int = Field(strict=True, ge=0)
    unspecified_minutes: int | None = Field(strict=True, ge=0)
    unknown_minutes: int | None = Field(strict=True, ge=0)
    avg_heart_rate: Number | None = Field(gt=0)
    min_heart_rate: Number | None = Field(gt=0)
    max_heart_rate: Number | None = Field(gt=0)
    spo2_avg: Number | None = Field(gt=0, le=100)
    spo2_min: Number | None = Field(gt=0, le=100)
    spo2_max: Number | None = Field(gt=0, le=100)
    spo2_count: int | None = Field(strict=True, ge=1)
    spo2_low_count: int | None = Field(strict=True, ge=0)
    # 이전 합성 입력은 키가 없으며, DB의 null은 재집계 전일 수도 있다.
    spo2_low_times: list[UtcDatetime] | None = None

    @field_serializer("spo2_low_times", when_used="json")
    def serialize_spo2_low_times(
        self, values: list[datetime] | None
    ) -> list[str] | None:
        if values is None:
            return None
        return [
            value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            for value in values
        ]

    @field_validator(
        "avg_heart_rate",
        "min_heart_rate",
        "max_heart_rate",
        "spo2_avg",
        "spo2_min",
        "spo2_max",
        mode="before",
    )
    @classmethod
    def validate_measurement_number(cls, value: object) -> object:
        if value is not None and type(value) not in (int, float, Decimal):
            raise ValueError("sleep measurement must be a number")
        return value

    @model_validator(mode="after")
    def validate_measurements(self) -> "Night":
        # 부분 결측은 보존하되, 존재하는 값끼리의 모순은 결측으로 숨기지 않는다.
        for low, average, high, tolerance in (
            # 심박 극값은 정수, 평균은 소수 첫째 자리로 따로 반올림되어 저장된다.
            (
                self.min_heart_rate,
                self.avg_heart_rate,
                self.max_heart_rate,
                Decimal("0.5"),
            ),
            (self.spo2_min, self.spo2_avg, self.spo2_max, 0),
        ):
            if (low is not None and high is not None and low > high) or (
                average is not None
                and (
                    (low is not None and average < low - tolerance)
                    or (high is not None and average > high + tolerance)
                )
            ):
                raise ValueError("invalid sleep measurement range")
        if (
            self.spo2_count is not None
            and self.spo2_low_count is not None
            and self.spo2_low_count > self.spo2_count
        ):
            raise ValueError("invalid low oxygen sample count")
        if self.spo2_low_times is not None:
            if (
                self.spo2_low_count is None
                or len(self.spo2_low_times) != self.spo2_low_count
            ):
                raise ValueError("low oxygen times must match count")
            if self.spo2_low_times != sorted(self.spo2_low_times):
                raise ValueError("low oxygen times must be ordered")
        return self


SLEEP_SESSION_LIMIT = 1000


class SleepSessionsQuery(HealthQuery):
    metric: Literal["sleeps"] = "sleeps"

    @model_validator(mode="after")
    def validate_session_period(self) -> "SleepSessionsQuery":
        if (self.end - self.start).days >= 31:
            raise ValueError("inclusive session period must be at most 31 days")
        return self


class SleepSessionDetail(Night):
    spo2_low_times: list[UtcDatetime] | None
    session_id: str = Field(min_length=1, max_length=64)
    is_main: StrictBool
    computed_at: UtcDatetime
    # 기존 Night의 수용 범위를 바꾸지 않고 새 조회의 저장 계약만 강화한다.
    sleep_minutes: int | None = Field(strict=True, ge=0)
    awake_minutes: int | None = Field(strict=True, ge=0)
    light_minutes: int | None = Field(strict=True, ge=0)
    deep_minutes: int | None = Field(strict=True, ge=0)
    rem_minutes: int | None = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def validate_session(self) -> "SleepSessionDetail":
        if (
            self.start_at >= self.end_at
            or self.end_at.astimezone(KST).date() != self.date
        ):
            raise ValueError("invalid sleep session dates")
        if self.sleep_minutes is not None and self.sleep_minutes > self.session_minutes:
            raise ValueError("sleep minutes exceed session minutes")
        return self


class SleepDataStatus(HealthSchema):
    last_sync_status: Literal["success", "fail"] | None
    last_data_at: UtcDatetime | None
    last_success_at: UtcDatetime | None
    checked_at: UtcDatetime


class SleepSessionsResult(HealthSchema):
    user_id: UUID
    period: Period
    sessions: list[SleepSessionDetail]
    data_status: SleepDataStatus


class DailyRange(HealthSchema):
    date: date
    min: Number | None
    max: Number | None
    sample_count: int | None = Field(strict=True, ge=0)


class HourlyStepsQuery(HealthQuery):
    metric: Literal["steps"] = "steps"
    start_hour: int = Field(strict=True, ge=0, le=23)
    end_hour: int = Field(strict=True, ge=1, le=24)

    @model_validator(mode="after")
    def validate_hours(self) -> "HourlyStepsQuery":
        if self.start_hour >= self.end_hour:
            raise ValueError("start_hour must precede end_hour")
        return self


class HourlyStepsDay(HealthSchema):
    date: date
    is_in_progress: StrictBool
    values: list[Annotated[int, Field(strict=True, ge=0)]] | None
    total: int | None = Field(strict=True, ge=0)
    reason: Literal["NO_RECORD", "NULL_VALUE"] | None


class HourlyStepsResult(HealthSchema):
    user_id: UUID
    period: Period
    start_hour: int
    end_hour: int
    days: list[HourlyStepsDay]
    total: int | None = Field(strict=True, ge=0)
    data_status: DataStatus


ActivitySourceMetric = Literal["exercise_sessions", "calories_burned", "distances"]


class ActivityDailyRecord(HealthSchema):
    date: date
    exercise_minutes: int = Field(strict=True, ge=0)
    exercise_count: int = Field(strict=True, ge=0)
    exercise_calories_kcal: Number | None = Field(ge=0)
    exercise_distance_m: Number | None = Field(ge=0)
    computed_at: UtcDatetime

    @field_validator("exercise_calories_kcal", "exercise_distance_m", mode="before")
    @classmethod
    def validate_measurement_number(cls, value: object) -> object:
        if value is not None and type(value) not in (int, float, Decimal):
            raise ValueError("activity measurement must be a number")
        return value


class ActivityDay(ActivityDailyRecord):
    exercise_minutes: int | None = Field(strict=True, ge=0)
    exercise_count: int | None = Field(strict=True, ge=0)
    computed_at: UtcDatetime | None
    is_in_progress: StrictBool
    # 행 유무만 나타낸다. 칼로리·거리의 결측이나 준비 상태와는 별개다.
    reason: Literal["NO_RECORD"] | None


class ActivitySourceStatus(HealthSchema):
    ready: StrictBool | None
    last_sync_status: Literal["success", "fail"] | None
    last_data_at: UtcDatetime | None
    last_success_at: UtcDatetime | None


class ActivityDailyResult(HealthSchema):
    user_id: UUID
    period: Period
    days: list[ActivityDay]
    source_statuses: dict[ActivitySourceMetric, ActivitySourceStatus]
    checked_at: UtcDatetime


BodyUnit = Literal["kg", "%", "kcal/day", "m"]
BODY_UNITS: dict[BodyMetric, BodyUnit] = {
    "weights": "kg",
    "body_fats": "%",
    "basal_metabolic_rates": "kcal/day",
    "heights": "m",
}


class BodyDailyQuery(HealthQuery):
    metric: BodyMetric


class BodyDailyRecord(HealthSchema):
    date: date
    value: Number = Field(gt=0)
    measured_at: UtcDatetime
    computed_at: UtcDatetime

    @field_validator("value", mode="before")
    @classmethod
    def validate_measurement_number(cls, value: object) -> object:
        if value is not None and type(value) not in (int, float, Decimal):
            raise ValueError("body measurement must be a number")
        return value

    @model_validator(mode="after")
    def validate_measurement_date(self) -> "BodyDailyRecord":
        if (
            self.measured_at is not None
            and self.measured_at.astimezone(KST).date() != self.date
        ):
            raise ValueError("body date must be the KST measurement date")
        return self


class BodyDay(BodyDailyRecord):
    value: Number | None = Field(gt=0)
    measured_at: UtcDatetime | None
    computed_at: UtcDatetime | None
    is_in_progress: StrictBool
    reason: Literal["NO_RECORD"] | None
    wear_hours: int | None = Field(default=None, strict=True, ge=0, le=24)
    is_well_worn: StrictBool | None = None


class BodyDataStatus(HealthSchema):
    last_sync_status: Literal["success", "fail"] | None
    last_data_at: UtcDatetime | None
    last_success_at: UtcDatetime | None
    checked_at: UtcDatetime


class BodyDailyResult(HealthSchema):
    user_id: UUID
    metric: BodyMetric
    unit: BodyUnit
    period: Period
    days: list[BodyDay]
    data_status: BodyDataStatus


VO2_MAX_RECORD_LIMIT = 1000


class Vo2MaxQuery(HealthQuery):
    metric: Literal["vo2_maxes"] = "vo2_maxes"

    @model_validator(mode="after")
    def validate_record_period(self) -> "Vo2MaxQuery":
        if (self.end - self.start).days >= 31:
            raise ValueError("inclusive VO2 max period must be at most 31 days")
        return self


class Vo2MaxRecord(HealthSchema):
    record_id: str = Field(min_length=1, max_length=64)
    date: date
    measured_at: UtcDatetime
    value: Number = Field(ge=0, le=100)
    measurement_method: int = Field(strict=True, ge=0, le=5)
    source_app: str = Field(min_length=1, max_length=128)
    last_modified_at: UtcDatetime
    ingested_at: UtcDatetime

    @field_validator("value", mode="before")
    @classmethod
    def validate_number(cls, value: object) -> object:
        if type(value) not in (int, float, Decimal):
            raise ValueError("VO2 max value must be a number")
        return value

    @model_validator(mode="after")
    def validate_measurement_date(self) -> "Vo2MaxRecord":
        if self.measured_at.astimezone(KST).date() != self.date:
            raise ValueError("invalid VO2 max measurement date")
        return self


class Vo2MaxDataStatus(HealthSchema):
    last_sync_status: Literal["success", "fail"] | None
    last_data_at: UtcDatetime | None
    last_success_at: UtcDatetime | None
    checked_at: UtcDatetime


class Vo2MaxResult(HealthSchema):
    user_id: UUID
    period: Period
    unit: Literal["mL/kg/min"]
    records: list[Vo2MaxRecord]
    data_status: Vo2MaxDataStatus


MeasurementMetric = Literal[
    "oxygen_saturations", "weights", "body_fats", "basal_metabolic_rates", "heights"
]
MEASUREMENT_FIELDS: dict[MeasurementMetric, tuple[str, BodyUnit, int]] = {
    "oxygen_saturations": ("percentage", "%", 100),
    "weights": ("weight_kg", "kg", 1000),
    "body_fats": ("percentage", "%", 100),
    "basal_metabolic_rates": ("bmr_kcal_per_day", "kcal/day", 10000),
    "heights": ("height_m", "m", 3),
}
MEASUREMENT_RECORD_LIMIT = 1000


class MeasurementRecordsQuery(HealthQuery):
    metric: MeasurementMetric

    @model_validator(mode="after")
    def validate_record_period(self) -> "MeasurementRecordsQuery":
        if (self.end - self.start).days >= 31:
            raise ValueError("inclusive measurement period must be at most 31 days")
        try:
            datetime.combine(self.start, time.min, tzinfo=KST).astimezone(UTC)
            datetime.combine(
                self.end + timedelta(days=1), time.min, tzinfo=KST
            ).astimezone(UTC)
        except OverflowError:
            raise ValueError(
                "measurement period is outside supported UTC dates"
            ) from None
        return self

    @property
    def error_metrics(self) -> tuple[HealthMetric, ...]:
        return ("oxygen_saturations",) if self.metric == "oxygen_saturations" else ()


class MeasurementRecord(HealthSchema):
    record_id: str = Field(min_length=1, max_length=64)
    date: date
    measured_at: UtcDatetime
    value: Number = Field(ge=0)
    source_app: str = Field(min_length=1, max_length=128)
    last_modified_at: UtcDatetime
    ingested_at: UtcDatetime

    @field_validator("value", mode="before")
    @classmethod
    def validate_number(cls, value: object) -> object:
        if type(value) not in (int, float, Decimal):
            raise ValueError("measurement must be a number")
        return value

    @model_validator(mode="after")
    def validate_measurement_date(self) -> "MeasurementRecord":
        if self.measured_at.astimezone(KST).date() != self.date:
            raise ValueError("invalid KST measurement date")
        return self


class MeasurementDataStatus(HealthSchema):
    last_sync_status: Literal["success", "fail"] | None
    last_data_at: UtcDatetime | None
    last_success_at: UtcDatetime | None
    checked_at: UtcDatetime


class MeasurementRecordsResult(HealthSchema):
    user_id: UUID
    metric: MeasurementMetric
    unit: BodyUnit
    period: Period
    records: list[MeasurementRecord]
    data_status: MeasurementDataStatus

    @model_validator(mode="after")
    def validate_metric_values(self) -> "MeasurementRecordsResult":
        _, unit, maximum = MEASUREMENT_FIELDS[self.metric]
        if self.unit != unit or any(record.value > maximum for record in self.records):
            raise ValueError("unexpected measurement unit or value range")
        return self


STEP_RECORD_LIMIT = 1000
STEP_HISTORY_LIMIT = 1000
STEP_RECORD_COUNT_MAX = 1_000_000
StepRecordCount = Annotated[int, Field(strict=True, ge=0, le=STEP_RECORD_COUNT_MAX)]


class StepRecordsQuery(HealthQuery):
    metric: Literal["steps"] = "steps"

    @model_validator(mode="after")
    def validate_record_period(self) -> "StepRecordsQuery":
        if (self.end - self.start).days >= 31:
            raise ValueError("inclusive step record period must be at most 31 days")
        try:
            datetime.combine(self.start, time.min, tzinfo=KST).astimezone(UTC)
            datetime.combine(
                self.end + timedelta(days=1), time.min, tzinfo=KST
            ).astimezone(UTC)
        except OverflowError:
            raise ValueError(
                "step record period is outside supported UTC dates"
            ) from None
        return self


class StepRecordHistoryQuery(StepRecordsQuery):
    record_id: str = Field(min_length=1, max_length=64)


class StepRawRecord(HealthSchema):
    record_id: str = Field(min_length=1, max_length=64)
    date: date
    start_at: UtcDatetime
    end_at: UtcDatetime | None
    step_count: StepRecordCount
    source_app: str = Field(min_length=1, max_length=128)
    last_modified_at: UtcDatetime
    ingested_at: UtcDatetime

    @model_validator(mode="after")
    def validate_record_time(self) -> "StepRawRecord":
        if self.end_at is not None and self.end_at <= self.start_at:
            raise ValueError("step record start must precede end")
        if self.start_at.astimezone(KST).date() != self.date:
            raise ValueError("step record date must match KST start")
        return self


class StepRecordVersion(HealthSchema):
    modified_at: UtcDatetime
    step_count: StepRecordCount
    ingested_at: UtcDatetime


class StepRecordDataStatus(HealthSchema):
    last_sync_status: Literal["success", "fail"] | None
    last_data_at: UtcDatetime | None
    last_success_at: UtcDatetime | None
    checked_at: UtcDatetime


class StepRecordsResult(HealthSchema):
    user_id: UUID
    metric: Literal["steps"]
    unit: Literal["steps"]
    period: Period
    records: list[StepRawRecord]
    data_status: StepRecordDataStatus


class StepRecordHistoryResult(HealthSchema):
    user_id: UUID
    metric: Literal["steps"]
    unit: Literal["steps"]
    period: Period
    record: StepRawRecord
    versions: list[StepRecordVersion]
    data_status: StepRecordDataStatus


class ExerciseTypeItem(HealthSchema):
    code: int = Field(strict=True)
    name_ko: str
    category: Literal["cardio", "strength", "flexibility", "other"]


class ExerciseSessionDetail(HealthSchema):
    record_id: str = Field(min_length=1, max_length=64)
    date: date
    start_at: UtcDatetime
    end_at: UtcDatetime
    duration_minutes: int = Field(strict=True, ge=1)
    exercise_type: int = Field(strict=True)
    type_name: str
    title: str | None
    category: Literal["cardio", "strength", "flexibility", "other"]
    calories_kcal: Number | None
    distance_m: Number | None
    avg_heart_rate: Number | None
    max_heart_rate: Number | None
    counts_as_exercise: bool = Field(strict=True)
    avg_speed_mps: Number | None

    @model_validator(mode="after")
    def validate_dates(self) -> "ExerciseSessionDetail":
        if (
            self.start_at.astimezone(KST).date() != self.date
            or self.start_at >= self.end_at
        ):
            raise ValueError("invalid exercise dates")
        return self


ExerciseActivityReason = Literal["BEFORE_LINK", "UNSYNCED", "NO_RECORD", "NULL_VALUE"]


class ExerciseActivityDay(ActivityDay):
    # 시간·횟수의 공통 날짜 판정. 칼로리·거리 결측은 별도로 보존한다.
    reason: ExerciseActivityReason | None
    calories_reason: ExerciseActivityReason | None
    distance_reason: ExerciseActivityReason | None


class ExerciseActivityStatistic(HealthSchema):
    total: Number | None = Field(ge=0)
    avg: Number | None = Field(ge=0)
    days_with_data: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def validate_availability(self) -> "ExerciseActivityStatistic":
        if (self.total is None) != (self.days_with_data == 0) or (self.avg is None) != (
            self.days_with_data == 0
        ):
            raise ValueError("activity statistics must match recorded days")
        return self


class ExerciseActivitySummary(HealthSchema):
    exercise_minutes: ExerciseActivityStatistic
    exercise_count: ExerciseActivityStatistic
    exercise_calories_kcal: ExerciseActivityStatistic
    exercise_distance_m: ExerciseActivityStatistic


class ExerciseActivityDetail(HealthSchema):
    """시간·횟수는 인정 운동만, 칼로리·거리는 미인정 포함 저장값(부분 합계 가능)."""

    days: list[ExerciseActivityDay]
    summary: ExerciseActivitySummary
    source_statuses: dict[ActivitySourceMetric, ActivitySourceStatus]

    @model_validator(mode="after")
    def validate_sources(self) -> "ExerciseActivityDetail":
        if set(self.source_statuses) != {
            "exercise_sessions",
            "calories_burned",
            "distances",
        }:
            raise ValueError("activity source statuses must contain all three sources")
        return self


class Detail(HealthSchema):
    measured_min: DatedValue | None
    measured_max: DatedValue | None
    nights: list[Night]
    daily_ranges: list[DailyRange]
    sessions: list[ExerciseSessionDetail]
    exercise_activity: ExerciseActivityDetail | None = Field(
        default=None,
        description="시간·횟수는 인정 운동만, 칼로리·거리는 미인정 포함 저장값이며 부분 합계일 수 있다.",
    )

    @model_serializer(mode="wrap")
    def omit_unused_activity(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.exercise_activity is None:
            data.pop("exercise_activity", None)
            data.pop("exerciseActivity", None)
        return data


EXERCISE_RAW_LIMIT = 1000
EXERCISE_SAMPLE_LIMIT = 20_000
EXERCISE_SAMPLE_BYTES_LIMIT = 8 * 1024 * 1024
EXERCISE_SAMPLE_HOURS_LIMIT = 24
ExerciseSampleMetric = Literal["heart_rates", "speeds"]


class ExerciseSamplesQuery(HealthSchema):
    user_id: UUID
    record_id: str = Field(min_length=1, max_length=64)
    metric: ExerciseSampleMetric


class ExerciseSampleWindow(TimelineSpan):
    record_id: str = Field(min_length=1, max_length=64)
    computed_at: UtcDatetime


class ExerciseSampleSource(TimelineSpan):
    record_id: str = Field(min_length=1, max_length=64)
    source_app: str = Field(min_length=1, max_length=128)
    last_modified_at: UtcDatetime
    ingested_at: UtcDatetime


class ExerciseSample(HealthSchema):
    record_id: str = Field(min_length=1, max_length=64)
    sample_index: int = Field(strict=True, ge=0)
    measured_at: UtcDatetime
    value: Number = Field(ge=0)
    source_app: str = Field(min_length=1, max_length=128)
    last_modified_at: UtcDatetime
    ingested_at: UtcDatetime

    @field_validator("value", mode="before")
    @classmethod
    def validate_number(cls, value: object) -> object:
        if type(value) not in (int, float, Decimal):
            raise ValueError("sample value must be a number")
        return value


class ExerciseSampleStatus(HealthSchema):
    last_sync_status: Literal["success", "fail"] | None
    last_data_at: UtcDatetime | None
    last_success_at: UtcDatetime


class ExerciseSamplesResult(HealthSchema):
    user_id: UUID
    metric: ExerciseSampleMetric
    unit: Literal["bpm", "m/s"]
    exercise: ExerciseSampleWindow
    samples: list[ExerciseSample]
    exercise_status: ExerciseSampleStatus
    source_status: ExerciseSampleStatus
    checked_at: UtcDatetime


class PublicHealthMetricResult(HealthSchema):
    metric: HealthMetric
    period: Period
    unit: Literal["steps", "bpm", "%", "min", "kg", "kcal/day", "m"]
    summary: Summary
    days: list[Day]
    in_progress: InProgress | None
    data_status: DataStatus
    detail: Detail

    @model_validator(mode="after")
    def validate_exercise_activity(self) -> "PublicHealthMetricResult":
        activity = self.detail.exercise_activity
        if activity is not None:
            if self.metric != "exercises":
                raise ValueError("exercise activity is only valid for exercises")
            dates = [day.date for day in activity.days]
            expected = [
                self.period.start + timedelta(days=i)
                for i in range((self.period.end - self.period.start).days + 1)
            ]
            if dates != expected or dates != [day.date for day in self.days]:
                raise ValueError("exercise activity dates must match the period")
        return self


class HealthMetricResult(PublicHealthMetricResult):
    user_id: UUID

    def to_public(self) -> PublicHealthMetricResult:
        """공개 계약에 정의된 필드만 선택한다."""
        return PublicHealthMetricResult.model_validate(
            {
                name: getattr(self, name)
                for name in PublicHealthMetricResult.model_fields
            }
        )


class ExerciseComparisonDay(HealthSchema):
    exercise_date: date
    metric_date: date
    value: Number


class ExerciseComparisonGroup(HealthSchema):
    days: list[ExerciseComparisonDay]
    days_with_data: int = Field(strict=True, ge=0)
    avg: Number | None


class ExerciseComparisonExclusion(HealthSchema):
    exercise_date: date
    metric_date: date | None
    source: Literal["exercises", "steps", "sleeps"]
    reason: MissingReason | Literal["IN_PROGRESS", "OTHER_EXERCISE"]


class PublicExerciseRecordComparison(HealthSchema):
    metric: ComparisonMetric
    period: Period
    metric_period: Period | None
    exercise_type: int | None
    unit: Literal["steps", "min"]
    with_exercise: ExerciseComparisonGroup
    without_exercise: ExerciseComparisonGroup
    difference: Number | None
    comparable: StrictBool
    reliable: StrictBool
    reliable_min_days: int = Field(strict=True, ge=1)
    excluded_days: list[ExerciseComparisonExclusion]
    linked_from: date | None
    exercise_data_status: DataStatus
    metric_data_status: DataStatus


class ExerciseRecordComparison(PublicExerciseRecordComparison):
    user_id: UUID

    def to_public(self) -> PublicExerciseRecordComparison:
        return PublicExerciseRecordComparison.model_validate(
            {
                name: getattr(self, name)
                for name in PublicExerciseRecordComparison.model_fields
            }
        )


ScoreValue = Annotated[int, Field(strict=True, ge=0, le=100)]
StepsScoreValue = Annotated[int, Field(strict=True, ge=0, le=60)]
ExerciseScoreValue = Annotated[int, Field(strict=True, ge=0, le=40)]
StoredGrade = Literal["excellent", "stable", "attention", "care"]
ScoreMissingReason = Literal["BEFORE_LINK", "UNSYNCED", "NO_RECORD"]


class MissingLifestyleScore(HealthSchema):
    """최초 집계 완료 후 점수 행이 없는 날짜. 저장 행의 계약과 구분한다."""

    end_user_id: UUID
    date: date
    reason: ScoreMissingReason


class LifestyleScoreRecord(HealthSchema):
    """저장 점수 한 행. 점수·등급을 재계산하지 않고 저장 계약만 검증한다."""

    end_user_id: UUID
    date: date
    total_score: ScoreValue | None
    activity_score: ScoreValue | None
    activity_grade: StoredGrade | None
    steps_score: StepsScoreValue | None
    exercise_score: ExerciseScoreValue | None
    steps: Annotated[int, Field(strict=True, ge=0)] | None
    exercise_minutes: Annotated[int, Field(strict=True, ge=0)] | None
    sleep_score: ScoreValue | None
    sleep_grade: StoredGrade | None
    sleep_minutes: Annotated[int, Field(strict=True, ge=0)] | None
    heart_score: ScoreValue | None
    heart_grade: StoredGrade | None
    sleep_hr_avg: Decimal | None
    hr_baseline: Decimal | None
    hr_baseline_nights: Annotated[int, Field(strict=True, ge=0)]
    check_wear: StrictBool
    computed_at: UtcDatetime

    @model_validator(mode="after")
    def validate_missing_scores(self) -> "LifestyleScoreRecord":
        for area in ("activity", "sleep", "heart"):
            if (getattr(self, f"{area}_score") is None) != (
                getattr(self, f"{area}_grade") is None
            ):
                raise ValueError("area score and grade must be missing together")
        missing_activity = self.activity_score is None
        if any(
            (value is None) != missing_activity
            for value in (self.steps_score, self.exercise_score, self.exercise_minutes)
        ):
            raise ValueError("activity details must match activity availability")
        if (self.total_score is None) != all(
            value is None
            for value in (self.activity_score, self.sleep_score, self.heart_score)
        ):
            raise ValueError("total availability must match area availability")
        return self
