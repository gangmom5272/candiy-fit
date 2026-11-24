from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, get_args
from zoneinfo import ZoneInfo

from coach_ai.adapters.exercise_activity import ExerciseActivityDay, activity_detail
from coach_ai.ports import (
    DailyMessageArea,
    DailyMessageMetric,
    DailyMessageNoticeCode,
    DailyMessageResult,
    DailyMessageStatus,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
    HealthDataUnavailableError,
    LifestyleScoreResult,
    MetricResult,
    ScoreArea,
    ScoreGrade,
    ScoreMissingReason,
    SleepSessionsResult,
)
from coach_ai.tools.periods import DEFAULT_TZ, MAX_LIST_DAYS, local_today
from coach_ai.tools.results import UNITS
from coach_ai.tools.schemas import PUBLIC_METRICS, Metric

_KST = ZoneInfo(DEFAULT_TZ)
# API 명세 5.2: 한 번에 조회하는 기간은 1~366일이다
MAX_DAYS = 366
# 하루가 끝나지 않은 오늘을 기간 통계에서 빼고 진행 중 값으로 따로 제공하는 지표
_IN_PROGRESS_METRICS: tuple[Metric, ...] = ("steps", "heart_rate", "exercise")
# 완료된 유효 날짜의 합계를 채우는 지표
_TOTAL_METRICS: tuple[Metric, ...] = ("steps", "exercise")
# missing_days(기록 없음)에 들어가는 사유. UNSYNCED 는 unsynced_days, BEFORE_LINK 는 어느 목록에도 넣지 않는다
_NO_RECORD_REASONS = ("NO_RECORD", "NULL_VALUE", "ZERO_STEPS")
# 충분히 착용한 날의 심박 측정 시간대 수 (calculation-rules.md 9장)
_WELL_WORN_HOURS = 10

StoredGrade = Literal["excellent", "stable", "attention", "care"]
# 저장 등급과 점수 응답(API 명세 5.3)의 등급 코드. 연동 함수가 응답을 만들 때와 같은 대응이다
_GRADE_CODES: dict[StoredGrade, ScoreGrade] = {"excellent": 1, "stable": 2, "attention": 3, "care": 4}
_SCORE_AREAS: tuple[ScoreArea, ...] = ("activity", "sleep", "heart")
# 점수 행이 없을 때 준비 여부를 확인하는 입력 지표. 연동 함수의 steps, exercise_sessions, sleeps, heart_rates 다
_SCORE_INPUTS: tuple[Metric, ...] = ("steps", "exercise", "sleep", "heart_rate")
# 한마디 행이 없을 때 준비 여부를 확인하는 입력 지표. 연동 쪽 한마디가 읽는 steps, exercise_sessions, sleeps 다
_MESSAGE_INPUTS: tuple[Metric, ...] = ("steps", "exercise", "sleep")
_MESSAGE_METRICS: tuple[DailyMessageMetric, ...] = get_args(DailyMessageMetric)
# 소개할 기록도 저장 값도 없을 때의 문구 (API 명세 5.5 NO_RECORDS)
_NO_RECORDS_TITLE = "오늘은 기록 없이도 내 하루를 돌아볼까요?"
_NO_RECORDS_DESCRIPTION = "지금 확인할 수 있는 활동·수면 기록은 없어요."


@dataclass(frozen=True, slots=True)
class StepDay:
    """activity_daily의 하루 걸음. steps가 None이면 행은 있으나 걸음 값이 없는 날이다."""

    date: dt.date
    steps: int | None


@dataclass(frozen=True, slots=True)
class HeartDay:
    """heart_daily의 하루 심박. hr_avg가 None이면 측정이 없는 날이다."""

    date: dt.date
    hr_avg: float | None
    hr_min: int | None = None
    hr_max: int | None = None
    # hourly_hr의 유효 시간대 수. 기간 평균의 가중치이고 그날 착용 값(wear_hours)이다
    hours: int = 1
    # 합성 입력에 명시한 측정 수. 과거 골든셋의 미수집 값은 None이며 시간대 수로 추정하지 않는다.
    sample_count: int | None = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class SleepSession:
    """sleep_session의 수면 한 덩어리. date는 수면 종료일(KST)이고 시각은 시간대 정보가 있다."""

    date: dt.date
    start_at: dt.datetime
    end_at: dt.datetime
    is_main: bool
    # 실제 수면 분을 모르면 None이다. 세션 길이로 대신하지 않는다
    sleep_minutes: int | None
    deep_minutes: int | None = None
    rem_minutes: int | None = None
    light_minutes: int | None = None
    awake_minutes: int | None = None
    # 이 수면 중 측정한 산소포화도. 주 수면의 값만 산소포화도 지표에 쓴다
    spo2_avg: float | None = None
    spo2_min: float | None = None
    spo2_max: float | None = None
    unspecified_minutes: int | None = None
    unknown_minutes: int | None = None
    avg_heart_rate: float | None = None
    min_heart_rate: float | None = None
    max_heart_rate: float | None = None
    spo2_count: int | None = None
    spo2_low_count: int | None = None
    # 90% 미만으로 기록된 시각. 같은 시각이 여러 번이면 그대로 둔다
    spo2_low_times: tuple[dt.datetime, ...] | None = None


