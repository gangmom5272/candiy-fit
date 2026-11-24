"""실제 DB의 웹 세션으로 코칭 WS 쿠키 인증을 검증한다. 어댑터는 대체하지 않는다."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.service import create_login_session
from app.auth.web_sessions import logout_web_session
from app.core.config import Settings
from tests.auth.conftest import social_database as social_database
from tests.coaching.test_coaching_flow_db import database_url
from tests.coaching.test_ws_connection import (
    READY,
    WS_PATH,
    RecordingHandler,
    expect_close_within,
    question_frame,
    receive_json_within,
)
from tests.coaching.test_ws_connection import (
    ws_app as ws_app,
)

pytestmark = pytest.mark.db
ORIGIN = "http://localhost:5173"
AUTH_REQUIRED = {
    "type": "connection_error",
    "data": {"code": "AUTH_REQUIRED", "message": "로그인이 필요합니다."},
}


def headers_for(token: str) -> dict[str, str]:
    """합성 세션 토큰을 쿠키로 싣고 허용 Origin을 붙인 헤더를 만든다."""
    return {"Cookie": f"__Host-cf_session={token}", "Origin": ORIGIN}


async def issue_cookie(
    factory: Any, user_id: Any, settings: Settings, now: datetime | None = None
) -> str:
    """회원의 WEB 로그인 세션을 DB에 만들고 쿠키 값(토큰)을 돌려준다. now로 발급 시각을 옮길 수 있다."""
    async with factory.begin() as db:
        issued = await create_login_session(
            db, user_id, "WEB", settings, now=now or datetime.now(UTC)
        )
    return issued.token


async def revoke(factory: Any, token: str) -> None:
    """웹 세션을 로그아웃으로 폐기하고 커밋한다."""
    async with factory() as db:
        await logout_web_session(db, token)


async def test_valid_cookie_reaches_handler_with_member(
    social_database: Any, ws_app: FastAPI, settings: Settings
) -> None:
    """유효 쿠키 → ready 후 질문을 접수하고 처리기가 해당 회원 ID를 받는다."""
    factory, members, _ = social_database
    token = await issue_cookie(factory, members[0].id, settings)
    engine = create_async_engine(database_url(), poolclass=NullPool)
    ws_app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    handler = RecordingHandler()
    ws_app.state.coaching_question_handler = handler

    def run() -> None:
        """TestClient로 연결해 ready와 질문 응답을 확인한다."""
        with TestClient(ws_app).websocket_connect(
            WS_PATH, headers=headers_for(token)
        ) as websocket:
            assert receive_json_within(websocket) == READY
            websocket.send_json(question_frame())
            assert receive_json_within(websocket)["type"] == "delta"

    try:
        await asyncio.to_thread(run)
    finally:
        await engine.dispose()
    assert [call[0].user_id for call in handler.calls] == [members[0].id]


async def test_logged_out_cookie_is_rejected_on_new_connection(
    social_database: Any, ws_app: FastAPI, settings: Settings
) -> None:
    """로그아웃한 쿠키로 새 연결 → AUTH_REQUIRED 후 close 1008."""
    factory, members, _ = social_database
    token = await issue_cookie(factory, members[0].id, settings)
    await revoke(factory, token)
    engine = create_async_engine(database_url(), poolclass=NullPool)
    ws_app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    ws_app.state.coaching_question_handler = RecordingHandler()

    def run() -> None:
        """폐기된 쿠키로 연결해 connection_error와 close를 확인한다."""
        with TestClient(ws_app).websocket_connect(
            WS_PATH, headers=headers_for(token)
        ) as websocket:
            assert receive_json_within(websocket) == AUTH_REQUIRED
            expect_close_within(websocket, 1008)

    try:
        await asyncio.to_thread(run)
    finally:
        await engine.dispose()


async def test_logout_after_connect_rejects_next_question_and_keeps_connection(
    social_database: Any, ws_app: FastAPI, settings: Settings
) -> None:
    """연결 뒤 로그아웃 → 질문은 접수 전 AUTH_REQUIRED 오류, 연결 유지, 처리기 없음."""
    factory, members, _ = social_database
    token = await issue_cookie(factory, members[0].id, settings)
    engine = create_async_engine(database_url(), poolclass=NullPool)
    ws_app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    handler = RecordingHandler()
    ws_app.state.coaching_question_handler = handler
    loop = asyncio.get_running_loop()

    def run() -> None:
        """연결 후 테스트 루프에서 로그아웃을 커밋하고 질문 거절을 확인한다."""
        with TestClient(ws_app).websocket_connect(
            WS_PATH, headers=headers_for(token)
        ) as websocket:
            assert receive_json_within(websocket) == READY
            asyncio.run_coroutine_threadsafe(revoke(factory, token), loop).result(5)
            websocket.send_json(question_frame())
            error = receive_json_within(websocket)
            assert error["type"] == "error"
            assert error["data"]["code"] == "AUTH_REQUIRED"
            assert error["data"]["accepted"] is False

    try:
        await asyncio.to_thread(run)
    finally:
        await engine.dispose()
    assert handler.calls == []


async def test_expired_cookie_is_rejected_as_auth_required(
    social_database: Any, ws_app: FastAPI, settings: Settings
) -> None:
    """8일 전에 발급된 만료 쿠키 → AUTH_EXPIRED가 아닌 AUTH_REQUIRED 후 close 1008."""
    factory, members, _ = social_database
    token = await issue_cookie(
        factory, members[0].id, settings, now=datetime.now(UTC) - timedelta(days=8)
    )
    engine = create_async_engine(database_url(), poolclass=NullPool)
    ws_app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    ws_app.state.coaching_question_handler = RecordingHandler()

    def run() -> None:
        """만료 쿠키로 연결해 connection_error와 close를 확인한다."""
        with TestClient(ws_app).websocket_connect(
            WS_PATH, headers=headers_for(token)
        ) as websocket:
            assert receive_json_within(websocket) == AUTH_REQUIRED
            expect_close_within(websocket, 1008)

    try:
        await asyncio.to_thread(run)
    finally:
        await engine.dispose()
