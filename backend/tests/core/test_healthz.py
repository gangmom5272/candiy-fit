from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from httpx import AsyncClient

from app.core.db import ping_db


async def test_healthz_ok(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ok", "db": "ok"}}


async def test_healthz_unavailable(app: FastAPI, client: AsyncClient) -> None:
    async def unavailable_db() -> bool:
        return False

    app.dependency_overrides[ping_db] = unavailable_db
    response = await client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "DB_UNAVAILABLE",
            "message": "데이터베이스에 연결할 수 없습니다.",
        }
    }


async def test_ping_db_hides_connection_error(
    app: FastAPI, client: AsyncClient
) -> None:
    session = AsyncMock()
    session.execute.side_effect = RuntimeError("SECRET-DATABASE-ERROR")
    context = AsyncMock()
    context.__aenter__.return_value = session
    app.state.sessionmaker = MagicMock(return_value=context)
    del app.dependency_overrides[ping_db]

    response = await client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DB_UNAVAILABLE"
    assert "SECRET-DATABASE-ERROR" not in response.text
    context.__aexit__.assert_awaited_once()


async def test_ping_db_runs_select_one(app: FastAPI) -> None:
    session = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = session
    app.state.sessionmaker = MagicMock(return_value=context)
    request = Request({"type": "http", "app": app})
    assert await ping_db(request) is True
    assert str(session.execute.call_args.args[0]) == "SELECT 1"
    context.__aexit__.assert_awaited_once()


async def test_lifespan_creates_and_disposes_engine(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    sessionmaker = MagicMock()
    make_engine = MagicMock(return_value=engine)
    make_sessionmaker = MagicMock(return_value=sessionmaker)
    monkeypatch.setattr("app.main.create_engine", make_engine)
    monkeypatch.setattr("app.main.create_sessionmaker", make_sessionmaker)

    async with app.router.lifespan_context(app):
        assert app.state.engine is engine
        assert app.state.sessionmaker is sessionmaker
        make_engine.assert_called_once_with(app.state.settings)
        make_sessionmaker.assert_called_once_with(engine)
        engine.dispose.assert_not_awaited()
    engine.dispose.assert_awaited_once()


async def test_openapi_has_domain_tags_users_and_healthz(client: AsyncClient) -> None:
    """OpenAPI의 도메인 태그와 공개 경로 집합이 계약과 같다."""
    response = await client.get("/openapi.json")
    schema = response.json()
    assert schema["info"] == {"title": "CANDiY-FiT API", "version": "0.1.0"}
    assert schema["tags"] == [
        {"name": "auth", "description": "회원·인증 API, 담당 개발팀"},
        {"name": "users", "description": "본인 정보 조회·수정 API, 담당 개발팀"},
        {"name": "coaching", "description": "코칭 대화 API, 담당 개발팀"},
        {"name": "health", "description": "건강데이터 조회·연동 상태 API, 담당 개발팀"},
    ]
    assert set(schema["paths"]) == {
        "/api/v1/auth/session",
        "/healthz",
        "/api/v1/users/me",
        "/api/v1/health/metrics/{metric}",
        "/api/v1/health/data-status",
        "/api/v1/health/app-connection",
        "/api/v1/health/sync",
        "/api/v1/health/lifestyle-score",
        "/api/v1/health/timeline",
        "/api/v1/health/daily-message",
        "/api/v1/coaching/requests/{requestId}",
        "/api/v1/coaching/conversations",
        "/api/v1/coaching/conversations/{conversationId}/messages",
        "/api/v1/auth/social/login",
        "/api/v1/auth/social/challenge",
        "/api/v1/auth/signup",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
    }
    login = schema["paths"]["/api/v1/auth/social/login"]["post"]
    assert login["tags"] == ["auth"]
    assert "security" not in login
    assert (
        "refreshToken"
        not in schema["components"]["schemas"]["WebLoggedInResponse"]["properties"]
    )
    assert schema["paths"]["/api/v1/users/me"]["get"]["tags"] == ["users"]
    assert schema["paths"]["/api/v1/health/metrics/{metric}"]["get"]["tags"] == [
        "health"
    ]
    assert schema["paths"]["/api/v1/coaching/requests/{requestId}"]["get"]["tags"] == [
        "coaching"
    ]
    assert "tags" not in schema["paths"]["/healthz"]["get"]
    assert schema["paths"]["/api/v1/coaching/conversations/{conversationId}/messages"][
        "get"
    ]["tags"] == ["coaching"]
    assert schema["paths"]["/api/v1/coaching/conversations"]["get"]["tags"] == [
        "coaching"
    ]


async def test_cors_allows_configured_origin(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"Origin": "http://localhost:5173"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