@dataclass(frozen=True, slots=True)
class ExerciseSession:
    """exercise_session의 운동 한 번. date는 시작 시각의 KST 날짜이고 시각은 시간대 정보가 있다.

    type_name·category는 운동 코드 기준표의 이름·분류이고, 기준표에 없는 코드면 None이다.
    """

    date: dt.date
    start_at: dt.datetime
    end_at: dt.datetime
    duration_minutes: int
    exercise_type: int
    type_name: str | None
    category: Literal["cardio", "strength", "flexibility", "other"] | None
    # 파이프라인이 판정해 저장한 운동 시간 포함 여부. 느린 걷기는 False다
    counts_as_exercise: bool
    title: str | None = None
    calories_kcal: float | None = None
    # 세션 거리(m). 세션 안에 거리 기록이 없으면 None이고 0과 구분한다
    distance_m: float | None = None
    avg_heart_rate: float | None = None
    max_heart_rate: int | None = None
    avg_speed_mps: float | None = None

    def __post_init__(self) -> None:
        # 연동 함수가 세션을 읽을 때 검증하는 규칙(ExerciseSessionDetail)과 같다
        if self.start_at.astimezone(_KST).date() != self.date or self.start_at >= self.end_at:
            raise ValueError("운동 세션의 date는 시작 시각의 KST 날짜이고 시작은 종료보다 앞서야 한다")
        if self.duration_minutes < 1:
            raise ValueError("운동 세션의 duration_minutes는 1 이상이어야 한다")
        # 연동 함수는 NaN·무한대 거리를 거부하고, 파이프라인은 음수 거리 기록을 더하지 않아 저장 거리가 음수일 수 없다
        if self.distance_m is not None and (not math.isfinite(self.distance_m) or self.distance_m < 0):
            raise ValueError("운동 세션의 distance_m은 0 이상의 유한한 값이어야 한다")


@dataclass(frozen=True, slots=True)
class ScoreDay:
    """lifestyle_score의 하루 한 행. 점수·등급은 파이프라인이 계산해 저장한 값이고 여기서 다시 계산하지 않는다.

    연동 함수의 저장 계약처럼 영역 점수와 등급, 활동 점수와 걸음·운동 점수·운동 분은 함께 비거나 함께 채워지고,
    오늘의 점수는 세 영역 점수가 모두 없을 때만 빈다.
    """

    date: dt.date
    total_score: int | None
    activity_score: int | None
    activity_grade: StoredGrade | None
    steps_score: int | None
    exercise_score: int | None
    sleep_score: int | None
    sleep_grade: StoredGrade | None
    heart_score: int | None
    heart_grade: StoredGrade | None
    # 점수에 쓴 값. 걸음은 기록이 없으면 None, 0보면 0이고 운동 분은 활동 점수가 없으면 None이다
    steps: int | None
    exercise_minutes: int | None
    sleep_minutes: int | None
    sleep_hr_avg: float | None
    # 이전 28일 주 수면의 수면 중 평균 심박 평균과 그 밤 수. 밤이 0이면 평균이 없다
    hr_baseline: float | None
    hr_baseline_nights: int
    computed_at: dt.datetime
    check_wear: bool = False

    def __post_init__(self) -> None:
        for area in _SCORE_AREAS:
            if (getattr(self, f"{area}_score") is None) != (getattr(self, f"{area}_grade") is None):
                raise ValueError("영역 점수와 등급은 함께 비거나 함께 채워져야 한다")
        details = (self.steps_score, self.exercise_score, self.exercise_minutes)
        if any((value is None) != (self.activity_score is None) for value in details):
            raise ValueError("걸음·운동 점수와 운동 분은 활동 점수가 없을 때만 빈다")
        if (self.total_score is None) != all(getattr(self, f"{area}_score") is None for area in _SCORE_AREAS):
            raise ValueError("오늘의 점수는 세 영역 점수가 모두 없을 때만 빈다")
        if self.computed_at.tzinfo is None:
            raise ValueError("computed_at에 시간대 정보가 없다")


