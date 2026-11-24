"""건강 REST 입출력. 공통 조회·AI snapshot의 snake_case 계약은 보존한다."""

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    BeforeValidator,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.core.schemas import ApiRequest, ApiResponse, ErrorDetail
from app.core.validation import invalid_request
from app.health.daily_message import (
    DailyMessageArea,
    DailyMessageDataAsOf,
    DailyMessageNotice,
    DailyMessageResult,
    DailyMessageStatus,
)
from app.health.schemas import (
    KST,
    ActivitySourceMetric,
    ActivitySourceStatus,
    DailyRange,
    DataStatus,
    Day,
    Detail,
    ExerciseActivityDay,
    ExerciseActivityDetail,
    ExerciseActivityStatistic,
    ExerciseActivitySummary,
    ExerciseScoreValue,
    ExerciseSegment,
    ExerciseSessionDetail,
    HealthDataStatusResult,
    HealthTimelineResult,
    HourlyHeartRate,
    InProgress,
    LifestyleScoreRecord,
    MetricReadiness,
    MissingLifestyleScore,
    Night,
    Number,
    Period,
    PublicHealthMetricResult,
    ScoreMissingReason,
    ScoreValue,
    SleepSegment,
    StepsScoreValue,
    StoredGrade,
    Summary,
    TimelineDay,
    TimelineSpan,
    UtcDatetime,
)
from app.users.schemas import AppConnection


def _query_date(value: object) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str):
        reason = "null_not_allowed" if value is None else "invalid_type"
        raise PydanticCustomError(reason, "Date string required")
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise PydanticCustomError("invalid_format", "YYYY-MM-DD required")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise PydanticCustomError("invalid_format", "Calendar date required") from None


QueryDate = Annotated[date, BeforeValidator(_query_date)]


class DailyMessageDataAsOfResponse(DailyMessageDataAsOf):
    model_config = ApiResponse.model_config


class DailyMessageNoticeResponse(DailyMessageNotice):
    model_config = ApiResponse.model_config


class DailyMessageResponse(ApiResponse):
    date: date
    status: DailyMessageStatus
    code: str
    area: DailyMessageArea | None
    title: str
    description: str
    data_as_of: DailyMessageDataAsOfResponse
    score_computed_at: UtcDatetime | None
    notices: list[DailyMessageNoticeResponse]


def daily_message_response(result: DailyMessageResult) -> DailyMessageResponse:
    return DailyMessageResponse.model_validate(
        result.model_dump(exclude={"user_id", "used_metrics"})
    )


class TimelinePeriodRequest(ApiRequest):
    start: QueryDate
    end: QueryDate

    def validate_period(self, *, today: date) -> None:
        details = []
        if self.start > self.end:
            details.append(ErrorDetail(field="start", reason="out_of_range"))
        if self.end > today or (self.end - self.start).days >= 31:
            details.append(ErrorDetail(field="end", reason="out_of_range"))
        if details:
            raise invalid_request(details)


class TimelineSpanResponse(TimelineSpan):
    model_config = ApiResponse.model_config


class SleepSegmentResponse(SleepSegment):
    model_config = ApiResponse.model_config


class ExerciseSegmentResponse(ExerciseSegment):
    model_config = ApiResponse.model_config


class HourlyHeartRateResponse(HourlyHeartRate):
    model_config = ApiResponse.model_config


class TimelineDayResponse(TimelineDay):
    model_config = ApiResponse.model_config
    main_sleep: TimelineSpanResponse | None
    sleep_segments: list[SleepSegmentResponse]
    exercise_segments: list[ExerciseSegmentResponse]
    hourly_heart_rates: (
        Annotated[
            list[HourlyHeartRateResponse | None], Field(min_length=24, max_length=24)
        ]
        | None
    )


class HealthTimelineResponse(ApiResponse):
    period: Period
    days: list[TimelineDayResponse]


def timeline_response(result: HealthTimelineResult) -> HealthTimelineResponse:
    return HealthTimelineResponse.model_validate(result.model_dump(exclude={"user_id"}))


