import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.db import ping_db
from app.main import create_app


@pytest.mark.parametrize(
    ("app_env", "expected_status"),
    [("local", 200), ("test", 200), ("dev", 200), ("prod", 404)],
)
@pytest.mark.parametrize(
    "path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"]
)
async def test_documentation_access_by_environment(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    app_env: str,
    expected_status: int,
    path: str,
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    application = create_app(
        Settings(_env_file=None, auth_signing_key=settings.auth_signing_key)
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get(path)

    assert response.status_code == expected_status
    if expected_status == 404:
        assert response.json()["error"]["code"] == "NOT_FOUND"
    elif path == "/openapi.json":
        assert "/api/v1/auth/social/login" in response.json()["paths"]
    else:
        assert response.headers["content-type"].startswith("text/html")


async def test_production_keeps_health_and_protected_api_routes(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    application = create_app(
        Settings(_env_file=None, auth_signing_key=settings.auth_signing_key)
    )

    async def healthy_db() -> bool:
        return True

    application.dependency_overrides[ping_db] = healthy_db
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        health = await client.get("/healthz")
        protected = await client.get("/api/v1/users/me")

    assert health.status_code == 200
    assert health.json() == {"data": {"status": "ok", "db": "ok"}}
    assert protected.status_code == 401
    assert protected.json()["error"]["code"] == "AUTH_REQUIRED"
