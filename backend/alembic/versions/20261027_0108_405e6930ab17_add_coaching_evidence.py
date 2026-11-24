"""add coaching evidence

Revision ID: 405e6930ab17
Revises: b66ae129e750
Create Date: 2026-10-27 01:08:05.258387+09:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "405e6930ab17"
down_revision: str | Sequence[str] | None = "b66ae129e750"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 복합 외래 키가 참조할 유일 제약을 근거 테이블보다 먼저 생성한다.
    op.create_unique_constraint(
        op.f("uq_coaching_message_id"), "coaching_message", ["id", "role"]
    )
    op.create_table(
        "coaching_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("assistant_message_id", sa.Uuid(), nullable=False),
        sa.Column(
            "assistant_role",
            sa.String(length=16),
            server_default=sa.text("'assistant'"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "assistant_role = 'assistant'",
            name=op.f("ck_coaching_evidence_assistant_role"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'",
            name=op.f("ck_coaching_evidence_snapshot"),
        ),
        sa.CheckConstraint(
            "metric IN ('steps', 'heart_rates', 'oxygen_saturations', 'sleeps', 'exercises')",
            name=op.f("ck_coaching_evidence_metric"),
        ),
        sa.CheckConstraint(
            "period_start <= period_end", name=op.f("ck_coaching_evidence_period")
        ),
        sa.CheckConstraint("position >= 1", name=op.f("ck_coaching_evidence_position")),
        sa.ForeignKeyConstraint(
            ["assistant_message_id", "assistant_role"],
            ["coaching_message.id", "coaching_message.role"],
            name=op.f("fk_coaching_evidence_assistant_message_id_coaching_message"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coaching_evidence")),
        sa.UniqueConstraint(
            "assistant_message_id",
            "position",
            name=op.f("uq_coaching_evidence_assistant_message_id"),
        ),
    )


def downgrade() -> None:
    # 유일 제약을 참조하는 외래 키가 사라지도록 근거 테이블을 먼저 삭제한다.
    op.drop_table("coaching_evidence")
    op.drop_constraint(
        op.f("uq_coaching_message_id"), "coaching_message", type_="unique"
    )
