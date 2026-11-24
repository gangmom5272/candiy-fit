"""서비스 액세스 전용. HTTP·DB·소셜 제공자 자격과 독립적이다."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt

from app.auth.contract import AuthenticatedUser, AuthenticationError, IssuedAccessToken
from app.core.config import Settings

ACCESS_LIFETIME_SECONDS = 900
ISSUER = "candiy-fit"
AUDIENCE = "candiy-fit-api"


def require_utc(value: datetime) -> None:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timezone-aware UTC datetime is required")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _uuid4(value: str) -> UUID:
    parsed = UUID(value)
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("canonical UUIDv4 required")
    return parsed


def issue_access_token(
    user_id: UUID, settings: Settings, *, now: datetime
) -> IssuedAccessToken:
    require_utc(now)
    _uuid4(str(user_id))
    key = settings.require_auth_signing_key()
    issued_at = int(now.timestamp())
    expires_at = issued_at + ACCESS_LIFETIME_SECONDS
    token = jwt.encode(
        {
            "sub": str(user_id),
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": issued_at,
            "exp": expires_at,
            "token_use": "access",
            "jti": str(uuid4()),
        },
        key,
        algorithm="HS256",
    )
    return IssuedAccessToken(
        token=token, expires_at=datetime.fromtimestamp(expires_at, UTC)
    )


def authenticate_token(token: str, settings: Settings) -> AuthenticatedUser:
    key = settings.require_auth_signing_key()
    now = _utc_now().timestamp()
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["HS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={
                "require": ["sub", "iss", "aud", "iat", "exp", "token_use", "jti"],
                # 용도·형식을 먼저 검사하고 단일 시계로 시간 오류를 분류한다.
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
                "strict_aud": True,
            },
        )
        user_id = _uuid4(claims["sub"])
        _uuid4(claims["jti"])
        issued_at, expires_at = claims["iat"], claims["exp"]
        if (
            claims["token_use"] != "access"  # noqa: S105 -- 공개 용도 식별자이며 비밀값이 아니다.
            or "nbf" in claims
            or type(issued_at) is not int
            or type(expires_at) is not int
            or expires_at - issued_at != ACCESS_LIFETIME_SECONDS
            or issued_at > now
        ):
            raise ValueError("invalid access claims")
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError):
        raise AuthenticationError("AUTH_REQUIRED") from None
    if now >= expires_at:
        raise AuthenticationError("AUTH_EXPIRED")
    return AuthenticatedUser(user_id=user_id)