@dataclass(frozen=True, slots=True)
class DailyMessageDay:
    """오늘의 한마디 한 건. 연동 쪽 규칙(API 명세 5.5)이 고른 값을 그대로 두고 여기서 다시 고르지 않는다.

    data_as_of 는 지표(공개 코드)마다 정상 조회한 기록의 기준 시각이고, 빠진 지표의 기준 시각은 None 이다.
    notices 는 (지표, 안내 코드, 화면 안내 문장) 이다.
    """

    date: dt.date
    status: DailyMessageStatus
    code: str
    area: DailyMessageArea | None
    title: str
    description: str
    used_metrics: tuple[DailyMessageMetric, ...] = ()
    data_as_of: Mapping[DailyMessageMetric, dt.datetime | None] = field(default_factory=dict)
    score_computed_at: dt.datetime | None = None
    notices: tuple[tuple[DailyMessageMetric, DailyMessageNoticeCode, str], ...] = ()

    def __post_init__(self) -> None:
        times = [*self.data_as_of.values(), self.score_computed_at]
        if any(value is not None and value.tzinfo is None for value in times):
            raise ValueError("한마디 기준 시각에 시간대 정보가 없다")


@dataclass(frozen=True, slots=True)
class SyncState:
    """sync_state의 (회원, 지표) 한 행."""

    last_data_at: dt.datetime | None = None
    last_status: Literal["success", "fail"] | None = None
    last_success_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class _Source:
    """하루 값의 원천 행. reason은 행이 있어도 결측인 사유(NULL_VALUE, ZERO_STEPS)다."""

    value: float | None
    reason: Literal["NULL_VALUE", "ZERO_STEPS"] | None
    low: float | None = None
    high: float | None = None
    weight: int = 1
    sample_count: int | None = None


