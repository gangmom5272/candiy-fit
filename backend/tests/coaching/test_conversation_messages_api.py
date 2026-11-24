"""합성 메시지와 실제 인증·HTTP 경계로 대화 메시지 조회 계약을 검사한다."""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from httpx import Response
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import tokens
from app.coaching import repository
from app.coaching.repository import (
    ConversationRecord,
    HistoryMessage,
    MessageInfo,
    MessagePage,
)
from app.coaching.repository import find_message_page as stored_message_page
from app.coaching.schemas import MessagePageQuery
from app.core.db import get_session
from tests.coaching.fakes import SYNTHETIC_SNAPSHOT, synthetic_evidence_info

NOW = datetime(2026, 10, 19, 1, tzinfo=UTC)
OWNER_ID = UUID("66666666-6666-4666-8666-666666666666")
OTHER_ID = UUID("77777777-7777-4777-8777-777777777777")
CONVERSATION_ID = UUID("22222222-2222-4222-8222-222222222222")
REQUEST_ID = UUID("33333333-3333-4333-8333-333333333333")
MISSING_ID = UUID("99999999-9999-4999-8999-999999999999")
PATH = f"/api/v1/coaching/conversations/{CONVERSATION_ID}/messages"


def fixed_now() -> datetime:
    """토큰 발급과 검증에 사용할 합성 UTC 시각을 반환한다."""
    return NOW


def expired_now() -> datetime:
    """합성 토큰이 정확히 만료되는 UTC 시각을 반환한다."""
    return NOW + timedelta(minutes=15)


def make_page() -> MessagePage:
    """질문 세 개의 합성 메시지 여섯 개와 요약이 있는 대화를 만든다."""
    messages = []
    for index in range(3):
        request_id = REQUEST_ID if index == 2 else uuid4()
        for role, offset, content in (
            ("user", 1, "합성 코칭 질문"),
            ("assistant", 2, "합성 완료 답변"),
        ):
            message_id = uuid4()
            if index == 2:
                message_id = UUID(
                    "44444444-4444-4444-8444-444444444444"
                    if role == "user"
                    else "55555555-5555-4555-8555-555555555555"
                )
            messages.append(
                HistoryMessage(
                    message=MessageInfo(
                        id=message_id,
                        role=role,
                        sequence_no=index * 2 + offset,
                        content=content,
                        created_at=NOW,
                    ),
                    request_id=request_id,
                    request_status="completed",
                    error_code=None,
                    error_context=None,
                )
            )
    return MessagePage(
        conversation=ConversationRecord(
            id=CONVERSATION_ID,
            title="합성 코칭 질문",
            summary="합성 요약",
            created_at=NOW,
            updated_at=NOW + timedelta(seconds=2),
        ),
        messages=tuple(messages),
        has_more=False,
    )


def assert_http_headers(response: Response) -> None:
    """모든 HTTP 응답의 캐시 금지와 UUIDv4 추적 및 인증 헤더를 검사한다."""
    assert response.headers["cache-control"] == "no-store"
    request_id = response.headers["x-request-id"]
    assert UUID(request_id).version == 4
    assert str(UUID(request_id)) == request_id
    if response.status_code == 401:
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.fixture
def messages_api(app, settings, monkeypatch):
    """합성 소유권별 페이지와 가짜 세션을 제공하고 모든 쓰기 호출을 금지한다."""
    monkeypatch.setattr(tokens, "_utc_now", fixed_now)
    pages = {(OWNER_ID, CONVERSATION_ID): make_page()}
    db = AsyncMock(spec=AsyncSession)

    async def read_page(
        db_session: AsyncSession,
        *,
        user_id: UUID,
        conversation_id: UUID,
        limit: int,
        before_sequence_no: int | None,
    ) -> MessagePage | None:
        """타입과 세션을 확인하고 합성 회원·대화에 해당하는 페이지를 반환한다."""
        assert db_session is db
        assert isinstance(user_id, UUID)
        assert isinstance(conversation_id, UUID)
        assert type(limit) is int
        assert before_sequence_no is None or type(before_sequence_no) is int
        page = pages.get((user_id, conversation_id))
        if page is None:
            return None
        messages = tuple(
            item
            for item in page.messages
            if before_sequence_no is None
            or item.message.sequence_no < before_sequence_no
        )
        return replace(page, messages=messages[-limit:], has_more=len(messages) > limit)

    lookup = AsyncMock(side_effect=read_page)
    monkeypatch.setattr(repository, "find_message_page", lookup)

    async def session() -> AsyncIterator[AsyncSession]:
        """실제 DB 연결 없이 조회에 사용할 가짜 세션을 제공한다."""
        yield db

    def headers(user_id: UUID = OWNER_ID) -> dict[str, str]:
        """지정한 합성 회원의 정상 토큰으로 인증 헤더를 만든다."""
        return {
            "Authorization": "Bearer "
            + tokens.issue_access_token(user_id, settings, now=NOW).token
        }

    app.dependency_overrides[get_session] = session
    try:
        yield pages, db, lookup, headers
    finally:
        app.dependency_overrides.pop(get_session)
        db.commit.assert_not_called()
        db.rollback.assert_not_called()
        db.flush.assert_not_called()
        db.add.assert_not_called()
        db.delete.assert_not_called()


