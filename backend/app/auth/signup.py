"""가입 자격 소비·회원 생성·최초 발급을 하나의 트랜잭션으로 처리한다."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import repository
from app.auth.models import AuthState
from app.auth.schemas import SignupRequest
from app.auth.service import PlatformType, create_login_session
from app.auth.social import LoggedIn, hash_signup_token
from app.auth.transactions import auth_transaction
from app.auth.web import is_allowed_origin
from app.core.config import Settings
from app.core.errors import TemporaryStorageError
from app.core.storage import STORAGE_ERRORS, is_temporary_storage_error
from app.users.validation import validate_profile


class SignupTokenInvalidError(Exception):
    """가입 자격이 없거나 만료·소비되었거나 용도가 다르다."""


class OriginNotAllowedError(Exception):
    """WEB 가입 자격의 요청 출처가 허용되지 않는다."""


@dataclass(frozen=True, slots=True)
class SignupResult(LoggedIn):
    created: bool
    platform_type: PlatformType


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_state(state: AuthState | None, now: datetime) -> AuthState:
    if (
        state is None
        or state.purpose != "SIGNUP"
        or state.platform_type not in {"WEB", "ANDROID"}
        or not state.social_user_id
        or state.consumed_at is not None
        or not state.created_at <= now < state.expires_at
    ):
        raise SignupTokenInvalidError
    return state


async def complete_signup(
    db_session: AsyncSession,
    body: SignupRequest,
    raw: bytes,
    origin: str | None,
    settings: Settings,
) -> SignupResult:
    """형식 검사된 모델과 같은 요청 원문을 받는다. 커밋 성공 후에만 반환한다."""
    token_hash = hash_signup_token(body.signup_token)
    try:
        async with auth_transaction(db_session):
            now = _utc_now()
            state = _validate_state(
                await repository.find_auth_state(db_session, token_hash), now
            )
            if state.platform_type == "WEB" and not is_allowed_origin(origin, settings):
                raise OriginNotAllowedError
            profile = validate_profile(body, raw, now=now)
            state = await repository.find_auth_state(db_session, token_hash, lock=True)
            now = _utc_now()
            state = _validate_state(state, now)
            state.consumed_at = now
            await db_session.flush()
            user = await repository.insert_social_user_if_absent(
                db_session, state.social_type, state.social_user_id, profile
            )
            created = user is not None
            if user is None:
                user = await repository.find_social_user(
                    db_session, state.social_type, state.social_user_id
                )
                if user is None:
                    raise RuntimeError("conflicting_member_missing")
            session = await create_login_session(
                db_session, user.id, state.platform_type, settings, now=_utc_now()
            )
            result = SignupResult(user, session, created, state.platform_type)
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise TemporaryStorageError("storage_connection_unavailable") from None
        raise
    return result