@dataclass
class InMemoryHealthData:
    """개발용 CLI와 테스트가 쓰는 메모리 HealthDataPort 구현.

    공통 원형을 연동 모듈과 같은 규칙(API 명세 2.2, backend/docs/health-query-contract.md 4·5장)으로 만든다.
    data_status.checked_at 은 now 다. 백엔드 local·test 의 합성 포트에서는 사용자가 질문한 시각이 들어간다.
    """

    steps: dict[str, list[StepDay]] = field(default_factory=dict)
    heart: dict[str, list[HeartDay]] = field(default_factory=dict)
    sleep: dict[str, list[SleepSession]] = field(default_factory=dict)
    # 운동 세션의 날짜에는 steps 에 그날 활동 행(StepDay)이 있어야 한다
    exercise: dict[str, list[ExerciseSession]] = field(default_factory=dict)
    # 지정한 회원만 새 활동값을 제공한다. 기존 고정 fixture에는 자동 생성하지 않는다.
    exercise_activity: dict[str, list[ExerciseActivityDay]] = field(default_factory=dict)
    exercise_activity_statuses: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 생활 습관 점수 행. 파이프라인은 활동 행이나 주 수면이 있는 날에 행을 만든다
    scores: dict[str, list[ScoreDay]] = field(default_factory=dict)
    # 오늘의 한마디. 연동 쪽은 저장하지 않고 조회할 때 고르므로 합성 데이터가 고른 결과를 날짜별로 둔다
    daily_messages: dict[str, list[DailyMessageDay]] = field(default_factory=dict)
    sync: dict[str, dict[Metric, SyncState]] = field(default_factory=dict)
    # 회원별 연동 시작일. 없으면 그 회원 기록 중 가장 이른 날짜다
    linked_from: dict[str, dt.date] = field(default_factory=dict)
    # 조회 시각(data_status.checked_at)이자 오늘(KST)의 기준. 없으면 현재 시각이다
    now: dt.datetime | None = None
    # 미연동 회원. 기록이 없는 회원과 달리 조회가 실패한다
    not_linked: set[str] = field(default_factory=set)
    calls: list[tuple] = field(default_factory=list)

    async def get_metric_result(self, user_id: str, metric: Metric, start: dt.date, end: dt.date) -> MetricResult:
        self.calls.append(("get_metric_result", user_id, metric, start, end))
        if user_id in self.not_linked:
            raise HealthDataNotLinkedError
        now = self.now or dt.datetime.now(dt.UTC)
        today = local_today(now)
        if not start <= end <= today or (end - start).days >= MAX_DAYS:
            raise ValueError("기간은 start <= end <= KST 오늘이고 366일 이하여야 한다")
        snapshot = self._prototype(user_id, metric, start, end, now, today)
        if metric == "exercise" and user_id in self.exercise_activity:
            snapshot["detail"]["exercise_activity"] = activity_detail(
                snapshot, self.exercise_activity[user_id], self.exercise_activity_statuses.get(user_id, {})
            )
        return MetricResult(user_id, snapshot)

    async def get_sleep_sessions(self, user_id: str, start: dt.date, end: dt.date) -> SleepSessionsResult:
        """기간의 모든 수면 세션을 백엔드 전체 수면 세션 조회(sleep-sessions-237.md)와 같은 모양으로 만든다.

        세션 순서는 깬 날, 잠든 시각, 깬 시각 순이다. 세션 수 한도는 넣지 않는다.
        """
        self.calls.append(("get_sleep_sessions", user_id, start, end))
        if user_id in self.not_linked:
            raise HealthDataNotLinkedError
        now = self.now or dt.datetime.now(dt.UTC)
        # 연동 쪽 입력 검증(양 끝 포함 1~31일, 끝은 KST 오늘까지)에 걸리면 백엔드 포트처럼 조회 실패다
        if not start <= end <= local_today(now) or (end - start).days >= MAX_LIST_DAYS:
            raise HealthDataUnavailableError(metrics=("sleep",))
        state = self.sync.get(user_id, {}).get("sleep", SyncState())
        computed_at = _timestamp(state.last_success_at or now)
        sessions = sorted(
            (s for s in self.sleep.get(user_id, []) if start <= s.date <= end),
            key=lambda s: (s.date, s.start_at, s.end_at),
        )
        snapshot = {
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "sessions": [
                {
                    **_night(s),
                    "session_id": f"synthetic-{s.start_at.astimezone(dt.UTC):%Y%m%dT%H%M%S}",
                    "is_main": s.is_main,
                    "computed_at": computed_at,
                }
                for s in sessions
            ],
            "data_status": {
                "last_sync_status": state.last_status,
                "last_data_at": _timestamp(state.last_data_at),
                "last_success_at": _timestamp(state.last_success_at),
                "checked_at": _timestamp(now),
            },
        }
        return SleepSessionsResult(user_id, snapshot)

    async def get_lifestyle_score(self, user_id: str, day: dt.date) -> LifestyleScoreResult:
        self.calls.append(("get_lifestyle_score", user_id, day))
        if user_id in self.not_linked:
            raise HealthDataNotLinkedError
        today = local_today(self.now or dt.datetime.now(dt.UTC))
        if day > today:
            raise ValueError("점수 날짜는 KST 오늘을 넘지 않아야 한다")
        row = next((r for r in self.scores.get(user_id, []) if r.date == day), None)
        if row is not None:
            return LifestyleScoreResult(user_id, _score_snapshot(row, today))
        # 행이 없으면 연동 함수처럼 입력 지표의 동기화 상태가 모두 있어야 사유를 정한다. 저장된 행은 그대로 반환한다
        states = self.sync.get(user_id, {})
        if any(metric not in states for metric in _SCORE_INPUTS):
            raise HealthDataNotReadyError
        return LifestyleScoreResult(user_id, _missing_score(day, self._score_reason(user_id, day), today))

    async def get_daily_message(self, user_id: str, day: dt.date, *, request_id: str) -> DailyMessageResult:
        self.calls.append(("get_daily_message", user_id, day, request_id))
        if user_id in self.not_linked:
            raise HealthDataNotLinkedError
        if day > local_today(self.now or dt.datetime.now(dt.UTC)):
            raise ValueError("한마디 날짜는 KST 오늘을 넘지 않아야 한다")
        row = next((r for r in self.daily_messages.get(user_id, []) if r.date == day), None)
        if row is not None:
            return DailyMessageResult(user_id, _message_snapshot(row))
        # 행이 없으면 연동 쪽처럼 세 지표의 동기화 상태가 있어야 문구를 고를 수 있다
        states = self.sync.get(user_id, {})
        not_ready = [metric for metric in _MESSAGE_INPUTS if metric not in states]
        if not_ready:
            raise HealthDataNotReadyError(metrics=not_ready)
        return DailyMessageResult(user_id, _no_records_message(day, states))

    def _score_reason(self, user_id: str, day: dt.date) -> ScoreMissingReason:
        """점수 행이 없는 날짜의 사유. 지표 원형의 결측 사유와 같은 기록 시작일·동기화 기준일로 나눈다."""
        linked = self.linked_from.get(user_id) or self._first_record(user_id)
        synced = self._synced_until(user_id)
        if linked is None or day < linked:
            return "BEFORE_LINK"
        if synced is None or day >= synced.astimezone(_KST).date():
            return "UNSYNCED"
        return "NO_RECORD"

    def _synced_until(self, user_id: str) -> dt.datetime | None:
        return max((s.last_data_at for s in self.sync.get(user_id, {}).values() if s.last_data_at), default=None)

    def _prototype(
        self, user_id: str, metric: Metric, start: dt.date, end: dt.date, now: dt.datetime, today: dt.date
    ) -> dict[str, Any]:
        linked = self.linked_from.get(user_id) or self._first_record(user_id)
        synced = self._synced_until(user_id)
        synced_day = None if synced is None else synced.astimezone(_KST).date()
        state = self.sync.get(user_id, {}).get(metric, SyncState())
        sources = self._sources(user_id, metric)
        dates = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
        heart_state = self.sync.get(user_id, {}).get("heart_rate")
        heart_hours = {r.date: r.hours for r in self.heart.get(user_id, [])}

        reasons: dict[dt.date, str | None] = {}
        days = []
        for d in dates:
            src = sources.get(d)
            if linked is None or d < linked:
                reason = "BEFORE_LINK"
            elif src is not None and src.reason is None:
                reason = None
            # synced_until 이 없으면 기준일을 알 수 없어 결측을 모두 동기화 전으로 간주한다 (연동 함수와 같은 규칙)
            elif synced_day is None or d >= synced_day:
                reason = "UNSYNCED"
            else:
                reason = "NO_RECORD" if src is None else src.reason
            reasons[d] = reason
            # 0보는 어떤 사유로 결측이 되어도 저장된 0을 그대로 둔다
            keep = src is not None and (reason is None or src.reason == "ZERO_STEPS")
            wear_hours, well_worn = _wear(d, heart_hours, heart_state, linked, synced_day, today)
            days.append(
                {
                    "date": d.isoformat(),
                    "value": src.value if keep else None,
                    "missing": reason is not None,
                    "reason": reason,
                    "is_in_progress": metric in _IN_PROGRESS_METRICS and d == today,
                    "wear_hours": wear_hours,
                    "is_well_worn": well_worn,
                }
            )

        counted = [
            d
            for d in dates
            if linked is not None and d >= linked and not (metric in _IN_PROGRESS_METRICS and d == today)
        ]
        valid = [(d, sources[d]) for d in counted if reasons[d] is None]
        values = [(d, src.value) for d, src in valid if src.value is not None]

        in_progress = None
        if metric in _IN_PROGRESS_METRICS and start <= today <= end:
            row = days[dates.index(today)]
            in_progress = {
                "date": today.isoformat(),
                "value": row["value"],
                "missing": row["missing"],
                "as_of": _timestamp(state.last_data_at),
            }

        return {
            "metric": PUBLIC_METRICS[metric],
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "unit": UNITS[metric],
            "summary": {
                "avg": _average(metric, valid),
                "daily_min": _extreme(values, lowest=True),
                "daily_max": _extreme(values, lowest=False),
                # 연동 함수는 합계를 Decimal 로 계산해 JSON number(float)로 직렬화한다
                "total": float(sum(v for _, v in values)) if metric in _TOTAL_METRICS and values else None,
                "days_with_data": len(valid),
                "days_used_for_summary": len(valid),
                "completed_days": len(counted),
                "missing_days": [d.isoformat() for d in dates if reasons[d] in _NO_RECORD_REASONS],
                "unsynced_days": [d.isoformat() for d in dates if reasons[d] == "UNSYNCED"],
                "linked_from": None if linked is None else linked.isoformat(),
            },
            "days": days,
            "in_progress": in_progress,
            "data_status": {
                "last_data_at": _timestamp(state.last_data_at),
                "synced_until": _timestamp(synced),
                "last_sync_status": state.last_status,
                "last_success_at": _timestamp(state.last_success_at),
                "checked_at": _timestamp(now),
            },
            "detail": self._detail(user_id, metric, dates, valid, sources, reasons),
        }

    def _sources(self, user_id: str, metric: Metric) -> dict[dt.date, _Source]:
        match metric:
            case "steps":
                return {r.date: _Source(r.steps, _steps_reason(r.steps)) for r in self.steps.get(user_id, [])}
            case "heart_rate":
                # 하루 평균은 numeric 컬럼이라 연동 함수 JSON 에서 float 이고, 측정 최저·최고는 integer 컬럼이라 int 다
                return {
                    r.date: _Source(
                        _decimal_number(r.hr_avg),
                        _null_reason(r.hr_avg),
                        r.hr_min,
                        r.hr_max,
                        r.hours,
                        sample_count=r.sample_count,
                    )
                    for r in self.heart.get(user_id, [])
                }
            case "sleep":
                return {
                    s.date: _Source(s.sleep_minutes, "NULL_VALUE" if s.sleep_minutes is None else None)
                    for s in self._mains(user_id)
                }
            case "oxygen_saturation":
                return {
                    s.date: _Source(
                        _decimal_number(s.spo2_avg),
                        _null_reason(s.spo2_avg),
                        _decimal_number(s.spo2_min),
                        _decimal_number(s.spo2_max),
                    )
                    for s in self._mains(user_id)
                }
            case "exercise":
                # 활동 행이 있는 날만 하루 값이 있고, 운동 시간에 들어가는 세션이 없는 날은 0분이다
                minutes: dict[dt.date, int] = {}
                for s in self.exercise.get(user_id, []):
                    if s.counts_as_exercise:
                        minutes[s.date] = minutes.get(s.date, 0) + s.duration_minutes
                return {r.date: _Source(minutes.get(r.date, 0), None) for r in self.steps.get(user_id, [])}
        raise ValueError(f"알 수 없는 지표: {metric}")

    def _mains(self, user_id: str) -> list[SleepSession]:
        # 낮잠 등 주 수면이 아닌 세션은 수면·산소포화도 하루 값과 밤별 상세에 넣지 않는다
        return sorted((s for s in self.sleep.get(user_id, []) if s.is_main), key=lambda s: s.date)

    def _sessions(self, user_id: str, start: dt.date, end: dt.date) -> list[ExerciseSession]:
        """기간 안에 시작한 운동 세션을 시작 시각 순으로 반환한다.

        활동 행이 없는 날의 세션은 연동 함수처럼 집계 불일치로 보고 조회 실패로 처리한다.
        """
        sessions = sorted(
            (s for s in self.exercise.get(user_id, []) if start <= s.date <= end), key=lambda s: s.start_at
        )
        rows = {r.date for r in self.steps.get(user_id, [])}
        if any(s.date not in rows for s in sessions):
            raise HealthDataUnavailableError("활동 행이 없는 날의 운동 세션이 있다", metrics=("exercise",))
        return sessions

    def _first_record(self, user_id: str) -> dt.date | None:
        dates = [r.date for r in self.steps.get(user_id, [])]
        dates += [r.date for r in self.heart.get(user_id, [])]
        dates += [s.date for s in self.sleep.get(user_id, [])]
        return min(dates, default=None)

    def _detail(
        self,
        user_id: str,
        metric: Metric,
        dates: Sequence[dt.date],
        valid: Sequence[tuple[dt.date, _Source]],
        sources: dict[dt.date, _Source],
        reasons: dict[dt.date, str | None],
    ) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "measured_min": None,
            "measured_max": None,
            "nights": [],
            "daily_ranges": [],
            "sessions": [],
        }
        if metric in ("heart_rate", "oxygen_saturation"):
            detail["measured_min"] = _extreme([(d, s.low) for d, s in valid if s.low is not None], lowest=True)
            detail["measured_max"] = _extreme([(d, s.high) for d, s in valid if s.high is not None], lowest=False)
            detail["daily_ranges"] = [
                {
                    "date": d.isoformat(),
                    "min": sources[d].low if reasons[d] is None else None,
                    "max": sources[d].high if reasons[d] is None else None,
                    "sample_count": sources[d].sample_count if metric == "heart_rate" and d in sources else None,
                }
                for d in dates
            ]
        if metric == "sleep":
            detail["nights"] = [_night(s) for s in self._mains(user_id) if dates[0] <= s.date <= dates[-1]]
        if metric == "exercise":
            # 운동 시간에 들어가지 않는 세션도 상세에는 포함한다
            detail["sessions"] = [_session(s) for s in self._sessions(user_id, dates[0], dates[-1])]
        return detail


