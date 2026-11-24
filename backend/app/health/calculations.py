"""DB 없이 검증하는 날짜·결측·기간 통계 계산."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.health.errors import InvalidHealthData
from app.health.schemas import (
    KST,
    ActivityDailyRecord,
    ActivitySourceMetric,
    ActivitySourceStatus,
    BodyDailyResult,
    DailyRange,
    DataStatus,
    DatedValue,
    Day,
    Detail,
    ExerciseActivityDay,
    ExerciseActivityDetail,
    ExerciseActivityStatistic,
    ExerciseActivitySummary,
    ExerciseComparisonDay,
    ExerciseComparisonExclusion,
    ExerciseComparisonGroup,
    ExerciseRecordComparison,
    ExerciseRecordComparisonQuery,
    ExerciseSegment,
    ExerciseSessionDetail,
    HealthMetricResult,
    HealthQuery,
    HealthTimelineResult,
    HourlyHeartRate,
    HourlyHeartRates,
    HourlyHeartRecord,
    HourlySteps,
    InProgress,
    MainSleepRecord,
    Night,
    Period,
    SleepSegment,
    Summary,
    TimelineDay,
    TimelineSpan,
)

EXERCISE_COMPARISON_MIN_DAYS = 3


def comparison_metric_query(
    query: ExerciseRecordComparisonQuery, today: date
) -> HealthQuery | None:
    """요청은 운동 날짜다. 오늘뿐인 수면 요청은 내일을 조회하지 않는다."""
    start, end = query.start, query.end
    if query.metric == "sleeps":
        if start == today:
            return None
        start += timedelta(days=1)
        end = min(end, today - timedelta(days=1)) + timedelta(days=1)
    return HealthQuery.model_validate(
        dict(user_id=query.user_id, metric=query.metric, start=start, end=end),
        context={"today": today},
    )


def calculate_exercise_record_comparison(
    query: ExerciseRecordComparisonQuery,
    exercises: list["DailyRecord"],
    records: list["DailyRecord"],
    *,
    linked_from: date | None,
    exercise_status: DataStatus,
    metric_status: DataStatus,
    today: date,
    registered_codes: set[int],
) -> ExerciseRecordComparison:
    exercise_query = HealthQuery.model_validate(
        dict(
            user_id=query.user_id, metric="exercises", start=query.start, end=query.end
        ),
        context={"today": today},
    )
    exercise_result = calculate_metric(
        exercise_query,
        exercises,
        linked_from=linked_from,
        status=exercise_status,
        today=today,
    )
    metric_query = comparison_metric_query(query, today)
    metric_result = (
        calculate_metric(
            metric_query,
            records,
            linked_from=linked_from,
            status=metric_status,
            today=today,
        )
        if metric_query
        else None
    )
    metric_days = {day.date: day for day in metric_result.days} if metric_result else {}
    codes_by_date: dict[date, set[int]] = {}
    for session in exercise_result.detail.sessions:
        if session.counts_as_exercise:
            code = (
                session.exercise_type
                if session.exercise_type in registered_codes
                else 0
            )
            codes_by_date.setdefault(session.date, set()).add(code)
    synced_date = (
        exercise_status.synced_until.astimezone(KST).date()
        if exercise_status.synced_until
        else None
    )
    with_exercise, without_exercise, excluded = [], [], []
    for day in exercise_result.days:
        metric_date = (
            day.date
            if query.metric == "steps"
            else day.date + timedelta(days=1)
            if day.date < today
            else None
        )
        source, reason = "exercises", None
        codes = codes_by_date.get(day.date, set())
        if day.is_in_progress:
            reason = "IN_PROGRESS"
        elif day.reason == "BEFORE_LINK":
            reason = day.reason
        elif synced_date is None or day.date >= synced_date:
            # 값이 있어도 해당 날짜 이후의 동기화 전에는 그룹을 확정하지 않는다.
            reason = "UNSYNCED"
        elif day.missing:
            reason = day.reason
        elif (
            query.exercise_type is not None
            and codes
            and query.exercise_type not in codes
        ):
            reason = "OTHER_EXERCISE"
        else:
            target = metric_days[metric_date]
            if target.missing:
                source, reason = query.metric, target.reason
            else:
                group = with_exercise if codes else without_exercise
                group.append(
                    ExerciseComparisonDay(
                        exercise_date=day.date,
                        metric_date=metric_date,
                        value=target.value,
                    )
                )
        if reason is not None:
            excluded.append(
                ExerciseComparisonExclusion(
                    exercise_date=day.date,
                    metric_date=metric_date,
                    source=source,
                    reason=reason,
                )
            )

    def mean(days: list[ExerciseComparisonDay]) -> Decimal | None:
        return (
            sum((Decimal(day.value) for day in days), Decimal(0)) / len(days)
            if days
            else None
        )

    def rounded(value: Decimal | None) -> Decimal | None:
        return (
            value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
            if value is not None
            else None
        )

    with_avg, without_avg = mean(with_exercise), mean(without_exercise)
    comparable = with_avg is not None and without_avg is not None
    return ExerciseRecordComparison(
        user_id=query.user_id,
        metric=query.metric,
        period=Period(start=query.start, end=query.end),
        metric_period=metric_result.period if metric_result else None,
        exercise_type=query.exercise_type,
        unit="steps" if query.metric == "steps" else "min",
        with_exercise=ExerciseComparisonGroup(
            days=with_exercise, days_with_data=len(with_exercise), avg=rounded(with_avg)
        ),
        without_exercise=ExerciseComparisonGroup(
            days=without_exercise,
            days_with_data=len(without_exercise),
            avg=rounded(without_avg),
        ),
        difference=rounded(with_avg - without_avg) if comparable else None,
        comparable=comparable,
        reliable=(
            len(with_exercise) >= EXERCISE_COMPARISON_MIN_DAYS
            and len(without_exercise) >= EXERCISE_COMPARISON_MIN_DAYS
        ),
        reliable_min_days=EXERCISE_COMPARISON_MIN_DAYS,
        excluded_days=excluded,
        linked_from=linked_from,
        exercise_data_status=exercise_status,
        metric_data_status=metric_status,
    )


def timeline_hearts(
    records: list[HourlyHeartRecord],
) -> dict[date, HourlyHeartRates | None]:
    """저장된 KST 시간 키만 24칸으로 펼친다. count로 다시 평균 내지 않는다."""
    result = {}
    for record in records:
        if record.hourly_hr is None:
            result[record.date] = None
            continue
        hours = [None] * 24
        for key, value in record.hourly_hr.items():
            if key not in {str(hour) for hour in range(24)}:
                raise InvalidHealthData("invalid hourly heart key")
            if not {"avg", "min", "max"} <= value.keys():
                raise InvalidHealthData("missing hourly heart range")
            hours[int(key)] = HourlyHeartRate(
                avg=value["avg"],
                min=value["min"],
                max=value["max"],
                sample_count=value.get("count"),
            )
        result[record.date] = hours
    return result


def timeline_sleeps(
    records: list[MainSleepRecord],
) -> dict[date, tuple[TimelineSpan, list[SleepSegment]]]:
    result = {}
    for record in records:
        segments = []
        for stored in record.sleep_timeline or []:
            if not {"start", "end", "stage"} <= stored.keys():
                raise InvalidHealthData("missing sleep segment fields")
            segments.append(
                SleepSegment(
                    start_at=stored["start"],
                    end_at=stored["end"],
                    stage=stored["stage"],
                )
            )
        result[record.date] = (
            TimelineSpan(start_at=record.start_at, end_at=record.end_at),
            sorted(segments, key=lambda item: (item.start_at, item.end_at)),
        )
    return result


def timeline_exercises(
    sessions: list[ExerciseSessionDetail],
) -> dict[date, list[ExerciseSegment]]:
    result = {}
    for session in sorted(sessions, key=lambda item: (item.start_at, item.end_at)):
        if not session.counts_as_exercise:
            continue
        result.setdefault(session.date, []).append(
            ExerciseSegment(
                start_at=session.start_at,
                end_at=session.end_at,
                exercise_type=session.exercise_type,
                type_name=session.type_name,
                title=session.title,
            )
        )
    return result


def build_timeline(
    user_id: UUID,
    start: date,
    end: date,
    *,
    today: date,
    steps: dict[date, HourlySteps | None],
    hearts: dict[date, HourlyHeartRates | None],
    sleeps: dict[date, tuple[TimelineSpan, list[SleepSegment]]],
    exercises: dict[date, list[ExerciseSegment]],
) -> HealthTimelineResult:
    days = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        main_sleep, sleep_segments = sleeps.get(day, (None, []))
        days.append(
            TimelineDay(
                date=day,
                is_in_progress=day == today,
                hourly_steps=steps.get(day),
                hourly_heart_rates=hearts.get(day),
                main_sleep=main_sleep,
                sleep_segments=sleep_segments,
                exercise_segments=exercises.get(day, []),
            )
        )
    return HealthTimelineResult(
        user_id=user_id, period=Period(start=start, end=end), days=days
    )


@dataclass(frozen=True)
class DailyRecord:
    date: date
    value: int | Decimal | None
    weight: int = 1
    measured_min: int | Decimal | None = None
    measured_max: int | Decimal | None = None
    night: Night | None = None
    sessions: tuple[ExerciseSessionDetail, ...] = ()
    sample_count: int | None = None
    activity: dict[str, object] | None = None


def exercise_records(sessions: list[ExerciseSessionDetail]) -> list[DailyRecord]:
    """저장된 인정 여부로 시간만 합산하고 미인정 세션도 상세에 보존한다."""
    by_date: dict[date, list[ExerciseSessionDetail]] = {}
    for session in sorted(sessions, key=lambda item: item.start_at):
        by_date.setdefault(session.date, []).append(session)
    return [
        DailyRecord(
            day,
            sum(
                session.duration_minutes
                for session in day_sessions
                if session.counts_as_exercise
            ),
            sessions=tuple(day_sessions),
        )
        for day, day_sessions in sorted(by_date.items())
    ]


def sleep_record(night: Night) -> DailyRecord:
    if (
        night.end_at.astimezone(KST).date() != night.date
        or night.start_at >= night.end_at
    ):
        raise InvalidHealthData("invalid sleep dates")
    return DailyRecord(night.date, night.sleep_minutes, night=night)


def oxygen_record(
    day: date,
    start_at: datetime,
    end_at: datetime,
    *,
    spo2_avg: Decimal | None,
    spo2_min: Decimal | None,
    spo2_max: Decimal | None,
) -> DailyRecord:
    """산소포화도 조회는 무관한 수면 상세의 검증에 의존하지 않는다."""
    if start_at.utcoffset() is None or end_at.utcoffset() is None:
        raise InvalidHealthData("sleep times require timezone")
    if end_at.astimezone(KST).date() != day or start_at >= end_at:
        raise InvalidHealthData("invalid sleep dates")
    if any(
        value is not None and not value.is_finite()
        for value in (spo2_avg, spo2_min, spo2_max)
    ):
        raise InvalidHealthData("non-finite oxygen value")
    if spo2_avg is not None and (
        spo2_min is None or spo2_max is None or not spo2_min <= spo2_avg <= spo2_max
    ):
        raise InvalidHealthData("invalid oxygen measurement range")
    return DailyRecord(day, spo2_avg, measured_min=spo2_min, measured_max=spo2_max)


def heart_record(
    day: date,
    average: Decimal | None,
    sample_count: int,
    hourly_hr: dict | None,
    measured_min: int | None,
    measured_max: int | None,
) -> DailyRecord:
    if type(sample_count) is not int or sample_count < 0:
        raise InvalidHealthData("invalid heart sample count")
    if sample_count == 0 or average is None:
        return DailyRecord(day, None, sample_count=sample_count)
    if not average.is_finite():
        raise InvalidHealthData("non-finite heart average")
    if not isinstance(hourly_hr, dict) or not hourly_hr:
        raise InvalidHealthData("missing heart hours")
    for hour, values in hourly_hr.items():
        if hour not in {str(value) for value in range(24)} or not isinstance(
            values, dict
        ):
            raise InvalidHealthData("invalid heart hour")
        if not {"avg", "min", "max", "count"} <= values.keys():
            raise InvalidHealthData("incomplete heart hour")
        if type(values["count"]) is not int or values["count"] <= 0:
            raise InvalidHealthData("invalid heart hour count")
        numbers = [values[key] for key in ("min", "avg", "max")]
        if any(
            type(value) not in (int, float, Decimal)
            or not Decimal(str(value)).is_finite()
            for value in numbers
        ):
            raise InvalidHealthData("invalid heart hour values")
        # 극값은 정수, 평균은 소수 첫째 자리로 따로 반올림된 저장값이다.
        low, hourly_average, high = (Decimal(str(value)) for value in numbers)
        if low > high or not low - Decimal("0.5") <= hourly_average <= high + Decimal(
            "0.5"
        ):
            raise InvalidHealthData("invalid heart hour range")
    if (
        measured_min is None
        or measured_max is None
        or measured_min > measured_max
        or not measured_min - Decimal("0.5") <= average <= measured_max + Decimal("0.5")
    ):
        raise InvalidHealthData("invalid heart measurement range")
    return DailyRecord(
        day,
        average,
        len(hourly_hr),
        measured_min,
        measured_max,
        sample_count=sample_count,
    )


def _measured_extreme(
    valid: list[Day], records: dict[date, DailyRecord], *, maximum: bool = False
) -> DatedValue | None:
    values = [
        DatedValue(
            date=day.date,
            value=records[day.date].measured_max
            if maximum
            else records[day.date].measured_min,
        )
        for day in valid
    ]
    return (
        (max if maximum else min)(values, key=lambda item: item.value)
        if values
        else None
    )


def _extreme(days: list[Day], *, maximum: bool = False) -> DatedValue | None:
    if not days:
        return None
    choose = max if maximum else min
    day = choose(days, key=lambda day: day.value)
    return DatedValue(date=day.date, value=day.value)


def calculate_metric(
    query: HealthQuery,
    records: list[DailyRecord],
    *,
    linked_from: date | None,
    status: DataStatus,
    today: date,
    activity_source_statuses: dict[ActivitySourceMetric, ActivitySourceStatus]
    | None = None,
) -> HealthMetricResult:
    by_date = {record.date: record for record in records}
    if len(by_date) != len(records):
        raise InvalidHealthData("duplicate daily record")
    days = []
    synced_date = (
        status.synced_until.astimezone(KST).date() if status.synced_until else None
    )
    progresses = query.metric in ("steps", "heart_rates", "exercises")
    for offset in range((query.end - query.start).days + 1):
        day_date = query.start + timedelta(days=offset)
        record = by_date.get(day_date)
        value = record.value if record else None
        zero_steps = query.metric == "steps" and value == 0
        if linked_from is None or day_date < linked_from:
            reason = "BEFORE_LINK"
        elif value is not None and not zero_steps:
            reason = None
        elif synced_date is None or day_date >= synced_date:
            reason = "UNSYNCED"
        elif record is None:
            reason = "NO_RECORD"
        else:
            reason = "ZERO_STEPS" if zero_steps else "NULL_VALUE"
        days.append(
            Day(
                date=day_date,
                value=value if reason is None or zero_steps else None,
                missing=reason is not None,
                reason=reason,
                is_in_progress=progresses and day_date == today,
            )
        )

    completed = [
        day for day in days if day.reason != "BEFORE_LINK" and not day.is_in_progress
    ]
    valid = [day for day in completed if not day.missing]
    total = sum((Decimal(day.value) for day in valid), Decimal(0)) if valid else None
    if any(by_date[day.date].weight <= 0 for day in valid):
        raise InvalidHealthData("invalid daily weight")
    average = (
        (
            sum(
                (Decimal(day.value) * by_date[day.date].weight for day in valid),
                Decimal(0),
            )
            / sum(by_date[day.date].weight for day in valid)
        ).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        if valid
        else None
    )
    progress_day = next((day for day in days if day.is_in_progress), None)
    in_progress = (
        InProgress(
            date=progress_day.date,
            value=progress_day.value,
            missing=progress_day.missing,
            as_of=status.last_data_at,
        )
        if progress_day
        else None
    )
    result = HealthMetricResult(
        user_id=query.user_id,
        metric=query.metric,
        period=Period(start=query.start, end=query.end),
        unit={
            "steps": "steps",
            "heart_rates": "bpm",
            "oxygen_saturations": "%",
            "sleeps": "min",
            "exercises": "min",
        }[query.metric],
        summary=Summary(
            avg=average,
            daily_min=_extreme(valid),
            daily_max=_extreme(valid, maximum=True),
            total=total if query.metric in ("steps", "exercises") else None,
            days_with_data=len(valid),
            days_used_for_summary=len(valid),
            completed_days=len(completed),
            missing_days=[
                day.date
                for day in days
                if day.reason in ("NO_RECORD", "NULL_VALUE", "ZERO_STEPS")
            ],
            unsynced_days=[day.date for day in days if day.reason == "UNSYNCED"],
            linked_from=linked_from,
        ),
        days=days,
        in_progress=in_progress,
        data_status=status,
        detail=Detail(
            measured_min=_measured_extreme(valid, by_date)
            if query.metric in ("heart_rates", "oxygen_saturations")
            else None,
            measured_max=_measured_extreme(valid, by_date, maximum=True)
            if query.metric in ("heart_rates", "oxygen_saturations")
            else None,
            nights=[
                by_date[day.date].night
                for day in days
                if day.date in by_date and by_date[day.date].night is not None
            ]
            if query.metric == "sleeps"
            else [],
            daily_ranges=[
                DailyRange(
                    date=day.date,
                    min=by_date[day.date].measured_min if not day.missing else None,
                    max=by_date[day.date].measured_max if not day.missing else None,
                    sample_count=by_date[day.date].sample_count
                    if query.metric == "heart_rates" and day.date in by_date
                    else None,
                )
                for day in days
            ]
            if query.metric in ("heart_rates", "oxygen_saturations")
            else [],
            sessions=[
                session
                for day in days
                if day.date in by_date
                for session in by_date[day.date].sessions
            ]
            if query.metric == "exercises"
            else [],
        ),
    )

    if query.metric == "exercises" and activity_source_statuses is not None:
        activity = _exercise_activity(result, by_date, activity_source_statuses)
        result = result.model_copy(
            update={
                "detail": result.detail.model_copy(
                    update={"exercise_activity": activity}
                )
            }
        )
    return result


def _exercise_activity(
    result: HealthMetricResult,
    records: dict[date, DailyRecord],
    source_statuses: dict[ActivitySourceMetric, ActivitySourceStatus],
) -> ExerciseActivityDetail:
    synced = result.data_status.synced_until
    synced_date = synced.astimezone(KST).date() if synced else None

    def measurement_reason(day: Day, value):
        if day.reason is not None:
            return day.reason
        if value is not None:
            return None
        return (
            "UNSYNCED"
            if synced_date is None or day.date >= synced_date
            else "NULL_VALUE"
        )

    days = []
    for day in result.days:
        record = records.get(day.date)
        activity = record.activity if record else None
        if record is not None and activity is None:
            raise InvalidHealthData("missing stored exercise activity")
        # 추가 지표 검증은 해당 값을 반환하는 경로에만 적용한다.
        activity = (
            ActivityDailyRecord.model_validate(activity)
            if activity is not None
            else None
        )
        values = {
            name: getattr(activity, name) if activity and not day.missing else None
            for name in (
                "exercise_minutes",
                "exercise_count",
                "exercise_calories_kcal",
                "exercise_distance_m",
            )
        }

        days.append(
            ExerciseActivityDay(
                date=day.date,
                **values,
                computed_at=activity.computed_at if activity else None,
                is_in_progress=day.is_in_progress,
                reason=day.reason,
                calories_reason=measurement_reason(
                    day, values["exercise_calories_kcal"]
                ),
                distance_reason=measurement_reason(day, values["exercise_distance_m"]),
            )
        )

    def statistic(field):
        values = [
            getattr(day, field)
            for day in days
            if not day.is_in_progress
            and day.reason is None
            and getattr(day, field) is not None
        ]
        total = (
            sum((Decimal(value) for value in values), Decimal(0)) if values else None
        )
        return ExerciseActivityStatistic(
            total=total,
            avg=(total / len(values)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
            if values
            else None,
            days_with_data=len(values),
        )

    return ExerciseActivityDetail(
        days=days,
        summary=ExerciseActivitySummary(
            exercise_minutes=ExerciseActivityStatistic(
                total=result.summary.total,
                avg=result.summary.avg,
                days_with_data=result.summary.days_with_data,
            ),
            exercise_count=statistic("exercise_count"),
            exercise_calories_kcal=statistic("exercise_calories_kcal"),
            exercise_distance_m=statistic("exercise_distance_m"),
        ),
        source_statuses=source_statuses,
    )


def calculate_body_metric(body: BodyDailyResult) -> HealthMetricResult:
    """신체 일별 대표값을 공통 원형으로 옮긴다. 오늘과 무기록은 통계에서 뺀다."""
    days = [
        Day(
            date=row.date,
            value=row.value,
            missing=row.reason is not None,
            reason=row.reason,
            is_in_progress=row.is_in_progress,
            wear_hours=row.wear_hours,
            is_well_worn=row.is_well_worn,
        )
        for row in body.days
    ]
    completed = [day for day in days if not day.is_in_progress]
    valid = [day for day in completed if not day.missing]
    progress = next((day for day in days if day.is_in_progress), None)
    average = (
        (sum((Decimal(day.value) for day in valid), Decimal(0)) / len(valid)).quantize(
            Decimal(".01"), rounding=ROUND_HALF_UP
        )
        if valid
        else None
    )
    return HealthMetricResult(
        user_id=body.user_id,
        metric=body.metric,
        period=body.period,
        unit=body.unit,
        summary=Summary(
            avg=average,
            daily_min=_extreme(valid),
            daily_max=_extreme(valid, maximum=True),
            total=None,
            days_with_data=len(valid),
            days_used_for_summary=len(valid),
            completed_days=len(completed),
            missing_days=[day.date for day in days if day.missing],
            unsynced_days=[],
            linked_from=None,
        ),
        days=days,
        in_progress=InProgress(
            date=progress.date,
            value=progress.value,
            missing=progress.missing,
            as_of=body.data_status.last_data_at,
        )
        if progress
        else None,
        data_status=DataStatus(**body.data_status.model_dump(), synced_until=None),
        detail=Detail(
            measured_min=None,
            measured_max=None,
            nights=[],
            daily_ranges=[],
            sessions=[],
        ),
    )