async def test_messages_latest_page_returns_literal_contract(
    client, messages_api
) -> None:
    """이전 메시지가 있는 최신 페이지 → 오름차순 두 항목과 최소 순번 커서를 받는다."""
    _, db, lookup, headers = messages_api
    response = await client.get(
        PATH, params={"limit": "2"}, headers=headers() | {"X-User-Id": str(OTHER_ID)}
    )
    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "conversation": {
                "id": "22222222-2222-4222-8222-222222222222",
                "title": "합성 코칭 질문",
                "summary": "합성 요약",
                "createdAt": "2026-10-19T01:00:00Z",
                "updatedAt": "2026-10-19T01:00:02Z",
            },
            "items": [
                {
                    "id": "44444444-4444-4444-8444-444444444444",
                    "requestId": "33333333-3333-4333-8333-333333333333",
                    "role": "user",
                    "sequenceNo": 5,
                    "content": "합성 코칭 질문",
                    "requestStatus": "completed",
                    "errorCode": None,
                    "createdAt": "2026-10-19T01:00:00Z",
                    "evidence": [],
                    "errorContext": None,
                },
                {
                    "id": "55555555-5555-4555-8555-555555555555",
                    "requestId": "33333333-3333-4333-8333-333333333333",
                    "role": "assistant",
                    "sequenceNo": 6,
                    "content": "합성 완료 답변",
                    "requestStatus": "completed",
                    "errorCode": None,
                    "createdAt": "2026-10-19T01:00:00Z",
                    "evidence": [],
                    "errorContext": None,
                },
            ],
            "nextBeforeSequenceNo": 5,
        }
    }
    data = response.json()["data"]
    assert len(data) == 3
    assert len(data["conversation"]) == 5
    assert all(len(item) == 10 for item in data["items"])
    lookup.assert_awaited_once_with(
        db,
        user_id=OWNER_ID,
        conversation_id=CONVERSATION_ID,
        limit=2,
        before_sequence_no=None,
    )
    assert_http_headers(response)


@pytest.mark.parametrize(
    "query,limit,before,expected_sequences",
    [
        ({}, 50, None, [1, 2, 3, 4, 5, 6]),
        ({"limit": "020"}, 20, None, [1, 2, 3, 4, 5, 6]),
        ({"limit": "000000000000000000001"}, 1, None, [6]),
        ({"limit": "0" * 4400 + "5"}, 5, None, [2, 3, 4, 5, 6]),
        (
            {"beforeSequenceNo": "9223372036854775807"},
            50,
            9223372036854775807,
            [1, 2, 3, 4, 5, 6],
        ),
        (
            {"beforeSequenceNo": "009223372036854775807"},
            50,
            9223372036854775807,
            [1, 2, 3, 4, 5, 6],
        ),
        ({"beforeSequenceNo": "4", "limit": "2"}, 2, 4, [2, 3]),
    ],
)
async def test_messages_valid_query_passes_integer_arguments(
    client, messages_api, query, limit, before, expected_sequences
) -> None:
    """기본값·긴 선행 0·BIGINT 상한·경계 조회 → 저장소에 검증된 정수를 전달한다."""
    _, db, lookup, headers = messages_api
    response = await client.get(PATH, params=query, headers=headers())
    assert response.status_code == 200
    assert [
        item["sequenceNo"] for item in response.json()["data"]["items"]
    ] == expected_sequences
    lookup.assert_awaited_once_with(
        db,
        user_id=OWNER_ID,
        conversation_id=CONVERSATION_ID,
        limit=limit,
        before_sequence_no=before,
    )
    assert_http_headers(response)