def _wear(
    day: dt.date,
    hours: Mapping[dt.date, int],
    heart: SyncState | None,
    linked: dt.date | None,
    synced_day: dt.date | None,
    today: dt.date,
) -> tuple[int | None, bool | None]:
    """그날의 착용 값(wear_hours, is_well_worn)을 calculation-rules.md 9.1 표의 순서대로 정한다.

    연동 함수와 같은 순서다. 심박 동기화 상태가 없거나, 심박 수집이 실패 중이고 그날이 마지막 성공 날짜와 같거나
    이후면 판단을 보류한다. 오늘·아직 안 들어옴인 날은 10시간 이상일 때만 True 이고 아니면 None 이다.
    """
    if linked is None or day < linked:
        return None, None
    if heart is None or (
        heart.last_status == "fail"
        and (heart.last_success_at is None or day >= heart.last_success_at.astimezone(_KST).date())
    ):
        return None, None
    if day == today or synced_day is None or day >= synced_day:
        count = hours.get(day)
        return count, True if count is not None and count >= _WELL_WORN_HOURS else None
    count = hours.get(day, 0)
    return count, count >= _WELL_WORN_HOURS


def _night(s: SleepSession) -> dict[str, Any]:
    """수면 세션 하나의 원형 칸. 지표 원형의 nights 와 전체 수면 세션 조회가 함께 쓴다."""
    return {
        "date": s.date.isoformat(),
        "start_at": _timestamp(s.start_at),
        "end_at": _timestamp(s.end_at),
        "sleep_minutes": s.sleep_minutes,
        "deep_minutes": s.deep_minutes,
        "rem_minutes": s.rem_minutes,
        "light_minutes": s.light_minutes,
        "awake_minutes": s.awake_minutes,
        # 합성 세션에는 DB 집계가 없으므로 동일한 분 반올림을 사용한다.
        "session_minutes": round((s.end_at - s.start_at).total_seconds() / 60),
        "unspecified_minutes": s.unspecified_minutes,
        "unknown_minutes": s.unknown_minutes,
        "avg_heart_rate": s.avg_heart_rate,
        "min_heart_rate": s.min_heart_rate,
        "max_heart_rate": s.max_heart_rate,
        "spo2_avg": s.spo2_avg,
        "spo2_min": s.spo2_min,
        "spo2_max": s.spo2_max,
        "spo2_count": s.spo2_count,
        "spo2_low_count": s.spo2_low_count,
        # 연동 쪽 원형처럼 UTC 초 단위 문자열 목록이나 null 이다
        "spo2_low_times": None if s.spo2_low_times is None else [_second(t) for t in s.spo2_low_times],
    }


