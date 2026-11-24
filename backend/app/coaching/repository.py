"""코칭 저장 쿼리. 전달받은 세션의 트랜잭션은 호출자가 소유한다."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    ScalarSelect,
    Select,
    and_,
    func,
    insert,
    select,
    true,
    tuple_,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.coaching.history import (
    HistorySnapshot,
    HistoryTurn,
    StoredSummary,
    SummaryPlan,
    SummaryResult,
    SummaryValidationError,
    validate_plan,
    validate_result,
    validate_snapshot,
)
from app.coaching.models import (
    CoachingConversation,
    CoachingEvidence,
    CoachingMessage,
    CoachingRequest,
    CoachingToolCall,
)
from coach_ai.tools.schemas import PublicMetric

GENERATION_BUDGET = timedelta(seconds=120)
TITLE_MAX_LENGTH = 80
RequestStatus = Literal["accepted", "generating", "completed", "failed"]
MessageRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class ConversationInfo:
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MessageInfo:
    id: UUID
    role: MessageRole
    sequence_no: int
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceInfo:
    """AI가 선택한 근거의 식별자·순서·기간과 저장 원형을 보관한다."""

    id: UUID
    position: int
    metric: PublicMetric
    period_start: date
    period_end: date
    snapshot: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCallInfo:
    """도구 호출 기록 한 행. 칸의 뜻은 ai/docs/agent-interface.md 4.2를 따른다.

    회원 ID(user_id)는 저장할 때 요청 행에서 가져오므로 이 값에 없다.
    """

    request_id: UUID
    sequence_no: int
    round_no: int
    tool_name: str
    args: dict[str, Any] | None
    # 기간 인자 이름마다 {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
    period_dates: dict[str, dict[str, str]]
    decision: str
    deny_reason: str | None
    result_status: str | None
    latency_ms: int
    result_count: int
    evidence_ids: tuple[UUID, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RequestState:
    """요청의 현재 상태와 메시지·오류·저장된 최종 근거를 보관한다."""

    id: UUID
    client_request_id: UUID
    conversation_id: UUID
    status: RequestStatus
    user_message: MessageInfo
    assistant_message: MessageInfo
    error_code: str | None
    error_context: dict[str, Any] | None
    created_at: datetime
    deadline_at: datetime
    finished_at: datetime | None
    evidence: tuple[EvidenceInfo, ...] = ()


@dataclass(frozen=True, slots=True)
class Accepted:
    request: RequestState
    conversation: ConversationInfo


@dataclass(frozen=True, slots=True)
class Duplicate:
    request: RequestState


@dataclass(frozen=True, slots=True)
class Conflict:
    pass


@dataclass(frozen=True, slots=True)
class Busy:
    pass


@dataclass(frozen=True, slots=True)
class ConversationNotFound:
    pass


AcceptResult = Accepted | Duplicate | Conflict | Busy | ConversationNotFound


def input_hash(conversation_id: UUID | None, content: str) -> str:
    """최초 전송의 conversationId와 정규화된 본문을 그대로 해싱한다."""
    payload = json.dumps(
        {
            "content": content,
            "conversationId": str(conversation_id) if conversation_id else None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_aware_now(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")


def _message_info(message: CoachingMessage) -> MessageInfo:
    return MessageInfo(
        id=message.id,
        role=cast(MessageRole, message.role),
        sequence_no=message.sequence_no,
        content=message.content,
        created_at=message.created_at,
    )


def _request_state(
    request: CoachingRequest,
    user_message: CoachingMessage,
    assistant_message: CoachingMessage,
    *,
    evidence: tuple[EvidenceInfo, ...] = (),
) -> RequestState:
    """요청과 메시지 ORM 값에 조회한 근거를 붙여 불변 상태를 만든다."""
    return RequestState(
        id=request.id,
        client_request_id=request.client_request_id,
        conversation_id=request.conversation_id,
        status=cast(RequestStatus, request.status),
        user_message=_message_info(user_message),
        assistant_message=_message_info(assistant_message),
        error_code=request.error_code,
        error_context=request.error_context,
        created_at=request.created_at,
        deadline_at=request.deadline_at,
        finished_at=request.finished_at,
        evidence=evidence,
    )


def _request_query() -> Select[
    tuple[CoachingRequest, CoachingMessage, CoachingMessage]
]:
    user_message = aliased(CoachingMessage)
    assistant_message = aliased(CoachingMessage)
    # 한 SELECT에서 상태와 본문을 읽고 세션에 이미 있는 ORM 값도 갱신한다.
    return (
        select(CoachingRequest, user_message, assistant_message)
        .select_from(CoachingRequest)
        .join(
            user_message,
            and_(
                user_message.request_id == CoachingRequest.id,
                user_message.role == "user",
            ),
        )
        .join(
            assistant_message,
            and_(
                assistant_message.request_id == CoachingRequest.id,
                assistant_message.role == "assistant",
            ),
        )
        .execution_options(populate_existing=True)
    )


async def _find_existing(
    session: AsyncSession, *, user_id: UUID, client_request_id: UUID, digest: str
) -> Duplicate | Conflict | None:
    """소유 회원의 동일 전송을 확인하고 완료로 읽은 중복 요청에만 최종 근거를 붙인다."""
    result = await session.execute(
        _request_query().where(
            CoachingRequest.user_id == user_id,
            CoachingRequest.client_request_id == client_request_id,
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    if row[0].input_hash != digest:
        return Conflict()
    evidence = (
        await _list_evidence(session, [row[2].id])
        if _can_load_evidence(row[2].role, row[0].status)
        else {}
    )
    return Duplicate(_request_state(*row, evidence=evidence.get(row[2].id, ())))


async def accept(
    session: AsyncSession,
    *,
    user_id: UUID,
    client_request_id: UUID,
    conversation_id: UUID | None,
    content: str,
    now: datetime,
) -> AcceptResult:
    """질문과 빈 assistant 메시지를 함께 접수한다. content는 다시 strip하지 않는다.

    `Accepted`가 아닌 결과면 호출자는 commit하지 않고 롤백한다
    (순번 할당 UPDATE와 새 대화 INSERT가 남지 않게 한다).
    """
    _require_aware_now(now)
    digest = input_hash(conversation_id, content)
    existing = await _find_existing(
        session, user_id=user_id, client_request_id=client_request_id, digest=digest
    )
    if existing is not None:
        return existing

    if conversation_id is not None:
        result = await session.execute(
            update(CoachingConversation)
            .where(
                CoachingConversation.id == conversation_id,
                CoachingConversation.user_id == user_id,
            )
            .values(
                next_sequence=CoachingConversation.next_sequence + 2,
                updated_at=now,
            )
            .returning(
                CoachingConversation.id,
                CoachingConversation.title,
                CoachingConversation.created_at,
                CoachingConversation.updated_at,
                CoachingConversation.next_sequence,
            )
        )
        row = result.one_or_none()
        if row is None:
            return ConversationNotFound()
        conversation = ConversationInfo(
            id=row.id,
            title=row.title,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        sequence_no = row.next_sequence - 2
        in_progress = await session.scalar(
            select(CoachingRequest.id).where(
                CoachingRequest.conversation_id == conversation_id,
                CoachingRequest.status.in_(("accepted", "generating")),
            )
        )
        if in_progress is not None:
            return Busy()
    else:
        conversation_id = uuid4()
        title = content[:TITLE_MAX_LENGTH]
        session.add(
            CoachingConversation(
                id=conversation_id,
                user_id=user_id,
                title=title,
                next_sequence=3,
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        conversation = ConversationInfo(
            id=conversation_id, title=title, created_at=now, updated_at=now
        )
        sequence_no = 1

    request = CoachingRequest(
        id=uuid4(),
        conversation_id=conversation_id,
        user_id=user_id,
        client_request_id=client_request_id,
        input_hash=digest,
        status="accepted",
        created_at=now,
        deadline_at=now + GENERATION_BUDGET,
    )
    user_message = CoachingMessage(
        id=uuid4(),
        conversation_id=conversation_id,
        request_id=request.id,
        role="user",
        sequence_no=sequence_no,
        content=content,
        created_at=now,
    )
    assistant_message = CoachingMessage(
        id=uuid4(),
        conversation_id=conversation_id,
        request_id=request.id,
        role="assistant",
        sequence_no=sequence_no + 1,
        content="",
        created_at=now,
    )
    try:
        async with session.begin_nested():
            session.add(request)
            await session.flush()
            session.add_all([user_message, assistant_message])
            await session.flush()
    except IntegrityError:
        existing = await _find_existing(
            session, user_id=user_id, client_request_id=client_request_id, digest=digest
        )
        return existing if existing is not None else Busy()

    return Accepted(
        request=_request_state(request, user_message, assistant_message),
        conversation=conversation,
    )


async def mark_generating(session: AsyncSession, *, request_id: UUID) -> bool:
    changed = await session.scalar(
        update(CoachingRequest)
        .where(CoachingRequest.id == request_id, CoachingRequest.status == "accepted")
        .values(status="generating")
        .returning(CoachingRequest.id)
    )
    return changed is not None


async def _finish_request(
    session: AsyncSession,
    *,
    request_id: UUID,
    status: Literal["completed", "failed"],
    content: str,
    error_code: str | None,
    error_context: Mapping[str, Any] | None,
    now: datetime,
) -> UUID | None:
    """진행 중 요청과 본문을 조건부로 종결하고 변경한 AI 메시지 ID를 반환한다."""
    conversation_id = await session.scalar(
        update(CoachingRequest)
        .where(
            CoachingRequest.id == request_id,
            CoachingRequest.status.in_(("accepted", "generating")),
        )
        .values(
            status=status,
            finished_at=now,
            error_code=error_code,
            error_context=dict(error_context) if error_context is not None else None,
        )
        .returning(CoachingRequest.conversation_id)
    )
    if conversation_id is None:
        return None
    assistant_message_id = await session.scalar(
        update(CoachingMessage)
        .where(
            CoachingMessage.request_id == request_id,
            CoachingMessage.role == "assistant",
        )
        .values(content=content)
        .returning(CoachingMessage.id)
    )
    await session.execute(
        update(CoachingConversation)
        .where(CoachingConversation.id == conversation_id)
        .values(updated_at=now)
    )
    return assistant_message_id


async def complete(
    session: AsyncSession,
    *,
    request_id: UUID,
    answer: str,
    now: datetime,
    evidence: Sequence[EvidenceInfo] = (),
) -> bool:
    """진행 중 요청을 완료하고 바꿨을 때만 근거를 같은 트랜잭션에 넣는다.

    이미 끝난 요청이면 False다. 제약 위반은 IntegrityError로 올리며 commit하지 않는다.
    """
    _require_aware_now(now)
    assistant_message_id = await _finish_request(
        session,
        request_id=request_id,
        status="completed",
        content=answer,
        error_code=None,
        error_context=None,
        now=now,
    )
    if assistant_message_id is None:
        return False
    if evidence:
        await session.execute(
            insert(CoachingEvidence),
            [
                {
                    "id": item.id,
                    "assistant_message_id": assistant_message_id,
                    "assistant_role": "assistant",
                    "position": item.position,
                    "metric": item.metric,
                    "period_start": item.period_start,
                    "period_end": item.period_end,
                    "snapshot": dict(item.snapshot),
                    "created_at": now,
                }
                for item in evidence
            ],
        )
    return True


async def fail(
    session: AsyncSession,
    *,
    request_id: UUID,
    error_code: str,
    error_context: Mapping[str, Any] | None,
    partial_answer: str,
    now: datetime,
) -> bool:
    """진행 중 요청을 오류와 부분 답변으로 닫고 변경 여부를 반환한다."""
    _require_aware_now(now)
    if not error_code:
        raise ValueError("error_code must not be empty")
    assistant_message_id = await _finish_request(
        session,
        request_id=request_id,
        status="failed",
        content=partial_answer,
        error_code=error_code,
        error_context=error_context,
        now=now,
    )
    return assistant_message_id is not None


def _can_load_evidence(role: str, request_status: str) -> bool:
    """앞선 SELECT에서 완료로 읽은 요청의 AI 메시지만 근거 조회 대상으로 판정한다."""
    return role == "assistant" and request_status == "completed"


async def _list_evidence(
    session: AsyncSession, message_ids: Sequence[UUID]
) -> dict[UUID, tuple[EvidenceInfo, ...]]:
    """소유권을 확인한 메시지의 저장 근거를 한 번에 순서대로 읽는다.

    빈 ID 목록이면 조회하지 않으며 snapshot은 현재 원형으로 재검증하지 않는다.
    """
    if not message_ids:
        return {}
    result = await session.execute(
        select(
            CoachingEvidence.assistant_message_id,
            CoachingEvidence.id,
            CoachingEvidence.position,
            CoachingEvidence.metric,
            CoachingEvidence.period_start,
            CoachingEvidence.period_end,
            CoachingEvidence.snapshot,
        )
        .where(CoachingEvidence.assistant_message_id.in_(message_ids))
        .order_by(CoachingEvidence.assistant_message_id, CoachingEvidence.position)
        .execution_options(autoflush=False)
    )
    grouped: dict[UUID, list[EvidenceInfo]] = {}
    for row in result:
        grouped.setdefault(row.assistant_message_id, []).append(
            EvidenceInfo(
                id=row.id,
                position=row.position,
                metric=cast(PublicMetric, row.metric),
                period_start=row.period_start,
                period_end=row.period_end,
                snapshot=row.snapshot,
            )
        )
    return {message_id: tuple(items) for message_id, items in grouped.items()}


async def get_request_state(
    session: AsyncSession, *, user_id: UUID, request_id: UUID
) -> RequestState | None:
    """소유 회원의 요청을 읽고, 완료로 읽은 경우에만 저장된 최종 근거를 붙인다."""
    result = await session.execute(
        _request_query().where(
            CoachingRequest.user_id == user_id, CoachingRequest.id == request_id
        )
    )
    row = result.one_or_none()
    if row is None:
        return None
    evidence = (
        await _list_evidence(session, [row[2].id])
        if _can_load_evidence(row[2].role, row[0].status)
        else {}
    )
    return _request_state(*row, evidence=evidence.get(row[2].id, ()))


async def list_history(
    session: AsyncSession, *, conversation_id: UUID, before_sequence_no: int
) -> list[MessageInfo]:
    """소유권을 확인한 대화에서 완료된 요청만 AI 문맥용으로 읽는다."""
    messages = await session.scalars(
        select(CoachingMessage)
        .join(CoachingRequest, CoachingMessage.request_id == CoachingRequest.id)
        .where(
            CoachingMessage.conversation_id == conversation_id,
            CoachingMessage.sequence_no < before_sequence_no,
            CoachingRequest.status == "completed",
        )
        .order_by(CoachingMessage.sequence_no)
        .execution_options(populate_existing=True)
    )
    return [_message_info(message) for message in messages]


async def find_history_context(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    before_sequence_no: int | None = None,
) -> HistorySnapshot | None:
    """본인 대화의 요약·전체 완료 쌍·미종결 경계를 한 SELECT 스냅샷으로 읽는다.

    상한 생략 시 같은 SELECT의 next_sequence를 사용한다. 페이지 제한은 없고
    현재 질문용 상한은 질문 번호 미만이다. 없거나 타인 대화면 None이다.
    완료 쌍의 answered_on은 요청 접수 시각(created_at)의 KST 날짜이며 runner가
    run_coach에 넘긴 now와 같다.
    """
    if before_sequence_no is not None and (
        type(before_sequence_no) is not int or before_sequence_no <= 0
    ):
        raise ValueError("invalid history cutoff")
    question, answer = aliased(CoachingMessage), aliased(CoachingMessage)
    pairs = (
        select(
            CoachingRequest.id.label("request_id"),
            CoachingRequest.status,
            CoachingRequest.created_at,
            CoachingRequest.conversation_id,
            question.id.label("question_id"),
            question.sequence_no.label("question_seq"),
            question.content.label("question"),
            answer.id.label("answer_id"),
            answer.sequence_no.label("answer_seq"),
            answer.content.label("answer"),
        )
        .select_from(CoachingRequest)
        .join(
            question,
            and_(question.request_id == CoachingRequest.id, question.role == "user"),
        )
        .join(
            answer,
            and_(answer.request_id == CoachingRequest.id, answer.role == "assistant"),
        )
        .where(
            CoachingRequest.user_id == user_id,
            CoachingRequest.conversation_id == conversation_id,
        )
        .subquery()
    )
    cutoff = (
        before_sequence_no
        if before_sequence_no is not None
        else CoachingConversation.next_sequence
    )
    result = await session.execute(
        select(
            CoachingConversation.summary,
            CoachingConversation.summary_until_seq,
            CoachingConversation.next_sequence,
            pairs,
        )
        .select_from(CoachingConversation)
        .outerjoin(
            pairs,
            and_(
                pairs.c.conversation_id == CoachingConversation.id,
                pairs.c.question_seq < cutoff,
                pairs.c.answer_seq < cutoff,
                pairs.c.answer_seq
                > func.coalesce(CoachingConversation.summary_until_seq, 0),
            ),
        )
        .where(
            CoachingConversation.id == conversation_id,
            CoachingConversation.user_id == user_id,
        )
        .order_by(pairs.c.question_seq)
        .execution_options(autoflush=False)
    )
    rows = result.all()
    if not rows:
        return None
    first = rows[0]
    if (first.summary is None) != (first.summary_until_seq is None):
        raise SummaryValidationError("inconsistent stored summary")
    summary = (
        StoredSummary(first.summary, first.summary_until_seq)
        if first.summary is not None
        else None
    )
    until_seq = summary.until_seq if summary is not None else 0
    turns: list[HistoryTurn] = []
    pending: int | None = None
    for row in rows:
        if row.request_id is None or row.answer_seq <= until_seq:
            continue
        if row.question_seq <= until_seq:
            raise SummaryValidationError("summary splits a completed pair")
        if row.status in {"accepted", "generating"}:
            pending = (
                row.question_seq if pending is None else min(pending, row.question_seq)
            )
        elif row.status == "completed":
            if row.created_at is None or row.created_at.utcoffset() is None:
                raise SummaryValidationError("invalid acceptance timestamp")
            turns.append(
                HistoryTurn(
                    str(row.request_id),
                    str(row.question_id),
                    str(row.answer_id),
                    row.question_seq,
                    row.answer_seq,
                    row.question,
                    row.answer,
                    row.created_at.astimezone(ZoneInfo("Asia/Seoul")).date(),
                )
            )
    snapshot = HistorySnapshot(
        summary,
        tuple(turns),
        before_sequence_no if before_sequence_no is not None else first.next_sequence,
        pending,
    )
    validate_snapshot(snapshot)
    return snapshot


async def save_summary(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    snapshot: HistorySnapshot,
    plan: SummaryPlan,
    result: SummaryResult,
    now: datetime,
) -> bool:
    """소유권·기존 요약·완료 앞부분을 재검증하여 세 요약 컬럼만 조건부 갱신한다.

    대화 행을 잠그므로 접수·다른 요약 갱신과 직렬화된다. 요청 행을 잠그지 않아
    종결의 요청→대화 잠금 순서와 충돌하지 않는다. 원문은 변경하지 않으며 commit은 호출자 책임이다.
    """
    _require_aware_now(now)
    validate_plan(snapshot, plan, plan.policy_version)
    validate_result(plan, result)
    owned_result = await session.execute(
        select(CoachingConversation.summary, CoachingConversation.summary_until_seq)
        .where(
            CoachingConversation.id == conversation_id,
            CoachingConversation.user_id == user_id,
        )
        .with_for_update()
        .execution_options(autoflush=False)
    )
    owned = owned_result.one_or_none()
    previous = snapshot.summary
    if owned is None or (
        owned.summary != (previous.text if previous else None)
        or owned.summary_until_seq != (previous.until_seq if previous else None)
    ):
        return False
    current = await find_history_context(
        session,
        user_id=user_id,
        conversation_id=conversation_id,
        before_sequence_no=snapshot.before_sequence_no,
    )
    if current is None or current.summary != snapshot.summary:
        return False
    # DB에 다시 읽은 본문·식별자·미종결 경계도 같은 계획을 허용해야 한다.
    try:
        validate_plan(current, plan, plan.policy_version)
    except SummaryValidationError:
        return False
    changed = await session.scalar(
        update(CoachingConversation)
        .where(
            CoachingConversation.id == conversation_id,
            CoachingConversation.user_id == user_id,
            CoachingConversation.summary_until_seq
            == (previous.until_seq if previous else None),
            CoachingConversation.summary == (previous.text if previous else None),
        )
        .values(
            summary=result.text,
            summary_until_seq=plan.until_seq,
            summary_updated_at=now,
        )
        .returning(CoachingConversation.id)
    )
    return changed is not None


async def _fail_in_progress(
    session: AsyncSession,
    *,
    now: datetime,
    error_code: str,
    condition: ColumnElement[bool] | None,
) -> int:
    """진행 요청 중 추가 조건에 맞는 것을 failed로 닫고 대화 갱신 시각을 맞춘 뒤 개수를 반환한다.

    AI 메시지 본문은 바꾸지 않고 commit은 호출자가 한다.
    """
    statement = update(CoachingRequest).where(
        CoachingRequest.status.in_(("accepted", "generating"))
    )
    if condition is not None:
        statement = statement.where(condition)
    result = await session.scalars(
        statement.values(
            status="failed",
            error_code=error_code,
            error_context=None,
            finished_at=now,
        ).returning(CoachingRequest.conversation_id)
    )
    conversation_ids = result.all()
    if conversation_ids:
        await session.execute(
            update(CoachingConversation)
            .where(CoachingConversation.id.in_(conversation_ids))
            .values(updated_at=now)
        )
    return len(conversation_ids)


async def interrupt_in_progress(
    session: AsyncSession, *, now: datetime, created_before: datetime | None = None
) -> int:
    """기준 시각 이전의 진행 요청을 중단 처리하고 변경 개수를 반환한다.

    기준이 없으면 모든 진행 요청을 닫고 assistant 본문은 보존한다.
    시간대 없는 시각은 ValueError로 거부하며 commit은 호출자가 수행한다.
    """
    _require_aware_now(now)
    condition = None
    if created_before is not None:
        _require_aware_now(created_before)
        condition = CoachingRequest.created_at < created_before
    return await _fail_in_progress(
        session, now=now, error_code="GENERATION_INTERRUPTED", condition=condition
    )


async def fail_expired_requests(
    session: AsyncSession, *, now: datetime, expired_before: datetime
) -> int:
    """생성 기한이 기준 시각보다 앞선 진행 요청을 GENERATION_TIMEOUT으로 닫고 변경 개수를 반환한다.

    AI 메시지 본문은 바꾸지 않는다. 시간대 없는 시각은 ValueError로 거부하며 commit은 호출자가 한다.
    """
    _require_aware_now(now)
    _require_aware_now(expired_before)
    return await _fail_in_progress(
        session,
        now=now,
        error_code="GENERATION_TIMEOUT",
        condition=CoachingRequest.deadline_at < expired_before,
    )


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    """REST 조회에 필요한 대화 정보와 저장된 요약을 보관한다."""

    id: UUID
    title: str
    summary: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class HistoryMessage:
    """저장된 메시지와 소속 요청의 상태·오류 문맥·최종 근거를 보관한다."""

    message: MessageInfo
    request_id: UUID
    request_status: RequestStatus
    error_code: str | None
    error_context: dict[str, Any] | None
    evidence: tuple[EvidenceInfo, ...] = ()


@dataclass(frozen=True, slots=True)
class MessagePage:
    """대화와 순번 오름차순 메시지 및 이전 페이지 유무를 보관한다."""

    conversation: ConversationRecord
    messages: tuple[HistoryMessage, ...]
    has_more: bool


async def find_message_page(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    limit: int,
    before_sequence_no: int | None,
) -> MessagePage | None:
    """본인 대화의 메시지 페이지를 읽고 완료로 읽은 AI 메시지에만 최종 근거를 붙인다.

    대화가 없거나 다른 회원의 것이면 None이다. 읽기만 하며 commit하지 않는다.
    컬럼 조회와 autoflush 비활성화로 세션의 미저장 ORM 상태를 보존한다.
    """
    conversation_result = await session.execute(
        select(
            CoachingConversation.id,
            CoachingConversation.title,
            CoachingConversation.summary,
            CoachingConversation.created_at,
            CoachingConversation.updated_at,
        )
        .where(
            CoachingConversation.id == conversation_id,
            CoachingConversation.user_id == user_id,
        )
        .execution_options(autoflush=False)
    )
    conversation = conversation_result.one_or_none()
    if conversation is None:
        return None

    statement = (
        select(
            CoachingMessage.id,
            CoachingMessage.role,
            CoachingMessage.sequence_no,
            CoachingMessage.content,
            CoachingMessage.created_at,
            CoachingRequest.id.label("request_id"),
            CoachingRequest.status,
            CoachingRequest.error_code,
            CoachingRequest.error_context,
        )
        .join(CoachingRequest, CoachingRequest.id == CoachingMessage.request_id)
        .where(CoachingMessage.conversation_id == conversation_id)
        .order_by(CoachingMessage.sequence_no.desc())
        .limit(limit + 1)
        .execution_options(autoflush=False)
    )
    if before_sequence_no is not None:
        statement = statement.where(CoachingMessage.sequence_no < before_sequence_no)
    result = await session.execute(statement)
    rows = result.all()
    evidence = await _list_evidence(
        session,
        [row.id for row in rows[:limit] if _can_load_evidence(row.role, row.status)],
    )
    return MessagePage(
        conversation=ConversationRecord(
            id=conversation.id,
            title=conversation.title,
            summary=conversation.summary,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        ),
        messages=tuple(
            HistoryMessage(
                message=MessageInfo(
                    id=row.id,
                    role=cast(MessageRole, row.role),
                    sequence_no=row.sequence_no,
                    content=row.content,
                    created_at=row.created_at,
                ),
                request_id=row.request_id,
                request_status=cast(RequestStatus, row.status),
                error_code=row.error_code,
                error_context=row.error_context,
                evidence=evidence.get(row.id, ()),
            )
            for row in reversed(rows[:limit])
        ),
        has_more=len(rows) > limit,
    )


@dataclass(frozen=True, slots=True)
class ConversationKey:
    """대화 목록의 최근 활동순 페이지 경계 시각과 식별자를 보관한다."""

    updated_at: datetime
    id: UUID


@dataclass(frozen=True, slots=True)
class ConversationListItem:
    """대화 정보와 가장 큰 메시지 순번에 속한 요청 상태를 보관한다."""

    conversation: ConversationRecord
    last_request_status: RequestStatus


@dataclass(frozen=True, slots=True)
class ConversationPage:
    """최근 활동순 대화 목록과 다음 페이지 유무를 보관한다."""

    items: tuple[ConversationListItem, ...]
    has_more: bool


async def list_conversations(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int,
    after: ConversationKey | None,
) -> ConversationPage:
    """본인 대화를 최근 활동순으로 한 번 조회하고 다음 페이지 유무를 반환한다.

    가장 큰 메시지 순번의 요청 상태를 읽으며 요청 없는 대화는 제외한다.
    컬럼 조회와 autoflush 비활성화로 미저장 ORM 상태를 보존한다.
    """
    last_request = (
        select(CoachingRequest.status)
        .select_from(CoachingMessage)
        .join(CoachingRequest, CoachingRequest.id == CoachingMessage.request_id)
        .where(CoachingMessage.conversation_id == CoachingConversation.id)
        .order_by(CoachingMessage.sequence_no.desc())
        .limit(1)
        .correlate(CoachingConversation)
        .lateral("last_request")
    )
    statement = (
        select(
            CoachingConversation.id,
            CoachingConversation.title,
            CoachingConversation.summary,
            CoachingConversation.created_at,
            CoachingConversation.updated_at,
            last_request.c.status,
        )
        .select_from(CoachingConversation)
        .join(last_request, true())
        .where(CoachingConversation.user_id == user_id)
        .order_by(
            CoachingConversation.updated_at.desc(), CoachingConversation.id.desc()
        )
        .limit(limit + 1)
        .execution_options(autoflush=False)
    )
    if after is not None:
        statement = statement.where(
            tuple_(CoachingConversation.updated_at, CoachingConversation.id)
            < tuple_(after.updated_at, after.id)
        )
    result = await session.execute(statement)
    rows = result.all()
    return ConversationPage(
        items=tuple(
            ConversationListItem(
                conversation=ConversationRecord(
                    id=row.id,
                    title=row.title,
                    summary=row.summary,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                ),
                last_request_status=cast(RequestStatus, row.status),
            )
            for row in rows[:limit]
        ),
        has_more=len(rows) > limit,
    )


def _request_member(request_id: UUID) -> ScalarSelect[UUID]:
    """요청 행의 회원 ID를 읽는 하위 질의를 만든다."""
    return (
        select(CoachingRequest.user_id)
        .where(CoachingRequest.id == request_id)
        .scalar_subquery()
    )


async def save_tool_calls(
    session: AsyncSession, *, rows: Sequence[ToolCallInfo]
) -> None:
    """요청의 도구 호출 기록을 한 번에 넣는다. 제약 위반은 IntegrityError로 올리며 commit하지 않는다.

    회원 ID는 같은 INSERT 문에서 그 요청의 coaching_request.user_id를 그대로 넣는다.
    요청이 없으면 회원 ID가 NULL이라 IntegrityError다.
    """
    if not rows:
        return
    await session.execute(
        insert(CoachingToolCall).values(
            [
                {
                    "request_id": row.request_id,
                    "user_id": _request_member(row.request_id),
                    "sequence_no": row.sequence_no,
                    "round_no": row.round_no,
                    "tool_name": row.tool_name,
                    "args": row.args,
                    "period_dates": row.period_dates,
                    "decision": row.decision,
                    "deny_reason": row.deny_reason,
                    "result_status": row.result_status,
                    "latency_ms": row.latency_ms,
                    "result_count": row.result_count,
                    "evidence_ids": list(row.evidence_ids),
                    "created_at": row.created_at,
                }
                for row in rows
            ]
        )
    )


async def list_tool_calls(
    session: AsyncSession, *, request_id: UUID
) -> list[ToolCallInfo]:
    """요청 하나의 도구 호출 기록을 회차(sequence_no) 순으로 반환한다. 없으면 빈 목록이다."""
    result = await session.scalars(
        select(CoachingToolCall)
        .where(CoachingToolCall.request_id == request_id)
        .order_by(CoachingToolCall.sequence_no)
        .execution_options(autoflush=False)
    )
    return [
        ToolCallInfo(
            request_id=row.request_id,
            sequence_no=row.sequence_no,
            round_no=row.round_no,
            tool_name=row.tool_name,
            args=row.args,
            period_dates=row.period_dates,
            decision=row.decision,
            deny_reason=row.deny_reason,
            result_status=row.result_status,
            latency_ms=row.latency_ms,
            result_count=row.result_count,
            evidence_ids=tuple(row.evidence_ids),
            created_at=row.created_at,
        )
        for row in result
    ]