async def test_messages_mixed_outcomes_preserves_request_errors(
    client, messages_api
) -> None:
    """완료·건강 실패·AI 실패·중단이 섞인 페이지 → 두 역할 모두 소속 요청의 문맥을 받는다."""
    pages, _, _, headers = messages_api
    original = pages[OWNER_ID, CONVERSATION_ID]
    messages = list(original.messages)
    for index, item in enumerate(messages):
        if index < 2:
            continue
        health = index < 4
        messages[index] = replace(
            item,
            request_status="failed",
            error_code="HEALTH_DATA_UNAVAILABLE" if health else "AI_UNAVAILABLE",
            error_context={"reason": "DATA_NOT_READY", "metrics": ["sleeps"]}
            if health
            else None,
            message=replace(
                item.message,
                content="합성 부분 답변"
                if item.message.role == "assistant"
                else item.message.content,
            ),
        )
    interrupted_request_id = uuid4()
    for item, sequence_no in zip(original.messages[:2], (7, 8), strict=True):
        messages.append(
            replace(
                item,
                request_id=interrupted_request_id,
                request_status="failed",
                error_code="GENERATION_INTERRUPTED",
                error_context=None,
                message=replace(
                    item.message,
                    id=uuid4(),
                    sequence_no=sequence_no,
                    content="합성 중단 부분 답변"
                    if item.message.role == "assistant"
                    else item.message.content,
                ),
            )
        )
    pages[OWNER_ID, CONVERSATION_ID] = replace(original, messages=tuple(messages))
    response = await client.get(PATH, headers=headers())
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [
        [
            item["role"],
            item["requestStatus"],
            item["errorCode"],
            item["errorContext"],
            item["content"],
            item["evidence"],
        ]
        for item in items
    ] == [
        ["user", "completed", None, None, "합성 코칭 질문", []],
        ["assistant", "completed", None, None, "합성 완료 답변", []],
        [
            "user",
            "failed",
            "HEALTH_DATA_UNAVAILABLE",
            {"reason": "DATA_NOT_READY", "metrics": ["sleeps"]},
            "합성 코칭 질문",
            [],
        ],
        [
            "assistant",
            "failed",
            "HEALTH_DATA_UNAVAILABLE",
            {"reason": "DATA_NOT_READY", "metrics": ["sleeps"]},
            "합성 부분 답변",
            [],
        ],
        ["user", "failed", "AI_UNAVAILABLE", None, "합성 코칭 질문", []],
        ["assistant", "failed", "AI_UNAVAILABLE", None, "합성 부분 답변", []],
        ["user", "failed", "GENERATION_INTERRUPTED", None, "합성 코칭 질문", []],
        [
            "assistant",
            "failed",
            "GENERATION_INTERRUPTED",
            None,
            "합성 중단 부분 답변",
            [],
        ],
    ]
    assert_http_headers(response)


@pytest.mark.parametrize("query", [{}, {"beforeSequenceNo": "1"}])
async def test_messages_other_owner_matches_missing_conversation(
    client, messages_api, query
) -> None:
    """다른 회원 대화와 없는 대화 → 빈 경계 조회에서도 같은 404를 받고 소유자 값을 보존한다."""
    pages, _, lookup, headers = messages_api
    original = pages[OWNER_ID, CONVERSATION_ID]
    owner_before = await client.get(PATH, headers=headers())
    other = await client.get(PATH, params=query, headers=headers(OTHER_ID))
    assert lookup.await_args.kwargs["user_id"] == OTHER_ID
    missing = await client.get(
        f"/api/v1/coaching/conversations/{MISSING_ID}/messages",
        params=query,
        headers=headers(OTHER_ID),
    )
    assert other.status_code == missing.status_code == 404
    assert (
        other.json()
        == missing.json()
        == {
            "error": {
                "code": "CONVERSATION_NOT_FOUND",
                "message": "대화를 찾을 수 없습니다.",
            }
        }
    )
    owner_after = await client.get(PATH, headers=headers())
    assert owner_after.json() == owner_before.json()
    assert pages[OWNER_ID, CONVERSATION_ID] == original
    for response in (owner_before, other, missing, owner_after):
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
async def test_messages_invalid_id_returns_safe_details(
    client, messages_api, invalid_id
) -> None:
    """대문자·하이픈 누락·길이·UUID 오류 → 저장소 호출 없이 공개 경로명과 reason만 받는다."""
    _, _, lookup, headers = messages_api
    response = await client.get(
        f"/api/v1/coaching/conversations/{invalid_id}/messages", headers=headers()
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "요청 내용을 확인해 주세요.",
            "details": [{"field": "conversationId", "reason": "invalid_format"}],
        }
    }
    assert invalid_id not in response.text
    lookup.assert_not_called()
    assert_http_headers(response)


