from pathlib import Path
from urllib.parse import quote

import pytest
from pydantic import SecretStr, ValidationError

from app.core import config
from app.core.config import Settings


def test_password_is_masked_and_url_is_not_serialized() -> None:
    password = "SYNTHETIC-PASSWORD@:/?#% +"
    settings = Settings(_env_file=None, app_env="test", postgres_password=password)
    assert password not in repr(settings)
    assert password not in str(settings)
    assert isinstance(settings.postgres_password, SecretStr)
    assert settings.postgres_password.get_secret_value() == password
    assert "database_url" not in settings.model_dump()
    assert settings.database_url not in repr(settings)
    assert settings.database_url not in str(settings)
    assert password not in settings.model_dump_json()


def test_database_url_encodes_password() -> None:
    password = "synthetic@:/?#% +"
    settings = Settings(
        _env_file=None,
        app_env="test",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="synthetic_db",
        postgres_user="synthetic_user",
        postgres_password=password,
    )
    assert settings.database_url == (
        f"postgresql+asyncpg://synthetic_user:{quote(password, safe='')}@localhost:5432/synthetic_db"
    )
    assert password not in settings.database_url


def test_cors_origins_are_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://a, http://b,")
    settings = Settings(_env_file=None, app_env="test")
    assert settings.cors_allowed_origins == ["http://a", "http://b"]


def test_android_provider_settings_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "GOOGLE_ANDROID_CLIENT_IDS", "synthetic-debug, synthetic-release,"
    )
    monkeypatch.setenv("KAKAO_NATIVE_APP_KEY", "synthetic-native-key")
    settings = Settings(_env_file=None)
    assert settings.google_android_client_ids == [
        "synthetic-debug",
        "synthetic-release",
    ]
    assert settings.kakao_native_app_key.get_secret_value() == "synthetic-native-key"
    assert "synthetic-native-key" not in repr(settings) + settings.model_dump_json()


def test_android_provider_settings_are_optional_and_isolated(settings) -> None:
    assert settings.google_android_client_ids == []
    assert settings.kakao_native_app_key.get_secret_value() == ""


def test_cors_value_is_not_json_decoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", '["http://a"]')
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="test")


@pytest.mark.parametrize(
    "origin",
    ["*", "null", "https://a/", "https://a?x=1", "https://user@a", "https://*.example"],
)
def test_cors_rejects_non_origins(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_allowed_origins=[origin])


def test_candiy_secret_is_masked_and_sync_is_disabled_by_default(monkeypatch):
    monkeypatch.setenv("CANDIY_CLIENT_SECRET", "SYNTHETIC-CLIENT-SECRET")
    settings = Settings(_env_file=None, app_env="test")
    assert settings.candiy_client_secret.get_secret_value() == "SYNTHETIC-CLIENT-SECRET"
    assert not settings.pipeline_enabled and not settings.pipeline_include_members
    assert not hasattr(settings, "CANDIY_CLIENT_SECRET")
    assert "SYNTHETIC-CLIENT-SECRET" not in repr(settings.model_dump())
    assert "SYNTHETIC-CLIENT-SECRET" not in settings.model_dump_json()


def test_env_path_points_to_repository_root_without_reading_it() -> None:
    assert (
        Settings.model_config["env_file"]
        == Path(config.__file__).resolve().parents[3] / ".env"
    )
    assert Settings.model_config["extra"] == "ignore"


@pytest.mark.parametrize("value", ["", " ", "\t", "false", "true"])
def test_legacy_sample_app_flag_from_environment(value, monkeypatch, settings):
    from app.main import create_app

    monkeypatch.setenv("PIPELINE_INCLUDE_SAMPLE_APP", value)
    configured = Settings(_env_file=None, auth_signing_key=settings.auth_signing_key)
    assert configured.pipeline_include_sample_app is (value == "true")
    assert not configured.pipeline_enabled
    assert "/api/v1/health/sync" in create_app(configured).openapi()["paths"]


def test_invalid_sample_app_flag_is_not_silently_disabled(monkeypatch):
    monkeypatch.setenv("PIPELINE_INCLUDE_SAMPLE_APP", "not-a-boolean")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_is_cached_without_loading_dotenv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, app_env="test")
    calls = []

    def make_settings() -> Settings:
        calls.append(True)
        return settings

    config.get_settings.cache_clear()
    monkeypatch.setattr(config, "Settings", make_settings)
    try:
        assert config.get_settings() is settings
        assert config.get_settings() is settings
        assert len(calls) == 1
    finally:
        config.get_settings.cache_clear()
