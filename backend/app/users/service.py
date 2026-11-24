"""본인 프로필 유스케이스. 저장소 장애와 회원 부재를 구분한다."""

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import TemporaryStorageError
from app.core.storage import STORAGE_ERRORS, is_temporary_storage_error
from app.users import repository
from app.users.models import User
from app.users.schemas import AppConnection, app_connection
from app.users.transactions import member_transaction
from app.users.validation import ProfileChanges


class UserNotFoundError(Exception):
    """검증된 자격의 회원이 비정상적으로 존재하지 않는다."""


@dataclass(frozen=True, slots=True, repr=False)
class BodyProfile:
    """사용자가 등록한 현재 값과 서버에서 계산한 BMI. 측정 이력이 아니다."""

    height_cm: Decimal
    weight_kg: Decimal
    bmi: Decimal


async def get_body_profile(db_session: AsyncSession, user_id: UUID) -> BodyProfile:
    """인증된 본인의 프로필을 읽고 BMI를 소수 첫째 자리 사사오입으로 반환한다."""
    user = await get_current_profile(db_session, user_id)
    # 호출자의 Decimal 정밀도 설정이 BMI 계산에 영향을 주지 않게 고정한다.
    with localcontext() as context:
        context.prec = 28
        bmi = (user.weight_kg / (user.height_cm / 100) ** 2).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
    return BodyProfile(user.height_cm, user.weight_kg, bmi)


async def get_app_connection(db_session: AsyncSession, user_id: UUID) -> AppConnection:
    return app_connection(await get_current_profile(db_session, user_id))


async def complete_app_connection(
    db_session: AsyncSession, user_id: UUID
) -> AppConnection:
    """응답 전 커밋한다. 결과 불명확 실패도 같은 본인의 PUT으로 안전하게 재시도한다."""
    try:
        async with member_transaction(db_session):
            user = await repository.complete_app_connection(db_session, user_id)
            if user is None:
                raise UserNotFoundError
            result = app_connection(user)
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise TemporaryStorageError("storage_connection_unavailable") from None
        raise
    return result


async def get_current_profile(db_session: AsyncSession, user_id: UUID) -> User:
    # 인증·건강·코칭이 이미 소유한 트랜잭션의 기한과 커밋 책임을 유지한다.
    transaction = (
        nullcontext() if db_session.in_transaction() else member_transaction(db_session)
    )
    try:
        async with transaction:
            user = await repository.find_user(db_session, user_id)
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise TemporaryStorageError("storage_connection_unavailable") from None
        raise
    if user is None:
        raise UserNotFoundError
    return user


async def update_current_profile(
    db_session: AsyncSession, user_id: UUID, changes: ProfileChanges, *, now: datetime
) -> User:
    """값 검증 완료 후 새 요청 세션으로 호출한다. 반환 전에 커밋을 완료한다."""
    try:
        async with member_transaction(db_session):
            user = await repository.update_user(db_session, user_id, changes, now=now)
            if user is None:
                raise UserNotFoundError
    except STORAGE_ERRORS as error:
        if is_temporary_storage_error(error):
            raise TemporaryStorageError("storage_connection_unavailable") from None
        raise
    return user
