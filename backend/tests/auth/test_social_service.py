from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import social
from app.auth.contract import VerifiedSocialSubject
from app.auth.models import AuthState
from app.core.errors import TemporaryStorageError
from app.users.models import User

NOW = datetime(2026, 10, 23, tzinfo=UTC)
SUBJECT = VerifiedSocialSubject("GOOGLE", "synthetic-subject")


@pytest.fixture
def db():
    return AsyncMock(spec=AsyncSession)


async def test_new_member_only_issues_hashed_signup(db, settings):
    db.scalar.return_value = None
    result = await social.decide_social_login(db, SUBJECT, "WEB", settings, now=NOW)
    state = db.add.call_args.args[0]
    assert isinstance(result, social.SignupRequired)
    assert isinstance(state, AuthState)
    assert state.purpose == "SIGNUP" and state.platform_type == "WEB"
    assert state.social_user_id == SUBJECT.social_user_id
    assert state.nonce_hash is None and state.consumed_at is None
    assert state.token_hash == social.hash_signup_token(result.signup_token)
    assert state.token_hash != result.signup_token
    assert state.created_at == NOW
    assert state.expires_at == result.expires_at == NOW + timedelta(minutes=10)
    assert result.signup_token not in repr(result)
    db.add.assert_called_once()
    db.begin.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


async def test_existing_reuses_session_issuer(db, settings, monkeypatch):
    user = User(id=uuid4(), display_name="기존이름")
    db.scalar.return_value = user
    issue = AsyncMock()
    monkeypatch.setattr(social, "create_login_session", issue)
    result = await social.decide_social_login(db, SUBJECT, "WEB", settings, now=NOW)
    assert result.user is user and user.display_name == "기존이름"
    issue.assert_awaited_once_with(db, user.id, "WEB", settings, now=NOW)
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "failure,expected",
    [
        (ConnectionError(), TemporaryStorageError),
        (RuntimeError("synthetic-private"), RuntimeError),
    ],
)
async def test_transaction_errors_classified(db, settings, failure, expected):
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    db.begin.return_value = tx
    db.scalar.side_effect = failure
    with pytest.raises(expected):
        await social.complete_social_login(db, SUBJECT, "WEB", settings, now=NOW)
    tx.__aexit__.assert_awaited_once()


async def test_commit_failure_never_returns_credentials(db, settings):
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(side_effect=ConnectionError())
    db.begin.return_value = tx
    db.scalar.return_value = None
    with pytest.raises(TemporaryStorageError):
        await social.complete_social_login(db, SUBJECT, "WEB", settings, now=NOW)
