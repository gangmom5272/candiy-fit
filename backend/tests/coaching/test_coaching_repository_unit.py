"""DB 없이 코칭 입력 해시와 저장 전 입력 검증을 검사한다."""

import hashlib
import re
from datetime import UTC, datetime, timedelta, tzinfo
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.coaching import repository

CONVERSATION_ID = UUID("22222222-2222-4222-8222-222222222222")
NOW = datetime(2026, 10, 23, tzinfo=UTC)


def test_input_hash_is_deterministic_lowercase_sha256():
    result = repository.input_hash(CONVERSATION_ID, "synthetic question")

    assert re.fullmatch(r"[0-9a-f]{64}", result)
    assert result == repository.input_hash(CONVERSATION_ID, "synthetic question")


def test_input_hash_distinguishes_original_conversation_and_content():
    hashes = {
        repository.input_hash(None, "question"),
        repository.input_hash(CONVERSATION_ID, "question"),
        repository.input_hash(UUID(int=1), "question"),
        repository.input_hash(None, "different question"),
        repository.input_hash(None, " question "),
    }

    assert len(hashes) == 5


def test_input_hash_preserves_korean_and_emoji_in_canonical_json():
    content = "합성 질문 🏃🙂"
    canonical = '{"content":"합성 질문 🏃🙂","conversationId":null}'
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    assert repository.input_hash(None, content) == expected
    assert repository.input_hash(None, content) == repository.input_hash(None, content)


class MissingOffset(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None


@pytest.mark.parametrize(
    "now", [NOW.replace(tzinfo=None), NOW.replace(tzinfo=MissingOffset())]
)
@pytest.mark.parametrize("operation", ["accept", "complete", "fail", "interrupt"])
async def test_naive_now_is_rejected_before_database_access(now, operation):
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(ValueError, match="timezone-aware"):
        if operation == "accept":
            await repository.accept(
                session,
                user_id=uuid4(),
                client_request_id=uuid4(),
                conversation_id=None,
                content="synthetic question",
                now=now,
            )
        elif operation == "complete":
            await repository.complete(session, request_id=uuid4(), answer="", now=now)
        elif operation == "fail":
            await repository.fail(
                session,
                request_id=uuid4(),
                error_code="GENERATION_TIMEOUT",
                error_context=None,
                partial_answer="",
                now=now,
            )
        else:
            await repository.interrupt_in_progress(session, now=now)

    assert session.mock_calls == []


async def test_fail_rejects_empty_error_code_before_database_access():
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(ValueError, match="error_code"):
        await repository.fail(
            session,
            request_id=uuid4(),
            error_code="",
            error_context=None,
            partial_answer="",
            now=NOW,
        )

    assert session.mock_calls == []
