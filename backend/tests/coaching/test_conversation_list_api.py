"""합성 대화와 실제 인증·HTTP 경계로 목록 및 커서 계약을 검사한다."""

import base64
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import Response
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import tokens
from app.coaching import repository
from app.coaching.repository import (
    ConversationKey,
    ConversationListItem,
    ConversationPage,
    ConversationRecord,
)
from app.coaching.schemas import (
    ConversationPageQuery,
    decode_conversation_cursor,
    encode_conversation_cursor,
)
from app.core.db import get_session

NOW = datetime(2026, 10, 19, 1, 0, 0, 123456, tzinfo=UTC)
OWNER_ID = UUID("66666666-6666-4666-8666-666666666666")
OTHER_ID = UUID("77777777-7777-4777-8777-777777777777")
CONVERSATION_ID = UUID("22222222-2222-4222-8222-222222222222")
PATH = "/api/v1/coaching/conversations"
CURSOR = (
    base64.urlsafe_b64encode(b"1.1792371600123456.22222222222242228222222222222222")
    .rstrip(b"=")
    .decode("ascii")
)


def raw_cursor(value: bytes) -> str:
    """구현 함수와 독립적으로 합성 평문 바이트를 base64url로 인코딩한다."""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


PAD_BITS_CURSOR = raw_cursor(b"1.10.22222222222242228222222222222222")


INVALID_CURSORS = [
    pytest.param("synthetic-forged-cursor", id="forged"),
    pytest.param(CURSOR[:-3], id="truncated"),
    pytest.param(raw_cursor(b"2.0.22222222222242228222222222222222"), id="version"),
    pytest.param(raw_cursor(b"2."), id="version-only"),
    pytest.param(raw_cursor(b"1.0"), id="missing-part"),
    pytest.param(
        raw_cursor(b"1.0.22222222222242228222222222222222.x"), id="extra-part"
    ),
    pytest.param(
        raw_cursor(b"1.0001.22222222222242228222222222222222"), id="leading-zero"
    ),
    pytest.param(
        raw_cursor(b"1.100000000000000000.22222222222242228222222222222222"),
        id="18-digits",
    ),
    pytest.param(raw_cursor(b"1.-1.22222222222242228222222222222222"), id="negative"),
    pytest.param(raw_cursor(b"1.0.AAAAAAAAaaaa4aaa8aaaaaaaaaaaaaaa"), id="uppercase"),
    pytest.param(raw_cursor(b"1.0.22222222-2222-4222-8222-222222222222"), id="hyphens"),
    pytest.param(raw_cursor(b"1.0." + b"a" * 31), id="short-hex"),
    pytest.param(raw_cursor(b"1.0." + b"a" * 33), id="long-hex"),
    pytest.param(raw_cursor(b"1.0.gggggggggggg4ggg8ggggggggggggggg"), id="nonhex"),
    pytest.param(raw_cursor(b"\xff"), id="non-ascii"),
    # 37바이트 평문의 마지막 'Mg'를 'Mh'로 바꾸면 해독 바이트는 같고 남는 비트만 다르다.
    pytest.param(PAD_BITS_CURSOR[:-1] + "h", id="noncanonical-bits"),
    pytest.param(CURSOR + "==", id="padding"),
    pytest.param(CURSOR + "+", id="plus"),
    pytest.param(CURSOR + "/", id="slash"),
    pytest.param(CURSOR + ".", id="dot"),
    pytest.param(CURSOR + " ", id="space"),
    pytest.param(CURSOR + "\n", id="newline"),
    pytest.param("한글", id="unicode"),
    pytest.param("Z", id="invalid-base64-length"),
    pytest.param("", id="empty"),
    pytest.param("A" * 129, id="too-long"),
]


def fixed_now() -> datetime:
    """합성 토큰 발급·검증에 사용할 UTC 시각을 반환한다."""
    return NOW


def expired_now() -> datetime:
    """합성 액세스 토큰이 만료된 UTC 시각을 반환한다."""
    return NOW + timedelta(minutes=15)


