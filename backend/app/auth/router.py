"""회원·인증 API(담당 개발팀). 명세는 backend/local/docs/api-spec.md."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import android, sessions, signup, social, web_sessions
from app.auth.contract import AuthenticationError
from app.auth.dependencies import get_social_provider, session_cookie
from app.auth.providers import SocialProviderClient, SocialProviderError
from app.auth.schemas import (
    AndroidLoggedInResponse,
    AndroidRefreshResponse,
    LogoutRequest,
    RefreshRequest,
    RefreshResponse,
    SignupRequest,
    SignupRequiredResponse,
    SignupResponse,
    SocialChallengeRequest,
    SocialChallengeResponse,
    SocialLoginRequest,
    SocialLoginResponse,
    WebLoggedInResponse,
    WebSessionResponse,
)
from app.auth.service import PlatformType
from app.auth.transactions import auth_transaction
from app.auth.web import (
    SESSION_COOKIE,
    delete_refresh_cookie,
    delete_session_cookie,
    is_allowed_origin,
    set_session_cookie,
    validate_origin,
    validate_web_login,
)
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.core.errors import ApiError, TemporaryStorageError, error_response
from app.core.routing import ApiRoute
from app.core.schemas import DataResponse
from app.core.storage import STORAGE_ERRORS, is_temporary_storage_error
from app.core.validation import invalid_field, invalid_request
from app.users.schemas import user_response
from app.users.service import UserNotFoundError, get_current_profile
from app.users.validation import ProfileValidationError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"], route_class=ApiRoute)


@router.post("/social/challenge", response_model=DataResponse[SocialChallengeResponse])
async def social_challenge(
    body: SocialChallengeRequest,
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> DataResponse[SocialChallengeResponse]:
    try:
        result = await android.issue_login_challenge(
            db_session, body.social_type, now=datetime.now(UTC)
        )
    except TemporaryStorageError:
        raise ApiError(
            503, "AUTH_UNAVAILABLE", "인증 처리를 일시적으로 이용할 수 없습니다."
        ) from None
    return DataResponse(
        data=SocialChallengeResponse(
            challenge_token=result.challenge_token,
            nonce=result.nonce,
            expires_at=result.expires_at,
        )
    )


@router.post("/social/login", response_model=DataResponse[SocialLoginResponse])
async def social_login(
    body: SocialLoginRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
    provider: Annotated[SocialProviderClient, Depends(get_social_provider)],
) -> DataResponse[SocialLoginResponse]:
    if body.platform_type == "WEB":
        origins = request.headers.getlist("origin")
        validate_web_login(body, origins[0] if len(origins) == 1 else None, settings)
    try:
        if body.platform_type == "ANDROID":
            result = await android.login_android(
                db_session,
                provider,
                settings,
                social_type=body.social_type,
                id_token=body.id_token,
                challenge_token=body.challenge_token,
            )
        else:
            result = await social.login_web(db_session, provider, body, settings)
    except SocialProviderError as error:
        status = 400 if error.code == "SOCIAL_CREDENTIAL_INVALID" else 503
        raise ApiError(status, error.code, "소셜 인증을 완료하지 못했습니다.") from None
    except TemporaryStorageError:
        raise ApiError(
            503, "AUTH_UNAVAILABLE", "인증 처리를 일시적으로 이용할 수 없습니다."
        ) from None
    if isinstance(result, social.SignupRequired):
        return DataResponse(
            data=SignupRequiredResponse(
                signup_token=result.signup_token,
                signup_token_expires_at=result.expires_at,
                social_type=result.social_type,
            )
        )
    if body.platform_type == "ANDROID":
        data = AndroidLoggedInResponse(
            access_token=result.session.access.token,
            access_token_expires_at=result.session.access.expires_at,
            user=user_response(result.user),
            refresh_token=result.session.refresh_token,
        )
    else:
        data = WebLoggedInResponse(
            expires_at=result.session.expires_at,
            user=user_response(result.user),
        )
        set_session_cookie(response, result.session, now=datetime.now(UTC))
    return DataResponse(data=data)


@router.post(
    "/signup",
    status_code=201,
    response_model=DataResponse[SignupResponse],
    responses={
        200: {
            "model": DataResponse[SignupResponse],
            "description": "다른 유효 자격으로 이미 가입된 회원 로그인",
        }
    },
)
async def signup_member(
    body: SignupRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
) -> DataResponse[SignupResponse]:
    origins = request.headers.getlist("origin")
    try:
        result = await signup.complete_signup(
            db_session,
            body,
            await request.body(),
            origins[0] if len(origins) == 1 else None,
            settings,
        )
    except signup.SignupTokenInvalidError:
        raise ApiError(
            401, "SIGNUP_TOKEN_INVALID", "소셜 로그인부터 다시 진행해 주세요."
        ) from None
    except signup.OriginNotAllowedError:
        raise ApiError(
            403, "ORIGIN_NOT_ALLOWED", "요청 출처를 확인해 주세요."
        ) from None
    except ProfileValidationError as error:
        raise invalid_request(error.details) from None
    except TemporaryStorageError:
        raise ApiError(
            503, "AUTH_UNAVAILABLE", "인증 처리를 일시적으로 이용할 수 없습니다."
        ) from None
    response.status_code = 201 if result.created else 200
    if result.created:
        response.headers["Location"] = "/api/v1/users/me"
    if result.platform_type == "ANDROID":
        data = AndroidLoggedInResponse(
            access_token=result.session.access.token,
            access_token_expires_at=result.session.access.expires_at,
            user=user_response(result.user),
            refresh_token=result.session.refresh_token,
        )
    else:
        data = WebLoggedInResponse(
            expires_at=result.session.expires_at,
            user=user_response(result.user),
        )
        set_session_cookie(response, result.session, now=datetime.now(UTC))
    return DataResponse(data=data)


async def _credential_platform(
    request: Request, body: RefreshRequest | None, settings: Settings
) -> PlatformType:
    has_cookie = "cf_refresh" in request.cookies or SESSION_COOKIE in request.cookies
    if has_cookie and request.headers.getlist("authorization"):
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
    if has_cookie and await request.body():
        raise invalid_field("body", "extra_forbidden")
    if body is not None:
        return "ANDROID"
    origins = request.headers.getlist("origin")
    if has_cookie or origins:
        if not is_allowed_origin(origins[0] if len(origins) == 1 else None, settings):
            raise ApiError(403, "ORIGIN_NOT_ALLOWED", "요청 출처를 확인해 주세요.")
        return "WEB"
    return "ANDROID"


def _rejected_refresh_response(platform: PlatformType) -> Response:
    response = error_response(
        ApiError(401, "REFRESH_REJECTED", "다시 로그인해 주세요.")
    )
    if platform == "WEB":
        delete_refresh_cookie(response)
    return response


def _omit_null_body_schema(schema: dict) -> None:
    # 검증용 Optional은 유지해야 422 field 경로에 Union 모델명이 붙지 않는다.
    alternatives = schema.pop("anyOf")
    schema.update(next(item for item in alternatives if item.get("type") != "null"))


@router.post("/refresh", response_model=DataResponse[RefreshResponse])
async def refresh(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
    body: Annotated[
        RefreshRequest | None, Body(json_schema_extra=_omit_null_body_schema)
    ] = None,
) -> DataResponse[RefreshResponse] | Response:
    platform = await _credential_platform(request, body, settings)
    if platform == "WEB":
        return _rejected_refresh_response(platform)
    token = body.refresh_token if body is not None else None
    if token is None:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
    try:
        result = await sessions.refresh_session(db_session, token, platform, settings)
    except sessions.RefreshRejectedError:
        return _rejected_refresh_response(platform)
    except sessions.RefreshUnavailableError as error:
        raise ApiError(
            503,
            "AUTH_UNAVAILABLE",
            "인증 처리를 일시적으로 이용할 수 없습니다.",
            retryable=error.retryable,
        ) from None
    except UserNotFoundError:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.") from None
    data = AndroidRefreshResponse(
        access_token=result.session.access.token,
        access_token_expires_at=result.session.access.expires_at,
        user=user_response(result.user),
        refresh_token=result.session.refresh_token,
    )
    return DataResponse(data=data)


@router.post("/logout", status_code=204, response_class=Response)
async def logout(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
    body: Annotated[
        LogoutRequest | None, Body(json_schema_extra=_omit_null_body_schema)
    ] = None,
) -> Response:
    platform = await _credential_platform(request, body, settings)
    if body is None and platform == "ANDROID":
        raise invalid_field("body", "missing")
    token = (
        request.cookies.get(SESSION_COOKIE) if platform == "WEB" else body.refresh_token
    )
    if token is not None:
        try:
            if platform == "WEB":
                await web_sessions.logout_web_session(db_session, token)
            else:
                await sessions.logout_session(db_session, token, platform)
        except sessions.RefreshRejectedError:
            return _rejected_refresh_response(platform)
        except TemporaryStorageError:
            raise ApiError(
                503, "AUTH_UNAVAILABLE", "인증 처리를 일시적으로 이용할 수 없습니다."
            ) from None
    response = Response(status_code=204)
    if platform == "WEB":
        delete_session_cookie(response)
    return response


@router.get("/session", response_model=DataResponse[WebSessionResponse])
async def current_web_session(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db_session: Annotated[AsyncSession, Depends(get_session)],
    token: Annotated[str | None, Depends(session_cookie)],
) -> DataResponse[WebSessionResponse]:
    if not token or request.headers.getlist("authorization"):
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.")
    origins = request.headers.getlist("origin")
    if origins:
        validate_origin(origins, settings)
    try:
        session = await web_sessions.authenticate_web_session(db_session, token)
        async with auth_transaction(db_session):
            user = await get_current_profile(db_session, session.user_id)
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise ApiError(
                503, "AUTH_UNAVAILABLE", "인증 처리를 일시적으로 이용할 수 없습니다."
            ) from None
        raise
    except AuthenticationError as error:
        raise ApiError(401, error.code, "로그인이 필요합니다.") from None
    except UserNotFoundError:
        raise ApiError(401, "AUTH_REQUIRED", "로그인이 필요합니다.") from None
    except TemporaryStorageError:
        raise ApiError(
            503, "AUTH_UNAVAILABLE", "인증 처리를 일시적으로 이용할 수 없습니다."
        ) from None
    return DataResponse(
        data=WebSessionResponse(user=user_response(user), expires_at=session.expires_at)
    )