class MetricPeriodRequest(ApiRequest):
    start: QueryDate
    end: QueryDate

    @model_validator(mode="after")
    def validate_period(self, info: ValidationInfo) -> "MetricPeriodRequest":
        today = (info.context or {}).get("today") or datetime.now(KST).date()
        details = []
        if self.start > self.end:
            details.append(ErrorDetail(field="start", reason="out_of_range"))
        if self.end > today or (self.end - self.start).days >= 366:
            details.append(ErrorDetail(field="end", reason="out_of_range"))
        if details:
            raise invalid_request(details)
        return self


class SummaryResponse(Summary):
    model_config = ApiResponse.model_config


class DayResponse(Day):
    model_config = ApiResponse.model_config


class InProgressResponse(InProgress):
    model_config = ApiResponse.model_config


class DataStatusResponse(DataStatus):
    model_config = ApiResponse.model_config


class NightResponse(Night):
    model_config = ApiResponse.model_config


class ExerciseSessionResponse(ExerciseSessionDetail):
    model_config = ApiResponse.model_config


class DailyRangeResponse(DailyRange):
    model_config = ApiResponse.model_config


class ExerciseActivityDayResponse(ExerciseActivityDay):
    model_config = ApiResponse.model_config


class ExerciseActivityStatisticResponse(ExerciseActivityStatistic):
    model_config = ApiResponse.model_config


class ExerciseActivitySummaryResponse(ExerciseActivitySummary):
    model_config = ApiResponse.model_config
    exercise_minutes: ExerciseActivityStatisticResponse
    exercise_count: ExerciseActivityStatisticResponse
    exercise_calories_kcal: ExerciseActivityStatisticResponse
    exercise_distance_m: ExerciseActivityStatisticResponse


class ActivitySourceStatusResponse(ActivitySourceStatus):
    model_config = ApiResponse.model_config


class ExerciseActivityDetailResponse(ExerciseActivityDetail):
    model_config = ApiResponse.model_config
    days: list[ExerciseActivityDayResponse]
    summary: ExerciseActivitySummaryResponse
    source_statuses: dict[ActivitySourceMetric, ActivitySourceStatusResponse]


class DetailResponse(Detail):
    model_config = ApiResponse.model_config
    nights: list[NightResponse]
    sessions: list[ExerciseSessionResponse]
    daily_ranges: list[DailyRangeResponse]
    exercise_activity: ExerciseActivityDetailResponse | None = None


class MetricResponse(PublicHealthMetricResult):
    model_config = ApiResponse.model_config
    summary: SummaryResponse
    days: list[DayResponse]
    in_progress: InProgressResponse | None
    data_status: DataStatusResponse
    detail: DetailResponse


def metric_response(result: PublicHealthMetricResult) -> MetricResponse:
    return MetricResponse.model_validate(result.model_dump(mode="python"))


class FirstRecordResponse(ApiResponse):
    status: Literal["CONFIRMED", "UNCONFIRMED"]
    first_arrived_at: UtcDatetime | None

    @model_validator(mode="after")
    def validate_arrival(self) -> "FirstRecordResponse":
        if self.status == "UNCONFIRMED" and self.first_arrived_at is not None:
            raise ValueError("UNCONFIRMED requires no arrival time")
        if self.status == "CONFIRMED" and self.first_arrived_at is None:
            raise ValueError("CONFIRMED requires an arrival time")
        return self


class MetricReadinessResponse(MetricReadiness):
    model_config = ApiResponse.model_config


class AppConnectionResponse(AppConnection):
    model_config = ApiResponse.model_config


class HealthDataStatusResponse(ApiResponse):
    app_connection: AppConnectionResponse
    first_record: FirstRecordResponse
    metrics: list[MetricReadinessResponse]
    checked_at: UtcDatetime


def data_status_response(result: HealthDataStatusResult) -> HealthDataStatusResponse:
    return HealthDataStatusResponse(
        app_connection=AppConnectionResponse.model_validate(
            result.app_connection.model_dump()
        ),
        first_record=FirstRecordResponse(
            status="CONFIRMED"
            if result.first_arrived_at is not None
            else "UNCONFIRMED",
            first_arrived_at=result.first_arrived_at,
        ),
        metrics=[
            MetricReadinessResponse.model_validate(item.model_dump())
            for item in result.metrics
        ],
        checked_at=result.checked_at,
    )