@pytest.mark.parametrize(
    "field,value,details",
    [
        ("limit", "0", [{"field": "limit", "reason": "out_of_range"}]),
        ("limit", "-0", [{"field": "limit", "reason": "out_of_range"}]),
        ("limit", "-000", [{"field": "limit", "reason": "out_of_range"}]),
        ("limit", "101", [{"field": "limit", "reason": "out_of_range"}]),
        ("limit", "-1", [{"field": "limit", "reason": "out_of_range"}]),
        (
            "limit",
            "0" * 4400 + "1" * 21,
            [{"field": "limit", "reason": "out_of_range"}],
        ),
        ("limit", "abc", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "1.5", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "5.0", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "+5", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", " 5", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "5 ", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "1e2", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "1_0", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "\uff12\uff10", [{"field": "limit", "reason": "invalid_type"}]),
        (
            "beforeSequenceNo",
            "0",
            [{"field": "beforeSequenceNo", "reason": "out_of_range"}],
        ),
        (
            "beforeSequenceNo",
            "-1",
            [{"field": "beforeSequenceNo", "reason": "out_of_range"}],
        ),
        (
            "beforeSequenceNo",
            "1.5",
            [{"field": "beforeSequenceNo", "reason": "invalid_type"}],
        ),
        (
            "beforeSequenceNo",
            "abc",
            [{"field": "beforeSequenceNo", "reason": "invalid_type"}],
        ),
        (
            "beforeSequenceNo",
            "9223372036854775808",
            [{"field": "beforeSequenceNo", "reason": "out_of_range"}],
        ),
        (
            "beforeSequenceNo",
            "009223372036854775808",
            [{"field": "beforeSequenceNo", "reason": "out_of_range"}],
        ),
        (
            "beforeSequenceNo",
            "1234567890123456789012345",
            [{"field": "beforeSequenceNo", "reason": "out_of_range"}],
        ),
        (
            "beforeSequenceNo",
            "-1234567890123456789012345",
            [{"field": "beforeSequenceNo", "reason": "out_of_range"}],
        ),
        (
            "beforeSequenceNo",
            "-" + "0" * 4400 + "1",
            [{"field": "beforeSequenceNo", "reason": "out_of_range"}],
        ),
        (
            "before_sequence_no",
            "synthetic-private-input",
            [{"field": "before_sequence_no", "reason": "extra_forbidden"}],
        ),
        (
            "userId",
            "77777777-7777-4777-8777-777777777777",
            [{"field": "userId", "reason": "extra_forbidden"}],
        ),
    ],
)
async def test_messages_invalid_query_returns_safe_details(
    client, messages_api, field, value, details
) -> None:
    """잘못된 쿼리 타입·범위·별칭 → 입력값 없이 지정된 field·reason만 422로 받는다."""
    _, _, lookup, headers = messages_api
    response = await client.get(PATH, params={field: value}, headers=headers())
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "요청 내용을 확인해 주세요.",
            "details": details,
        }
    }
    if len(value) > 1:
        assert value not in response.text
    lookup.assert_not_called()
    assert_http_headers(response)


