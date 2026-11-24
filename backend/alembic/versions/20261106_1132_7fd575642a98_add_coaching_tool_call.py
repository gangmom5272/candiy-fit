"""add coaching tool call

Revision ID: 7fd575642a98
Revises: 3c7d2a9e4f10
Create Date: 2026-11-06 11:32:47.784673+09:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7fd575642a98"
down_revision: str | Sequence[str] | None = "3c7d2a9e4f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coaching_tool_call",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column(
            "args",
            postgresql.JSONB(none_as_null=True, astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "period_dates", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("deny_reason", sa.String(length=32), nullable=True),
        sa.Column("result_status", sa.String(length=32), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("evidence_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(decision = 'allow') = (result_status IS NOT NULL)",
            name=op.f("ck_coaching_tool_call_result_status"),
        ),
        sa.CheckConstraint(
            "(decision = 'deny') = (deny_reason IS NOT NULL)",
            name=op.f("ck_coaching_tool_call_deny_reason"),
        ),
        sa.CheckConstraint(
            "args IS NULL OR jsonb_typeof(args) = 'object'",
            name=op.f("ck_coaching_tool_call_args"),
        ),
        sa.CheckConstraint(
            "decision IN ('allow', 'deny')", name=op.f("ck_coaching_tool_call_decision")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(period_dates) = 'object'",
            name=op.f("ck_coaching_tool_call_period_dates"),
        ),
        sa.CheckConstraint(
            "round_no >= 1", name=op.f("ck_coaching_tool_call_round_no")
        ),
        sa.CheckConstraint(
            "sequence_no >= 1", name=op.f("ck_coaching_tool_call_sequence_no")
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["coaching_request.id"],
            name=op.f("fk_coaching_tool_call_request_id_coaching_request"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_coaching_tool_call_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coaching_tool_call")),
        sa.UniqueConstraint(
            "request_id", "sequence_no", name=op.f("uq_coaching_tool_call_request_id")
        ),
    )
    op.create_index(
        "ix_coaching_tool_call_created_at",
        "coaching_tool_call",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_coaching_tool_call_evidence_ids",
        "coaching_tool_call",
        ["evidence_ids"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    # 테이블을 지우므로 저장한 도구 호출 기록도 함께 삭제된다.
    op.drop_index(
        "ix_coaching_tool_call_evidence_ids",
        table_name="coaching_tool_call",
        postgresql_using="gin",
    )
    op.drop_index("ix_coaching_tool_call_created_at", table_name="coaching_tool_call")
    op.drop_table("coaching_tool_call")
