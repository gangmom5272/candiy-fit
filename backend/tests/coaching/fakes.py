"""코칭 실행·접수·종료 테스트에서 공유하는 합성 저장소와 비동기 가짜다."""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.coaching import repository
from app.coaching.history import HistorySnapshot, HistoryTurn, StoredSummary
from app.coaching.repository import (
    Accepted,
    AcceptResult,
    Busy,
    Conflict,
    ConversationInfo,
    ConversationNotFound,
    Duplicate,
    EvidenceInfo,
    MessageInfo,
    RequestState,
)
from app.coaching.runner import GenerationJob
from app.users.models import User
from coach_ai.agent.context import CoachContext, CoachProfile
from coach_ai.agent.events import EventSink
from coach_ai.agent.graph import CoachResult
from coach_ai.agent.state import Evidence
from coach_ai.ports import HealthDataPort
from coach_ai.security.audit import AuditSink, ToolCallRecord
from coach_ai.tools.periods import DateRange
from coach_ai.tools.results import PeriodSpan

NOW = datetime(2026, 10, 25, 3, tzinfo=UTC)
MODEL = cast(BaseChatModel, object())
PORT = cast(HealthDataPort, object())
SYNTHETIC_PROFILE = CoachProfile(
    height_cm=Decimal("170"), weight_kg=Decimal("65"), bmi=Decimal("22.5")
)

# API v12 §2.3 전체 예시의 합성 원형을 그대로 보존한다.
EVIDENCE_IDS = (
    "66666666-6666-4666-8666-666666666666",
    "77777777-7777-4777-8777-777777777777",
)
SYNTHETIC_SNAPSHOT = {
    "metric": "steps",
    "period": {"start": "2026-10-12", "end": "2026-10-18"},
    "unit": "steps",
    "summary": {
        "avg": 1000,
        "daily_min": {"date": "2026-10-12", "value": 1000},
        "daily_max": {"date": "2026-10-12", "value": 1000},
        "total": 5000,
        "days_with_data": 5,
        "days_used_for_summary": 5,
        "completed_days": 7,
        "missing_days": [],
        "unsynced_days": ["2026-10-17", "2026-10-18"],
        "linked_from": "2026-10-01",
    },
    "days": [
        {
            "date": "2026-10-12",
            "value": 1000,
            "missing": False,
            "reason": None,
            "is_in_progress": False,
        },
        {
            "date": "2026-10-13",
            "value": 1000,
            "missing": False,
            "reason": None,
            "is_in_progress": False,
        },
        {
            "date": "2026-10-14",
            "value": 1000,
            "missing": False,
            "reason": None,
            "is_in_progress": False,
        },
        {
            "date": "2026-10-15",
            "value": 1000,
            "missing": False,
            "reason": None,
            "is_in_progress": False,
        },
        {
            "date": "2026-10-16",
            "value": 1000,
            "missing": False,
            "reason": None,
            "is_in_progress": False,
        },
        {
            "date": "2026-10-17",
            "value": None,
            "missing": True,
            "reason": "UNSYNCED",
            "is_in_progress": False,
        },
        {
            "date": "2026-10-18",
            "value": None,
            "missing": True,
            "reason": "UNSYNCED",
            "is_in_progress": False,
        },
    ],
    "in_progress": None,
    "data_status": {
        "last_data_at": "2026-10-16T14:59:00Z",
        "synced_until": "2026-10-16T14:59:00Z",
        "last_sync_status": "success",
        "last_success_at": "2026-10-19T00:00:00Z",
        "checked_at": "2026-10-19T01:00:00Z",
    },
    "detail": {
        "measured_min": None,
        "measured_max": None,
        "nights": [],
        "daily_ranges": [],
        "sessions": [],
    },
}


