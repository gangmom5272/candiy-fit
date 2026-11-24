"""코칭 대화, 접수 요청과 메시지. 스키마 생성은 Alembic이 담당한다."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.users.models import User


class CoachingConversation(Base):
    __tablename__ = "coaching_conversation"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        CheckConstraint("next_sequence >= 1", name="next_sequence"),
        CheckConstraint("char_length(title) >= 1", name="title"),
        CheckConstraint("updated_at >= created_at", name="timestamps"),
        Index("ix_coaching_conversation_user_recent", "user_id", "updated_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey(User.id, ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(80))
    next_sequence: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    summary: Mapped[str | None] = mapped_column(Text)
    summary_until_seq: Mapped[int | None] = mapped_column(BigInteger)
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CoachingRequest(Base):
    __tablename__ = "coaching_request"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "user_id"],
            ["coaching_conversation.id", "coaching_conversation.user_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("user_id", "client_request_id"),
        UniqueConstraint("id", "conversation_id"),
        CheckConstraint(
            "status IN ('accepted', 'generating', 'completed', 'failed')", name="status"
        ),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash"),
        CheckConstraint(
            "(status IN ('completed', 'failed')) = (finished_at IS NOT NULL)",
            name="finished_at",
        ),
        CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL)", name="error_code"
        ),
        CheckConstraint(
            "error_context IS NULL OR status = 'failed'", name="error_context"
        ),
        CheckConstraint(
            "deadline_at > created_at "
            "AND (finished_at IS NULL OR finished_at >= created_at)",
            name="timestamps",
        ),
        Index(
            "uq_coaching_request_in_progress",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('accepted', 'generating')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column()
    user_id: Mapped[UUID] = mapped_column()
    client_request_id: Mapped[UUID] = mapped_column()
    input_hash: Mapped[str] = mapped_column(CHAR(64))
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64))
    # None은 SQL NULL이어야 진행 중 요청의 error_context 제약을 만족한다.
    error_context: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CoachingMessage(Base):
    """요청의 사용자·AI 메시지를 저장하고 근거의 복합 외래 키 대상을 제공한다."""

    __tablename__ = "coaching_message"
    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id"], ["coaching_conversation.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["request_id", "conversation_id"],
            ["coaching_request.id", "coaching_request.conversation_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("conversation_id", "sequence_no"),
        UniqueConstraint("request_id", "role"),
        UniqueConstraint("id", "role"),
        CheckConstraint("role IN ('user', 'assistant')", name="role"),
        CheckConstraint("sequence_no >= 1", name="sequence_no"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column()
    request_id: Mapped[UUID] = mapped_column()
    role: Mapped[str] = mapped_column(String(16))
    sequence_no: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CoachingEvidence(Base):
    """완료된 AI 답변의 근거와 조회 당시 공통 원형을 저장한다."""

    __tablename__ = "coaching_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["assistant_message_id", "assistant_role"],
            ["coaching_message.id", "coaching_message.role"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("assistant_message_id", "position"),
        CheckConstraint("assistant_role = 'assistant'", name="assistant_role"),
        CheckConstraint("position >= 1", name="position"),
        CheckConstraint(
            "metric IN ('steps', 'heart_rates', 'oxygen_saturations', 'sleeps', 'exercises', "
            "'weights', 'body_fats', 'basal_metabolic_rates', 'heights')",
            name="metric",
        ),
        CheckConstraint("period_start <= period_end", name="period"),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="snapshot"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    assistant_message_id: Mapped[UUID] = mapped_column()
    assistant_role: Mapped[str] = mapped_column(
        String(16), server_default=text("'assistant'")
    )
    position: Mapped[int] = mapped_column(Integer)
    metric: Mapped[str] = mapped_column(String(32))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CoachingToolCall(Base):
    """코칭 요청의 도구 호출을 호출 하나에 한 행씩 저장한다.

    건강 수치, 도구 결과 문장, 질문·답변 원문, 예외 문구는 저장하지 않는다.
    """

    __tablename__ = "coaching_tool_call"
    __table_args__ = (
        UniqueConstraint("request_id", "sequence_no"),
        CheckConstraint("sequence_no >= 1", name="sequence_no"),
        CheckConstraint("round_no >= 1", name="round_no"),
        CheckConstraint("decision IN ('allow', 'deny')", name="decision"),
        CheckConstraint(
            "(decision = 'deny') = (deny_reason IS NOT NULL)", name="deny_reason"
        ),
        CheckConstraint(
            "(decision = 'allow') = (result_status IS NOT NULL)", name="result_status"
        ),
        CheckConstraint("args IS NULL OR jsonb_typeof(args) = 'object'", name="args"),
        CheckConstraint("jsonb_typeof(period_dates) = 'object'", name="period_dates"),
        # 근거 UUID로 그 근거를 만든 호출을 찾는다 (evidence_ids @> ARRAY[...])
        Index(
            "ix_coaching_tool_call_evidence_ids", "evidence_ids", postgresql_using="gin"
        ),
        # 보관 기간이 지난 행을 지우는 작업이 created_at 범위로 행을 찾는다
        Index("ix_coaching_tool_call_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey(CoachingRequest.id, ondelete="RESTRICT")
    )
    # 저장 함수(repository.save_tool_calls)가 그 요청의 coaching_request.user_id를 그대로 넣는다.
    user_id: Mapped[UUID] = mapped_column(ForeignKey(User.id, ondelete="RESTRICT"))
    sequence_no: Mapped[int] = mapped_column(Integer)
    round_no: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(64))
    # 검증 전에 거부된 호출은 LLM 출력 인자를 저장하지 않아 NULL이다.
    args: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    period_dates: Mapped[dict[str, Any]] = mapped_column(JSONB)
    decision: Mapped[str] = mapped_column(String(16))
    deny_reason: Mapped[str | None] = mapped_column(String(32))
    result_status: Mapped[str | None] = mapped_column(String(32))
    latency_ms: Mapped[int] = mapped_column(Integer)
    result_count: Mapped[int] = mapped_column(Integer)
    evidence_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
