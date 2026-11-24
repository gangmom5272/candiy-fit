"""건강 REST와 코칭 WS가 공유하는 공개 오류 문구를 만든다."""

from collections.abc import Sequence

from app.health.schemas import HealthMetric, HealthReason

METRIC_LABELS: dict[HealthMetric, str] = {
    "steps": "걸음",
    "heart_rates": "심박",
    "oxygen_saturations": "산소포화도",
    "sleeps": "수면",
    "exercises": "운동",
    "weights": "체중",
    "body_fats": "체지방률",
    "basal_metabolic_rates": "기초대사량",
    "heights": "키",
}


def health_error_message(
    reason: HealthReason, metrics: Sequence[HealthMetric] = ()
) -> str:
    """검증된 사유·공개 지표로 문구를 만들며 중복 지표는 한 종류로 센다."""
    if reason == "LOOKUP_FAILED":
        return "건강데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요."
    if reason == "DATA_NOT_READY":
        unique_metrics = tuple(dict.fromkeys(metrics))
        label = (
            f"{METRIC_LABELS[unique_metrics[0]]} 데이터"
            if len(unique_metrics) == 1
            else "건강데이터"
        )
        return f"{label}를 준비 중입니다. 잠시 후 다시 시도해 주세요."
    raise ValueError("unknown health failure reason")
