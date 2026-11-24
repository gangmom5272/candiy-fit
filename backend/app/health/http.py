"""건강 조회 내부 오류를 REST 공개 계약으로 변환한다."""

from app.core.errors import ApiError
from app.health.errors import (
    HealthDataError,
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
)
from app.health.messages import METRIC_LABELS, health_error_message


def health_api_error(error: HealthDataError) -> ApiError:
    """내부 건강 오류를 공개 REST 상태·문맥과 공유 문구로 변환한다."""
    if isinstance(error, HealthDataForbiddenError):
        return ApiError(
            403, "HEALTH_DATA_FORBIDDEN", "건강데이터 조회 권한을 확인해 주세요."
        )
    if isinstance(error, HealthDataNotLinkedError):
        return ApiError(
            409,
            "CANDIY_USER_NOT_LINKED",
            "앱 연동을 확인해 주세요. 이미 연동했다면 첫 데이터가 준비될 때까지 기다린 뒤 다시 시도해 주세요.",
        )
    if not isinstance(error, (HealthDataNotReadyError, HealthDataLookupError)):
        raise error
    metrics = list(
        dict.fromkeys(metric for metric in error.metrics if metric in METRIC_LABELS)
    )
    return ApiError(
        503,
        "HEALTH_DATA_UNAVAILABLE",
        health_error_message(error.reason, metrics),
        context={"reason": error.reason, "metrics": metrics},
    )