def _session(s: ExerciseSession) -> dict[str, Any]:
    # 기준표에 없는 코드는 원본 코드를 두고 이름·분류만 API 명세 2.2 의 기본값으로 채운다
    return {
        "date": s.date.isoformat(),
        "start_at": _timestamp(s.start_at),
        "end_at": _timestamp(s.end_at),
        "duration_minutes": s.duration_minutes,
        "exercise_type": s.exercise_type,
        "type_name": "기타 운동" if s.type_name is None else s.type_name,
        "title": s.title,
        "category": "other" if s.category is None else s.category,
        # 칼로리·거리·평균 심박·속도는 numeric 컬럼이고 최고 심박은 integer 컬럼이다
        "calories_kcal": _decimal_number(s.calories_kcal),
        "distance_m": _decimal_number(s.distance_m),
        "avg_heart_rate": _decimal_number(s.avg_heart_rate),
        "max_heart_rate": s.max_heart_rate,
        "counts_as_exercise": s.counts_as_exercise,
        "avg_speed_mps": _decimal_number(s.avg_speed_mps),
    }


def _score_snapshot(row: ScoreDay, today: dt.date) -> dict[str, Any]:
    """저장 점수 한 행을 API 명세 5.3 응답 data로 바꾼다. 연동 함수가 응답을 만드는 규칙과 같다."""
    return {
        "date": row.date.isoformat(),
        "is_in_progress": row.date == today,
        "reason": None,
        "total": {
            "score": row.total_score,
            "missing_areas": [area for area in _SCORE_AREAS if getattr(row, f"{area}_score") is None],
        },
        "areas": {
            "activity": {
                "score": row.activity_score,
                "grade": _grade_code(row.activity_grade),
                "steps": {"score": row.steps_score, "value": row.steps},
                "exercise": {"score": row.exercise_score, "minutes": row.exercise_minutes},
            },
            "sleep": {
                "score": row.sleep_score,
                "grade": _grade_code(row.sleep_grade),
                "sleep_minutes": row.sleep_minutes,
            },
            "heart": {
                "score": row.heart_score,
                "grade": _grade_code(row.heart_grade),
                # 수면 중 심박과 평소 값은 numeric 컬럼이라 연동 함수 JSON 에서 float 이다
                "sleep_hr_avg": _decimal_number(row.sleep_hr_avg),
                "baseline_hr_avg": _decimal_number(row.hr_baseline),
                "baseline_nights": row.hr_baseline_nights,
                "check_wear": row.check_wear,
            },
        },
        "computed_at": _timestamp(row.computed_at),
    }


