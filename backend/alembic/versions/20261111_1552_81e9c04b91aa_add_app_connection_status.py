"""add_app_connection_status

Revision ID: 81e9c04b91aa
Revises: 77fd2d34a342
Create Date: 2026-11-11 15:52:08.026994+09:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "81e9c04b91aa"
down_revision: str | Sequence[str] | None = "77fd2d34a342"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 기존 회원의 보고 부재는 미연동의 증거가 아니다.
    op.add_column(
        "users",
        sa.Column(
            "app_connection_status", sa.Text(), nullable=False, server_default="UNKNOWN"
        ),
    )
    op.add_column("users", sa.Column("app_connected_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "app_connection_status",
        "users",
        "app_connection_status IN ('UNKNOWN', 'NOT_CONNECTED', 'CONNECTED')",
    )
    op.create_check_constraint(
        "app_connection_time",
        "users",
        "(app_connection_status = 'CONNECTED') = (app_connected_at IS NOT NULL)",
    )
    op.alter_column("users", "app_connection_status", server_default="NOT_CONNECTED")


def downgrade() -> None:
    # 백엔드를 먼저 이전 버전으로 되돌려야 한다. 완료 보고 이력은 삭제된다.
    op.drop_constraint(op.f("ck_users_app_connection_time"), "users", type_="check")
    op.drop_constraint(op.f("ck_users_app_connection_status"), "users", type_="check")
    op.drop_column("users", "app_connected_at")
    op.drop_column("users", "app_connection_status")
