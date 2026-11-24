"""REST 인증 어댑터. 회원 존재·소유권 검사는 각 유스케이스가 수행한다."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer

from app.auth.contract import AuthenticatedUser, AuthenticationError
from app.auth.providers import SocialProviderClient
from app.auth.tokens import authenticate_token
from app.auth.web import SESSION_COOKIE, validate_origin
from app.auth.web_sessions import authenticate_web_session
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, TemporaryStorageError

bearer = HTTPBearer(auto_error=False)
session_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)


def get_social_provider(request: Request) -> SocialProviderClient:
    return request.app.state.social_provider


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    cookie: Annotated[str | None, Depends(session_cookie)],
) -> AuthenticatedUser:
    if SESSION_COOKIE in request.cookies:
        if request.headers.getlist("authorization"):
            raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            validate_origin(request.headers.getlist("origin"), settings)
        try:
            # 업무용 get_session과 분리해 읽기 트랜잭션이 쓰기 트랜잭션을 선점하지 않는다.
            async with request.app.state.sessionmaker() as db:
                session = await authenticate_web_session(db, cookie or "")
                return AuthenticatedUser(session.user_id)
        except AuthenticationError as error:
            raise ApiError(401, error.code, "로그인이 필요합니다.") from None
        except TemporaryStorageError:
            raise ApiError(
                503, "AUTH_UNAVAILABLE", "인증 처리를 일시적으로 이용할 수 없습니다."
            ) from None
    if credentials is None or len(request.headers.getlist("authorization")) != 1:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
    try:
        return authenticate_token(credentials.credentials, settings)
    except AuthenticationError as error:
        raise ApiError(401, error.code, "로그인이 필요합니다.") from None
