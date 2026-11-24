import pytest

from coach_ai.config import Settings


@pytest.fixture
def settings(monkeypatch) -> Settings:
    """실제 .env를 읽지 않는 테스트용 설정."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.test/v1")
    return Settings(_env_file=None)
