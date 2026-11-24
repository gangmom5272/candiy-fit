"""동기 파이프라인 연결에서 회원의 외부 수집 여부만 읽는다."""

from uuid import UUID

import psycopg
from candiy_pipeline.targets import SYNTHETIC_SOCIAL_PREFIX


def is_synthetic(conn: psycopg.Connection, user_id: UUID) -> bool:
    row = conn.execute(
        "SELECT social_user_id FROM users WHERE id = %s", (user_id,)
    ).fetchone()
    if row is None:
        # 인증 후 탈퇴한 회원의 새 외부 수집은 시작하지 않는다.
        raise MemberRemovedError
    return row[0].startswith(SYNTHETIC_SOCIAL_PREFIX)


class MemberRemovedError(Exception):
    """인증 확인과 동기화 연결 사이에 회원이 사라졌다."""
