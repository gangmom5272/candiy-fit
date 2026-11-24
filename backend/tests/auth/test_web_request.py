import pytest
from pydantic import ValidationError

from app.auth.schemas import WebSocialLoginRequest
from app.auth.web import validate_web_login
from app.core.errors import ApiError
from app.core.validation import validation_details


@pytest.fixture
def login_body():
    return {
        "socialType": "GOOGLE",
        "platformType": "WEB",
        "authorizationCode": "synthetic-code",
        "codeVerifier": "a" * 43,
        "redirectUri": "https://web.example/auth/callback/google",
    }


@pytest.mark.parametrize("size", [43, 128])
def test_verifier_boundaries(login_body, size):
    login_body["codeVerifier"] = ("a-._~" * 26)[:size]
    assert (
        WebSocialLoginRequest.model_validate(login_body).code_verifier
        == login_body["codeVerifier"]
    )


@pytest.mark.parametrize(
    "value",
    ["a" * 42, "a" * 129, "가" * 43, "a" * 42 + "!", "a" * 42 + "\n", None, 123],
)
def test_invalid_verifier(login_body, value):
    login_body["codeVerifier"] = value
    with pytest.raises(ValidationError):
        WebSocialLoginRequest.model_validate(login_body)


@pytest.mark.parametrize("field", ["idToken", "challengeToken", "nonce", "unknown"])
def test_extra_fields(login_body, field):
    login_body[field] = "synthetic"
    with pytest.raises(ValidationError) as error:
        WebSocialLoginRequest.model_validate(login_body)
    assert validation_details(error.value.errors())[0].reason == "extra_forbidden"


@pytest.mark.parametrize(
    "field",
    ["socialType", "platformType", "authorizationCode", "codeVerifier", "redirectUri"],
)
def test_required_fields(login_body, field):
    del login_body[field]
    with pytest.raises(ValidationError) as error:
        WebSocialLoginRequest.model_validate(login_body)
    assert validation_details(error.value.errors())[0].reason == "missing"


def test_web_only(login_body):
    login_body["platformType"] = "ANDROID"
    with pytest.raises(ValidationError):
        WebSocialLoginRequest.model_validate(login_body)


@pytest.mark.parametrize(
    "origin",
    [None, "null", "*", "http://localhost:5173/", "http://localhost:5173.evil"],
)
def test_exact_origin(login_body, settings, origin):
    with pytest.raises(ApiError) as error:
        validate_web_login(
            WebSocialLoginRequest.model_validate(login_body), origin, settings
        )
    assert error.value.code == "ORIGIN_NOT_ALLOWED"


def test_exact_provider_redirect(login_body, settings):
    settings.google_redirect_uris = [login_body["redirectUri"]]
    request = WebSocialLoginRequest.model_validate(login_body)
    validate_web_login(request, "http://localhost:5173", settings)
    for value in [request.redirect_uri + "/", request.redirect_uri + "?next=x"]:
        with pytest.raises(ApiError) as error:
            validate_web_login(
                request.model_copy(update={"redirect_uri": value}),
                "http://localhost:5173",
                settings,
            )
        assert error.value.details[0].field == "redirectUri"
    with pytest.raises(ApiError):
        validate_web_login(
            request.model_copy(update={"social_type": "KAKAO"}),
            "http://localhost:5173",
            settings,
        )


def test_oauth_settings_mask_secrets_and_parse_lists(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv(
        "GOOGLE_REDIRECT_URIS", "https://web.example/a,https://web.example/b"
    )
    settings = Settings(
        _env_file=None,
        google_client_secret="synthetic-google-secret",
        kakao_rest_api_key="synthetic-kakao-key",
        kakao_client_secret="synthetic-kakao-secret",
    )
    assert settings.google_redirect_uris == [
        "https://web.example/a",
        "https://web.example/b",
    ]
    assert "synthetic-google-secret" not in settings.model_dump_json()
    assert "synthetic-kakao" not in repr(settings)
