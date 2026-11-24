import asyncio
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from httpx import AsyncClient

from app.auth.service import hash_refresh_token
from app.core.errors import ApiError
from app.core.http import HttpContractMiddleware, SafeErrorMiddleware
from app.core.schemas import ApiResponse, DataResponse, ErrorDetail
from app.users.schemas import user_response


class MemberResponse(ApiResponse):
    display_name: str
    health_snapshot: dict[str, int]


async def test_response_accepts_internal_names_and_emits_public_aliases(app, client):
    @app.get("/api/v1/member", response_model=DataResponse[MemberResponse])
    async def member():
        return DataResponse(
            data=MemberResponse(
                display_name="합성회원", health_snapshot={"step_count": 100}
            )
        )

    response = await client.get("/api/v1/member")
    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "displayName": "합성회원",
            "healthSnapshot": {"step_count": 100},
        }
    }


@pytest.fixture
def contract_routes(app: FastAPI):
    @app.get("/api/v1/test")
    async def success(request: Request):
        return DataResponse(data={"requestId": request.state.request_id, "some_key": 1})

    @app.delete("/api/v1/test", status_code=204)
    async def empty():
        return Response(status_code=204)

    @app.get("/api/v1/unauthorized")
    async def unauthorized():
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")

    @app.get("/api/v1/broken")
    async def broken():
        raise RuntimeError("synthetic-secret SQL token")

    @app.get("/api/v1/framework")
    async def framework():
        raise HTTPException(404, "synthetic-secret")


async def test_success_headers_and_empty_body(client: AsyncClient, contract_routes):
    response = await client.get("/api/v1/test", headers={"X-Request-Id": "untrusted"})
    request_id = response.headers["x-request-id"]
    assert UUID(request_id).version == 4
    assert response.json() == {"data": {"requestId": request_id, "some_key": 1}}
    assert response.headers["cache-control"] == "no-store"
    other = await client.delete("/api/v1/test")
    assert other.status_code == 204
    assert other.content == b""
    assert other.headers["x-request-id"] != request_id
    health = await client.get("/healthz")
    assert health.json() == {"data": {"status": "ok", "db": "ok"}}
    assert "x-request-id" in health.headers


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("missing", 404, "NOT_FOUND"),
        ("unauthorized", 401, "AUTH_REQUIRED"),
        ("broken", 500, "INTERNAL_ERROR"),
        ("framework", 404, "NOT_FOUND"),
    ],
)
async def test_safe_errors(client, contract_routes, caplog, path, status, code):
    response = await client.get(f"/api/v1/{path}")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "details" not in response.json()["error"]
    assert response.headers["cache-control"] == "no-store"
    assert "x-request-id" in response.headers
    assert "synthetic-secret" not in response.text + caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"


async def test_unexpected_errors_identify_project_locations_without_values(
    app, client, caplog
):
    @app.get("/api/v1/review-profile")
    async def broken_profile():
        return user_response(None)

    @app.get("/api/v1/review-token")
    async def broken_token():
        return hash_refresh_token(None)

    for path, source in (
        ("review-profile", "app/users/schemas.py"),
        ("review-token", "app/auth/service.py"),
    ):
        caplog.clear()
        response = await client.get(
            "/api/v1/" + path + "?private=synthetic-query-secret",
            headers={"Authorization": "Bearer synthetic-token-secret"},
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        record = next(r for r in caplog.records if r.name == "app.core.http")
        assert f"error_kind=AttributeError source={source}:" in record.getMessage()
        assert response.headers["x-request-id"] in record.getMessage()
        assert record.exc_info is None
        assert "synthetic-query-secret" not in caplog.text
        assert "synthetic-token-secret" not in caplog.text
        assert "NoneType" not in caplog.text


async def test_dynamic_exception_names_causes_and_external_frames_are_not_logged(
    app, client, caplog
):
    private = "synthetic-private-name-SQL-health-data"

    @app.get("/api/v1/review-private")
    async def broken():
        raise type(private, (Exception,), {})(private) from ValueError(private)

    response = await client.get("/api/v1/review-private")
    assert response.status_code == 500
    record = next(r for r in caplog.records if r.name == "app.core.http")
    assert "error_kind=unexpected source=unknown" in record.getMessage()
    assert private not in response.text + caplog.text
    assert "test_http_contract" not in record.getMessage()
    assert record.exc_info is None


async def test_method_allow_and_cors(client, contract_routes):
    response = await client.post(
        "/api/v1/test", headers={"Origin": "http://localhost:5173"}
    )
    assert response.status_code == 405
    assert response.headers["allow"]
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "X-Request-Id" in response.headers["access-control-expose-headers"]


@pytest.mark.parametrize("path", ["test", "unauthorized", "broken", "missing"])
async def test_cors_on_success_and_errors(client, contract_routes, path):
    response = await client.get(
        f"/api/v1/{path}", headers={"Origin": "http://localhost:5173"}
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in response.headers["vary"]
    assert "X-Request-Id" in response.headers["access-control-expose-headers"]
    assert response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "origin,allowed",
    [("http://localhost:5173", True), ("https://denied.example", False)],
)
async def test_preflight_keeps_common_headers(client, origin, allowed):
    response = await client.options(
        "/api/v1/auth/social/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,authorization",
        },
    )
    assert response.status_code == (200 if allowed else 400)
    assert ("access-control-allow-origin" in response.headers) is allowed
    assert response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"


def test_details_only_on_422():
    with pytest.raises(ValueError):
        ApiError(
            500,
            "INTERNAL_ERROR",
            "safe",
            details=[ErrorDetail(field="body", reason="invalid_json")],
        )


async def test_cancellation_propagates():
    async def cancelled(scope, receive, send):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await HttpContractMiddleware(SafeErrorMiddleware(cancelled))(
            {"type": "http"}, None, None
        )


async def test_middleware_preserves_websocket_scope():
    seen = []

    async def endpoint(scope, receive, send):
        seen.append(scope)

    scope = {"type": "websocket"}
    await HttpContractMiddleware(SafeErrorMiddleware(endpoint))(scope, None, None)
    assert seen == [{"type": "websocket"}]
