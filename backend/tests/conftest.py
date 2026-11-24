"""기본 테스트는 합성 값과 ASGI 전송을 사용한다. db 테스트는 별도 명시한 테스트 DB만 쓴다."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.db import ping_db
from app.main import create_app


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "APP_ENV",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "CORS_ALLOWED_ORIGINS",
        "AUTH_SIGNING_KEY",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_ANDROID_CLIENT_IDS",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URIS",
        "KAKAO_REST_API_KEY",
        "KAKAO_NATIVE_APP_KEY",
        "KAKAO_CLIENT_SECRET",
        "KAKAO_APP_ID",
        "KAKAO_REDIRECT_URIS",
        "PIPELINE_ENABLED",
        "PIPELINE_INCLUDE_MEMBERS",
        "PIPELINE_INCLUDE_SAMPLE_APP",
        "CANDIY_BASE_URL",
        "CANDIY_CLIENT_ID",
        "CANDIY_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="synthetic_test",
        postgres_user="synthetic_user",
        postgres_password="synthetic-password",
        cors_allowed_origins=["http://localhost:5173"],
        auth_signing_key="synthetic-auth-key-for-tests-only-32bytes",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    application = create_app(settings)

    async def healthy_db() -> bool:
        return True

    application.dependency_overrides[ping_db] = healthy_db
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client
