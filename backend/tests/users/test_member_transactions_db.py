"""419: 실제 회원 DB 잠금·쿼리 취소·풀 대기와 트랜잭션 소유권 회귀."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.tokens import issue_access_token
from app.core.db import create_sessionmaker
from app.users import repository, service, transactions
from app.users.models import User

pytestmark = pytest.mark.db
SETTINGS = text(
    "SELECT current_setting('statement_timeout'), current_setting('lock_timeout')"
)
CASES = [
    ("GET", "/api/v1/users/me", "find_user", "USER_UNAVAILABLE"),
    ("GET", "/api/v1/health/data-status", "find_user", "HEALTH_DATA_UNAVAILABLE"),
    ("PATCH", "/api/v1/users/me", "update_user", "USER_UNAVAILABLE"),
    (
        "PUT",
        "/api/v1/health/app-connection",
        "complete_app_connection",
        "APP_CONNECTION_UNAVAILABLE",
    ),
]


@pytest.fixture
async def member_api(user_database, app, client, settings):
    observer_factory, members = user_database
    engine = create_async_engine(
        observer_factory.kw["bind"].url, pool_size=1, max_overflow=0
    )
    factory = create_sessionmaker(engine)
    app.state.sessionmaker = factory
    headers = {
        "Authorization": "Bearer "
        + issue_access_token(members[0].id, settings, now=datetime.now(UTC)).token
    }

    async def request(case):
        method, path, _, _ = case
        kwargs = {"headers": headers}
        if method == "PATCH":
            kwargs["json"] = {"displayName": "변경합성"}
        return await client.request(method, path, **kwargs)

    try:
        async with factory() as db:
            original = (await db.execute(SETTINGS)).one()
        yield factory, observer_factory, members[0], engine, original, request
    finally:
        await engine.dispose()


def assert_unavailable(response, case):
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == case[3]
    if case[3] == "HEALTH_DATA_UNAVAILABLE":
        assert error["context"] == {"reason": "LOOKUP_FAILED", "metrics": []}
    if case[0] == "PUT":
        assert error["retryable"] is True
    assert response.headers["cache-control"] == "no-store"
    assert "x-request-id" in response.headers
    assert "pg_sleep" not in response.text


async def assert_released(api):
    factory, _, member, engine, original, _ = api
    assert engine.pool.checkedout() == 0
    async with asyncio.timeout(3), factory() as db, db.begin():
        assert (await db.execute(SETTINGS)).one() == original
        saved = await db.scalar(
            select(User).where(User.id == member.id).with_for_update(nowait=True)
        )
        assert saved.display_name == member.display_name
        assert saved.app_connection_status == "NOT_CONNECTED"
        assert saved.app_connected_at is None
    assert engine.pool.checkedout() == 0


async def wait_for_sleep(observer_factory, backend_pid):
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


@pytest.mark.parametrize("case", CASES, ids=["profile", "health", "patch", "connect"])
async def test_real_lock_timeout_maps_to_public_error_and_can_retry(member_api, case):
    _, observer_factory, member, _, _, request = member_api
    async with observer_factory() as blocker, blocker.begin():
        if case[0] == "GET":
            await blocker.execute(text("LOCK TABLE users IN ACCESS EXCLUSIVE MODE"))
        else:
            await blocker.scalar(
                select(User).where(User.id == member.id).with_for_update()
            )
        async with asyncio.timeout(4):
            response = await request(case)
        assert_unavailable(response, case)
    await assert_released(member_api)
    assert (await request(case)).status_code == 200


@pytest.mark.parametrize("case", CASES, ids=["profile", "health", "patch", "connect"])
@pytest.mark.parametrize("interruption", ["statement", "deadline", "caller_cancel"])
async def test_running_query_interruption_rolls_back_and_reuses_pool(
    member_api, monkeypatch, case, interruption
):
    _, observer_factory, _, _, _, request = member_api
    monkeypatch.setattr(
        transactions,
        "MEMBER_STATEMENT_TIMEOUT_MS",
        50 if interruption == "statement" else 0,
    )
    monkeypatch.setattr(
        transactions,
        "MEMBER_DB_TIMEOUT_SECONDS",
        1 if interruption == "deadline" else 5,
    )
    original = getattr(repository, case[2])
    backend_pid = asyncio.get_running_loop().create_future()

    async def delayed(db, *args, **kwargs):
        result = await original(db, *args, **kwargs)
        backend_pid.set_result(await db.scalar(text("SELECT pg_backend_pid()")))
        # PATCH/PUT은 실제 UPDATE 이후 중단하여 저장 롤백도 확인한다.
        await db.execute(text("SELECT pg_sleep(10)"))
        return result

    monkeypatch.setattr(repository, case[2], delayed)
    task = asyncio.create_task(request(case))
    try:
        if interruption != "statement":
            await wait_for_sleep(observer_factory, backend_pid)
        if interruption == "caller_cancel":
            task.cancel()
        done, _ = await asyncio.wait({task}, timeout=4)
        assert task in done, "interruption did not finish DB cleanup"
        if interruption == "caller_cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert_unavailable(await task, case)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await assert_released(member_api)
    monkeypatch.setattr(repository, case[2], original)
    assert (await request(case)).status_code == 200


@pytest.mark.parametrize("case", CASES, ids=["profile", "health", "patch", "connect"])
@pytest.mark.parametrize("interruption", ["deadline", "caller_cancel"])
async def test_pool_wait_interruption_leaves_no_waiter(
    member_api, monkeypatch, case, interruption
):
    _, _, _, engine, _, request = member_api
    monkeypatch.setattr(
        transactions,
        "MEMBER_DB_TIMEOUT_SECONDS",
        0.1 if interruption == "deadline" else 5,
    )
    entered = asyncio.Event()
    original = transactions.bounded_transaction

    @asynccontextmanager
    async def observed(*args, **kwargs):
        entered.set()
        async with original(*args, **kwargs):
            yield

    monkeypatch.setattr(transactions, "bounded_transaction", observed)
    # 실제 풀을 가득 채우고 요청이 경계에 진입한 뒤 취소한다.
    async with engine.connect() as holder:
        task = asyncio.create_task(request(case))
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            if interruption == "caller_cancel":
                task.cancel()
            done, _ = await asyncio.wait({task}, timeout=3)
            assert task in done
            if interruption == "caller_cancel":
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert_unavailable(await task, case)
            assert engine.pool.checkedout() == 1
            assert await holder.scalar(text("SELECT 1")) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    await assert_released(member_api)
    assert (await request(case)).status_code == 200


async def test_profile_read_preserves_caller_transaction_and_local_limits(
    user_database,
):
    factory, members = user_database
    async with factory() as db, db.begin():
        await db.execute(text("SET LOCAL statement_timeout = '250ms'"))
        await db.execute(text("SET LOCAL lock_timeout = '125ms'"))
        await db.execute(
            update(User).where(User.id == members[0].id).values(display_name="변경합성")
        )
        transaction = db.get_transaction()
        result = await service.get_current_profile(db, members[0].id)
        assert result.display_name == "변경합성"
        assert db.get_transaction() is transaction
        assert (await db.execute(SETTINGS)).one() == ("250ms", "125ms")
        await db.rollback()
    async with factory() as db:
        assert (
            await db.scalar(select(User.display_name).where(User.id == members[0].id))
            == members[0].display_name
        )


async def test_standalone_profile_read_closes_owned_transaction(user_database):
    factory, members = user_database
    async with factory() as db:
        result = await service.get_current_profile(db, members[0].id)
        assert result.id == members[0].id
        assert not db.in_transaction()