def make_page() -> ConversationPage:
    """네 요청 상태와 요약 유무가 섞인 최근 활동순 합성 페이지를 만든다."""
    return ConversationPage(
        items=tuple(
            ConversationListItem(
                conversation=ConversationRecord(
                    id=UUID(value),
                    title="합성 목록 질문",
                    summary="합성 요약" if index % 2 == 0 else None,
                    created_at=NOW,
                    updated_at=NOW + timedelta(seconds=3 - index),
                ),
                last_request_status=status,
            )
            for index, (value, status) in enumerate(
                (
                    ("55555555-5555-4555-8555-555555555555", "accepted"),
                    ("44444444-4444-4444-8444-444444444444", "generating"),
                    ("33333333-3333-4333-8333-333333333333", "failed"),
                    ("22222222-2222-4222-8222-222222222222", "completed"),
                )
            )
        ),
        has_more=False,
    )


def assert_http_headers(response: Response) -> None:
    """HTTP 응답의 캐시 금지·UUIDv4 추적 및 인증 오류 헤더를 검사한다."""
    assert response.headers["cache-control"] == "no-store"
    request_id = response.headers["x-request-id"]
    assert UUID(request_id).version == 4
    assert str(UUID(request_id)) == request_id
    if response.status_code == 401:
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.fixture
def conversations_api(app, settings, monkeypatch):
    """합성 회원별 페이지를 제공하고 저장소 호출 인자와 쓰기 금지를 검사한다."""
    monkeypatch.setattr(tokens, "_utc_now", fixed_now)
    pages = {OWNER_ID: make_page()}
    db = AsyncMock(spec=AsyncSession)

    async def read_page(
        db_session: AsyncSession,
        *,
        user_id: UUID,
        limit: int,
        after: ConversationKey | None,
    ) -> ConversationPage:
        """검증된 정렬 키와 회원별 합성 데이터로 다음 페이지를 반환한다."""
        assert db_session is db
        assert isinstance(user_id, UUID)
        assert type(limit) is int
        assert after is None or isinstance(after, ConversationKey)
        page = pages.get(user_id, ConversationPage(items=(), has_more=False))
        items = tuple(
            item
            for item in page.items
            if after is None
            or (item.conversation.updated_at, item.conversation.id.bytes)
            < (after.updated_at, after.id.bytes)
        )
        return replace(page, items=items[:limit], has_more=len(items) > limit)

    lookup = AsyncMock(side_effect=read_page)
    monkeypatch.setattr(repository, "list_conversations", lookup)

    async def session() -> AsyncIterator[AsyncSession]:
        """실제 DB 연결 없이 조회용 가짜 세션을 제공한다."""
        yield db

    def headers(user_id: UUID = OWNER_ID) -> dict[str, str]:
        """합성 회원 UUIDv4의 정상 토큰으로 인증 헤더를 만든다."""
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


@pytest.mark.parametrize(
    "stamp",
    [
        NOW,
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(5000, 1, 1, 0, 0, 0, 999999, tzinfo=UTC),
        NOW.astimezone(timezone(timedelta(hours=9))),
    ],
)
def test_cursor_roundtrip_valid_timestamp_preserves_key(stamp) -> None:
    """마이크로초·에포크·먼 미래·UTC 외 시각 → 인코딩과 디코딩 뒤 같은 키를 받는다."""
    key = ConversationKey(updated_at=stamp, id=CONVERSATION_ID)
    assert decode_conversation_cursor(encode_conversation_cursor(key)) == key


def test_cursor_encoding_known_timestamp_matches_literal() -> None:
    """알려진 마이크로초 시각 → 독립적으로 만든 평문과 정확히 같은 커서를 받는다."""
    assert encode_conversation_cursor(ConversationKey(NOW, CONVERSATION_ID)) == CURSOR
    assert PAD_BITS_CURSOR.endswith("Mg")
    assert base64.urlsafe_b64decode(
        PAD_BITS_CURSOR[:-1] + "h=="
    ) == base64.urlsafe_b64decode(PAD_BITS_CURSOR + "==")


@pytest.mark.parametrize(
    "stamp",
    [NOW.replace(tzinfo=None), datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=UTC)],
)
def test_cursor_encoding_invalid_timestamp_raises_value_error(stamp) -> None:
    """시간대 누락·에포크 이전 시각 → 서버 인코딩 오류를 ValueError로 알린다."""
    with pytest.raises(ValueError):
        encode_conversation_cursor(ConversationKey(stamp, CONVERSATION_ID))