def _missing_score(day: dt.date, reason: ScoreMissingReason, today: dt.date) -> dict[str, Any]:
    """점수 행이 없는 날짜의 응답. 객체와 키는 그대로 두고 값은 모두 비운다 (0·false 로 채우지 않는다)."""
    return {
        "date": day.isoformat(),
        "is_in_progress": day == today,
        "reason": reason,
        "total": {"score": None, "missing_areas": list(_SCORE_AREAS)},
        "areas": {
            "activity": {
                "score": None,
                "grade": None,
                "steps": {"score": None, "value": None},
                "exercise": {"score": None, "minutes": None},
            },
            "sleep": {"score": None, "grade": None, "sleep_minutes": None},
            "heart": {
                "score": None,
                "grade": None,
                "sleep_hr_avg": None,
                "baseline_hr_avg": None,
                "baseline_nights": None,
                "check_wear": None,
            },
        },
        "computed_at": None,
    }


def _message_snapshot(row: DailyMessageDay) -> dict[str, Any]:
    """저장한 한마디를 API 명세 5.5 응답 data 에 used_metrics 를 더한 모양으로 바꾼다."""
    return {
        "date": row.date.isoformat(),
        "status": row.status,
        "code": row.code,
        "area": row.area,
        "title": row.title,
        "description": row.description,
        "data_as_of": {metric: _timestamp(row.data_as_of.get(metric)) for metric in _MESSAGE_METRICS},
        "score_computed_at": _timestamp(row.score_computed_at),
        "notices": [{"metric": metric, "code": code, "message": text} for metric, code, text in row.notices],
        "used_metrics": list(row.used_metrics),
    }