@pytest.mark.parametrize(
    "query,body,details",
    [
        (
            [("limit", "2"), ("limit", "3")],
            None,
            [{"field": "limit", "reason": "duplicate"}],
        ),
        (
            [("beforeSequenceNo", "2"), ("beforeSequenceNo", "3")],
            None,
            [{"field": "beforeSequenceNo", "reason": "duplicate"}],
        ),
        (
            [],
            '{"synthetic-private-body": true}',
            [{"field": "body", "reason": "extra_forbidden"}],
        ),
    ],
)
async def test_messages_duplicate_query_or_body_returns_422(
    client, messages_api, query, body, details
) -> None:
    """중복 쿼리·GET 본문 → 저장소 호출 없이 duplicate·extra_forbidden을 받는다."""
    _, _, lookup, headers = messages_api
    response = await client.request(
        "GET", PATH, params=query, content=body, headers=headers()
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "요청 내용을 확인해 주세요.",
            "details": details,
        }
    }
    assert "synthetic-private-body" not in response.text
    lookup.assert_not_called()
    assert_http_headers(response)


@pytest.mark.parametrize(
    "mode", ["missing", "invalid", "wrong_scheme", "wrong_key", "duplicate", "expired"]
)
async def test_messages_invalid_auth_returns_401(
    client, messages_api, settings, monkeypatch, mode
) -> None:
    """누락·무효·중복·만료 토큰 → 저장소 호출 없이 인증 오류와 Bearer 헤더를 받는다."""
    _, _, lookup, headers = messages_api
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
    elif mode == "expired":
        monkeypatch.setattr(tokens, "_utc_now", expired_now)
    response = await client.get(PATH, headers=auth)
    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "AUTH_EXPIRED" if mode == "expired" else "AUTH_REQUIRED",
            "message": "로그인이 필요합니다.",
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
async def test_messages_database_error_returns_safe_500(
    client, messages_api, caplog, error
) -> None:
    """저장소 연결·운영 오류 → 응답·로그에 예외·회원·토큰·본문을 노출하지 않는다."""
    _, db, lookup, headers = messages_api
    lookup.side_effect = error
    auth = headers()
    response = await client.get(PATH, headers=auth)
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "요청을 처리하지 못했습니다."}
    }
    lookup.assert_awaited_once_with(
        db,
        user_id=OWNER_ID,
        conversation_id=CONVERSATION_ID,
        limit=50,
        before_sequence_no=None,
    )
    combined = response.text + caplog.text
    for private in (
        "synthetic-private",
        str(OWNER_ID),
        "합성 코칭 질문",
        "합성 완료 답변",
        auth["Authorization"].removeprefix("Bearer "),
    ):
        assert private not in combined
    assert all(record.exc_info is None for record in caplog.records)
    assert_http_headers(response)


async def test_messages_before_one_returns_empty_page(client, messages_api) -> None:
    """beforeSequenceNo=1 → 본인 대화 정보와 빈 목록 및 null 커서를 받는다."""
    _, _, _, headers = messages_api
    response = await client.get(
        PATH, params={"beforeSequenceNo": "1"}, headers=headers()
    )
    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "conversation": {
                "id": "22222222-2222-4222-8222-222222222222",
                "title": "합성 코칭 질문",
                "summary": "합성 요약",
                "createdAt": "2026-10-19T01:00:00Z",
                "updatedAt": "2026-10-19T01:00:02Z",
            },
            "items": [],
            "nextBeforeSequenceNo": None,
        }
    }
    assert_http_headers(response)


@pytest.mark.parametrize("summary", [None, "합성 요약"])
async def test_messages_summary_and_offset_returns_five_utc_fields(
    client, messages_api, summary
) -> None:
    """요약 유무와 UTC 외 시각 → 다섯 대화 필드와 UTC 시각을 그대로 직렬화한다."""
    pages, _, _, headers = messages_api
    original = pages[OWNER_ID, CONVERSATION_ID]
    offset = timezone(timedelta(hours=9))
    pages[OWNER_ID, CONVERSATION_ID] = replace(
        original,
        conversation=replace(
            original.conversation,
            summary=summary,
            created_at=NOW.astimezone(offset),
            updated_at=(NOW + timedelta(seconds=2)).astimezone(offset),
        ),
        messages=tuple(
            replace(
                item, message=replace(item.message, created_at=NOW.astimezone(offset))
            )
            for item in original.messages
        ),
    )
    response = await client.get(PATH, headers=headers())
    assert response.status_code == 200
    assert response.json()["data"]["conversation"] == {
        "id": "22222222-2222-4222-8222-222222222222",
        "title": "합성 코칭 질문",
        "summary": summary,
        "createdAt": "2026-10-19T01:00:00Z",
        "updatedAt": "2026-10-19T01:00:02Z",
    }
    assert all(
        item["createdAt"] == "2026-10-19T01:00:00Z"
        for item in response.json()["data"]["items"]
    )
    assert_http_headers(response)


