"""코칭 설정·계측을 격리하고 DB 테스트는 건강 조회와 같은 전용 DB를 쓴다."""

from collections.abc import Iterator
from unittest.mock import Mock

import pytest
from pydantic_settings import DotEnvSettingsSource

from coach_ai.llm import metrics
from tests.health.conftest import health_db as health_db


@pytest.fixture(autouse=True)
def forbid_implicit_configuration(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """코칭 테스트의 전역 계측·환경 파일 접근은 콜백이 예외를 삼켜도 실패한다."""
    global_recorder = Mock(side_effect=AssertionError("메모리 recorder를 주입하세요"))
    dotenv_access = Mock(side_effect=AssertionError("_env_file=None을 지정하세요"))
    read_env_files = DotEnvSettingsSource._read_env_files

    def require_no_env_file(source: DotEnvSettingsSource) -> dict[str, str | None]:
        """환경 파일 경로가 설정되면 파일을 찾거나 열기 전에 실패한다."""
        if source.env_file is not None:
            dotenv_access()
        return read_env_files(source)

    monkeypatch.setattr(metrics, "get_recorder", global_recorder)
    monkeypatch.setattr(DotEnvSettingsSource, "_read_env_files", require_no_env_file)
    yield
    global_recorder.assert_not_called()
    dotenv_access.assert_not_called()