@pytest.mark.parametrize("value", INVALID_CURSORS)
def test_cursor_decoding_malformed_value_raises_safe_value_error(value) -> None:
    """형식·버전·길이·정규 표기 오류 → 입력을 담지 않은 ValueError로 거부한다."""
    with pytest.raises(ValueError) as caught:
        decode_conversation_cursor(value)
    if value:
        assert value not in str(caught.value)


@pytest.mark.parametrize("value", [None, 1, True, b"synthetic-cursor", {}, []])
def test_cursor_decoding_nonstring_value_raises_value_error(value) -> None:
    """문자열이 아닌 커서 입력 → 타입에 상관없이 ValueError로 거부한다."""
    with pytest.raises(ValueError):
        decode_conversation_cursor(value)


def test_query_model_defaults_and_cursor_preserves_strict_dataclass() -> None:
    """생략·정수 기본값·문자열 커서 → strict 모델에서 int와 ConversationKey를 받는다."""
    default = ConversationPageQuery.model_validate({})
    explicit = ConversationPageQuery.model_validate({"limit": 20, "cursor": CURSOR})
    optional = ConversationPageQuery.model_validate({"cursor": None})
    assert default.limit == explicit.limit == 20
    assert type(default.limit) is int
    assert default.cursor is optional.cursor is None
    assert isinstance(explicit.cursor, ConversationKey)
    assert explicit.cursor == ConversationKey(NOW, CONVERSATION_ID)


@pytest.mark.parametrize(
    "value", [True, 5, 5.0, b"synthetic", {}, [], ConversationKey(NOW, CONVERSATION_ID)]
)
def test_query_model_nonstring_cursor_returns_invalid_type(value) -> None:
    """문자열·생략값 이외의 커서 → dataclass 입력도 invalid_type으로 거부한다."""
    with pytest.raises(ValidationError) as caught:
        ConversationPageQuery.model_validate({"cursor": value})
    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        (("cursor",), "invalid_type")
    ]


@pytest.mark.parametrize(
    "value,reason",
    [
        (True, "invalid_type"),
        (5.0, "invalid_type"),
        (None, "invalid_type"),
        (0, "out_of_range"),
        (101, "out_of_range"),
    ],
)
def test_query_model_invalid_limit_returns_expected_reason(value, reason) -> None:
    """bool·소수·null·범위 밖 limit → 기본값 경로와 같은 검사기로 거부한다."""
    with pytest.raises(ValidationError) as caught:
        ConversationPageQuery.model_validate({"limit": value})
    assert [(error["loc"], error["type"]) for error in caught.value.errors()] == [
        (("limit",), reason)
    ]


async def test_conversations_empty_owner_returns_empty_page(
    client, conversations_api
) -> None:
    """본인 대화가 없음 → 빈 items와 null nextCursor를 200으로 받는다."""
    _, _, _, headers = conversations_api
    response = await client.get(PATH, headers=headers(OTHER_ID))
    assert response.status_code == 200
    assert response.json() == {"data": {"items": [], "nextCursor": None}}
    assert_http_headers(response)


