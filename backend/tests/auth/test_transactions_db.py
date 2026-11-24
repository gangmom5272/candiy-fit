"""414: 전용 PostgreSQL에서 잠금 대기·롤백·풀 반환과 공개 오류를 검증한다."""

import asyncio

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import transactions
from app.auth.models import AuthSession
from app.auth.web import SESSION_COOKIE
from app.users.models import User
from tests.auth.test_refresh_db import ORIGIN, seed, snapshot
from tests.auth.test_refresh_db import db_api as db_api
from tests.auth.test_refresh_db import refresh_db as refresh_db

pytestmark = pytest.mark.db


@pytest.fixture
async def single_connection_database(social_database):
    observer_factory, members, _ = social_database
    engine = create_async_engine(
        observer_factory.kw["bind"].url, pool_size=1, max_overflow=0
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # 최초 연결 비용이 짧은 취소 기한을 소모하지 않도록 미리 연결한다.
        async with factory() as db:
            await db.execute(text("SELECT 1"))
        yield factory, members, engine, observer_factory
    finally:
        await engine.dispose()


@pytest.mark.parametrize("operation", ["refresh", "android_logout", "web_logout"])
async def test_locked_session_fails_without_consumption_then_can_retry(
    refresh_db, db_api, settings, operation
):
    factory, members, _ = refresh_db
    platform = "WEB" if operation == "web_logout" else "ANDROID"
    issued = await seed(factory, members[0], settings, platform)

    async def request():
        if platform == "WEB":
            db_api.cookies.set(SESSION_COOKIE, issued.token)
            return await db_api.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
        return await db_api.post(
            "/api/v1/auth/" + ("refresh" if operation == "refresh" else "logout"),
            json={"refreshToken": issued.refresh_token},
        )

    async with factory() as blocker, blocker.begin():
        await blocker.scalar(
            select(AuthSession)
            .where(AuthSession.id == issued.session_id)
            .with_for_update()
        )
        response = await request()
        assert response.status_code == 503
        error = response.json()["error"]
        assert error["code"] == "AUTH_UNAVAILABLE"
        if operation == "refresh":
            assert error["retryable"] is True
        assert response.headers["cache-control"] == "no-store"
        session, tokens = await snapshot(factory, issued.session_id)
        assert session.revoked_at is None
        assert all(token.used_at is None for token in tokens)

    response = await request()
    assert response.status_code == (200 if operation == "refresh" else 204)


async def test_statement_timeout_rolls_back_write_and_resets_pool_settings(
    social_database, monkeypatch
):
    factory, members, _ = social_database
    monkeypatch.setattr(transactions, "AUTH_STATEMENT_TIMEOUT_MS", 30)
    query = text(
        "SELECT current_setting('statement_timeout'), current_setting('lock_timeout')"
    )
    async with factory() as db:
        original = (await db.execute(query)).one()
    with pytest.raises(TimeoutError):
        async with factory() as db, transactions.auth_transaction(db):
            await db.execute(
                update(User)
                .where(User.id == members[0].id)
                .values(display_name="변경합성")
            )
            await db.execute(text("SELECT pg_sleep(1)"))
    async with factory() as db:
        assert (await db.execute(query)).one() == original
        assert (
            await db.scalar(select(User.display_name).where(User.id == members[0].id))
            == "합성회원"
        )


@pytest.mark.parametrize("interruption", ["deadline", "caller_cancel"])
async def test_python_interruption_rolls_back_and_releases_connection(
    single_connection_database, monkeypatch, interruption
):
    factory, members, engine, observer_factory = single_connection_database
    monkeypatch.setattr(
        transactions, "AUTH_DB_TIMEOUT_SECONDS", 1 if interruption == "deadline" else 5
    )
    # PostgreSQL이 먼저 중단하면 Python 취소 경로를 검증한 것으로 볼 수 없다.
    monkeypatch.setattr(transactions, "AUTH_STATEMENT_TIMEOUT_MS", 0)
    settings_query = text(
        "SELECT current_setting('statement_timeout'), current_setting('lock_timeout')"
    )
    async with factory() as db:
        original_settings = (await db.execute(settings_query)).one()
    backend_pid = asyncio.get_running_loop().create_future()

    async def write_then_wait():
        async with factory() as db, transactions.auth_transaction(db):
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            await db.execute(
                update(User)
                .where(User.id == members[0].id)
                .values(display_name="변경합성")
            )
            backend_pid.set_result(pid)
            await db.execute(text("SELECT pg_sleep(10)"))

    task = asyncio.create_task(write_then_wait())
    try:
        # 임의 sleep 대신 서버가 실제 쿼리를 실행 중인지 독립 연결로 확인한다.
        async with asyncio.timeout(3):
            pid = await backend_pid
            async with observer_factory() as observer:
                while not await observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE pid=:pid AND wait_event='PgSleep')"
                    ),
                    {"pid": pid},
                ):
                    await observer.rollback()
                    await asyncio.sleep(0.01)
        if interruption == "caller_cancel":
            task.cancel()
        done, _ = await asyncio.wait({task}, timeout=4)
        assert task in done, "Python interruption did not finish DB cleanup"
        expected = (
            TimeoutError if interruption == "deadline" else asyncio.CancelledError
        )
        with pytest.raises(expected):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert engine.pool.checkedout() == 0
    async with asyncio.timeout(3), factory() as db, db.begin():
        assert (await db.execute(settings_query)).one() == original_settings
        user = await db.scalar(
            select(User).where(User.id == members[0].id).with_for_update(nowait=True)
        )
        assert user.display_name == "합성회원"
    assert engine.pool.checkedout() == 0


async def test_pool_acquisition_timeout_leaves_no_waiter_or_connection_leak(
    single_connection_database, monkeypatch
):
    factory, _, engine, _ = single_connection_database
    monkeypatch.setattr(transactions, "AUTH_DB_TIMEOUT_SECONDS", 0.1)

    async def acquire():
        async with factory() as db, transactions.auth_transaction(db):
            pytest.fail("acquired a connection while the only slot was held")

    async with engine.connect() as holder:
        task = asyncio.create_task(acquire())
        try:
            done, _ = await asyncio.wait({task}, timeout=3)
            assert task in done, "pool acquisition ignored the Python deadline"
            with pytest.raises(TimeoutError):
                await task
            assert engine.pool.checkedout() == 1
            assert await holder.scalar(text("SELECT 1")) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert engine.pool.checkedout() == 0
    async with asyncio.timeout(3), factory() as db:
        assert await db.scalar(text("SELECT 1")) == 1
    assert engine.pool.checkedout() == 0