class LifestyleScoreRequest(ApiRequest):
    date: QueryDate | None = None

    @field_validator("date", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> date:
        return _query_date(value)

    def resolve_date(self, *, today: date) -> date:
        """호출부가 한 번 정한 KST 오늘을 응답의 isInProgress에도 사용한다."""
        selected = self.date if self.date is not None else today
        if selected > today:
            raise invalid_request([ErrorDetail(field="date", reason="out_of_range")])
        return selected


class ScoreTotalResponse(ApiResponse):
    score: ScoreValue | None
    missing_areas: list[Literal["activity", "sleep", "heart"]]


class ScoreAreaResponse(ApiResponse):
    score: ScoreValue | None
    grade: Literal[1, 2, 3, 4] | None


class ScoreStepsResponse(ApiResponse):
    score: StepsScoreValue | None
    value: int | None


class ScoreExerciseResponse(ApiResponse):
    score: ExerciseScoreValue | None
    minutes: int | None


class ScoreActivityResponse(ScoreAreaResponse):
    steps: ScoreStepsResponse
    exercise: ScoreExerciseResponse


class ScoreSleepResponse(ScoreAreaResponse):
    sleep_minutes: int | None


class ScoreHeartResponse(ScoreAreaResponse):
    sleep_hr_avg: Number | None
    baseline_hr_avg: Number | None
    baseline_nights: int | None
    check_wear: bool | None


class ScoreAreasResponse(ApiResponse):
    activity: ScoreActivityResponse
    sleep: ScoreSleepResponse
    heart: ScoreHeartResponse


class LifestyleScoreResponse(ApiResponse):
    date: date
    is_in_progress: bool
    reason: ScoreMissingReason | None
    total: ScoreTotalResponse
    areas: ScoreAreasResponse
    computed_at: UtcDatetime | None


_SCORE_GRADES: dict[StoredGrade | None, Literal[1, 2, 3, 4] | None] = {
    "excellent": 1,
    "stable": 2,
    "attention": 3,
    "care": 4,
    None: None,
}


def lifestyle_score_response(
    record: LifestyleScoreRecord | MissingLifestyleScore, *, today: date
) -> LifestyleScoreResponse:
    if isinstance(record, MissingLifestyleScore):
        return LifestyleScoreResponse(
            date=record.date,
            is_in_progress=record.date == today,
            reason=record.reason,
            total=ScoreTotalResponse(
                score=None, missing_areas=["activity", "sleep", "heart"]
            ),
            areas=ScoreAreasResponse(
                activity=ScoreActivityResponse(
                    score=None,
                    grade=None,
                    steps=ScoreStepsResponse(score=None, value=None),
                    exercise=ScoreExerciseResponse(score=None, minutes=None),
                ),
                sleep=ScoreSleepResponse(score=None, grade=None, sleep_minutes=None),
                heart=ScoreHeartResponse(
                    score=None,
                    grade=None,
                    sleep_hr_avg=None,
                    baseline_hr_avg=None,
                    baseline_nights=None,
                    check_wear=None,
                ),
            ),
            computed_at=None,
        )
    return LifestyleScoreResponse(
        date=record.date,
        is_in_progress=record.date == today,
        reason=None,
        total=ScoreTotalResponse(
            score=record.total_score,
            missing_areas=[
                area
                for area in ("activity", "sleep", "heart")
                if getattr(record, f"{area}_score") is None
            ],
        ),
        areas=ScoreAreasResponse(
            activity=ScoreActivityResponse(
                score=record.activity_score,
                grade=_SCORE_GRADES[record.activity_grade],
                steps=ScoreStepsResponse(score=record.steps_score, value=record.steps),
                exercise=ScoreExerciseResponse(
                    score=record.exercise_score,
                    minutes=record.exercise_minutes,
                ),
            ),
            sleep=ScoreSleepResponse(
                score=record.sleep_score,
                grade=_SCORE_GRADES[record.sleep_grade],
                sleep_minutes=record.sleep_minutes,
            ),
            heart=ScoreHeartResponse(
                score=record.heart_score,
                grade=_SCORE_GRADES[record.heart_grade],
                sleep_hr_avg=record.sleep_hr_avg,
                baseline_hr_avg=record.hr_baseline,
                baseline_nights=record.hr_baseline_nights,
                check_wear=record.check_wear,
            ),
        ),
        computed_at=record.computed_at,
    )
