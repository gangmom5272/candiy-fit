"""오늘의 한마디 선택. 조회·정리까지 완료한 입력만 받으며 I/O를 수행하지 않는다."""

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from app.health.errors import HealthDataLookupError, HealthDataNotReadyError
from app.health.schemas import HealthSchema, MissingReason, UtcDatetime

DailyMessageMetric = Literal["steps", "exercises", "sleeps"]
DailyMessageArea = Literal["activity", "sleep"]
DailyMessageStatus = Literal["READY", "PARTIAL", "EMPTY", "WAITING"]
DailyMessageNoticeCode = Literal["LOOKUP_FAILED", "DATA_NOT_READY", "SYNC_FAILED"]
DailyMessageCode = Literal[
    "BALANCED_RECORDS",
    "ACTIVE_DAY",
    "EXERCISE_MILESTONE",
    "STEPS_MILESTONE",
    "SLEEP_REFLECTION",
    "EXERCISE_PROGRESS",
    "STEPS_PROGRESS",
    "SLEEP_RECORDED",
    "DETAIL_RECORDS",
    "NO_RECORDS",
    "PENDING_RECORDS",
]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]


class MetricSuccess(HealthSchema):
    """has_detail: 정상 주 수면 세션 또는 미인정 운동 세션의 존재."""

    state: Literal["SUCCESS"] = "SUCCESS"
    value: NonnegativeInt | None
    missing: StrictBool
    reason: MissingReason | None
    has_stored_value: StrictBool
    has_detail: StrictBool
    as_of: UtcDatetime | None
    sync_failed: StrictBool | None

    @model_validator(mode="after")
    def validate_facts(self) -> "MetricSuccess":
        if self.missing != (self.reason is not None):
            raise ValueError("missing and reason must agree")
        if not self.missing and self.value is None:
            raise ValueError("nonmissing metric requires a value")
        if self.value is not None and not self.has_stored_value:
            raise ValueError("value requires stored value presence")
        if (
            self.value is None
            and self.has_stored_value
            and self.reason != "BEFORE_LINK"
        ):
            raise ValueError("only BEFORE_LINK may suppress an existing stored value")
        return self


class MetricFailure(HealthSchema):
    state: Literal["DATA_NOT_READY", "LOOKUP_FAILED"]
    sync_failed: StrictBool | None


MetricOutcome = Annotated[MetricSuccess | MetricFailure, Field(discriminator="state")]


class UsableSleepScore(HealthSchema):
    """호출자가 본인·날짜·수면 시간 대응을 검증한 저장 점수."""

    state: Literal["USABLE"] = "USABLE"
    value: Annotated[int, Field(strict=True, ge=0, le=100)]
    computed_at: UtcDatetime


class SleepScoreUnavailable(HealthSchema):
    state: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: Literal[
        "NOT_QUERIED",
        "NO_ROW",
        "NULL_SCORE",
        "MISMATCH",
        "INVALID_VALUE",
        "LOOKUP_FAILED",
    ]


class DailyMessageInput(HealthSchema):
    user_id: UUID
    date: date
    steps: MetricOutcome
    exercises: MetricOutcome
    sleeps: MetricOutcome
    sleep_score: Annotated[
        UsableSleepScore | SleepScoreUnavailable, Field(discriminator="state")
    ]

    @model_validator(mode="after")
    def validate_metric_facts(self) -> "DailyMessageInput":
        for metric in ("steps", "exercises", "sleeps"):
            outcome = getattr(self, metric)
            if not isinstance(outcome, MetricSuccess):
                continue
            if (
                outcome.missing
                and outcome.value is not None
                and (
                    metric != "steps"
                    or outcome.value != 0
                    or outcome.reason not in ("BEFORE_LINK", "UNSYNCED", "ZERO_STEPS")
                )
            ):
                raise ValueError("only missing zero steps may retain a value")
            if outcome.reason == "ZERO_STEPS" and (
                metric != "steps" or outcome.value != 0
            ):
                raise ValueError("ZERO_STEPS requires zero steps")
            if metric == "steps" and (
                outcome.has_detail or (outcome.value == 0 and not outcome.missing)
            ):
                raise ValueError("steps has no detail and zero steps must be missing")
            if metric == "sleeps" and not outcome.missing and not outcome.has_detail:
                raise ValueError("valid sleep time requires a main sleep session")
        if (
            isinstance(self.sleep_score, UsableSleepScore)
            and _valid_value(self.sleeps) is None
        ):
            raise ValueError("usable score requires valid sleep time")
        return self