@pytest.mark.parametrize("method", ["POST", "DELETE"])
async def test_messages_unsupported_method_returns_405(
    client, messages_api, method
) -> None:
    """POST·DELETE → 저장소 호출 없이 405 METHOD_NOT_ALLOWED를 받는다."""
    _, _, lookup, headers = messages_api
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


@pytest.mark.parametrize(
    "query,status,expected",
    [
        (
            {"limit": "0"},
            401,
            {"error": {"code": "AUTH_REQUIRED", "message": "로그인이 필요합니다."}},
        ),
        (
            {"x": "1"},
            422,
            {
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "요청 내용을 확인해 주세요.",
                    "details": [{"field": "x", "reason": "extra_forbidden"}],
                }
            },
        ),
    ],
)
async def test_messages_unauthenticated_invalid_query_preserves_precedence(
    client, messages_api, query, status, expected
) -> None:
    """토큰 없는 범위 오류·추가 쿼리 → 각각 인증 우선·공통 라우트 우선 규칙을 지킨다."""
    _, _, lookup, _ = messages_api
    response = await client.get(PATH, params=query)
    assert response.status_code == status
    assert response.json() == expected
    lookup.assert_not_called()
    assert_http_headers(response)


@pytest.mark.parametrize(
    "payload,field,reason",
    [
        ({"limit": True}, "limit", "invalid_type"),
        ({"limit": 5.0}, "limit", "invalid_type"),
        ({"limit": None}, "limit", "invalid_type"),
        ({"limit": 0}, "limit", "out_of_range"),
        ({"limit": 101}, "limit", "out_of_range"),
        ({"beforeSequenceNo": False}, "beforeSequenceNo", "invalid_type"),
        ({"beforeSequenceNo": 9223372036854775808}, "beforeSequenceNo", "out_of_range"),
    ],
)
def test_query_model_nonquery_types_rejects_invalid_values(
    payload, field, reason
) -> None:
    """내부 기본값 경로와 같은 모델 검사에 bool·소수·범위 밖 정수 → 지정 오류로 거부한다."""
    with pytest.raises(ValidationError) as caught:
        MessagePageQuery.model_validate(payload)
    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        ((field,), reason)
    ]


def test_query_model_defaults_and_integer_values_preserves_types() -> None:
    """쿼리 생략·정수 입력·선택적 None → 기본값과 정수 타입을 유지한다."""
    default = MessagePageQuery.model_validate({})
    explicit = MessagePageQuery.model_validate(
        {"limit": 20, "beforeSequenceNo": 9223372036854775807}
    )
    optional = MessagePageQuery.model_validate({"beforeSequenceNo": None})
    assert default.limit == 50
    assert type(default.limit) is int
    assert default.before_sequence_no is optional.before_sequence_no is None
    assert explicit.limit == 20
    assert explicit.before_sequence_no == 9223372036854775807
    assert type(explicit.before_sequence_no) is int


def test_query_model_long_leading_zeros_returns_integer() -> None:
    """선행 0이 30개인 쿼리 정수 문자열 → 값 7과 int 타입을 반환한다."""
    query = MessagePageQuery.model_validate({"limit": "0" * 30 + "7"})
    assert query.limit == 7
    assert type(query.limit) is int


async def test_messages_completed_evidence_preserves_snapshot_and_empty_user(
    client, messages_api
) -> None:
    """AI 메시지에 근거가 있음 → 원형을 직렬화하고 사용자 메시지 근거는 비어 있다."""
    pages, _, _, headers = messages_api
    page = pages[OWNER_ID, CONVERSATION_ID]
    pages[OWNER_ID, CONVERSATION_ID] = replace(
        page,
        messages=(
            page.messages[-2],
            replace(page.messages[-1], evidence=(synthetic_evidence_info(),)),
        ),
    )
    response = await client.get(PATH, headers=headers())
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert items[0]["evidence"] == []
    assert items[1]["evidence"] == [
        {
            "id": "66666666-6666-4666-8666-666666666666",
            "position": 1,
            "metric": "steps",
            "periodStart": "2026-10-12",
            "periodEnd": "2026-10-18",
            "snapshot": SYNTHETIC_SNAPSHOT,
        },
    ]