def synthetic_evidence(position: int = 1, *, id: str | None = None) -> Evidence:
    """명세의 합성 원형과 UUIDv4로 AI 근거를 만든다."""
    return Evidence(
        id=id or EVIDENCE_IDS[position - 1],
        position=position,
        tool="get_metric_summary",
        metric="steps",
        period=PeriodSpan(
            period="last_7_days",
            start=date(2026, 10, 12),
            end=date(2026, 10, 18),
            basis="날짜(KST)",
        ),
        summary="합성 요약 문장",
        snapshot=deepcopy(SYNTHETIC_SNAPSHOT),
    )


def synthetic_evidence_info(
    position: int = 1, *, id: UUID | None = None
) -> EvidenceInfo:
    """구현의 변환 함수를 사용하지 않고 저장 근거 입력을 만든다."""
    return EvidenceInfo(
        id=id or UUID(EVIDENCE_IDS[position - 1]),
        position=position,
        metric="steps",
        period_start=date(2026, 10, 12),
        period_end=date(2026, 10, 18),
        snapshot=deepcopy(SYNTHETIC_SNAPSHOT),
    )


def synthetic_tool_call(
    request_id: UUID, sequence_no: int = 1, **changes: Any
) -> ToolCallRecord:
    """허용된 합성 걸음 요약 호출의 AI 기록을 만들고 지정한 칸만 바꾼다."""
    record = ToolCallRecord(
        request_id=str(request_id),
        user_id=str(uuid4()),
        sequence_no=sequence_no,
        round_no=1,
        tool_name="get_metric_summary",
        args={"metric": "steps", "period": "recent_days:7"},
        period_dates={"period": DateRange(date(2026, 10, 19), date(2026, 10, 25))},
        decision="allow",
        reason=None,
        result_status="ok",
        latency_ms=12,
        result_count=7,
        evidence_ids=(EVIDENCE_IDS[0],),
        created_at=NOW,
    )
    return replace(record, **changes)


def fixed_now() -> datetime:
    """합성 테스트가 공유하는 고정 UTC 시각을 반환한다."""
    return NOW


class FakeSession:
    """세션 진입·종료와 commit·rollback 횟수를 팩토리에 기록한다."""

    def __init__(self, factory: "FakeSessionFactory") -> None:
        """횟수와 오류·지연 설정을 공유할 팩토리를 보관한다."""
        self.factory = factory

    def in_transaction(self) -> bool:
        """프로필은 코칭 저장소 작업 이후 호출하므로 호출자 소유 트랜잭션 안이다."""
        return True

    async def get(self, model: type[User], owner: UUID) -> User:
        """프로필 조회는 회원별 합성값만 반환한다."""
        assert model is User
        return User(
            id=owner,
            height_cm=SYNTHETIC_PROFILE.height_cm,
            weight_kg=SYNTHETIC_PROFILE.weight_kg,
        )

    async def __aenter__(self) -> "FakeSession":
        """세션을 열고 현재 열린 세션 수를 늘린다."""
        self.factory.opened += 1
        self.factory.active += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """예외로 종료하면 rollback을 세고, 종료 지연·오류를 적용한 뒤 열린 세션 수를 줄인다."""
        try:
            if exc_type is not None:
                await self.rollback()
            if self.factory.exit_gate is not None:
                await self.factory.exit_gate.wait()
            if self.factory.exit_errors:
                raise self.factory.exit_errors.pop(0)
        finally:
            self.factory.active -= 1

    async def commit(self) -> None:
        """commit 직전 콜백·지연·예약 오류를 적용하고 시도 횟수를 센다."""
        self.factory.commits += 1
        if self.factory.on_commit is not None:
            self.factory.on_commit()
        if self.factory.commit_gate is not None:
            await self.factory.commit_gate.wait()
        if self.factory.commit_errors:
            raise self.factory.commit_errors.pop(0)

    async def rollback(self) -> None:
        """rollback 호출 횟수를 늘린다."""
        self.factory.rollbacks += 1


