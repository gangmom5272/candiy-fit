"""백엔드 설정. .env는 Settings를 생성할 때만 읽는다."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["local", "test", "dev", "prod"] = "local"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "candiy_fit"
    postgres_user: str = "candiy"
    postgres_password: SecretStr = SecretStr("")
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    auth_signing_key: SecretStr | None = None
    google_client_id: str = ""
    google_android_client_ids: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uris: Annotated[list[str], NoDecode] = Field(default_factory=list)
    kakao_rest_api_key: SecretStr = SecretStr("")
    kakao_native_app_key: SecretStr = SecretStr("")
    kakao_client_secret: SecretStr = SecretStr("")
    kakao_app_id: int | None = Field(default=None, gt=0)
    kakao_redirect_uris: Annotated[list[str], NoDecode] = Field(default_factory=list)
    pipeline_enabled: bool = False
    pipeline_include_members: bool = False
    pipeline_include_sample_app: bool = False
    candiy_base_url: str = ""
    candiy_client_id: SecretStr = SecretStr("")
    candiy_client_secret: SecretStr = SecretStr("")

    @field_validator("pipeline_include_sample_app", mode="before")
    @classmethod
    def parse_legacy_sample_app_flag(cls, value: str | bool) -> str | bool:
        return False if isinstance(value, str) and not value.strip() else value

    @field_validator(
        "cors_allowed_origins",
        "google_redirect_uris",
        "google_android_client_ids",
        "kakao_redirect_uris",
        mode="before",
    )
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        origins = value.split(",") if isinstance(value, str) else value
        return [origin.strip() for origin in origins if origin.strip()]

    @field_validator("cors_allowed_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        for value in values:
            url = urlsplit(value)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or value != f"{url.scheme}://{url.netloc}"
                or "*" in value
            ):
                raise ValueError("CORS origins must be exact HTTP origins")
        return values

    @property
    def database_url(self) -> str:
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password.get_secret_value(), safe="")
        database = quote(self.postgres_db, safe="")
        return f"postgresql+asyncpg://{user}:{password}@{self.postgres_host}:{self.postgres_port}/{database}"

    def require_auth_signing_key(self) -> bytes:
        if self.auth_signing_key is None:
            raise ValueError("AUTH_SIGNING_KEY is required for the API")
        key = self.auth_signing_key.get_secret_value().encode("utf-8")
        if len(key) < 32:
            raise ValueError("AUTH_SIGNING_KEY must contain at least 32 bytes")
        return key


@lru_cache
def get_settings() -> Settings:
    return Settings()