async def test_conversations_all_statuses_returns_literal_contract(
    client, conversations_api
) -> None:
    """접수·생성·실패·완료와 요약 유무 → 정확한 여섯 필드와 UTC 마이크로초를 받는다."""
    pages, db, lookup, headers = conversations_api
    offset = timezone(timedelta(hours=9))
    page = pages[OWNER_ID]
    pages[OWNER_ID] = replace(
        page,
        items=tuple(
            replace(
                item,
                conversation=replace(
                    item.conversation,
                    created_at=item.conversation.created_at.astimezone(offset),
                    updated_at=item.conversation.updated_at.astimezone(offset),
                ),
            )
            for item in page.items
        ),
    )
    response = await client.get(PATH, headers=headers() | {"X-User-Id": str(OTHER_ID)})
    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "items": [
                {
                    "id": "55555555-5555-4555-8555-555555555555",
                    "title": "합성 목록 질문",
                    "summary": "합성 요약",
                    "createdAt": "2026-10-19T01:00:00.123456Z",
                    "updatedAt": "2026-10-19T01:00:03.123456Z",
                    "lastRequestStatus": "accepted",
                },
                {
                    "id": "44444444-4444-4444-8444-444444444444",
                    "title": "합성 목록 질문",
                    "summary": None,
                    "createdAt": "2026-10-19T01:00:00.123456Z",
                    "updatedAt": "2026-10-19T01:00:02.123456Z",
                    "lastRequestStatus": "generating",
                },
                {
                    "id": "33333333-3333-4333-8333-333333333333",
                    "title": "합성 목록 질문",
                    "summary": "합성 요약",
                    "createdAt": "2026-10-19T01:00:00.123456Z",
                    "updatedAt": "2026-10-19T01:00:01.123456Z",
                    "lastRequestStatus": "failed",
                },
                {
                    "id": "22222222-2222-4222-8222-222222222222",
                    "title": "합성 목록 질문",
                    "summary": None,
                    "createdAt": "2026-10-19T01:00:00.123456Z",
                    "updatedAt": "2026-10-19T01:00:00.123456Z",
                    "lastRequestStatus": "completed",
                },
            ],
            "nextCursor": None,
        }
    }
    data = response.json()["data"]
    assert len(data) == 2
    assert all(
        list(item)
        == ["id", "title", "summary", "createdAt", "updatedAt", "lastRequestStatus"]
        for item in data["items"]
    )
    lookup.assert_awaited_once_with(db, user_id=OWNER_ID, limit=20, after=None)
    assert_http_headers(response)


@pytest.mark.parametrize(
    "query,after",
    [
        ({}, None),
        ({"limit": "020"}, None),
        ({"cursor": CURSOR}, ConversationKey(NOW, CONVERSATION_ID)),
    ],
)
async def test_conversations_valid_query_passes_decoded_arguments(
    client, conversations_api, query, after
) -> None:
    """쿼리 생략·앞자리 0·정상 커서 → limit 20과 마이크로초가 보존된 키를 전달한다."""
    _, db, lookup, headers = conversations_api
    response = await client.get(PATH, params=query, headers=headers())
    assert response.status_code == 200
    lookup.assert_awaited_once_with(db, user_id=OWNER_ID, limit=20, after=after)
    assert_http_headers(response)


async def test_conversations_next_cursor_uses_last_item_and_continues(
    client, conversations_api
) -> None:
    """다음 페이지가 있음 → 마지막 항목 커서로 이어 읽고 정확히 limit개인 끝에서 멈춘다."""
    _, db, lookup, headers = conversations_api
    first = await client.get(PATH, params={"limit": "2"}, headers=headers())
    expected = raw_cursor(b"1.1792371602123456.44444444444444448444444444444444")
    assert first.status_code == 200
    assert first.json()["data"]["nextCursor"] == expected
    second = await client.get(
        PATH, params={"limit": "2", "cursor": expected}, headers=headers()
    )
    assert second.status_code == 200
    lookup.assert_awaited_with(
        db,
        user_id=OWNER_ID,
        limit=2,
        after=ConversationKey(
            NOW + timedelta(seconds=2), UUID("44444444-4444-4444-8444-444444444444")
        ),
    )
    assert [
        item["id"]
        for item in first.json()["data"]["items"] + second.json()["data"]["items"]
    ] == [
        "55555555-5555-4555-8555-555555555555",
        "44444444-4444-4444-8444-444444444444",
        "33333333-3333-4333-8333-333333333333",
        "22222222-2222-4222-8222-222222222222",
    ]
    assert second.json()["data"]["nextCursor"] is None
    assert_http_headers(first)
    assert_http_headers(second)


@pytest.mark.parametrize("value", INVALID_CURSORS)
async def test_conversations_invalid_cursor_returns_safe_details(
    client, conversations_api, value
) -> None:
    """변조·잘림·비정규 표기 커서 → 저장소 호출 없이 원문 없는 invalid_format을 받는다."""
    _, _, lookup, headers = conversations_api
    response = await client.get(PATH, params={"cursor": value}, headers=headers())
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "요청 내용을 확인해 주세요.",
            "details": [{"field": "cursor", "reason": "invalid_format"}],
        }
    }
    if value:
        assert value not in response.text
    lookup.assert_not_called()
    assert_http_headers(response)


