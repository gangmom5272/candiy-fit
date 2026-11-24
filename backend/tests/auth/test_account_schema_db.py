"""Alembic으로 만든 별도 PostgreSQL에서 제약·ORM 매핑을 검증한다. 각 테스트는 롤백한다."""

import hashlib
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth.models import AuthRefreshToken, AuthSession, AuthState
from app.users.models import User

pytestmark = pytest.mark.db
NOW = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def member(**overrides) -> User:
    values = dict(
        social_type="GOOGLE",
        social_user_id=str(uuid4()),
        display_name="테스트회원",
        birth_year=2000,
        gender="FEMALE",
        height_cm=Decimal("165.25"),
        weight_kg=Decimal("55"),
        created_at=NOW,
        updated_at=NOW,
    )
    return User(**(values | overrides))


def signup(**overrides) -> AuthState:
    values = dict(
        purpose="SIGNUP",
        token_hash=digest(str(uuid4())),
        social_type="GOOGLE",
        platform_type="WEB",
        social_user_id="synthetic-subject",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    return AuthState(**(values | overrides))


@pytest.fixture
async def db():
    raw_url = os.environ.get("AUTH_TEST_DATABASE_URL")
    if not raw_url:
        pytest.fail(
            "AUTH_TEST_DATABASE_URL에 Alembic을 적용한 별도 테스트 DB를 지정하세요. docs/auth-account-schema.md 참고"
        )
    url = make_url(raw_url)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").startswith(
        "candiy_auth_test"
    ):
        pytest.fail(
            "postgresql+asyncpg 및 candiy_auth_test로 시작하는 전용 DB 이름이 필요합니다"
        )
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            async with AsyncSession(
                bind=conn, join_transaction_mode="create_savepoint"
            ) as session:
                yield session
            await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
async def login(db):
    user = member()
    db.add(user)
    await db.flush()
    session = AuthSession(
        user_id=user.id,
        platform_type="WEB",
        created_at=NOW,
        last_refreshed_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )
    db.add(session)
    await db.flush()
    return session


async def test_profile_roundtrip_and_pipeline_member_id(db):
    user = member(display_name="한글🙂é", height_cm=Decimal("165.250"))
    db.add(user)
    await db.flush()
    user_id = user.id
    assert user_id.version == 4
    db.expunge_all()
    loaded = await db.get(User, user_id)
    assert loaded.height_cm == Decimal("165.25")
    assert loaded.created_at == NOW
    assert loaded.display_name == "한글🙂é"
    # 파이프라인이 실제 사용하는 쿼리와 같은 계약.
    assert user_id in (await db.execute(text("SELECT id FROM users"))).scalars().all()


async def test_social_identity_is_unique_across_platforms_but_nickname_is_not(db):
    db.add_all(
        [
            member(social_user_id="same"),
            member(social_type="KAKAO", social_user_id="same"),
        ]
    )
    await db.flush()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(member(social_user_id="same"))
            await db.flush()


@pytest.mark.parametrize(
    "override",
    [
        {"display_name": ""},
        {"display_name": "가" * 13},
        {"birth_year": 1899},
        {"gender": "UNKNOWN"},
        {"social_type": "OTHER"},
        {"social_user_id": ""},
        {"height_cm": None},
        {"height_cm": Decimal("165.251")},
        {"height_cm": Decimal("300.01")},
        {"weight_kg": Decimal("0")},
        {"weight_kg": Decimal("500.01")},
        {"weight_kg": Decimal("NaN")},
    ],
)
async def test_invalid_profile_is_rejected_without_rounding(db, override):
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(member(**override))
            await db.flush()


async def test_session_requires_existing_member(db):
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                AuthSession(
                    user_id=uuid4(),
                    platform_type="WEB",
                    expires_at=NOW + timedelta(days=7),
                    created_at=NOW,
                    last_refreshed_at=NOW,
                )
            )
            await db.flush()


async def test_refresh_requires_existing_session(db):
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(AuthRefreshToken(session_id=uuid4(), token_hash=digest("orphan")))
            await db.flush()


async def test_refresh_history_and_one_current_token(db, login):
    old = AuthRefreshToken(
        session_id=login.id, token_hash=digest("old"), created_at=NOW
    )
    db.add(old)
    await db.flush()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                AuthRefreshToken(
                    session_id=login.id, token_hash=digest("too-early"), created_at=NOW
                )
            )
            await db.flush()
    old.used_at = NOW + timedelta(minutes=1)
    await db.flush()  # 회전은 기존 토큰 사용 처리를 먼저 flush한 뒤 새 토큰을 추가한다.
    db.add(
        AuthRefreshToken(
            session_id=login.id, token_hash=digest("new"), created_at=old.used_at
        )
    )
    await db.flush()
    found = await db.scalar(
        select(AuthRefreshToken).where(AuthRefreshToken.token_hash == digest("old"))
    )
    assert found.session_id == login.id and found.used_at is not None
    assert (
        len(
            (
                await db.scalars(
                    select(AuthRefreshToken).where(
                        AuthRefreshToken.session_id == login.id
                    )
                )
            ).all()
        )
        == 2
    )


