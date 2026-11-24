import pytest
from pydantic import ValidationError

from coach_ai.config import Settings


def test_key_is_masked(settings):
    assert "test-key" not in repr(settings)
    assert settings.llm_api_key.get_secret_value() == "test-key"


def test_defaults(settings):
    assert settings.llm_model == "gemma4-31b"
    assert settings.llm_max_concurrency == 1


def test_concurrency_cannot_exceed_server_limit(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MAX_CONCURRENCY", "4")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_missing_key_fails(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
