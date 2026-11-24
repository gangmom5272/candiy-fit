"""실제 AI 이력 타입·대상 선정과 백엔드 경계를 외부 호출 없이 검사한다."""

import asyncio
from dataclasses import asdict, replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.coaching import summary_adapter
from app.coaching.agent import CoachAgent
from app.coaching.history import HistorySnapshot, StoredSummary
from app.coaching.summary import SummaryManager
from coach_ai.agent.context import CoachContext
from coach_ai.agent.graph import CoachResult
from coach_ai.history.tokens import TOKEN_POLICY_VERSION
from coach_ai.history.types import HistoryTurn as AIHistoryTurn
from coach_ai.history.types import HistoryUsage
from coach_ai.history.types import StoredSummary as AIStoredSummary
from coach_ai.history.types import SummaryError as AISummaryError
from coach_ai.history.types import SummaryPlan as AISummaryPlan
from coach_ai.history.types import SummaryResult as AISummaryResult
from tests.coaching.fakes import (
    MODEL,
    NOW,
    CountingModelFactory,
    RecordingPortFactory,
    fixed_now,
)
from tests.coaching.summary_fakes import SummaryFakeStore, SummarySessionFactory
from tests.coaching.test_coaching_runner import bounded_test
from tests.coaching.test_summary_contract import turn


@pytest.mark.parametrize("with_summary", [False, True])
@pytest.mark.parametrize("with_turns", [False, True])
@pytest.mark.parametrize("finish_reason", ["stop", "error"])
@bounded_test
async def test_agent_passes_ai_context_and_preserves_last_usage(
    with_summary: bool, with_turns: bool, finish_reason: str
) -> None:
    """요약·빈 이력·완료 쌍을 새 Context로 넘기고 마지막 사용량을 재계산하지 않는다."""
    usage = HistoryUsage(
        summary_used=False,
        included_turns=0,
        excluded_turns=7,
        history_tokens=0,
        summary_tokens=0,
    )
    result = CoachResult("합성 답변", [], finish_reason, history_usage=usage)
    run = AsyncMock(return_value=result)
    factory = CountingModelFactory()
    agent = CoachAgent(
        model_factory=factory, port_factory=RecordingPortFactory(), run=run
    )
    assert agent.ensure_ready()
    summary = StoredSummary("  합성 누적 요약  ", 2) if with_summary else None
    turns = (replace(turn(5), answer=""), turn(9)) if with_turns else ()
    snapshot = HistorySnapshot(summary, turns, 11)
    ctx = CoachContext(
        user_id="user", conversation_id="conversation", request_id="request"
    )
    received = await agent.run_with_history(
        ctx=ctx,
        question="현재 질문",
        history=snapshot,
        sink=AsyncMock(),
        now=NOW,
        message_id="current-answer",
    )
    assert received is result
    assert received.history_usage is usage
    args, kwargs = run.call_args
    actual = args[0]
    assert actual == replace(
        ctx,
        summary=AIStoredSummary(**asdict(summary)) if summary else None,
        turns=tuple(AIHistoryTurn(**asdict(item)) for item in turns),
    )
    assert ctx.summary is None and ctx.turns == ()
    assert args[1] == "현재 질문"
    assert "summary" not in kwargs and "history" not in kwargs
    assert kwargs["model"] is agent.get_model() is MODEL
    assert factory.calls == 1


@bounded_test
async def test_agent_preserves_no_llm_history_usage() -> None:
    """LLM을 호출하지 않은 결과의 None 사용량을 임의로 만들지 않는다."""
    result = CoachResult("합성 안내", [], "guard", history_usage=None)
    agent = CoachAgent(
        model_factory=CountingModelFactory(),
        port_factory=RecordingPortFactory(),
        run=AsyncMock(return_value=result),
    )
    assert agent.ensure_ready()
    received = await agent.run_with_history(
        ctx=CoachContext(
            user_id="user", conversation_id="conversation", request_id="request"
        ),
        question="현재 질문",
        history=HistorySnapshot(None, (), 1),
        sink=AsyncMock(),
        now=NOW,
        message_id="answer",
    )
    assert received is result and received.history_usage is None


def test_model_access_requires_answer_model_preparation() -> None:
    """요약 접근만으로 새 모델이나 설정을 만들지 않는다."""
    factory = CountingModelFactory()
    agent = CoachAgent(model_factory=factory, port_factory=RecordingPortFactory())
    with pytest.raises(RuntimeError):
        agent.get_model()
    assert factory.calls == 0


