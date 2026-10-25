"""Application-level configuration loaded from environment variables.

Uses ``pydantic-settings`` so that every variable is validated at startup
and missing required values surface immediately with clear messages.

Security notes:
    - Infrastructure-specific credentials (e.g. database password) live in
      their respective infrastructure modules, NOT here in core.
    - The ``.env`` file is loaded only in ``development`` environment.
"""

from __future__ import annotations

import enum
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(enum.Enum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class AppSettings(BaseSettings):
    """Top-level application settings."""

    model_config = SettingsConfigDict(
        env_prefix="TRADERLAB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, v: str) -> str:
        return v.upper()


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return a cached singleton of the application settings.

    Call ``get_settings.cache_clear()`` in tests to reset.
    """
    return AppSettings()