class DailyMessageContent(HealthSchema):
    code: DailyMessageCode
    area: DailyMessageArea | None
    title: str
    description: str
    used_metrics: tuple[DailyMessageMetric, ...]


class DailyMessageDataAsOf(HealthSchema):
    steps: UtcDatetime | None
    exercises: UtcDatetime | None
    sleeps: UtcDatetime | None


class DailyMessageNotice(HealthSchema):
    metric: DailyMessageMetric
    code: DailyMessageNoticeCode
    message: str


class DailyMessageResult(DailyMessageContent):
    user_id: UUID
    date: date
    status: DailyMessageStatus
    data_as_of: DailyMessageDataAsOf
    score_computed_at: UtcDatetime | None
    notices: tuple[DailyMessageNotice, ...]


_COPY: dict[DailyMessageCode, tuple[DailyMessageArea | None, str, str]] = {
    "BALANCED_RECORDS": (
        None,
        "오늘은 나에게 칭찬 한마디 건네볼까요?",
        "수면과 활동 기록이 함께 쌓였어요. 오늘 남긴 작은 성취들을 돌아보세요.",
    ),
    "ACTIVE_DAY": (
        "activity",
        "차곡차곡 쌓은 움직임, 뿌듯해해도 좋아요",
        "운동과 걸음 기록에 오늘의 움직임이 담겼어요.",
    ),
    "EXERCISE_MILESTONE": (
        "activity",
        "운동한 나에게 작은 칭찬을 보내주세요",
        "오늘 운동 기록을 돌아보며, 좋았던 순간 하나를 떠올려볼까요?",
    ),
    "STEPS_MILESTONE": (
        "activity",
        "한 걸음씩 쌓아온 오늘, 잘하셨어요",
        "오늘 기록이 7,000보를 채웠어요. 더 채우기보다 지금까지의 걸음도 칭찬해 주세요.",
    ),
    "SLEEP_REFLECTION": (
        "sleep",
        "잠깐, 오늘의 내 컨디션을 살펴볼까요?",
        "수면 기록과 함께, 깨어났을 때의 느낌도 돌아보세요.",
    ),
    "EXERCISE_PROGRESS": (
        "activity",
        "오늘의 움직임에도 박수를 보내요",
        "시간을 더 채워야 한다는 부담 없이, 남겨진 운동 기록을 돌아보세요.",
    ),
    "STEPS_PROGRESS": (
        "activity",
        "한 걸음씩, 내 페이스대로 가볼까요?",
        "오늘 남겨진 걸음도 소중해요. 움직이고 싶은 때에 편한 만큼 이어가 보세요.",
    ),
    "SLEEP_RECORDED": (
        "sleep",
        "오늘 잠은 어땠나요?",
        "기록된 시간과 내가 느낀 잠은 다를 수 있어요. 오늘의 느낌도 함께 살펴보세요.",
    ),
    "DETAIL_RECORDS": (
        None,
        "숫자로 다 담기지 않은 기록도 살펴보세요",
        "남겨진 기록을 보며 오늘 하루를 가볍게 돌아볼까요?",
    ),
}


def _content(
    code: DailyMessageCode, used: tuple[DailyMessageMetric, ...]
) -> DailyMessageContent:
    area, title, description = _COPY[code]
    return DailyMessageContent(
        code=code, area=area, title=title, description=description, used_metrics=used
    )


def _valid_value(outcome: MetricOutcome) -> int | None:
    return (
        outcome.value
        if isinstance(outcome, MetricSuccess) and not outcome.missing
        else None
    )


