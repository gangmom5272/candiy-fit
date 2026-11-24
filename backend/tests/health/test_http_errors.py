import json

import pytest

from app.core.errors import ApiError, error_response
from app.core.schemas import ErrorDetail
from app.health.errors import (
    HealthDataForbiddenError,
    HealthDataLookupError,
    HealthDataNotLinkedError,
    HealthDataNotReadyError,
)
from app.health.http import health_api_error


@pytest.mark.parametrize(
    "error,status,code,context",
    [
        (
            HealthDataNotReadyError(("sleeps",)),
            503,
            "HEALTH_DATA_UNAVAILABLE",
            {"reason": "DATA_NOT_READY", "metrics": ["sleeps"]},
        ),
        (
            HealthDataLookupError(),
            503,
            "HEALTH_DATA_UNAVAILABLE",
            {"reason": "LOOKUP_FAILED", "metrics": []},
        ),
        (HealthDataForbiddenError(("steps",)), 403, "HEALTH_DATA_FORBIDDEN", None),
        (HealthDataNotLinkedError(), 409, "CANDIY_USER_NOT_LINKED", None),
    ],
)
def test_health_error_mapping(error, status, code, context):
    response = error_response(health_api_error(error))
    payload = json.loads(response.body)["error"]
    assert response.status_code == status
    assert payload["code"] == code
    assert payload.get("context") == context
    assert set(payload) == (
        {"code", "message", "context"} if context else {"code", "message"}
    )


@pytest.mark.parametrize(
    "metric", ["steps", "heart_rates", "oxygen_saturations", "sleeps", "exercises"]
)
def test_health_error_preserves_public_metric_codes(metric):
    payload = json.loads(
        error_response(health_api_error(HealthDataLookupError((metric,)))).body
    )
    assert payload["error"]["context"] == {
        "reason": "LOOKUP_FAILED",
        "metrics": [metric],
    }


@pytest.mark.parametrize(
    ("error_type", "reason", "message"),
    [
        (
            HealthDataNotReadyError,
            "DATA_NOT_READY",
            "걸음 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요.",
        ),
        (
            HealthDataLookupError,
            "LOOKUP_FAILED",
            "건강데이터를 조회하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        ),
    ],
)
def test_health_error_filters_internal_metric_names_without_exposing_input(
    error_type, reason, message
):
    """REST는 미지원 지표를 숨기고 중복 제거한 공개 지표로 문구와 문맥을 만든다."""
    error = error_type(("exercise_sessions", "synthetic-secret", "steps", "steps"))
    response = error_response(health_api_error(error))
    assert json.loads(response.body)["error"] == {
        "code": "HEALTH_DATA_UNAVAILABLE",
        "message": message,
        "context": {"reason": reason, "metrics": ["steps"]},
    }
    assert b"synthetic-secret" not in response.body


def test_not_ready_uses_spec_message():
    error = health_api_error(HealthDataNotReadyError(("sleeps",)))
    assert error.message == "수면 데이터를 준비 중입니다. 잠시 후 다시 시도해 주세요."


def test_unknown_error_is_not_hidden_as_lookup_failure():
    error = RuntimeError("synthetic-secret")
    with pytest.raises(RuntimeError) as raised:
        health_api_error(error)
    assert raised.value is error


@pytest.mark.parametrize("retryable", [True, False])
def test_existing_error_shape_keeps_retryable_and_omits_context(retryable):
    error = ApiError(503, "AUTH_TEMPORARILY_UNAVAILABLE", "safe", retryable=retryable)
    assert json.loads(error_response(error).body) == {
        "error": {
            "code": "AUTH_TEMPORARILY_UNAVAILABLE",
            "message": "safe",
            "retryable": retryable,
        }
    }
    validation = ApiError(
        422,
        "INVALID_REQUEST",
        "safe",
        details=[ErrorDetail(field="start", reason="missing")],
    )
    assert json.loads(error_response(validation).body)["error"] == {
        "code": "INVALID_REQUEST",
        "message": "safe",
        "details": [{"field": "start", "reason": "missing"}],
    }


async def test_context_through_existing_http_handler(app, client):
    @app.get("/api/v1/test-health-error")
    async def unavailable():
        raise health_api_error(HealthDataNotReadyError(("exercises",)))

    response = await client.get("/api/v1/test-health-error")
    assert response.status_code == 503
    assert response.json()["error"]["context"] == {
        "reason": "DATA_NOT_READY",
        "metrics": ["exercises"],
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
