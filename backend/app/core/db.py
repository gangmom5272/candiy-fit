"""비동기 DB 연결과 요청 단위 세션. 스키마 변경은 Alembic(alembic/)이 맡는다."""

import asyncio
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    # 세션은 짧은 작업 단위로 사용한다. WebSocket 연결 전체에 세션을 붙잡지 않는다.
    # 커밋 여부는 해당 작업을 수행하는 서비스가 결정한다.
    async with request.app.state.sessionmaker() as session:
        yield session


async def ping_db(request: Request) -> bool:
    try:
        async with asyncio.timeout(2):
            async with request.app.state.sessionmaker() as session:
                await session.execute(text("SELECT 1"))
    except Exception:
        return False
    return True
