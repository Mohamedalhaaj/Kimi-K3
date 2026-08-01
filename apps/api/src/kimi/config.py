"""Application configuration.

Every value is sourced from the environment (or a local ``.env``). Secrets are
never written to logs: :class:`Settings` deliberately keeps API keys as
``SecretStr`` so that accidental interpolation renders ``**********``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- runtime -------------------------------------------------------
    environment: Literal["local", "production"] = "local"
    debug: bool = False
    log_level: str = "INFO"

    # ---- server --------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8787
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # ---- database ------------------------------------------------------
    # aiosqlite for the default local install; swap for postgresql+asyncpg://
    database_url: str = f"sqlite+aiosqlite:///{REPO_ROOT / 'data' / 'kimi.db'}"

    # ---- model provider ------------------------------------------------
    tokenrouter_api_key: SecretStr | None = None
    tokenrouter_base_url: str = "https://api.tokenrouter.com/v1"
    default_model: str = "moonshotai/kimi-k3-free"
    request_timeout_s: float = 120.0
    connect_timeout_s: float = 10.0
    max_retries: int = 2

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Allow ``CORS_ORIGINS=a,b`` in addition to a JSON list."""
        if isinstance(v, str) and not v.strip().startswith("["):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def has_model_provider(self) -> bool:
        """True when a provider credential is configured."""
        return self.tokenrouter_api_key is not None and bool(
            self.tokenrouter_api_key.get_secret_value().strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
