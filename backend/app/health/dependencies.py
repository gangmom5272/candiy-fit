"""REST 건강 조회 전에 회원 존재를 확인하고 짧은 세션을 닫는다."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials

from app.auth.contract import AuthenticatedUser, AuthenticationError
from app.auth.dependencies import bearer, get_current_user
from app.auth.tokens import authenticate_token
from app.auth.web import SESSION_COOKIE
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, TemporaryStorageError
from app.core.storage import STORAGE_ERRORS, is_temporary_storage_error
from app.health.errors import HealthDataLookupError
from app.health.http import health_api_error
from app.users import service


async def get_app_connection_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticatedUser:
    """앱의 완료 보고만 받는다. 웹 쿠키와 혼합 자격은 허용하지 않는다."""
    if (
        SESSION_COOKIE in request.cookies
        or credentials is None
        or len(request.headers.getlist("authorization")) != 1
    ):
        raise ApiError(401, "AUTH_REQUIRED", "앱 로그인이 필요합니다.")
    try:
        return authenticate_token(credentials.credentials, settings)
    except AuthenticationError as error:
        raise ApiError(401, error.code, "앱 로그인이 필요합니다.") from None


async def get_health_user(
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthenticatedUser:
    try:
        async with request.app.state.sessionmaker() as db_session:
            await service.get_current_profile(db_session, user.user_id)
    except service.UserNotFoundError:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.") from None
    except TemporaryStorageError:
        raise health_api_error(HealthDataLookupError()) from None
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise health_api_error(HealthDataLookupError()) from None
        raise
    return user
