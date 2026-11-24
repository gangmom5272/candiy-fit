"""프로필은 요청마다 본인 값으로 조회하며 실패한 요청은 LLM을 부르지 않는다."""

from dataclasses import replace
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.core.errors import TemporaryStorageError
from app.users import service
from tests.coaching.test_coaching_runner import Harness


async def test_runner_reads_latest_profile_for_owner_before_llm(monkeypatch):
    harness = Harness()
    lookup = AsyncMock(
        return_value=service.BodyProfile(Decimal("170"), Decimal("65"), Decimal("22.5"))
    )
    monkeypatch.setattr(service, "get_body_profile", lookup)
    await harness.submit()
    assert lookup.await_args.args[1] == harness.user_id
    assert harness.script.calls[0]["ctx"].profile.bmi == Decimal("22.5")
    assert harness.factory.active == 0
    assert harness.store.state(harness.request.id).status == "completed"

    lookup.return_value = service.BodyProfile(
        Decimal("170"), Decimal("68"), Decimal("23.5")
    )
    request = harness.store.seed_accepted(
        user_id=harness.user_id,
        conversation_id=harness.request.conversation_id,
        content="다시 알려줘",
    )
    await harness.runner.submit(
        replace(harness.job, request=request, question="다시 알려줘")
    )
    assert lookup.await_count == 2
    assert harness.script.calls[1]["ctx"].profile.weight_kg == Decimal("68")
    assert harness.script.calls[1]["ctx"].profile.bmi == Decimal("23.5")


@pytest.mark.parametrize(
    "error",
    [
        service.UserNotFoundError(),
        TemporaryStorageError("storage_connection_unavailable"),
    ],
)
async def test_runner_profile_failure_does_not_call_model(monkeypatch, error):
    harness = Harness()
    monkeypatch.setattr(service, "get_body_profile", AsyncMock(side_effect=error))
    await harness.submit()
    assert harness.script.calls == []
    harness.assert_failure("INTERNAL_ERROR")


async def test_profile_session_closed_before_model(monkeypatch):
    harness = Harness()
    original = harness.agent.run_with_history

    async def checked(**kwargs):
        assert harness.factory.active == 0
        assert kwargs["ctx"].profile.bmi == Decimal("22.5")
        return await original(**kwargs)

    monkeypatch.setattr(harness.agent, "run_with_history", checked)
    await harness.submit()
    assert harness.store.state(harness.request.id).status == "completed"
