from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from pydantic import SecretStr

from app.auth import tokens
from app.auth.contract import AuthenticationError
from app.core.config import Settings
from app.main import create_app

NOW = datetime(2031, 1, 1, tzinfo=UTC)
USER_ID = uuid4()


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(tokens, "_utc_now", lambda: NOW)


def payload():
    issued_at = int(NOW.timestamp())
    return {
        "sub": str(USER_ID),
        "iss": tokens.ISSUER,
        "aud": tokens.AUDIENCE,
        "iat": issued_at,
        "exp": issued_at + 900,
        "token_use": "access",
        "jti": str(uuid4()),
    }


def signed(settings, claims):
    return jwt.encode(claims, settings.require_auth_signing_key(), algorithm="HS256")


def test_round_trip_unique_and_masked(settings):
    first = tokens.issue_access_token(USER_ID, settings, now=NOW)
    second = tokens.issue_access_token(USER_ID, settings, now=NOW)
    assert first.token != second.token
    assert first.expires_at == NOW + timedelta(minutes=15)
    assert tokens.authenticate_token(first.token, settings).user_id == USER_ID
    assert first.token not in repr(first)
    key = settings.auth_signing_key.get_secret_value()
    assert key not in repr(settings) + settings.model_dump_json()


@pytest.mark.parametrize("year", [2000, 2040])
@pytest.mark.parametrize(
    ("seconds", "code"), [(899, None), (900, "AUTH_EXPIRED"), (901, "AUTH_EXPIRED")]
)
def test_expiry_boundary_uses_single_clock(settings, monkeypatch, year, seconds, code):
    now = NOW.replace(year=year)
    issued = tokens.issue_access_token(USER_ID, settings, now=now)
    monkeypatch.setattr(tokens, "_utc_now", lambda: now + timedelta(seconds=seconds))
    if code:
        with pytest.raises(AuthenticationError, match=code):
            tokens.authenticate_token(issued.token, settings)
    else:
        assert tokens.authenticate_token(issued.token, settings).user_id == USER_ID


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("sub", str(USER_ID).upper()),
        ("sub", USER_ID.hex),
        ("sub", "00000000-0000-0000-0000-000000000000"),
        ("iss", "other"),
        ("aud", "other"),
        ("aud", [tokens.AUDIENCE]),
        ("token_use", "refresh"),
        ("token_use", "signup"),
        ("token_use", "android_login"),
        ("iat", True),
        ("iat", "1"),
        ("exp", False),
        ("exp", "1"),
        ("exp", int(NOW.timestamp()) + 901),
        ("jti", "bad"),
        ("nbf", 0),
    ],
)
def test_invalid_claims(settings, name, value):
    claims = payload()
    claims[name] = value
    with pytest.raises(AuthenticationError, match="AUTH_REQUIRED"):
        tokens.authenticate_token(signed(settings, claims), settings)


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_issued_at_boundary_with_valid_lifetime(settings, offset):
    claims = payload()
    claims["iat"] = int(NOW.timestamp()) + offset
    claims["exp"] = claims["iat"] + 900
    token = signed(settings, claims)
    if offset > 0:
        with pytest.raises(AuthenticationError, match="AUTH_REQUIRED"):
            tokens.authenticate_token(token, settings)
    else:
        assert tokens.authenticate_token(token, settings).user_id == USER_ID


@pytest.mark.parametrize(
    "name", ["sub", "iss", "aud", "iat", "exp", "jti", "token_use"]
)
def test_required_claims(settings, name):
    claims = payload()
    del claims[name]
    with pytest.raises(AuthenticationError, match="AUTH_REQUIRED"):
        tokens.authenticate_token(signed(settings, claims), settings)


def test_wrong_purpose_expired_is_required(settings):
    claims = payload()
    claims.update(
        token_use="signup",
        iat=int(NOW.timestamp()) - 1000,
        exp=int(NOW.timestamp()) - 100,
    )
    with pytest.raises(AuthenticationError, match="AUTH_REQUIRED"):
        tokens.authenticate_token(signed(settings, claims), settings)


@pytest.mark.parametrize(
    "kind", ["none", "other_key", "HS384", "tampered", "opaque", "empty"]
)
def test_untrusted_tokens(settings, kind):
    key = settings.require_auth_signing_key()
    if kind == "none":
        raw = jwt.encode(payload(), None, algorithm="none")
    elif kind == "other_key":
        raw = jwt.encode(
            payload(), b"other-synthetic-key-at-least-32bytes", algorithm="HS256"
        )
    elif kind == "HS384":
        raw = jwt.encode(payload(), key * 2, algorithm="HS384")
    elif kind == "tampered":
        parts = signed(settings, payload()).split(".")
        parts[1] = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
        raw = ".".join(parts)
    else:
        raw = "synthetic-opaque" if kind == "opaque" else ""
    with pytest.raises(AuthenticationError, match="AUTH_REQUIRED"):
        tokens.authenticate_token(raw, settings)


@pytest.mark.parametrize("key", [None, "", "short"])
def test_settings_and_migrations_do_not_require_key_but_api_does(key):
    settings = Settings(_env_file=None, auth_signing_key=key)
    assert settings.database_url.startswith("postgresql+asyncpg://")
    with pytest.raises(ValueError, match="AUTH_SIGNING_KEY"):
        create_app(settings)
    with pytest.raises(ValueError, match="AUTH_SIGNING_KEY"):
        tokens.authenticate_token("invalid", settings)


def test_key_byte_length(settings):
    settings.auth_signing_key = SecretStr("가" * 11)
    assert len(settings.require_auth_signing_key()) == 33


def test_naive_time_rejected(settings):
    with pytest.raises(ValueError, match="UTC"):
        tokens.issue_access_token(USER_ID, settings, now=NOW.replace(tzinfo=None))