def select_candidate(facts: DailyMessageInput) -> DailyMessageContent | None:
    """명세 1~9순위 중 처음 성립하는 문구와 실제 근거 지표를 반환한다."""
    steps = _valid_value(facts.steps)
    exercise = _valid_value(facts.exercises)
    sleep = _valid_value(facts.sleeps)
    positive_sleep = (
        sleep is not None
        and isinstance(facts.sleep_score, UsableSleepScore)
        and facts.sleep_score.value >= 80
    )
    steps_milestone = steps is not None and steps >= 7000
    exercise_milestone = exercise is not None and exercise >= 30
    if positive_sleep and exercise_milestone and steps_milestone:
        return _content("BALANCED_RECORDS", ("steps", "exercises", "sleeps"))
    if exercise_milestone and steps_milestone:
        return _content("ACTIVE_DAY", ("steps", "exercises"))
    if exercise_milestone:
        return _content("EXERCISE_MILESTONE", ("exercises",))
    if steps_milestone:
        return _content("STEPS_MILESTONE", ("steps",))
    if positive_sleep:
        return _content("SLEEP_REFLECTION", ("sleeps",))
    if exercise is not None and exercise > 0:
        return _content("EXERCISE_PROGRESS", ("exercises",))
    if steps is not None and steps > 0:
        return _content("STEPS_PROGRESS", ("steps",))
    if sleep is not None:
        return _content("SLEEP_RECORDED", ("sleeps",))
    details = tuple(
        metric
        for metric in ("exercises", "sleeps")
        if isinstance(getattr(facts, metric), MetricSuccess)
        and getattr(facts, metric).has_detail
    )
    return _content("DETAIL_RECORDS", details) if details else None


def build_daily_message(facts: DailyMessageInput) -> DailyMessageResult:
    """후보 없는 실패는 기존 내부 오류로 전파한다. 전체 오류는 입력 대상이 아니다."""
    outcomes = {
        "steps": facts.steps,
        "exercises": facts.exercises,
        "sleeps": facts.sleeps,
    }
    failed = tuple(
        metric
        for metric, outcome in outcomes.items()
        if isinstance(outcome, MetricFailure)
    )
    content = select_candidate(facts)
    if content is not None:
        status = "PARTIAL" if failed else "READY"
    elif failed:
        if any(outcome.state == "LOOKUP_FAILED" for outcome in outcomes.values()):
            raise HealthDataLookupError(metrics=failed)
        raise HealthDataNotReadyError(metrics=failed)
    else:
        status, content = _empty_content(tuple(outcomes.values()))

    notices = []
    labels = {"steps": "걸음", "exercises": "운동", "sleeps": "수면"}
    for metric, outcome in outcomes.items():
        if isinstance(outcome, MetricFailure):
            message = (
                f"{labels[metric]} 기록은 불러오지 못했어요."
                if outcome.state == "LOOKUP_FAILED"
                else f"{labels[metric]} 기록은 아직 준비 중이에요. 잠시 후 다시 확인해 주세요."
            )
            notices.append(
                DailyMessageNotice(metric=metric, code=outcome.state, message=message)
            )
        if outcome.sync_failed is True:
            message = (
                "최근 동기화에 실패해 이전에 저장된 기록을 보여 드려요."
                if metric in content.used_metrics
                else "최근 동기화에 실패했어요. 앱의 동기화 상태를 확인해 주세요."
            )
            notices.append(
                DailyMessageNotice(metric=metric, code="SYNC_FAILED", message=message)
            )

    return DailyMessageResult(
        **content.model_dump(),
        user_id=facts.user_id,
        date=facts.date,
        status=status,
        data_as_of=DailyMessageDataAsOf(
            **{
                metric: outcome.as_of if isinstance(outcome, MetricSuccess) else None
                for metric, outcome in outcomes.items()
            }
        ),
        score_computed_at=(
            facts.sleep_score.computed_at
            if content.code in ("BALANCED_RECORDS", "SLEEP_REFLECTION")
            and isinstance(facts.sleep_score, UsableSleepScore)
            else None
        ),
        notices=tuple(notices),
    )


def _empty_content(
    outcomes: tuple[MetricSuccess, ...],
) -> tuple[DailyMessageStatus, DailyMessageContent]:
    if any(outcome.reason == "UNSYNCED" for outcome in outcomes):
        return "WAITING", DailyMessageContent(
            code="PENDING_RECORDS",
            area=None,
            used_metrics=(),
            title="오늘의 기록을 아직 확인하지 못했어요",
            description="괜찮다면 앱의 동기화 상태를 확인해 주세요.",
        )
    stored = any(outcome.has_stored_value for outcome in outcomes)
    return "EMPTY", DailyMessageContent(
        code="NO_RECORDS",
        area=None,
        used_metrics=(),
        title="오늘은 내 하루를 가볍게 돌아볼까요?"
        if stored
        else "오늘은 기록 없이도 내 하루를 돌아볼까요?",
        description="오늘의 한마디로 소개할 활동·수면 기록이 아직 없어요."
        if stored
        else "지금 확인할 수 있는 활동·수면 기록은 없어요.",
    )