def _no_records_message(day: dt.date, states: Mapping[Metric, SyncState]) -> dict[str, Any]:
    """그날 한마디 행이 없는 회원의 응답. 연동 쪽에서 소개할 기록이 없을 때의 응답(NO_RECORDS)과 같은 모양이다."""
    return {
        "date": day.isoformat(),
        "status": "EMPTY",
        "code": "NO_RECORDS",
        "area": None,
        "title": _NO_RECORDS_TITLE,
        "description": _NO_RECORDS_DESCRIPTION,
        "data_as_of": {PUBLIC_METRICS[metric]: _timestamp(states[metric].last_data_at) for metric in _MESSAGE_INPUTS},
        "score_computed_at": None,
        "notices": [],
        "used_metrics": [],
    }


def _grade_code(grade: StoredGrade | None) -> ScoreGrade | None:
    return None if grade is None else _GRADE_CODES[grade]


def _steps_reason(steps: int | None) -> Literal["NULL_VALUE", "ZERO_STEPS"] | None:
    # 0보는 실제 기록이지만 결측으로 센다 (명세 2.2)
    if steps is None:
        return "NULL_VALUE"
    return "ZERO_STEPS" if steps == 0 else None


def _null_reason(value: float | None) -> Literal["NULL_VALUE"] | None:
    return "NULL_VALUE" if value is None else None


def _decimal_number(value: float | None) -> float | None:
    # 연동 함수는 numeric 컬럼 값을 Decimal 로 읽고 JSON number(float)로 직렬화한다
    return None if value is None else float(value)


def _average(metric: Metric, valid: Sequence[tuple[dt.date, _Source]]) -> float | None:
    """유효 날짜의 평균을 원래 정밀도로 계산해 소수 둘째 자리 ROUND_HALF_UP으로 만든다.

    심박은 하루 평균을 그날 유효 시간대 수로 가중한다 (명세 2.2). 나머지 지표는 하루 값의 단순 평균이다.
    연동 함수처럼 정수인 평균도 float 으로 반환한다.
    """
    if not valid:
        return None
    if metric == "heart_rate":
        weight = sum(src.weight for _, src in valid)
        if weight <= 0:
            raise HealthDataUnavailableError("심박 유효 시간대 수가 없다", metrics=(metric,))
        avg = sum(Decimal(str(src.value)) * src.weight for _, src in valid) / weight
    else:
        avg = sum(Decimal(str(src.value)) for _, src in valid) / len(valid)
    return float(avg.quantize(Decimal("0.01"), ROUND_HALF_UP))


def _extreme(points: Sequence[tuple[dt.date, float]], *, lowest: bool) -> dict[str, Any] | None:
    # 같은 값이 여러 날이면 가장 이른 날을 고른다
    if not points:
        return None
    pick = min(points, key=lambda p: (p[1] if lowest else -p[1], p[0]))
    return {"date": pick[0].isoformat(), "value": pick[1]}


def _timestamp(value: dt.datetime | None) -> str | None:
    """RFC 3339 UTC 문자열로 바꾼다."""
    if value is None:
        return None
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def _second(value: dt.datetime) -> str:
    """연동 쪽 spo2_low_times 직렬화와 같은 UTC 초 단위 문자열이다."""
    return value.astimezone(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