class FakeSessionFactory:
    """독립 세션을 만들고 commit 실패·대기를 테스트에서 주입한다."""

    def __init__(self) -> None:
        """세션 통계와 선택적인 commit 제어 값을 초기화한다."""
        self.opened = 0
        self.active = 0
        self.commits = 0
        self.rollbacks = 0
        self.commit_errors: list[BaseException] = []
        self.on_commit: Callable[[], None] | None = None
        self.commit_gate: asyncio.Event | None = None
        self.exit_errors: list[BaseException] = []
        self.exit_gate: asyncio.Event | None = None

    def __call__(self) -> FakeSession:
        """async with에 사용할 새 가짜 세션을 반환한다."""
        return FakeSession(self)


class FakeStore:
    """저장소의 접수·이력·조건부 종결 규칙을 메모리에서 재현한다.

    변경은 commit과 무관하게 즉시 반영되며 rollback으로 되돌아가지 않는다.
    실제 DB의 트랜잭션 원자성이나 commit 성공 여부는 검증하지 않는다.
    """

    def __init__(self) -> None:
        """합성 대화·요청과 메서드별 오류·호출 기록을 초기화한다."""
        self.requests: dict[UUID, RequestState] = {}
        self.conversations: dict[UUID, ConversationInfo] = {}
        self.owners: dict[UUID, UUID] = {}
        self.inputs: dict[tuple[UUID, UUID], tuple[str, UUID]] = {}
        self.errors: dict[str, BaseException] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.summaries: dict[UUID, StoredSummary] = {}

    def _record(self, method: str, **kwargs: Any) -> None:
        """호출 인자를 기록하고 해당 메서드에 주입된 오류를 던진다."""
        self.calls.append((method, kwargs))
        if method in self.errors:
            raise self.errors[method]

    def state(self, request_id: UUID) -> RequestState:
        """검증할 요청의 현재 불변 상태를 반환한다."""
        return self.requests[request_id]

    def seed_accepted(
        self,
        *,
        user_id: UUID,
        client_request_id: UUID | None = None,
        conversation_id: UUID | None = None,
        content: str = "합성 질문",
        now: datetime = NOW,
        deadline_at: datetime | None = None,
    ) -> RequestState:
        """접수 규칙으로 테스트 요청을 만들고 필요하면 기한만 짧게 바꾼다."""
        accepted = self._accept(
            user_id=user_id,
            client_request_id=client_request_id or uuid4(),
            conversation_id=conversation_id,
            content=content,
            now=now,
        )
        if not isinstance(accepted, Accepted):
            raise ValueError("synthetic request was not accepted")
        state = accepted.request
        if deadline_at is not None:
            state = replace(state, deadline_at=deadline_at)
            self.requests[state.id] = state
        return state

    def _accept(
        self,
        *,
        user_id: UUID,
        client_request_id: UUID,
        conversation_id: UUID | None,
        content: str,
        now: datetime,
    ) -> AcceptResult:
        """중복·소유권·진행 상태를 검사하고 연속 순번으로 질문을 접수한다."""
        self._require_aware(now)
        digest = repository.input_hash(conversation_id, content)
        existing = self.inputs.get((user_id, client_request_id))
        if existing is not None:
            old_digest, request_id = existing
            return (
                Duplicate(self.state(request_id))
                if old_digest == digest
                else Conflict()
            )
        if conversation_id is not None:
            if self.owners.get(conversation_id) != user_id:
                return ConversationNotFound()
            previous = [
                state
                for state in self.requests.values()
                if state.conversation_id == conversation_id
            ]
            if any(state.status in {"accepted", "generating"} for state in previous):
                return Busy()
            sequence_no = (
                max(
                    (state.assistant_message.sequence_no for state in previous),
                    default=0,
                )
                + 1
            )
            conversation = replace(self.conversations[conversation_id], updated_at=now)
        else:
            conversation_id = uuid4()
            conversation = ConversationInfo(
                conversation_id, content[: repository.TITLE_MAX_LENGTH], now, now
            )
            self.owners[conversation_id] = user_id
            sequence_no = 1
        state = RequestState(
            id=uuid4(),
            client_request_id=client_request_id,
            conversation_id=conversation_id,
            status="accepted",
            user_message=MessageInfo(uuid4(), "user", sequence_no, content, now),
            assistant_message=MessageInfo(
                uuid4(), "assistant", sequence_no + 1, "", now
            ),
            error_code=None,
            error_context=None,
            created_at=now,
            deadline_at=now + repository.GENERATION_BUDGET,
            finished_at=None,
        )
        self.conversations[conversation_id] = conversation
        self.requests[state.id] = state
        self.inputs[user_id, client_request_id] = digest, state.id
        return Accepted(state, conversation)

    async def accept(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        client_request_id: UUID,
        conversation_id: UUID | None,
        content: str,
        now: datetime,
    ) -> AcceptResult:
        """실제 저장소와 같은 인자로 질문을 접수하고 결과 dataclass를 반환한다."""
        self._record(
            "accept",
            session=session,
            user_id=user_id,
            client_request_id=client_request_id,
            conversation_id=conversation_id,
            content=content,
            now=now,
        )
        return self._accept(
            user_id=user_id,
            client_request_id=client_request_id,
            conversation_id=conversation_id,
            content=content,
            now=now,
        )

    async def mark_generating(self, session: AsyncSession, *, request_id: UUID) -> bool:
        """접수 상태인 요청만 생성 중으로 바꾸고 변경 여부를 반환한다."""
        self._record("mark_generating", session=session, request_id=request_id)
        state = self.requests.get(request_id)
        if state is None or state.status != "accepted":
            return False
        self.requests[request_id] = replace(state, status="generating")
        return True

    async def list_history(
        self, session: AsyncSession, *, conversation_id: UUID, before_sequence_no: int
    ) -> list[MessageInfo]:
        """완료된 이전 요청의 메시지만 순번대로 반환한다."""
        self._record(
            "list_history",
            session=session,
            conversation_id=conversation_id,
            before_sequence_no=before_sequence_no,
        )
        messages = [
            message
            for state in self.requests.values()
            if state.conversation_id == conversation_id and state.status == "completed"
            for message in (state.user_message, state.assistant_message)
            if message.sequence_no < before_sequence_no
        ]
        return sorted(messages, key=lambda message: message.sequence_no)

    async def find_history_context(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        conversation_id: UUID,
        before_sequence_no: int | None = None,
    ) -> HistorySnapshot | None:
        """소유권·요약 기준점·질문 상한을 적용한 완료 쌍과 미종결 경계를 만든다."""
        self._record(
            "find_history_context",
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
            before_sequence_no=before_sequence_no,
        )
        if self.owners.get(conversation_id) != user_id:
            return None
        states = sorted(
            (
                state
                for state in self.requests.values()
                if state.conversation_id == conversation_id
            ),
            key=lambda state: state.user_message.sequence_no,
        )
        cutoff = (
            before_sequence_no
            if before_sequence_no is not None
            else max(
                (state.assistant_message.sequence_no + 1 for state in states),
                default=1,
            )
        )
        summary = self.summaries.get(conversation_id)
        until = summary.until_seq if summary else 0
        turns: list[HistoryTurn] = []
        pending = None
        for state in states:
            question, answer = state.user_message, state.assistant_message
            if question.sequence_no <= until or answer.sequence_no >= cutoff:
                continue
            if state.status in {"accepted", "generating"}:
                pending = (
                    question.sequence_no
                    if pending is None
                    else min(pending, question.sequence_no)
                )
            elif state.status == "completed":
                turns.append(
                    HistoryTurn(
                        str(state.id),
                        str(question.id),
                        str(answer.id),
                        question.sequence_no,
                        answer.sequence_no,
                        question.content,
                        answer.content,
                        state.created_at.astimezone(ZoneInfo("Asia/Seoul")).date(),
                    )
                )
        return HistorySnapshot(summary, tuple(turns), cutoff, pending)

    def _finish(
        self,
        request_id: UUID,
        *,
        content: str,
        now: datetime,
        error_code: str | None,
        error_context: Mapping[str, Any] | None,
    ) -> bool:
        """진행 중 요청의 최종 본문·상태와 대화 갱신 시각을 기록한다."""
        self._require_aware(now)
        state = self.requests.get(request_id)
        if state is None or state.status not in {"accepted", "generating"}:
            return False
        self.requests[request_id] = replace(
            state,
            status="failed" if error_code is not None else "completed",
            assistant_message=replace(state.assistant_message, content=content),
            error_code=error_code,
            error_context=dict(error_context) if error_context is not None else None,
            finished_at=now,
        )
        self.conversations[state.conversation_id] = replace(
            self.conversations[state.conversation_id], updated_at=now
        )
        return True

    async def complete(
        self,
        session: AsyncSession,
        *,
        request_id: UUID,
        answer: str,
        now: datetime,
        evidence: Sequence[EvidenceInfo] = (),
    ) -> bool:
        """진행 중 요청만 최종 답변으로 완료하고 변경했을 때만 근거를 남긴다."""
        self._record(
            "complete",
            session=session,
            request_id=request_id,
            answer=answer,
            now=now,
            evidence=evidence,
        )
        changed = self._finish(
            request_id, content=answer, now=now, error_code=None, error_context=None
        )
        if changed:
            self.requests[request_id] = replace(
                self.requests[request_id], evidence=tuple(evidence)
            )
        return changed

    async def fail(
        self,
        session: AsyncSession,
        *,
        request_id: UUID,
        error_code: str,
        error_context: Mapping[str, Any] | None,
        partial_answer: str,
        now: datetime,
    ) -> bool:
        """진행 중 요청만 공개 오류와 부분 답변으로 실패 처리한다."""
        self._record(
            "fail",
            session=session,
            request_id=request_id,
            error_code=error_code,
            error_context=error_context,
            partial_answer=partial_answer,
            now=now,
        )
        if not error_code:
            raise ValueError("error_code must not be empty")
        return self._finish(
            request_id,
            content=partial_answer,
            now=now,
            error_code=error_code,
            error_context=error_context,
        )

    async def get_request_state(
        self, session: AsyncSession, *, user_id: UUID, request_id: UUID
    ) -> RequestState | None:
        """소유 회원의 요청만 조회하고 남의 요청이나 없는 요청은 숨긴다."""
        self._record(
            "get_request_state", session=session, user_id=user_id, request_id=request_id
        )
        state = self.requests.get(request_id)
        if state is None or self.owners[state.conversation_id] != user_id:
            return None
        return state

    async def interrupt_in_progress(
        self,
        session: AsyncSession,
        *,
        now: datetime,
        created_before: datetime | None = None,
    ) -> int:
        """기준 시각 이전의 진행 요청만 중단 오류로 닫고 변경 개수를 반환한다."""
        self._record(
            "interrupt_in_progress",
            session=session,
            now=now,
            created_before=created_before,
        )
        self._require_aware(now)
        if created_before is not None:
            self._require_aware(created_before)
        changed = 0
        for state in list(self.requests.values()):
            if state.status in {"accepted", "generating"} and (
                created_before is None or state.created_at < created_before
            ):
                self.requests[state.id] = replace(
                    state,
                    status="failed",
                    error_code="GENERATION_INTERRUPTED",
                    finished_at=now,
                )
                self.conversations[state.conversation_id] = replace(
                    self.conversations[state.conversation_id], updated_at=now
                )
                changed += 1
        return changed

    async def fail_expired_requests(
        self, session: AsyncSession, *, now: datetime, expired_before: datetime
    ) -> int:
        """기한이 기준 시각보다 앞선 진행 요청만 시간 초과로 닫고 변경 개수를 반환한다."""
        self._record(
            "fail_expired_requests",
            session=session,
            now=now,
            expired_before=expired_before,
        )
        self._require_aware(now)
        self._require_aware(expired_before)
        changed = 0
        for state in list(self.requests.values()):
            if (
                state.status in {"accepted", "generating"}
                and state.deadline_at < expired_before
            ):
                self.requests[state.id] = replace(
                    state,
                    status="failed",
                    error_code="GENERATION_TIMEOUT",
                    error_context=None,
                    finished_at=now,
                )
                self.conversations[state.conversation_id] = replace(
                    self.conversations[state.conversation_id], updated_at=now
                )
                changed += 1
        return changed

    @staticmethod
    def _require_aware(now: datetime) -> None:
        """저장소와 동일하게 시간대 없는 시각을 거부한다."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")


class RollbackSession(FakeSession):
    """직렬 저장 테스트에서 예외 종료 시 세션 진입 전 요청·대화 상태를 복원한다."""

    def __init__(self, factory: "RollbackSessionFactory") -> None:
        """기존 세션 제어와 복원할 가짜 저장소를 보관한다."""
        super().__init__(factory)
        self.store = factory.store
        self.requests: dict[UUID, RequestState] = {}
        self.conversations: dict[UUID, ConversationInfo] = {}

    async def __aenter__(self) -> "RollbackSession":
        """세션 통계를 갱신하고 롤백에 사용할 불변 상태의 사본을 보관한다."""
        await super().__aenter__()
        self.requests = self.store.requests.copy()
        self.conversations = self.store.conversations.copy()
        return self

    async def rollback(self) -> None:
        """직렬 트랜잭션의 요청·대화를 복원하고 기존 롤백 횟수를 기록한다."""
        self.store.requests = self.requests.copy()
        self.store.conversations = self.conversations.copy()
        await super().rollback()


class RollbackSessionFactory(FakeSessionFactory):
    """기존 가짜를 바꾸지 않고 직렬 commit 실패·지연의 롤백을 재현한다."""

    def __init__(self, store: FakeStore) -> None:
        """기존 commit 제어 설정을 초기화하고 복원 대상 저장소를 보관한다."""
        super().__init__()
        self.store = store

    def __call__(self) -> RollbackSession:
        """예외 종료 시 상태를 복원하는 독립 가짜 세션을 반환한다."""
        return RollbackSession(self)


class RecordingChannel:
    """연결이 열려 있을 때만 이벤트를 모아 끊김을 재현한다."""

    def __init__(self) -> None:
        """빈 송신 기록과 열린 연결 상태를 준비한다."""
        self.events: list[dict[str, Any]] = []
        self.is_open = True

    def send(self, event: Mapping[str, Any]) -> None:
        """열린 연결이면 이벤트를 기록하고 닫힌 연결이면 버린다."""
        if self.is_open:
            self.events.append(dict(event))

    def close(self) -> None:
        """이후 이벤트를 버리도록 연결을 닫는다."""
        self.is_open = False


class ScriptedRunCoach:
    """내부 이벤트·지연·결과를 지정해 run_coach 호출과 취소를 재현한다."""

    def __init__(
        self,
        events: Sequence[dict[str, Any]] = (),
        *,
        result: CoachResult | None = None,
        hold: asyncio.Event | None = None,
        forever: bool = False,
        error: Exception | None = None,
        tool_calls: Sequence[ToolCallRecord] = (),
    ) -> None:
        """이벤트와 결과를 보관하고 호출·시작·취소 관측 값을 준비한다.

        tool_calls는 audit을 받으면 이벤트보다 먼저 쓰는 도구 호출 기록이다.
        """
        self.events = events
        self.result = result or CoachResult(
            answer="".join(
                event.get("delta", "")
                for event in events
                if event.get("type") == "token"
            ),
            evidence=[],
            finish_reason="stop",
        )
        self.hold = hold
        self.forever = forever
        self.error = error
        self.tool_calls = tool_calls
        self.calls: list[dict[str, Any]] = []
        self.entered = asyncio.Event()
        self.cancelled = False

    async def __call__(
        self,
        ctx: CoachContext,
        question: str,
        *,
        port: HealthDataPort,
        model: BaseChatModel,
        sink: EventSink,
        now: datetime | None = None,
        message_id: str | None = None,
        audit: AuditSink | None = None,
    ) -> CoachResult:
        """Context 전용 계약으로 요청을 기록하고 구형 summary/history 인자는 거부한다."""
        self.calls.append(
            {
                "ctx": ctx,
                "question": question,
                "port": port,
                "model": model,
                "now": now,
                "message_id": message_id,
                "audit": audit,
            }
        )
        try:
            if audit is not None:
                for record in self.tool_calls:
                    await audit.write(record)
            for event in self.events:
                await sink.emit(
                    {
                        **event,
                        "type": event["type"],
                        "conversation_id": ctx.conversation_id,
                        "request_id": ctx.request_id,
                    }
                )
            self.entered.set()
            if self.hold is not None:
                await self.hold.wait()
            if self.forever:
                await asyncio.Event().wait()
            if self.error is not None:
                raise self.error
            return self.result
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class CountingModelFactory:
    """공유 모델 생성 횟수와 재시도 오류를 기록한다."""

    def __init__(self, model: BaseChatModel = MODEL) -> None:
        """반환할 모델과 호출 횟수·오류 대기열을 준비한다."""
        self.model = model
        self.calls = 0
        self.errors: list[Exception] = []

    def __call__(self) -> BaseChatModel:
        """호출 횟수를 늘리고 예약 오류 또는 같은 모델을 반환한다."""
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.model


class RecordingPortFactory:
    """요청마다 받은 회원·시각을 기록하고 합성 포트를 반환한다."""

    def __init__(self) -> None:
        """포트 생성 호출 목록을 초기화한다."""
        self.calls: list[tuple[str, datetime]] = []

    def __call__(self, user_id: str, now: datetime) -> HealthDataPort:
        """포트 생성 인자를 기록하고 외부 I/O가 없는 sentinel을 반환한다."""
        self.calls.append((user_id, now))
        return PORT


class RecordingRunner:
    """생성 없이 준비 여부와 제출된 작업·제출 직전 관측을 제공한다."""

    def __init__(
        self, *, ready: bool = True, on_submit: Callable[[], None] | None = None
    ) -> None:
        """준비 여부·선택 콜백과 빈 작업 목록을 초기화한다."""
        self.ready = ready
        self.on_submit = on_submit
        self.jobs: list[GenerationJob] = []

    def is_ready(self) -> bool:
        """테스트가 지정한 준비 여부를 반환한다."""
        return self.ready

    def submit(self, job: GenerationJob) -> None:
        """제출 직전 콜백을 실행하고 생성 작업을 기록한다."""
        if self.on_submit is not None:
            self.on_submit()
        self.jobs.append(job)


class _DelayedEventSink:
    """기존 sink를 감싸 이벤트 사이에 앱 루프의 비동기 지연을 넣는다."""

    def __init__(self, sink: EventSink, interval: float) -> None:
        """원래 sink와 이벤트 간격을 보관한다."""
        self.sink = sink
        self.interval = interval
        self.started = False

    async def emit(self, event: dict[str, Any]) -> None:
        """첫 이벤트 이후에는 간격만큼 기다린 뒤 원래 sink에 전달한다."""
        if self.started:
            await asyncio.sleep(self.interval)
        self.started = True
        await self.sink.emit(event)


class DelayedRunCoach(ScriptedRunCoach):
    """내부 이벤트를 보낸 뒤 잠시 기다려 연결 종료 중 생성을 재현한다."""

    def __init__(
        self,
        events: Sequence[dict[str, Any]] = (),
        *,
        result: CoachResult | None = None,
        delay: float = 0.35,
        event_interval: float = 0.0,
    ) -> None:
        """기존 가짜의 이벤트·결과와 반환 전 비동기 지연 시간을 설정한다."""
        super().__init__(events, result=result)
        self.delay = delay
        self.event_interval = event_interval

    async def __call__(
        self, ctx: CoachContext, question: str, **kwargs: Any
    ) -> CoachResult:
        """호출 기록·이벤트를 기존 가짜에 맡기고 앱 루프에서 지연 후 반환한다."""
        if self.event_interval:
            kwargs["sink"] = _DelayedEventSink(kwargs["sink"], self.event_interval)
        result = await super().__call__(ctx, question, **kwargs)
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return result