@pytest.mark.parametrize("last_tokens, planned", [(399, False), (400, True)])
def test_real_plan_preserves_threshold_summary_and_pairs(
    last_tokens: int, planned: bool
) -> None:
    """실제 계획의 2399/2400 경계와 요약 뒤 첫 쌍 선택을 그대로 변환한다."""
    functions = summary_adapter.ai_summary_functions(model_provider=lambda: MODEL)
    summary = StoredSummary("합성 기존 요약", 2)
    turns = tuple(
        replace(turn(seq), question="1" * tokens, answer="")
        for seq, tokens in ((3, 1000), (5, 1000), (7, last_tokens))
    )
    plan = functions.plan_summary(summary, turns)
    assert functions.policy_version == TOKEN_POLICY_VERSION
    if not planned:
        assert plan is None
    else:
        assert plan is not None
        assert plan.targets == turns[:1]
        assert plan.until_seq == 4
        assert plan.unsummarized_tokens == 2400
        assert plan.policy_version == TOKEN_POLICY_VERSION


@pytest.mark.parametrize("invalid", [None, "tokens", "range", "policy"])
@bounded_test
async def test_real_plan_and_fake_generation_reach_manager_storage(
    invalid: str | None,
) -> None:
    """실제 계획·모델·기존 요약으로 생성하고 범위·800상한·정책 검증 뒤만 저장한다."""
    store = SummaryFakeStore()
    sessions = SummarySessionFactory(store)
    user_id = uuid4()
    conversation_id = None
    for tokens in (10, 1000, 1000, 400):
        state = store.seed_accepted(
            user_id=user_id, conversation_id=conversation_id, content="1" * tokens
        )
        conversation_id = state.conversation_id
        store._finish(
            state.id, content="", now=NOW, error_code=None, error_context=None
        )
    previous = StoredSummary("합성 기존 요약", 2)
    store.summaries[conversation_id] = previous
    factory = CountingModelFactory()
    agent = CoachAgent(model_factory=factory, port_factory=RecordingPortFactory())
    assert agent.ensure_ready()
    calls: list[AISummaryPlan] = []

    async def generate(plan: AISummaryPlan, *, model) -> AISummaryResult:
        """모델·누적 요약·식별자·원문을 관측하고 합성 생성 결과를 반환한다."""
        assert model is agent.get_model() is MODEL
        assert sessions.active == 0
        assert plan.summary == AIStoredSummary(text=previous.text, until_seq=2)
        assert len(plan.targets) == 1 and plan.targets[0].question == "1" * 1000
        assert plan.targets[0].question_seq == 3 and plan.targets[0].answer_seq == 4
        assert plan.unsummarized_tokens == 2400
        calls.append(plan)
        return AISummaryResult(
            text="합성 새 누적 요약",
            until_seq=6 if invalid == "range" else plan.until_seq,
            tokens=801 if invalid == "tokens" else 10,
            policy_version="other" if invalid == "policy" else plan.policy_version,
        )

    manager = SummaryManager(
        session_factory=sessions,
        store=store,
        clock=fixed_now,
        functions=summary_adapter.ai_summary_functions(
            model_provider=agent.get_model, generate=generate
        ),
    )
    task = manager.submit(user_id=user_id, conversation_id=conversation_id)
    assert task is not None
    await task
    assert len(calls) == 1 and factory.calls == 1
    if invalid is None:
        assert store.summaries[conversation_id] == StoredSummary("합성 새 누적 요약", 4)
        assert sessions.commits == 1 and store.saves == 1
    else:
        assert store.summaries[conversation_id] == previous
        assert sessions.commits == 0 and store.saves == 0
        await manager.submit(user_id=user_id, conversation_id=conversation_id)
        assert len(calls) == 1 and store.saves == 0


@pytest.mark.parametrize(
    "code,retryable",
    [
        ("llm_unavailable", True),
        ("rate_limited", True),
        ("timeout", True),
        ("truncated", False),
        ("invalid_format", False),
        ("input_too_large", False),
        ("config", False),
    ],
)
@bounded_test
async def test_ai_summary_errors_keep_classification(
    code: str, retryable: bool
) -> None:
    """실제 AI 오류 생성자의 재시도 분류만 작업 관리 오류로 변환한다."""
    generate = AsyncMock(side_effect=AISummaryError(code))
    functions = summary_adapter.ai_summary_functions(
        model_provider=lambda: MODEL, generate=generate
    )
    turns = (replace(turn(), question="1" * 3101, answer=""),)
    plan = functions.plan_summary(None, turns)
    assert plan is not None
    with pytest.raises(summary_adapter.SummaryError) as caught:
        await functions.generate_summary(None, plan)
    assert caught.value.code == code and caught.value.retryable is retryable
    assert str(caught.value) == code and caught.value.retry_after is None


@bounded_test
async def test_ai_summary_adapter_propagates_cancellation() -> None:
    """요약 생성 취소를 재시도 가능한 일반 오류로 바꾸지 않는다."""
    functions = summary_adapter.ai_summary_functions(
        model_provider=lambda: MODEL,
        generate=AsyncMock(side_effect=asyncio.CancelledError),
    )
    plan = functions.plan_summary(
        None, (replace(turn(), question="1" * 3101, answer=""),)
    )
    assert plan is not None
    with pytest.raises(asyncio.CancelledError):
        await functions.generate_summary(None, plan)
