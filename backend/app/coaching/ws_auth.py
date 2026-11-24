"""코칭 WS의 WEB 세션 쿠키 인증. REST 어댑터(app.auth.dependencies)와 같은 규칙을 쓴다."""

from fastapi import WebSocket

from app.auth.contract import AuthenticatedUser, AuthenticationError
from app.auth.web import SESSION_COOKIE, is_allowed_origin
from app.auth.web_sessions import authenticate_web_session
from app.core.config import Settings
from app.core.errors import TemporaryStorageError

__all__ = [
    "SESSION_COOKIE",
    "WebSessionUnavailableError",
    "authenticate_web_cookie",
    "has_web_cookie",
    "is_web_origin_allowed",
]


class WebSessionUnavailableError(Exception):
    """세션 검증 중 확인된 일시적 저장소 장애."""


def has_web_cookie(websocket: WebSocket) -> bool:
    """핸드셰이크에 __Host-cf_session 키가 있으면 True. 값이 비어 있어도 True."""
    return SESSION_COOKIE in websocket.cookies


def is_web_origin_allowed(websocket: WebSocket, settings: Settings) -> bool:
    """Origin 헤더가 정확히 하나이고 허용 목록에 있을 때만 True. 없음·null·중복·불허는 False."""
    origins = websocket.headers.getlist("origin")
    return len(origins) == 1 and is_allowed_origin(origins[0], settings)


async def authenticate_web_cookie(websocket: WebSocket) -> AuthenticatedUser:
    """쿠키 값으로 웹 세션을 검증하고 회원을 돌려준다.

    호출마다 새 DB 세션을 열고 반환 전에 닫는다. 거절(무효·폐기·만료)은 모두
    AuthenticationError("AUTH_REQUIRED"), 일시 저장소 장애는 WebSessionUnavailableError를
    던진다. 그 밖의 예외는 그대로 올린다.
    """
    token = websocket.cookies.get(SESSION_COOKIE, "")
    try:
        async with websocket.app.state.sessionmaker() as db:
            session = await authenticate_web_session(db, token)
    except AuthenticationError:
        raise AuthenticationError("AUTH_REQUIRED") from None
    except TemporaryStorageError:
        raise WebSessionUnavailableError from None
    return AuthenticatedUser(session.user_id)
