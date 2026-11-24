"""add web session credential

Revision ID: 77fd2d34a342
Revises: 1e3fb416134d
Create Date: 2026-11-09 13:12:07.220811+09:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "77fd2d34a342"
down_revision: str | Sequence[str] | None = "1e3fb416134d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_session", sa.Column("web_token_hash", sa.Text(), nullable=True))
    op.create_unique_constraint("uq_auth_session_web_token_hash", "auth_session", ["web_token_hash"])
    op.create_check_constraint(op.f("ck_auth_session_web_token_hash"), "auth_session",
        "web_token_hash IS NULL OR (platform_type = 'WEB' AND web_token_hash ~ '^[0-9a-f]{64}$')")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_auth_session_web_token_hash"), "auth_session", type_="check")
    op.drop_constraint("uq_auth_session_web_token_hash", "auth_session", type_="unique")
    op.drop_column("auth_session", "web_token_hash")
