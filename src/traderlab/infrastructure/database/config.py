"""Database configuration for PostgreSQL infrastructure.

Encapsulates connection parameters, credential secrets, and DSN generation
exclusively within the infrastructure layer.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection parameters."""

    model_config = SettingsConfigDict(
        env_prefix="TRADERLAB_DB_",
        populate_by_name=True,
        extra="ignore",
    )

    host: str = "localhost"
    port: int = 5432
    name: str = "traderlab"
    user: str = "traderlab_app"
    password: SecretStr = SecretStr("CHANGE_ME")
    schema_: str = "public"

    @field_validator("name")
    @classmethod
    def _validate_database_name(cls, v: str) -> str:
        forbidden = {"trader_muhendis", "borsa"}
        if v.lower() in forbidden or "muhendis" in v.lower():
            raise ValueError(f"Targeting database '{v}' is strictly prohibited for TraderLab.")
        return v

    @property
    def dsn(self) -> str:
        """Build a PostgreSQL DSN. Password is intentionally masked."""
        return f"postgresql+psycopg://{self.user}:***@{self.host}:{self.port}/{self.name}"

    @property
    def async_dsn(self) -> str:
        """Async DSN with the real password (used internally by connection pool)."""
        pw = self.password.get_secret_value()
        return f"postgresql+psycopg://{self.user}:{pw}@{self.host}:{self.port}/{self.name}"


def build_dsn(settings: DatabaseSettings) -> str:
    """Build the async PostgreSQL DSN from validated settings.

    Verifies DSN construction without instantiating an engine.
    """
    return settings.async_dsn


@lru_cache(maxsize=1)
def get_db_settings() -> DatabaseSettings:
    """Return a cached singleton of database settings."""
    return DatabaseSettings(
        _env_file=".env",
        _env_file_encoding="utf-8",
    )  # type: ignore[call-arg]
