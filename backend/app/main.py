"""앱 팩토리. uvicorn app.main:create_app --factory로 실행한다."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.auth.providers import SocialProviderClient
from app.auth.router import router as auth_router
from app.coaching.lifecycle import coaching_lifespan
from app.coaching.router import router as coaching_router
from app.core.config import Settings, get_settings
from app.core.db import create_engine, create_sessionmaker, ping_db
from app.core.errors import ApiError, handle_api_error, handle_http_error
from app.core.http import HttpContractMiddleware, SafeErrorMiddleware
from app.core.validation import handle_validation_error
from app.health.diagnostics import health_logging
from app.health.router import router as health_router
from app.health.sync_service import MemberSyncRunner
from app.users.router import router as users_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else get_settings()
    settings.require_auth_signing_key()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """앱 자원을 준비하고 코칭·HTTP 클라이언트·DB 엔진 순서로 정리한다."""
        with health_logging():
            engine = create_engine(settings)
            app.state.engine = engine
            try:
                app.state.sessionmaker = create_sessionmaker(engine)
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(5, connect=3),
                    follow_redirects=False,
                    trust_env=False,
                ) as oauth_http:
                    app.state.social_provider = SocialProviderClient(
                        settings, oauth_http
                    )
                    async with coaching_lifespan(
                        app, settings, app.state.sessionmaker, engine=app.state.engine
                    ):
                        yield
            finally:
                try:
                    await app.state.member_sync.aclose()
                finally:
                    await engine.dispose()

    app = FastAPI(
        title="CANDiY-FiT API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.app_env == "prod" else "/docs",
        redoc_url=None if settings.app_env == "prod" else "/redoc",
        openapi_url=None if settings.app_env == "prod" else "/openapi.json",
        openapi_tags=[
            {"name": "auth", "description": "회원·인증 API, 담당 개발팀"},
            {"name": "users", "description": "본인 정보 조회·수정 API, 담당 개발팀"},
            {"name": "coaching", "description": "코칭 대화 API, 담당 개발팀"},
            {
                "name": "health",
                "description": "건강데이터 조회·연동 상태 API, 담당 개발팀",
            },
        ],
    )
    app.state.settings = settings
    app.state.member_sync = MemberSyncRunner(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(HTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_middleware(SafeErrorMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(HttpContractMiddleware)
    app.include_router(auth_router)
    app.include_router(coaching_router)
    app.include_router(health_router)
    app.include_router(users_router)

    @app.get("/healthz")
    async def healthz(db_ok: bool = Depends(ping_db)) -> JSONResponse:
        if not db_ok:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "DB_UNAVAILABLE",
                        "message": "데이터베이스에 연결할 수 없습니다.",
                    }
                },
            )
        return JSONResponse(content={"data": {"status": "ok", "db": "ok"}})

    return app
