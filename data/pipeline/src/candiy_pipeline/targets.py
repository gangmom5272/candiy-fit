"""조회 대상 회원.

회원마다 ID가 두 개다.
- candiy_id: CANDiY에 조회할 때 보내는 end_user_id
- member_id: 우리 DB 건강 테이블의 end_user_id (uuid)

실제 회원은 둘이 같다 (users.id를 SDK endUserId로 그대로 쓴다).
테스트 사용자(mock-1013 등)와 합성 회원은 UUID 형식이 아니라서, 그 ID로 만든 고정 UUID를 member_id로 쓴다.
같은 ID는 항상 같은 UUID가 되므로 여러 번 적재해도 같은 회원으로 쌓인다.
고정 UUID는 UUIDv4 형식이다. 백엔드 토큰이 UUIDv4 회원 ID만 받는다 (S15P31A403-126).
"""

import uuid
from dataclasses import dataclass

import psycopg

TEST_USER_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://candiy-fit/test-user")
# 합성 데이터 회원(synthetic.py)의 users.social_user_id 접두어. 이 회원은 CANDiY에 조회하지 않는다.
SYNTHETIC_SOCIAL_PREFIX = "synthetic-"


@dataclass(frozen=True)
class Target:
    member_id: uuid.UUID
    candiy_id: str

    @property
    def is_member(self) -> bool:
        """실제 회원인지. 테스트 사용자·합성 회원은 candiy_id가 member_id와 다르다."""
        return self.candiy_id == str(self.member_id)


def fixed_uuid4(name: str) -> uuid.UUID:
    """이름으로 만든 고정 UUID. uuid5 값에서 버전 표시만 4로 바꾼다 (랜덤이 아니라 같은 이름이면 항상 같다)."""
    return uuid.UUID(bytes=uuid.uuid5(TEST_USER_NAMESPACE, name).bytes, version=4)


def target_for_test_user(candiy_id: str) -> Target:
    return Target(member_id=fixed_uuid4(candiy_id), candiy_id=candiy_id)


def target_for_member(member_id: uuid.UUID) -> Target:
    return Target(member_id=member_id, candiy_id=str(member_id))


def load_targets(
    conn: psycopg.Connection, test_users: tuple[str, ...], *, include_members: bool = True
) -> list[Target]:
    """테스트 사용자 + users 테이블의 회원.

    users 테이블은 개발팀 담당이라 없을 수 있다. 없으면 테스트 사용자만 조회한다.
    users 행은 가입을 마쳐야 생기므로(가입 중 상태는 auth_state) 모든 행이 가입 완료 회원이다.
    합성 데이터 회원은 CANDiY에 기록이 없으므로 뺀다.
    include_members=False면 회원은 조회하지 않는다 (싸피 개발 서버: 실데이터 반출 방지, PIPELINE_INCLUDE_MEMBERS).
    """
    targets = [target_for_test_user(u) for u in test_users]
    exists = conn.execute("SELECT to_regclass('public.users') IS NOT NULL").fetchone()[0]
    if exists and include_members:
        rows = conn.execute(
            "SELECT id FROM users WHERE social_user_id NOT LIKE %s", (SYNTHETIC_SOCIAL_PREFIX + "%",)
        ).fetchall()
        targets += [target_for_member(row[0]) for row in rows]
    return targets
