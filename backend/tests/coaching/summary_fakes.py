"""운영에는 넣지 않는 합성 요약 계획·생성·저장 대체 구현이다."""

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.coaching.history import (
    HistorySnapshot,
    HistoryTurn,
    StoredSummary,
    SummaryPlan,
    SummaryResult,
    validate_plan,
    validate_result,
)
from app.coaching.summary_adapter import SummaryFunctions
from tests.coaching.fakes import FakeStore, RollbackSession, RollbackSessionFactory


class SyntheticSummary:
    """고정된 합성 결과와 호출 관측으로 서버 동작만 검사한다."""

    def __init__(self, *, max_pairs: int = 100) -> None:
        """합성 계획 크기·오류 대기열·생성 관측 지점을 준비한다."""
        self.max_pairs = max_pairs
        self.plans: list[tuple[StoredSummary | None, tuple[HistoryTurn, ...]]] = []
        self.calls: list[tuple[StoredSummary | None, SummaryPlan]] = []
        self.errors: list[BaseException] = []
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()
        self.suppress_cancel = False
        self.cancelled = False
        self.active = 0
        self.maximum = 0
        self.observe: Callable[[], None] | None = None
        self.result: SummaryResult | None = None
        self.plan_override: SummaryPlan | None = None

    def plan_summary(
        self,
        summary: StoredSummary | None,
        turns: Sequence[HistoryTurn],
    ) -> SummaryPlan | None:
        """계수 없이 테스트가 지정한 개수의 오래된 쌍만 고른다."""
        self.plans.append((summary, tuple(turns)))
        if self.plan_override is not None:
            return self.plan_override
        targets = tuple(turns[: self.max_pairs])
        return (
            SummaryPlan(targets, targets[-1].answer_seq, 2500, "synthetic-v1")
            if targets
            else None
        )

    async def generate_summary(
        self,
        summary: StoredSummary | None,
        plan: SummaryPlan,
    ) -> SummaryResult:
        """지연·오류·취소를 재현하고 실제 LLM 없는 합성 요약을 반환한다."""
        self.calls.append((summary, plan))
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            if self.observe is not None:
                self.observe()
            self.entered.set()
            if self.gate is not None:
                try:
                    await self.gate.wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    if not self.suppress_cancel:
                        raise
            if self.errors:
                raise self.errors.pop(0)
            return self.result or SummaryResult(
                f"합성 누적 요약 {plan.until_seq}",
                plan.until_seq,
                10,
                plan.policy_version,
            )
        finally:
            self.active -= 1

    def functions(self) -> SummaryFunctions:
        """실제 연결과 같은 주입 객체로 합성 함수들을 반환한다."""
        return SummaryFunctions(
            "synthetic-v1", self.plan_summary, self.generate_summary
        )


class SummaryFakeStore(FakeStore):
    """기존 접수·종결 가짜에 요약의 소유권·조건부 저장 검증을 더한다."""

    def __init__(self) -> None:
        """기존 합성 저장소와 조건부 실패 설정을 준비한다."""
        super().__init__()
        self.reject_save = False
        self.saves = 0

    async def save_summary(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        conversation_id: UUID,
        snapshot: HistorySnapshot,
        plan: SummaryPlan,
        result: SummaryResult,
        now: datetime,
    ) -> bool:
        """메모리의 현재 범위와 요약을 비교하고 성공 때만 새 요약을 보관한다."""
        self._record("save_summary", conversation_id=conversation_id)
        self.saves += 1
        current = await self.find_history_context(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
            before_sequence_no=snapshot.before_sequence_no,
        )
        if self.reject_save or current is None or current.summary != snapshot.summary:
            return False
        validate_plan(current, plan, plan.policy_version)
        validate_result(plan, result)
        self.summaries[conversation_id] = StoredSummary(result.text, plan.until_seq)
        return True


class SummaryRollbackSession(RollbackSession):
    """요약 저장 실패 시 메모리 요약도 세션 진입 상태로 복원한다."""

    async def __aenter__(self) -> "SummaryRollbackSession":
        """기존 요청·대화 사본과 요약 사본을 함께 보관한다."""
        await super().__aenter__()
        self.summaries = self.store.summaries.copy()
        return self

    async def rollback(self) -> None:
        """요약과 기존 상태를 함께 되돌린다."""
        self.store.summaries = self.summaries.copy()
        await super().rollback()


class SummarySessionFactory(RollbackSessionFactory):
    """요약을 포함한 직렬 트랜잭션의 rollback을 재현한다."""

    def __call__(self) -> SummaryRollbackSession:
        """각 단계가 독립적인 요약 세션을 사용하도록 만든다."""
        return SummaryRollbackSession(self)


class TestClock:
    """실제 대기 없이 재시도 간격·실패 대기를 관측하는 합성 시계다."""

    __test__ = False

    def __init__(self) -> None:
        """합성 시각·대기 기록과 재시도 보류 지점을 초기화한다."""
        self.value = 0.0
        self.delays: list[float] = []
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()
        self.observe: Callable[[], Any] | None = None

    def now(self) -> float:
        """현재 합성 단조 시각을 반환한다."""
        return self.value

    async def sleep(self, delay: float) -> None:
        """간격을 기록하고 필요한 경우 테스트가 풀 때까지 대기한다."""
        self.delays.append(delay)
        if self.observe is not None:
            self.observe()
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        self.value += delay
        await asyncio.sleep(0)