async def test_refresh_hash_cannot_be_reissued_even_after_use(db, login):
    db.add(
        AuthRefreshToken(
            session_id=login.id, token_hash=digest("used"), created_at=NOW, used_at=NOW
        )
    )
    await db.flush()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                AuthRefreshToken(
                    session_id=login.id, token_hash=digest("used"), created_at=NOW
                )
            )
            await db.flush()


async def test_prelogin_state_and_multiple_signup_credentials_need_no_member(db):
    challenge = signup(
        purpose="ANDROID_LOGIN",
        platform_type="ANDROID",
        social_user_id=None,
        nonce_hash=digest("nonce"),
        expires_at=NOW + timedelta(minutes=5),
    )
    states = [challenge, signup(), signup()]
    db.add_all(states)
    await db.flush()
    assert all(state.id.version == 4 for state in states)
    assert (
        await db.scalar(select(User).where(User.social_user_id == "synthetic-subject"))
        is None
    )


@pytest.mark.parametrize(
    "override",
    [
        {"purpose": "OTHER"},
        {"social_user_id": None},
        {"social_user_id": ""},
        {"nonce_hash": digest("unexpected")},
        {"token_hash": "plain-token"},
        {"platform_type": "OTHER"},
        {
            "purpose": "ANDROID_LOGIN",
            "platform_type": "ANDROID",
            "social_user_id": None,
        },
        {
            "purpose": "ANDROID_LOGIN",
            "social_user_id": None,
            "nonce_hash": digest("nonce"),
        },
        {
            "purpose": "ANDROID_LOGIN",
            "platform_type": "ANDROID",
            "nonce_hash": digest("nonce"),
        },
        {"expires_at": NOW},
        {"consumed_at": NOW + timedelta(minutes=10)},
    ],
)
async def test_state_rejects_wrong_purpose_payload_and_times(db, override):
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(signup(**override))
            await db.flush()


async def test_signup_write_set_can_roll_back_together(db):
    state = signup()
    db.add(state)
    await db.flush()
    async with db.begin_nested() as transaction:
        user = member(social_user_id="rollback-subject")
        db.add(user)
        await db.flush()
        session = AuthSession(
            user_id=user.id,
            platform_type="WEB",
            created_at=NOW,
            last_refreshed_at=NOW,
            expires_at=NOW + timedelta(days=7),
        )
        db.add(session)
        await db.flush()
        db.add(
            AuthRefreshToken(
                session_id=session.id, token_hash=digest("rollback"), created_at=NOW
            )
        )
        state.consumed_at = NOW + timedelta(seconds=1)
        await db.flush()
        await transaction.rollback()
    await db.refresh(state)
    assert state.consumed_at is None
    assert (
        await db.scalar(select(User).where(User.social_user_id == "rollback-subject"))
        is None
    )
    assert (
        await db.scalar(
            select(AuthRefreshToken).where(
                AuthRefreshToken.token_hash == digest("rollback")
            )
        )
        is None
    )


async def test_orm_metadata_matches_migrated_tables(db):
    def check(sync_conn):
        inspector = inspect(sync_conn)
        for model in (User, AuthSession, AuthRefreshToken, AuthState):
            table = model.__table__
            columns = {
                column["name"]: column for column in inspector.get_columns(table.name)
            }
            assert set(columns) == set(table.columns.keys())
            for column in table.columns:
                actual = columns[column.name]
                assert actual["nullable"] == column.nullable
                assert str(actual["type"].compile(dialect=sync_conn.dialect)) == str(
                    column.type.compile(dialect=sync_conn.dialect)
                )
                expected_default = (
                    str(column.server_default.arg) if column.server_default else None
                )
                assert actual["default"] == expected_default
            constraints = [inspector.get_pk_constraint(table.name)]
            constraints += inspector.get_unique_constraints(table.name)
            constraints += inspector.get_check_constraints(table.name)
            constraints += inspector.get_foreign_keys(table.name)
            assert {item["name"] for item in constraints} == {
                item.name for item in table.constraints
            }
            indexes = {
                item["name"]: item
                for item in inspector.get_indexes(table.name)
                if not item.get("duplicates_constraint")
            }
            assert set(indexes) == {item.name for item in table.indexes}
            for index in table.indexes:
                actual = indexes[index.name]
                assert actual["unique"] == index.unique
                assert actual["column_names"] == [
                    column.name for column in index.columns
                ]
                expected_where = index.dialect_options["postgresql"].get("where")
                actual_where = actual.get("dialect_options", {}).get("postgresql_where")
                assert (actual_where or "").strip("()") == (
                    str(expected_where) if expected_where is not None else ""
                )
            for fk in inspector.get_foreign_keys(table.name):
                model_fk = next(
                    item
                    for item in table.foreign_key_constraints
                    if item.name == fk["name"]
                )
                assert fk["referred_table"] == model_fk.referred_table.name
                assert fk["options"].get("ondelete") == model_fk.ondelete

    conn = await db.connection()
    await conn.run_sync(check)
