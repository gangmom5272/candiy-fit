"""회원 저장 접근. 전달받은 세션의 트랜잭션은 호출자가 소유한다."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import User
from app.users.validation import ProfileChanges


async def find_user(db_session: AsyncSession, user_id: UUID) -> User | None:
    return await db_session.get(User, user_id)


async def complete_app_connection(
    db_session: AsyncSession, user_id: UUID
) -> User | None:
    # 조건부 UPDATE가 동시 보고를 직렬화한다. 재보고는 최초 시각을 변경하지 않는다.
    statement = (
        update(User)
        .where(User.id == user_id, User.app_connection_status != "CONNECTED")
        .values(
            app_connection_status="CONNECTED",
            app_connected_at=func.clock_timestamp(),
            updated_at=func.greatest(User.updated_at, func.clock_timestamp()),
        )
        .returning(User)
        .execution_options(populate_existing=True)
    )
    user = (await db_session.scalars(statement)).one_or_none()
    if user is None:
        user = await db_session.get(User, user_id, populate_existing=True)
    return user


async def update_user(
    db_session: AsyncSession, user_id: UUID, changes: ProfileChanges, *, now: datetime
) -> User | None:
    statement = (
        update(User)
        .where(User.id == user_id)
        .values(**changes, updated_at=func.greatest(User.updated_at, now))
        .returning(User)
        .execution_options(populate_existing=True)
    )
    return (await db_session.scalars(statement)).one_or_none()
