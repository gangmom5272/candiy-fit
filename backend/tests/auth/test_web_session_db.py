import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.auth import web_sessions
from app.auth.contract import AuthenticationError, IssuedWebSession
from app.auth.models import AuthRefreshToken, AuthSession
from app.auth.service import create_login_session, hash_refresh_token
from app.auth.web import SESSION_COOKIE
from app.coaching.models import CoachingConversation
from app.core.db import get_session
from app.health.models import SyncState

pytestmark = pytest.mark.db
NOW = datetime(2031, 1, 1, tzinfo=UTC)


@pytest.fixture
async def web_session(social_database, settings, monkeypatch):
    factory, members, _ = social_database
    monkeypatch.setattr(web_sessions, "_utc_now", lambda: NOW + timedelta(days=1))
    async with factory.begin() as db:
        issued = await create_login_session(db, members[0].id, "WEB", settings, now=NOW)
    assert isinstance(issued, IssuedWebSession)
    return factory, members[0], issued


async def test_repeated_concurrent_reads_do_not_rotate_or_extend(web_session):
    factory, member, issued = web_session

    async def read():
        async with factory() as db:
            session = await web_sessions.authenticate_web_session(db, issued.token)
            assert session.user_id == member.id
            assert session.expires_at == NOW + timedelta(days=7)
            assert session.last_refreshed_at == NOW
            assert session.web_token_hash == hash_refresh_token(issued.token)
            return session.id

    for _ in range(20):
        assert await read() == issued.session_id
    assert set(await asyncio.gather(*(read() for _ in range(10)))) == {
        issued.session_id
    }
    async with factory() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(AuthRefreshToken)
                .where(AuthRefreshToken.session_id == issued.session_id)
            )
            == 0
        )


async def test_logout_commit_rejects_copied_cookie_even_if_response_lost(web_session):
    factory, _, issued = web_session
    async with factory() as db:
        await web_sessions.logout_web_session(db, issued.token)
    for _ in range(2):
        async with factory() as db:
            with pytest.raises(AuthenticationError, match="AUTH_REQUIRED"):
                await web_sessions.authenticate_web_session(db, issued.token)
        async with factory() as db:
            await web_sessions.logout_web_session(db, issued.token)
    async with factory() as db:
        saved = await db.get(AuthSession, issued.session_id)
        assert saved.revoked_at == NOW + timedelta(days=1)


async def test_expiry_does_not_extend(web_session, monkeypatch):
    factory, _, issued = web_session
    monkeypatch.setattr(web_sessions, "_utc_now", lambda: issued.expires_at)
    async with factory() as db:
        with pytest.raises(AuthenticationError, match="AUTH_EXPIRED"):
            await web_sessions.authenticate_web_session(db, issued.token)


@pytest.fixture
async def web_api(web_session, app):
    factory, _, issued = web_session
    app.state.sessionmaker = factory

    async def db_session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = db_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test"
    ) as client:
        client.cookies.set(SESSION_COOKIE, issued.token)
        yield client


