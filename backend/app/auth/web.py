"""웹 인증 요청의 출처와 제공자별 콜백 검사."""

from datetime import datetime

from fastapi import Response

from app.auth.contract import IssuedWebSession
from app.auth.schemas import WebSocialLoginRequest
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.validation import invalid_field

SESSION_COOKIE = "__Host-cf_session"


def set_session_cookie(
    response: Response, session: IssuedWebSession, *, now: datetime
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session.token,
        max_age=max(0, int((session.expires_at - now).total_seconds())),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    delete_refresh_cookie(response)


def delete_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax"
    )
    delete_refresh_cookie(response)


def validate_origin(origins: list[str], settings: Settings) -> None:
    if not is_allowed_origin(origins[0] if len(origins) == 1 else None, settings):
        raise ApiError(403, "ORIGIN_NOT_ALLOWED", "요청 출처를 확인해 주세요.")


def validate_web_login(
    payload: WebSocialLoginRequest, origin: str | None, settings: Settings
) -> None:
    if not is_allowed_origin(origin, settings):
        raise ApiError(403, "ORIGIN_NOT_ALLOWED", "요청 출처를 확인해 주세요.")
    allowed = (
        settings.google_redirect_uris
        if payload.social_type == "GOOGLE"
        else settings.kakao_redirect_uris
    )
    if payload.redirect_uri not in allowed:
        raise invalid_field("redirectUri", "invalid_choice")


def is_allowed_origin(origin: str | None, settings: Settings) -> bool:
    return (
        origin is not None
        and origin != "null"
        and origin in settings.cors_allowed_origins
    )


def delete_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        "cf_refresh", path="/api/v1/auth", secure=True, httponly=True, samesite="lax"
    )
