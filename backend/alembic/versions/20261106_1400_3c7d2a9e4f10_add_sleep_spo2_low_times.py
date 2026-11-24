"""add sleep spo2_low_times

수면 중 산소포화도 90% 미만 측정 시각을 이른 순으로 저장한다 (S15P31A403-302).
spo2_low_count와 같은 샘플로 파이프라인이 집계할 때 함께 만든다 (data/docs/calculation-rules.md 7.5).

기존 행은 원본에서 다시 계산해야 해서 여기서 채우지 않고 NULL로 둔다.
`--aggregate-only --reaggregate` 뒤에 채워진다. 그 전까지 측정이 있는 행의 NULL은 "시각 준비 안 됨"이다.
[]는 측정이 있고 90% 미만이 0회인 행이다.

Revision ID: 3c7d2a9e4f10
Revises: 405e6930ab17
Create Date: 2026-11-06 14:00:00+09:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3c7d2a9e4f10"
down_revision: str | Sequence[str] | None = "405e6930ab17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sleep_session",
        sa.Column(
            "spo2_low_times",
            postgresql.ARRAY(sa.DateTime(timezone=True)),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        op.f("ck_sleep_session_spo2_low_times"),
        "sleep_session",
        "spo2_low_times IS NULL OR "
        "(spo2_low_count IS NOT NULL AND cardinality(spo2_low_times) = spo2_low_count)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_sleep_session_spo2_low_times"), "sleep_session", type_="check"
    )
    op.drop_column("sleep_session", "spo2_low_times")
