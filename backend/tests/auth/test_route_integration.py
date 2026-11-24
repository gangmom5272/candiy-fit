"""공개 라우팅·입력 모델·인증 의존성·응답 헤더를 한 요청에서 검증한다."""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, Depends

from app.auth import tokens
from app.auth.contract import AuthenticatedUser
from app.auth.dependencies import get_current_user
from app.core.routing import ApiRoute
from app.core.schemas import ApiRequest, ApiResponse, DataResponse

NOW = datetime(2031, 1, 1, tzinfo=UTC)


class SampleRequest(ApiRequest):
    sample_count: int


class SampleResponse(ApiResponse):
    user_id: UUID


@pytest.fixture
def protected_routes(app, settings, monkeypatch):
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)
    user_id = uuid4()
    called = []
    router = APIRouter(prefix="/api/v1/integration", route_class=ApiRoute)

    @router.post("", response_model=DataResponse[SampleResponse])
    async def update(
        body: SampleRequest,
        user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    ):
        called.append(user.user_id)
        return DataResponse(data=SampleResponse(user_id=user.user_id))

    @router.get("", response_model=DataResponse[SampleResponse])
    async def read(user: Annotated[AuthenticatedUser, Depends(get_current_user)]):
        called.append(user.user_id)
        return DataResponse(data=SampleResponse(user_id=user.user_id))

    app.include_router(router)
    return user_id, tokens.issue_access_token(user_id, settings, now=NOW).token, called


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_verified_identity_with_api_route(client, protected_routes, method):
    user_id, token, called = protected_routes
    response = await client.request(
        method,
        "/api/v1/integration",
        headers={"Authorization": f"Bearer {token}", "X-User-Id": str(uuid4())},
        **({"json": {"sampleCount": 1}} if method == "POST" else {}),
    )
    assert response.status_code == 200
    assert response.json() == {"data": {"userId": str(user_id)}}
    assert called == [user_id]
    assert response.headers["cache-control"] == "no-store"
    assert UUID(response.headers["x-request-id"]).version == 4


@pytest.mark.parametrize(
    ("method", "query", "raw", "field", "reason"),
    [
        (
            "POST",
            "?userId=synthetic-other",
            '{"sampleCount":1}',
            "userId",
            "extra_forbidden",
        ),
        (
            "POST",
            "",
            '{"sampleCount":1,"userId":"synthetic-other"}',
            "userId",
            "extra_forbidden",
        ),
        ("POST", "", '{"sampleCount":1,"sampleCount":2}', "sampleCount", "duplicate"),
        ("POST", "", '{"sampleCount":true}', "sampleCount", "invalid_type"),
        ("GET", "", "{}", "body", "extra_forbidden"),
    ],
)
async def test_invalid_input_never_reaches_protected_handler(
    client,
    protected_routes,
    method,
    query,
    raw,
    field,
    reason,
):
    _, token, called = protected_routes
    response = await client.request(
        method,
        "/api/v1/integration" + query,
        content=raw,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert response.json()["error"]["details"] == [{"field": field, "reason": reason}]
    assert response.headers["cache-control"] == "no-store"
    assert "x-request-id" in response.headers
    assert called == []
    assert "synthetic-other" not in response.text


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("missing", "AUTH_REQUIRED"),
        ("invalid", "AUTH_REQUIRED"),
        ("expired", "AUTH_EXPIRED"),
    ],
)
async def test_authentication_errors_with_api_route(
    client, protected_routes, monkeypatch, mode, code
):
    _, token, called = protected_routes
    headers = {}
    if mode == "expired":
        monkeypatch.setattr(tokens, "_utc_now", lambda: NOW + timedelta(minutes=15))
        headers["Authorization"] = f"Bearer {token}"
    elif mode == "invalid":
        headers["Authorization"] = "Bearer synthetic-invalid"
    response = await client.post(
        "/api/v1/integration", json={"sampleCount": 1}, headers=headers
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == code
    assert "details" not in response.json()["error"]
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    assert "x-request-id" in response.headers
    assert called == []