@pytest.mark.parametrize(
    "field,value,details",
    [
        ("limit", "0", [{"field": "limit", "reason": "out_of_range"}]),
        ("limit", "101", [{"field": "limit", "reason": "out_of_range"}]),
        ("limit", "-1", [{"field": "limit", "reason": "out_of_range"}]),
        ("limit", "abc", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "1.5", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "+5", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", " 5", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "5 ", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "\uff120", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "1e2", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "1_0", [{"field": "limit", "reason": "invalid_type"}]),
        ("limit", "1" * 21, [{"field": "limit", "reason": "out_of_range"}]),
        (
            "next_cursor",
            "synthetic-private-input",
            [{"field": "next_cursor", "reason": "extra_forbidden"}],
        ),
        ("user_id", str(OTHER_ID), [{"field": "user_id", "reason": "extra_forbidden"}]),
        ("userId", str(OTHER_ID), [{"field": "userId", "reason": "extra_forbidden"}]),
    ],
)
async def test_conversations_invalid_query_returns_safe_details(
    client, conversations_api, field, value, details
) -> None:
    """범위·타입·snake_case·미정의 쿼리 → 저장소 호출 없이 지정된 reason을 받는다."""
    _, _, lookup, headers = conversations_api
    response = await client.get(PATH, params={field: value}, headers=headers())
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "요청 내용을 확인해 주세요.",
            "details": details,
        }
    }
    assert "synthetic-private-input" not in response.text
    assert str(OTHER_ID) not in response.text
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
            [("cursor", CURSOR), ("cursor", CURSOR)],
            None,
            [{"field": "cursor", "reason": "duplicate"}],
        ),
        (
            [],
            '{"synthetic-private-body": true}',
            [{"field": "body", "reason": "extra_forbidden"}],
        ),
    ],
)
async def test_conversations_duplicate_query_or_body_returns_422(
    client, conversations_api, query, body, details
) -> None:
    """쿼리 중복·GET 본문 → 저장소 호출 없이 duplicate·extra_forbidden을 받는다."""
    _, _, lookup, headers = conversations_api
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
    assert CURSOR not in response.text
    lookup.assert_not_called()
    assert_http_headers(response)


@pytest.mark.parametrize(
    "mode", ["missing", "invalid", "wrong_scheme", "wrong_key", "duplicate", "expired"]
)
async def test_conversations_invalid_auth_returns_401(
    client, conversations_api, settings, monkeypatch, mode
) -> None:
    """누락·형식·서명·중복·만료 토큰 → 저장소 호출 없이 인증 오류와 Bearer 헤더를 받는다."""
    _, _, lookup, headers = conversations_api
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
async def test_conversations_database_error_returns_safe_500(
    client, conversations_api, caplog, error
) -> None:
    """연결·운영 오류 → 안전한 500을 받고 응답·로그에 예외·회원·토큰·제목이 없다."""
    _, db, lookup, headers = conversations_api
    lookup.side_effect = error
    auth = headers()
    response = await client.get(PATH, headers=auth)
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "요청을 처리하지 못했습니다."}
    }
    lookup.assert_awaited_once_with(db, user_id=OWNER_ID, limit=20, after=None)
    combined = response.text + caplog.text
    for private in (
        str(error),
        "synthetic-private",
        str(OWNER_ID),
        "합성 목록 질문",
        auth["Authorization"].removeprefix("Bearer "),
    ):
        assert private not in combined
    assert all(record.exc_info is None for record in caplog.records)
    assert_http_headers(response)


@pytest.mark.parametrize("method", ["POST", "DELETE"])
async def test_conversations_unsupported_method_returns_405(
    client, conversations_api, method
) -> None:
    """POST·DELETE → 저장소 호출 없이 405 METHOD_NOT_ALLOWED를 받는다."""
    _, _, lookup, headers = conversations_api
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
            {"cursor": "synthetic-invalid"},
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
async def test_conversations_unauthenticated_invalid_query_preserves_precedence(
    client, conversations_api, query, status, expected
) -> None:
    """토큰 없는 잘못된 커서·미정의 쿼리 → 각각 401·422로 기존 검증 순서를 지킨다."""
    _, _, lookup, _ = conversations_api
    response = await client.get(PATH, params=query)
    assert response.status_code == status
    assert response.json() == expected
    lookup.assert_not_called()
    assert_http_headers(response)
