"""호출자가 공개 오류로 명시적으로 변환할 수 있는 내부 예외."""

from typing import ClassVar

from app.health.schemas import HealthMetric, HealthReason


class InvalidHealthData(ValueError):
    """집계 필수 계약 위반. 원본 값은 예외 메시지에 포함하지 않는다."""


class UnknownExerciseTypeError(ValueError):
    """기준표에 없는 비교 요청 코드. 데이터 조회 실패와 구분하는 입력 오류다."""

    code = "UNKNOWN_EXERCISE_TYPE"

    def __init__(self) -> None:
        super().__init__(self.code)


class SleepSessionLimitError(ValueError):
    """전체 수면 조회의 건수 초과. 호출자는 기간을 줄이며 조회 장애와 구분한다."""

    code = "SLEEP_SESSION_LIMIT_EXCEEDED"

    def __init__(self) -> None:
        super().__init__(self.code)


class Vo2MaxRecordLimitError(ValueError):
    """VO₂max 기록 수 초과. 기간 축소가 필요한 경우이며 조회 장애와 구분한다."""

    code = "VO2_MAX_RECORD_LIMIT_EXCEEDED"

    def __init__(self) -> None:
        super().__init__(self.code)


class MeasurementRecordLimitError(ValueError):
    """개별 측정 건수 초과. 하루도 초과하면 기간 축소로 해결되지 않는다."""

    code = "MEASUREMENT_RECORD_LIMIT_EXCEEDED"

    def __init__(self) -> None:
        super().__init__(self.code)


class StepRecordLimitError(ValueError):
    """걸음 원본 목록의 한도 초과."""

    code = "STEP_RECORD_LIMIT_EXCEEDED"

    def __init__(self) -> None:
        super().__init__(self.code)


class StepHistoryLimitError(ValueError):
    """선택 기간에 저장된 걸음 버전 수의 한도 초과."""

    code = "STEP_HISTORY_LIMIT_EXCEEDED"

    def __init__(self) -> None:
        super().__init__(self.code)


class StepRecordNotFoundError(ValueError):
    """없는 걸음 원본과 다른 회원의 걸음 원본을 구분하지 않는다."""

    code = "STEP_RECORD_NOT_FOUND"

    def __init__(self) -> None:
        super().__init__(self.code)


class ExerciseRecordNotFoundError(ValueError):
    """본인에게 보이는 운동이 없음. 다른 회원의 운동 존재를 노출하지 않는다."""

    code = "EXERCISE_RECORD_NOT_FOUND"

    def __init__(self) -> None:
        super().__init__(self.code)


class ExerciseSampleLimitError(ValueError):
    """원본 조회 예산 초과. 시간 분할로 해결된다고 보장하지 않는다."""

    code = "EXERCISE_SAMPLE_LIMIT_EXCEEDED"

    def __init__(self) -> None:
        super().__init__(self.code)


class HealthDataError(Exception):
    """공개 코드와 건강 조회 사유·지표를 보관하는 내부 오류다."""

    code: ClassVar[str]
    reason: ClassVar[HealthReason | None] = None

    def __init__(self, metrics: tuple[HealthMetric, ...] = ()) -> None:
        self.metrics = metrics
        super().__init__(self.code)


class HealthDataNotReadyError(HealthDataError):
    """필요한 건강 지표의 최초 집계가 아직 준비되지 않은 오류다."""

    code = "HEALTH_DATA_UNAVAILABLE"
    reason: ClassVar[HealthReason] = "DATA_NOT_READY"


class HealthDataLookupError(HealthDataError):
    """건강 지표나 준비 상태를 조회하지 못한 오류다."""

    code = "HEALTH_DATA_UNAVAILABLE"
    reason: ClassVar[HealthReason] = "LOOKUP_FAILED"


class HealthDataNotLinkedError(HealthDataError):
    """회원의 저장된 동기화 상태가 모두 http_404인 경우."""

    code = "CANDIY_USER_NOT_LINKED"


class HealthDataForbiddenError(HealthDataError):
    code = "HEALTH_DATA_FORBIDDEN"