async def test_http_reloads_then_replayed_logout_cookie(web_api, web_session):
    _, member, issued = web_session
    for _ in range(20):
        response = await web_api.get("/api/v1/auth/session")
        assert response.status_code == 200
        assert response.json()["data"]["user"]["id"] == str(member.id)
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
    # 직전 조회 응답을 사용하지 않아도 쿠키 자체는 그대로 유효하다.
    response = await web_api.get("/api/v1/users/me")
    assert response.status_code == 200
    response = await web_api.post(
        "/api/v1/auth/logout", headers={"Origin": "http://localhost:5173"}
    )
    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    for path in ("/api/v1/auth/session", "/api/v1/users/me"):
        response = await web_api.get(
            path, headers={"Cookie": f"{SESSION_COOKIE}={issued.token}"}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("origin", [None, "null", "https://evil.example"])
async def test_cookie_mutation_rejects_untrusted_origin(web_api, origin):
    headers = {} if origin is None else {"Origin": origin}
    for path, method, body in (
        ("/api/v1/auth/logout", "POST", None),
        ("/api/v1/users/me", "PATCH", {"displayName": "수정"}),
    ):
        response = await web_api.request(
            method, path, headers=headers, **({"json": body} if body else {})
        )
        assert response.status_code == 403
    assert (await web_api.get("/api/v1/auth/session")).status_code == 200


async def test_cookie_mutation_has_independent_auth_transaction(web_api):
    response = await web_api.patch(
        "/api/v1/users/me",
        headers={"Origin": "http://localhost:5173"},
        json={"displayName": "변경회원"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["displayName"] == "변경회원"


async def test_cookie_never_falls_back_to_bearer(web_api):
    for path in ("/api/v1/auth/session", "/api/v1/users/me"):
        response = await web_api.get(
            path, headers={"Authorization": "Bearer synthetic"}
        )
        assert response.status_code == 401


async def test_web_credential_cannot_refresh_as_android(web_api, web_session):
    _, _, issued = web_session
    web_api.cookies.clear()
    response = await web_api.post(
        "/api/v1/auth/refresh", json={"refreshToken": issued.token}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "REFRESH_REJECTED"


async def test_old_web_refresh_cannot_rotate_new_session(web_api):
    response = await web_api.post(
        "/api/v1/auth/refresh", headers={"Origin": "http://localhost:5173"}
    )
    assert response.status_code == 401
    assert all(
        SESSION_COOKIE not in value for value in response.headers.get_list("set-cookie")
    )
    assert (await web_api.get("/api/v1/auth/session")).status_code == 200


async def test_expired_cookie_is_rejected_by_both_http_paths(
    web_api, web_session, monkeypatch
):
    _, _, issued = web_session
    monkeypatch.setattr(web_sessions, "_utc_now", lambda: issued.expires_at)
    for path in ("/api/v1/auth/session", "/api/v1/users/me"):
        response = await web_api.get(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_EXPIRED"
        assert "set-cookie" not in response.headers


@pytest.mark.parametrize("operation", ["read", "logout"])
async def test_storage_outage_preserves_cookie_and_session(
    web_api, web_session, monkeypatch, operation
):
    from app.auth import repository

    async def unavailable(*args, **kwargs):
        raise ConnectionError("synthetic-unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(repository, "find_web_session", unavailable)
        response = await (
            web_api.get("/api/v1/auth/session")
            if operation == "read"
            else web_api.post(
                "/api/v1/auth/logout", headers={"Origin": "http://localhost:5173"}
            )
        )
    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    assert (await web_api.get("/api/v1/auth/session")).status_code == 200


async def test_logout_commit_ack_lost_still_blocks_cookie(web_api, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSessionTransaction

    original = AsyncSessionTransaction.__aexit__

    async def lose_ack(transaction, exc_type, exc, tb):
        await original(transaction, exc_type, exc, tb)
        if exc_type is None:
            raise ConnectionError("synthetic-commit-ack-lost")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSessionTransaction, "__aexit__", lose_ack)
        response = await web_api.post(
            "/api/v1/auth/logout", headers={"Origin": "http://localhost:5173"}
        )
    assert response.status_code == 503
    assert "set-cookie" not in response.headers
    assert (await web_api.get("/api/v1/auth/session")).status_code == 401
    assert (await web_api.get("/api/v1/users/me")).status_code == 401
    response = await web_api.post(
        "/api/v1/auth/logout", headers={"Origin": "http://localhost:5173"}
    )
    assert response.status_code == 204


async def test_concurrent_logout_preserves_other_web_and_android_sessions(
    web_session, settings
):
    from app.auth import sessions

    factory, member, issued = web_session
    async with factory.begin() as db:
        other = await create_login_session(
            db, member.id, "WEB", settings, now=web_sessions._utc_now()
        )
        android = await create_login_session(
            db, member.id, "ANDROID", settings, now=web_sessions._utc_now()
        )

    async def logout():
        async with factory() as db:
            await web_sessions.logout_web_session(db, issued.token)

    await asyncio.gather(logout(), logout())
    async with factory() as db:
        await web_sessions.logout_web_session(db, "unknown-credential")
    async with factory() as db:
        assert (
            await web_sessions.authenticate_web_session(db, other.token)
        ).id == other.session_id
        saved_android = await db.get(AuthSession, android.session_id)
        assert saved_android.revoked_at is None
    # 쿠키 값은 Android refresh 해시 조회에 참여하지 않는다.
    async with factory() as db:
        with pytest.raises(sessions.RefreshRejectedError):
            await sessions.refresh_session(db, issued.token, "ANDROID", settings)


@pytest.fixture
async def domain_rows(social_database):
    factory, members, _ = social_database
    conversations = [uuid4(), uuid4()]
    try:
        async with factory.begin() as db:
            for index, member in enumerate(members):
                db.add(
                    CoachingConversation(
                        id=conversations[index],
                        user_id=member.id,
                        title="합성 대화",
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                db.add(
                    SyncState(
                        end_user_id=member.id,
                        metric="steps",
                        initial_sync_done=bool(index),
                        last_status="success",
                        last_success_at=NOW,
                    )
                )
        yield conversations
    finally:
        async with factory.begin() as db:
            await db.execute(
                delete(CoachingConversation).where(
                    CoachingConversation.id.in_(conversations)
                )
            )
            await db.execute(
                delete(SyncState).where(
                    SyncState.end_user_id.in_([member.id for member in members])
                )
            )


async def test_cookie_health_and_coaching_rest_isolate_owners_and_reject_replay(
    web_api,
    web_session,
    domain_rows,
    social_database,
):
    _, _, issued = web_session
    own, other = domain_rows
    health = "/api/v1/health/data-status"
    own_messages = f"/api/v1/coaching/conversations/{own}/messages"
    other_messages = f"/api/v1/coaching/conversations/{other}/messages"
    response = await web_api.get(health)
    assert response.status_code == 200
    steps = next(
        x for x in response.json()["data"]["metrics"] if x["metric"] == "steps"
    )
    assert steps["ready"] is False  # 다른 회원의 True 상태를 읽으면 실패한다.
    response = await web_api.get(own_messages)
    assert response.status_code == 200
    response = await web_api.get(other_messages)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
    # 건강 API는 대상 회원 ID 입력 자체를 허용하지 않는다.
    response = await web_api.get(
        health, params={"userId": str(social_database[1][1].id)}
    )
    assert response.status_code == 422
    response = await web_api.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 204
    for path in (health, own_messages, other_messages):
        response = await web_api.get(
            path,
            headers={"Cookie": f"{SESSION_COOKIE}={issued.token}"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"
