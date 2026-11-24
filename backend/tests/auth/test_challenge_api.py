import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import router as auth_router
from app.auth.dependencies import get_social_provider
from app.auth.models import AuthState
from app.auth.providers import SocialProviderClient
from app.core.db import get_session

PATH = "/api/v1/auth/social/challenge"
NOW = datetime(2026, 10, 29, tzinfo=UTC)
BODY = {"socialType": "GOOGLE", "platformType": "ANDROID"}


@pytest.fixture
def challenge_api(app, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(auth_router, "datetime", Clock)
    db = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=SocialProviderClient)

    async def session():
        yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_social_provider] = lambda: provider
    return db, provider


@pytest.mark.parametrize("social_type", ["GOOGLE", "KAKAO"])
async def test_challenge_returns_secrets_only_after_storing_hashes(
    client, challenge_api, social_type, caplog
):
    db, provider = challenge_api
    response = await client.post(PATH, json=BODY | {"socialType": social_type})
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"challengeToken", "nonce", "expiresAt"}
    assert data["expiresAt"] == "2026-10-29T00:05:00Z"
    assert data["challengeToken"] != data["nonce"]
    assert len(data["challengeToken"]) >= 43 and len(data["nonce"]) >= 43
    state = db.add.call_args.args[0]
    assert isinstance(state, AuthState)
    assert state.purpose == "ANDROID_LOGIN"
    assert state.platform_type == "ANDROID" and state.social_type == social_type
    assert state.created_at == NOW
    assert state.expires_at == NOW + timedelta(minutes=5)
    assert state.social_user_id is None and state.consumed_at is None
    for raw, stored in (
        (data["challengeToken"], state.token_hash),
        (data["nonce"], state.nonce_hash),
    ):
        assert stored == hashlib.sha256(raw.encode()).hexdigest()
        assert raw not in repr(state.__dict__) + caplog.text
    db.add.assert_called_once()
    db.flush.assert_awaited_once()
    db.begin.return_value.__aexit__.assert_awaited_once_with(None, None, None)
    provider.verify_web.assert_not_awaited()
    assert "set-cookie" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


async def test_each_challenge_has_independent_secrets(client, challenge_api):
    first = (await client.post(PATH, json=BODY)).json()["data"]
    second = (await client.post(PATH, json=BODY)).json()["data"]
    assert (
        len(
            {
                first["challengeToken"],
                first["nonce"],
                second["challengeToken"],
                second["nonce"],
            }
        )
        == 4
    )


@pytest.mark.parametrize(
    "body,field,reason",
    [
        ({"socialType": "GOOGLE"}, "platformType", "missing"),
        ({"platformType": "ANDROID"}, "socialType", "missing"),
        (BODY | {"platformType": "WEB"}, "platformType", "invalid_choice"),
        (BODY | {"socialType": "OTHER"}, "socialType", "invalid_choice"),
        *[
            (BODY | {field: value}, field, reason)
            for field in ("socialType", "platformType")
            for value, reason in (
                (None, "null_not_allowed"),
                (123, "invalid_type"),
                (True, "invalid_type"),
                ([], "invalid_type"),
            )
        ],
        *[
            (BODY | {field: "synthetic-private"}, field, "extra_forbidden")
            for field in (
                "nonce",
                "challengeToken",
                "idToken",
                "authorizationCode",
                "social_type",
            )
        ],
    ],
)
async def test_invalid_challenge_never_writes(
    client, challenge_api, body, field, reason
):
    db, _ = challenge_api
    response = await client.post(PATH, json=body)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_REQUEST"
    assert {"field": field, "reason": reason} in error["details"]
    assert "synthetic-private" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    db.begin.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.parametrize("stage", ["flush", "commit"])
@pytest.mark.parametrize(
    "failure,status,code",
    [
        (ConnectionError("synthetic-private"), 503, "AUTH_UNAVAILABLE"),
        (
            ProgrammingError(
                "synthetic-private SQL", {}, Exception("synthetic-private")
            ),
            500,
            "INTERNAL_ERROR",
        ),
    ],
)
async def test_storage_failure_never_returns_challenge(
    client, challenge_api, stage, failure, status, code, caplog
):
    db, _ = challenge_api
    target = db.flush if stage == "flush" else db.begin.return_value.__aexit__
    target.side_effect = failure
    response = await client.post(PATH, json=BODY)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "data" not in response.json()
    assert "synthetic-private" not in response.text + caplog.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    assert "set-cookie" not in response.headers
    db.begin.return_value.__aexit__.assert_awaited_once()


def test_challenge_openapi_matches_contract(app):
    schema = app.openapi()
    operation = schema["paths"][PATH]["post"]
    assert "200" in operation["responses"]
    request = schema["components"]["schemas"]["SocialChallengeRequest"]
    assert set(request["required"]) == {"socialType", "platformType"}
    assert request["additionalProperties"] is False
    assert request["properties"]["platformType"]["const"] == "ANDROID"
    response = schema["components"]["schemas"]["SocialChallengeResponse"]
    assert set(response["required"]) == {"challengeToken", "nonce", "expiresAt"}


@pytest.mark.parametrize(
    "content,content_type,field,reason",
    [
        ("", "application/json", "body", "missing"),
        ("null", "application/json", "body", "null_not_allowed"),
        ("{", "application/json", "body", "invalid_json"),
        (
            '{"socialType":"GOOGLE","socialType":"KAKAO","platformType":"ANDROID"}',
            "application/json",
            "socialType",
            "duplicate",
        ),
        (
            '{"socialType":"GOOGLE","platformType":"ANDROID"}',
            "text/plain",
            "body",
            "invalid_format",
        ),
    ],
)
async def test_challenge_preserves_strict_json_contract(
    client, challenge_api, content, content_type, field, reason
):
    db, _ = challenge_api
    response = await client.post(
        PATH, content=content, headers={"Content-Type": content_type}
    )
    assert response.status_code == 422
    assert {"field": field, "reason": reason} in response.json()["error"]["details"]
    db.begin.assert_not_called()


async def test_challenge_rejects_query_fields(client, challenge_api):
    db, _ = challenge_api
    response = await client.post(PATH + "?socialType=KAKAO", json=BODY)
    assert response.status_code == 422
    assert {"field": "socialType", "reason": "extra_forbidden"} in response.json()[
        "error"
    ]["details"]
    db.begin.assert_not_called()
