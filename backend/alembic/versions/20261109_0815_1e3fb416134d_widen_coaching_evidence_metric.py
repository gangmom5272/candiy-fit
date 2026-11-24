"""widen_coaching_evidence_metric

Revision ID: 1e3fb416134d
Revises: 7fd575642a98
Create Date: 2026-11-09 08:15:28.796684+09:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1e3fb416134d"
down_revision: str | Sequence[str] | None = "7fd575642a98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


METRIC_5 = "metric IN ('steps', 'heart_rates', 'oxygen_saturations', 'sleeps', 'exercises')"
METRIC_9 = (
    "metric IN ('steps', 'heart_rates', 'oxygen_saturations', 'sleeps', 'exercises', "
    "'weights', 'body_fats', 'basal_metabolic_rates', 'heights')"
)


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_coaching_evidence_metric"), "coaching_evidence", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_coaching_evidence_metric"), "coaching_evidence", METRIC_9
    )


# 신체 4종 metric 행이 있으면 CHECK 생성이 실패한다. 되돌리기 전에 그 행을 옮기거나 지워야 한다.
def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_coaching_evidence_metric"), "coaching_evidence", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_coaching_evidence_metric"), "coaching_evidence", METRIC_5
    )
