"""합성 저장 상태와 실제 인증·HTTP 경계로 요청 조회 계약을 검사한다."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from httpx import Response
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import tokens
from app.coaching import repository
from app.coaching.models import CoachingRequest
from app.coaching.repository import MessageInfo, RequestState
from app.coaching.repository import get_request_state as stored_request_state
from app.coaching.schemas import request_snapshot_event
from app.core.db import get_session
from tests.coaching.fakes import SYNTHETIC_SNAPSHOT, synthetic_evidence_info

NOW = datetime(2026, 10, 19, 1, tzinfo=UTC)
OWNER_ID = UUID("66666666-6666-4666-8666-666666666666")
OTHER_ID = UUID("77777777-7777-4777-8777-777777777777")
REQUEST_ID = UUID("33333333-3333-4333-8333-333333333333")
MISSING_ID = UUID("99999999-9999-4999-8999-999999999999")
PATH = f"/api/v1/coaching/requests/{REQUEST_ID}"


def fixed_now() -> datetime:
    """토큰 발급과 검증에 사용할 합성 UTC 시각을 반환한다."""
    return NOW


def expired_now() -> datetime:
    """합성 토큰이 정확히 만료되는 UTC 시각을 반환한다."""
    return NOW + timedelta(minutes=15)


def make_state() -> RequestState:
    """명세 §6.4 예시 구조에 맞는 합성 완료 상태를 만든다."""
    return RequestState(
        id=REQUEST_ID,
        client_request_id=UUID("11111111-1111-4111-8111-111111111111"),
        conversation_id=UUID("22222222-2222-4222-8222-222222222222"),
        status="completed",
        user_message=MessageInfo(
            id=UUID("44444444-4444-4444-8444-444444444444"),
            role="user",
            sequence_no=1,
            content="합성 코칭 질문",
            created_at=NOW,
        ),
        assistant_message=MessageInfo(
            id=UUID("55555555-5555-4555-8555-555555555555"),
            role="assistant",
            sequence_no=2,
            content="합성 완료 답변",
            created_at=NOW,
        ),
        error_code=None,
        error_context=None,
        created_at=NOW,
        deadline_at=NOW + timedelta(seconds=120),
        finished_at=NOW + timedelta(seconds=2),
    )


def assert_http_headers(response: Response) -> None:
    """모든 조회 응답에 캐시 금지와 표준 UUIDv4 추적 헤더가 있는지 검사한다."""
    assert response.headers["cache-control"] == "no-store"
    request_id = response.headers["x-request-id"]
    assert UUID(request_id).version == 4
    assert str(UUID(request_id)) == request_id
    if response.status_code == 401:
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.fixture
def status_api(app, settings, monkeypatch):
    """가짜 세션·소유권별 상태·고정 토큰을 제공하고 쓰기 호출이 없음을 검사한다."""
    monkeypatch.setattr(tokens, "_utc_now", fixed_now)
    states = {(OWNER_ID, REQUEST_ID): make_state()}
    db = AsyncMock(spec=AsyncSession)

    async def read_state(
        db_session: AsyncSession, *, user_id: UUID, request_id: UUID
    ) -> RequestState | None:
        """합성 회원과 요청 ID가 모두 일치하는 저장 상태만 반환한다."""
        assert db_session is db
        return states.get((user_id, request_id))

    lookup = AsyncMock(side_effect=read_state)
    monkeypatch.setattr(repository, "get_request_state", lookup)

    async def session() -> AsyncIterator[AsyncSession]:
        """실제 DB 연결 없이 조회 요청에 가짜 세션을 제공한다."""
        yield db

    def headers(user_id: UUID = OWNER_ID) -> dict[str, str]:
        """지정한 합성 회원의 정상 액세스 토큰으로 인증 헤더를 만든다."""
        return {
            "Authorization": "Bearer "
            + tokens.issue_access_token(user_id, settings, now=NOW).token
        }

    app.dependency_overrides[get_session] = session
    try:
        yield states, db, lookup, headers
    finally:
        app.dependency_overrides.pop(get_session)
        db.commit.assert_not_called()
        db.rollback.assert_not_called()
        db.flush.assert_not_called()
        db.add.assert_not_called()
        db.delete.assert_not_called()


@pytest.mark.parametrize(
    "status,content,error_code,error_context,finished_at",
    [
        ("accepted", "", None, None, None),
        ("generating", "", None, None, None),
        ("completed", "합성 완료 답변", None, None, "2026-10-19T01:00:02Z"),
        (
            "failed",
            "합성 부분 답변",
            "AI_UNAVAILABLE",
            None,
            "2026-10-19T01:00:02Z",
        ),
        (
            "failed",
            "합성 건강 실패 부분 답변",
            "HEALTH_DATA_UNAVAILABLE",
            {"reason": "DATA_NOT_READY", "metrics": ["sleeps"]},
            "2026-10-19T01:00:02Z",
        ),
    ],
)
async def test_request_status_literal_contract(
    client, status_api, status, content, error_code, error_context, finished_at
) -> None:
    """본인 요청의 네 상태·건강 실패 → 명세의 11필드와 저장된 답변을 200으로 받는다."""
    states, db, lookup, headers = status_api
    original = states[OWNER_ID, REQUEST_ID]
    states[OWNER_ID, REQUEST_ID] = replace(
        original,
        status=status,
        assistant_message=replace(original.assistant_message, content=content),
        error_code=error_code,
        error_context=error_context,
        finished_at=None if finished_at is None else NOW + timedelta(seconds=2),
    )
    response = await client.get(PATH, headers=headers() | {"X-User-Id": str(OTHER_ID)})
    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "requestId": "33333333-3333-4333-8333-333333333333",
            "clientRequestId": "11111111-1111-4111-8111-111111111111",
            "conversationId": "22222222-2222-4222-8222-222222222222",
            "status": status,
            "userMessageId": "44444444-4444-4444-8444-444444444444",
            "assistantMessage": {
                "id": "55555555-5555-4555-8555-555555555555",
                "role": "assistant",
                "sequenceNo": 2,
                "content": content,
                "createdAt": "2026-10-19T01:00:00Z",
            },
            "evidence": [],
            "errorCode": error_code,
            "createdAt": "2026-10-19T01:00:00Z",
            "finishedAt": finished_at,
            "errorContext": error_context,
        }
    }
    assert len(response.json()["data"]) == 11
    lookup.assert_awaited_once_with(db, user_id=OWNER_ID, request_id=REQUEST_ID)
    assert isinstance(lookup.await_args.kwargs["request_id"], UUID)
    assert_http_headers(response)


async def test_request_status_other_member_returns_not_found(
    client, status_api
) -> None:
    """다른 회원의 요청 조회 → 없는 요청과 같은 404를 받고 소유자 정보는 노출하지 않는다."""
    _, db, lookup, headers = status_api
    other = await client.get(PATH, headers=headers(OTHER_ID))
    lookup.assert_awaited_once_with(db, user_id=OTHER_ID, request_id=REQUEST_ID)
    missing = await client.get(
        f"/api/v1/coaching/requests/{MISSING_ID}", headers=headers(OTHER_ID)
    )
    assert other.status_code == missing.status_code == 404
    assert (
        other.json()
        == missing.json()
        == {
            "error": {
                "code": "REQUEST_NOT_FOUND",
                "message": "요청을 찾을 수 없습니다.",
            }
        }
    )
    assert_http_headers(other)
    assert_http_headers(missing)


async def test_request_status_missing_returns_not_found(client, status_api) -> None:
    """없는 요청 조회 → 공개 메시지만 담긴 404 REQUEST_NOT_FOUND를 받는다."""
    _, db, lookup, headers = status_api
    response = await client.get(
        f"/api/v1/coaching/requests/{MISSING_ID}", headers=headers()
    )
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "REQUEST_NOT_FOUND",
            "message": "요청을 찾을 수 없습니다.",
        }
    }
    lookup.assert_awaited_once_with(db, user_id=OWNER_ID, request_id=MISSING_ID)
    assert_http_headers(response)


@pytest.mark.parametrize(
    "mode", ["missing", "invalid", "wrong_scheme", "wrong_key", "duplicate"]
)
async def test_request_status_auth_required(client, status_api, settings, mode) -> None:
    """토큰 누락·형식·서명·중복 헤더 오류 → 저장소 호출 없이 401 AUTH_REQUIRED를 받는다."""
    _, _, lookup, headers = status_api
    auth = headers()
    if mode == "missing":
        auth = {}
    elif mode == "invalid":
        auth = {"Authorization": "Bearer synthetic-invalid"}
    elif mode == "wrong_scheme":
        auth = {"Authorization": "Basic synthetic-invalid"}
    elif mode == "wrong_key":
        other_settings = settings.model_copy(
            update={
                "auth_signing_key": SecretStr("synthetic-other-signing-key-32bytes")
            }
        )
        auth = {
            "Authorization": "Bearer "
            + tokens.issue_access_token(OWNER_ID, other_settings, now=NOW).token
        }
    elif mode == "duplicate":
        auth = [
            ("Authorization", auth["Authorization"]),
            ("Authorization", headers(OTHER_ID)["Authorization"]),
        ]
    response = await client.get(PATH, headers=auth)
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "AUTH_REQUIRED", "message": "로그인이 필요합니다."}
    }
    lookup.assert_not_called()
    assert_http_headers(response)


async def test_request_status_expired_token(client, status_api, monkeypatch) -> None:
    """만료된 토큰 → 저장소 호출 없이 401 AUTH_EXPIRED를 받는다."""
    _, _, lookup, headers = status_api
    monkeypatch.setattr(tokens, "_utc_now", expired_now)
    response = await client.get(PATH, headers=headers())
    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "AUTH_EXPIRED", "message": "로그인이 필요합니다."}
    }
    lookup.assert_not_called()
    assert_http_headers(response)


@pytest.mark.parametrize(
    "invalid_id",
    [
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
        "aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa",
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa",
        "synthetic-not-a-uuid",
    ],
)
async def test_request_status_invalid_path_uses_public_alias(
    client, status_api, invalid_id
) -> None:
    """비표준 UUID 경로 → 입력값 없이 requestId의 invalid_format을 422로 받는다."""
    _, _, lookup, headers = status_api
    response = await client.get(
        f"/api/v1/coaching/requests/{invalid_id}", headers=headers()
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "요청 내용을 확인해 주세요.",
            "details": [{"field": "requestId", "reason": "invalid_format"}],
        }
    }
    assert invalid_id not in response.text
    lookup.assert_not_called()
    assert_http_headers(response)


@pytest.mark.parametrize("query,body,field", [("?x=1", None, "x"), ("", "{}", "body")])
async def test_request_status_rejects_query_and_body(
    client, status_api, query, body, field
) -> None:
    """정의하지 않은 쿼리·GET 본문 → 저장소 호출 없이 extra_forbidden을 422로 받는다."""
    _, _, lookup, headers = status_api
    response = await client.request(
        "GET", PATH + query, content=body, headers=headers()
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "요청 내용을 확인해 주세요.",
            "details": [{"field": field, "reason": "extra_forbidden"}],
        }
    }
    lookup.assert_not_called()
    assert_http_headers(response)


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("synthetic-private-connection"),
        OperationalError(
            "synthetic-private-sql", {}, Exception("synthetic-private-database")
        ),
    ],
    ids=["connection", "operational"],
)
async def test_request_status_database_error_is_safe_500(
    client, status_api, caplog, error
) -> None:
    """저장소 연결·운영 오류 → 예외 원문을 응답·로그에 남기지 않고 500을 받는다."""
    _, db, lookup, headers = status_api
    lookup.side_effect = error
    auth = headers()
    response = await client.get(PATH, headers=auth)
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "요청을 처리하지 못했습니다."}
    }
    lookup.assert_awaited_once_with(db, user_id=OWNER_ID, request_id=REQUEST_ID)
    combined = response.text + caplog.text
    assert "synthetic-private" not in combined
    assert str(OWNER_ID) not in combined
    assert "합성 코칭 질문" not in combined
    assert auth["Authorization"].removeprefix("Bearer ") not in combined
    assert all(record.exc_info is None for record in caplog.records)
    assert_http_headers(response)


@pytest.mark.parametrize("method", ["POST", "DELETE"])
async def test_request_status_rejects_other_methods(client, status_api, method) -> None:
    """POST·DELETE 호출 → 저장소 호출 없이 405 METHOD_NOT_ALLOWED를 받는다."""
    _, _, lookup, headers = status_api
    response = await client.request(method, PATH, headers=headers())
    assert response.status_code == 405
    assert response.json() == {
        "error": {
            "code": "METHOD_NOT_ALLOWED",
            "message": "지원하지 않는 메서드입니다.",
        }
    }
    lookup.assert_not_called()
    assert_http_headers(response)


@pytest.mark.parametrize("status", ["accepted", "generating", "completed", "failed"])
async def test_request_status_matches_websocket_snapshot(
    client, status_api, status
) -> None:
    """동일한 저장 상태를 REST와 WS로 직렬화하면 → 네 상태 모두 data가 일치한다."""
    states, _, _, headers = status_api
    original = states[OWNER_ID, REQUEST_ID]
    state = replace(
        original,
        status=status,
        assistant_message=replace(
            original.assistant_message,
            content="" if status in {"accepted", "generating"} else "합성 저장 답변",
        ),
        error_code="HEALTH_DATA_UNAVAILABLE" if status == "failed" else None,
        error_context=(
            {"reason": "DATA_NOT_READY", "metrics": ["sleeps"]}
            if status == "failed"
            else None
        ),
        finished_at=None
        if status in {"accepted", "generating"}
        else original.finished_at,
    )
    states[OWNER_ID, REQUEST_ID] = state
    response = await client.get(PATH, headers=headers())
    assert response.status_code == 200
    assert response.json()["data"] == request_snapshot_event(state)["data"]
    assert_http_headers(response)


async def test_request_status_completed_evidence_serializes_literal_contract(
    client, status_api
) -> None:
    """근거 두 개가 있는 완료 요청 → 공개 외곽과 snake_case 원형을 그대로 반환한다."""
    states, _, _, headers = status_api
    states[OWNER_ID, REQUEST_ID] = replace(
        states[OWNER_ID, REQUEST_ID],
        evidence=(synthetic_evidence_info(), synthetic_evidence_info(2)),
    )
    response = await client.get(PATH, headers=headers())
    assert response.status_code == 200
    assert response.json()["data"]["evidence"] == [
        {
            "id": "66666666-6666-4666-8666-666666666666",
            "position": 1,
            "metric": "steps",
            "periodStart": "2026-10-12",
            "periodEnd": "2026-10-18",
            "snapshot": SYNTHETIC_SNAPSHOT,
        },
        {
            "id": "77777777-7777-4777-8777-777777777777",
            "position": 2,
            "metric": "steps",
            "periodStart": "2026-10-12",
            "periodEnd": "2026-10-18",
            "snapshot": SYNTHETIC_SNAPSHOT,
        },
    ]


async def test_request_status_foreign_request_skips_evidence_lookup(
    client, status_api, monkeypatch
) -> None:
    """실제 저장소의 소유권 조회가 빈 결과 → 404이며 근거 조회 함수는 호출하지 않는다."""
    _, db, _, headers = status_api
    db.execute.return_value = Mock(one_or_none=Mock(return_value=None))
    monkeypatch.setattr(repository, "get_request_state", stored_request_state)
    evidence_lookup = AsyncMock()
    monkeypatch.setattr(repository, "_list_evidence", evidence_lookup)
    response = await client.get(PATH, headers=headers(OTHER_ID))
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "REQUEST_NOT_FOUND", "message": "요청을 찾을 수 없습니다."}
    }
    evidence_lookup.assert_not_called()
    assert db.execute.await_count == 1


def make_request_row(
    status: repository.RequestStatus,
) -> tuple[CoachingRequest, MessageInfo, MessageInfo]:
    """상태별 합성 요청과 사용자·AI 메시지로 첫 SELECT 결과를 만든다."""
    state = make_state()
    request = CoachingRequest(
        id=state.id,
        user_id=OWNER_ID,
        client_request_id=state.client_request_id,
        conversation_id=state.conversation_id,
        input_hash=repository.input_hash(None, state.user_message.content),
        status=status,
        error_code="AI_UNAVAILABLE" if status == "failed" else None,
        error_context=None,
        created_at=state.created_at,
        deadline_at=state.deadline_at,
        finished_at=state.finished_at if status in {"completed", "failed"} else None,
    )
    content = {
        "accepted": "",
        "generating": "",
        "completed": "합성 완료 답변",
        "failed": "합성 부분 답변",
    }[status]
    return (
        request,
        state.user_message,
        replace(state.assistant_message, content=content),
    )


@pytest.mark.parametrize("status", ["accepted", "generating", "failed", "completed"])
async def test_request_state_only_completed_calls_evidence_lookup(
    monkeypatch, status
) -> None:
    """R5·R7 요청 상태별 조회 → completed만 AI ID 하나로 근거를 읽고 나머지는 호출하지 않는다."""
    row = make_request_row(status)
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = Mock(one_or_none=Mock(return_value=row))
    expected = (synthetic_evidence_info(),)
    evidence_lookup = AsyncMock(return_value={row[2].id: expected})
    monkeypatch.setattr(repository, "_list_evidence", evidence_lookup)

    state = await stored_request_state(db, user_id=OWNER_ID, request_id=REQUEST_ID)

    assert state is not None
    assert state.status == status
    assert state.assistant_message.content == row[2].content
    if status == "completed":
        evidence_lookup.assert_awaited_once_with(db, [row[2].id])
        assert state.evidence == expected
    else:
        evidence_lookup.assert_not_called()
        assert state.evidence == ()
    assert db.execute.await_count == 1
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.parametrize("status", ["accepted", "generating", "failed", "completed"])
async def test_duplicate_only_completed_calls_evidence_lookup(
    monkeypatch, status
) -> None:
    """R7 동일 전송의 상태별 재시도 → completed만 근거를 읽고 나머지는 빈 근거의 Duplicate를 반환한다."""
    row = make_request_row(status)
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = Mock(one_or_none=Mock(return_value=row))
    expected = (synthetic_evidence_info(),)
    evidence_lookup = AsyncMock(return_value={row[2].id: expected})
    monkeypatch.setattr(repository, "_list_evidence", evidence_lookup)

    result = await repository.accept(
        db,
        user_id=OWNER_ID,
        client_request_id=row[0].client_request_id,
        conversation_id=None,
        content=row[1].content,
        now=NOW,
    )

    assert isinstance(result, repository.Duplicate)
    assert result.request.status == status
    assert result.request.assistant_message.content == row[2].content
    if status == "completed":
        evidence_lookup.assert_awaited_once_with(db, [row[2].id])
        assert result.request.evidence == expected
    else:
        evidence_lookup.assert_not_called()
        assert result.request.evidence == ()
    assert db.execute.await_count == 1
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    db.flush.assert_not_called()
    db.add.assert_not_called()