async def test_messages_foreign_conversation_skips_evidence_lookup(
    client, messages_api, monkeypatch
) -> None:
    """실제 저장소의 대화 소유권 조회가 빈 결과 → 404이며 근거 조회 함수는 호출하지 않는다."""
    _, db, _, headers = messages_api
    db.execute.return_value = Mock(one_or_none=Mock(return_value=None))
    monkeypatch.setattr(repository, "find_message_page", stored_message_page)
    evidence_lookup = AsyncMock()
    monkeypatch.setattr(repository, "_list_evidence", evidence_lookup)
    response = await client.get(PATH, headers=headers(OTHER_ID))
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "CONVERSATION_NOT_FOUND",
            "message": "대화를 찾을 수 없습니다.",
        }
    }
    evidence_lookup.assert_not_called()
    assert db.execute.await_count == 1


def make_message_rows(messages: tuple[HistoryMessage, ...]) -> list[SimpleNamespace]:
    """합성 이력을 실제 메시지 SELECT와 같은 컬럼과 순번 내림차순으로 변환한다."""
    return [
        SimpleNamespace(
            id=item.message.id,
            role=item.message.role,
            sequence_no=item.message.sequence_no,
            content=item.message.content,
            created_at=item.message.created_at,
            request_id=item.request_id,
            status=item.request_status,
            error_code=item.error_code,
            error_context=item.error_context,
        )
        for item in sorted(
            messages, key=lambda item: item.message.sequence_no, reverse=True
        )
    ]


async def test_messages_mixed_states_only_loads_completed_assistant_evidence(
    monkeypatch,
) -> None:
    """R6 완료·생성·실패 혼합 페이지 → 페이지 안의 완료 AI ID만 근거 조회에 전달한다."""
    page = make_page()
    statuses = ("completed", "generating", "failed")
    messages = tuple(
        replace(item, request_status=statuses[index // 2])
        for index, item in enumerate(page.messages)
    )
    lookahead = replace(
        messages[1],
        request_id=uuid4(),
        message=replace(messages[1].message, id=uuid4(), sequence_no=0),
    )
    db = AsyncMock(spec=AsyncSession)
    db.execute.side_effect = [
        Mock(one_or_none=Mock(return_value=page.conversation)),
        Mock(all=Mock(return_value=make_message_rows((*messages, lookahead)))),
    ]
    completed_id = messages[1].message.id
    expected = (synthetic_evidence_info(), synthetic_evidence_info(2))
    evidence_lookup = AsyncMock(return_value={completed_id: expected})
    monkeypatch.setattr(repository, "_list_evidence", evidence_lookup)

    result = await stored_message_page(
        db,
        user_id=OWNER_ID,
        conversation_id=CONVERSATION_ID,
        limit=6,
        before_sequence_no=None,
    )

    assert result is not None
    evidence_lookup.assert_awaited_once_with(db, [completed_id])
    assert [item.evidence for item in result.messages] == [(), expected, (), (), (), ()]
    assert [item.message.id for item in result.messages] == [
        item.message.id for item in messages
    ]
    assert result.has_more
    assert db.execute.await_count == 2
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.parametrize(
    "status,role",
    [
        ("accepted", "assistant"),
        ("generating", "assistant"),
        ("failed", "assistant"),
        ("completed", "user"),
    ],
)
async def test_messages_without_completed_assistant_skips_evidence_select(
    status, role
) -> None:
    """R5·R6 완료 AI가 없는 페이지 → 근거 SELECT 없이 모든 메시지의 근거를 비워 반환한다."""
    page = make_page()
    messages = tuple(
        replace(item, request_status=status)
        for item in page.messages
        if item.message.role == role
    )
    db = AsyncMock(spec=AsyncSession)
    db.execute.side_effect = [
        Mock(one_or_none=Mock(return_value=page.conversation)),
        Mock(all=Mock(return_value=make_message_rows(messages))),
    ]

    result = await stored_message_page(
        db,
        user_id=OWNER_ID,
        conversation_id=CONVERSATION_ID,
        limit=50,
        before_sequence_no=None,
    )

    assert result is not None
    assert len(result.messages) == 3
    assert all(item.evidence == () for item in result.messages)
    assert db.execute.await_count == 2
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
    db.flush.assert_not_called()
