from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# 해당 파일 위치(ai/src/coach_ai/)에서 세 단계 위가 저장소 루트
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """저장소 루트 .env 또는 OS 환경변수에서만 읽는 설정. 키는 코드·로그·커밋에 기록하지 않는다."""

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    llm_base_url: str = Field(default="https://llm.example.invalid/v1", alias="LLM_BASE_URL")
    llm_api_key: SecretStr = Field(alias="LLM_API_KEY")  # repr과 로그에는 '**********'로 표시
    llm_model: str = Field(default="gemma4-31b", alias="LLM_CHAT_MODEL")
    llm_embed_model: str = Field(default="bge-m3", alias="LLM_EMBEDDING_MODEL")
    # 서버 동시 요청 한도가 3이라 3을 넘는 값은 허용하지 않음
    llm_max_concurrency: int = Field(default=1, ge=1, le=3, alias="LLM_MAX_CONCURRENCY")
    # 컨텍스트 상한이 16,384토큰(입력+출력)이라 답변 길이를 묶어 입력 예산을 확보한다
    llm_max_tokens: int = Field(default=1000, ge=1, alias="LLM_MAX_TOKENS")
    llm_connect_timeout: float = Field(default=5.0, gt=0, alias="LLM_CONNECT_TIMEOUT")
    # 스트리밍에서 청크 사이 허용 대기. 첫 청크까지의 대기(긴 입력 prefill 포함)에도 적용된다
    llm_chunk_timeout: float = Field(default=30.0, gt=0, alias="LLM_CHUNK_TIMEOUT")
    # 비스트리밍은 생성이 끝나야 응답이 오므로 답변 전체 생성 시간을 덮을 만큼 길게 둔다
    llm_read_timeout: float = Field(default=90.0, gt=0, alias="LLM_READ_TIMEOUT")
    llm_max_retries: int = Field(default=2, ge=0, le=5, alias="LLM_MAX_RETRIES")
    # 비워 두면 계측 기록을 메모리에만 저장한다
    llm_metrics_path: Path | None = Field(default=None, alias="LLM_METRICS_PATH")
    # 계측 기록에 값을 남길 응답 헤더 이름(쉼표 구분). 값에 키·개인정보가 없는지 확인한 헤더만 넣는다
    llm_instance_headers: str = Field(default="", alias="LLM_INSTANCE_HEADERS")
    # 근거로 내보낼 새 지표·kind 이름(쉼표 구분). 웹이 모르는 이름의 근거가 오면 대화 화면이 열리지 않아, 웹이 지원을
    # 병합한 환경에서만 이름을 더한다. 기본 다섯 지표의 근거는 이 목록과 관계없이 내보낸다
    coach_evidence_enabled: str = Field(default="", alias="COACH_EVIDENCE_ENABLED")

    @property
    def instance_header_names(self) -> list[str]:
        return [h.strip() for h in self.llm_instance_headers.split(",") if h.strip()]

    @property
    def evidence_names(self) -> frozenset[str]:
        return frozenset(name.strip() for name in self.coach_evidence_enabled.split(",") if name.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
