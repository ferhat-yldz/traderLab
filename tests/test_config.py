"""Unit tests for configuration and database DSN construction."""

from __future__ import annotations

from pydantic import SecretStr

from traderlab.core.config import AppSettings, Environment, get_settings
from traderlab.infrastructure.database.config import DatabaseSettings, build_dsn, get_db_settings


class TestDatabaseSettings:
    def test_defaults(self) -> None:
        settings = DatabaseSettings()
        assert settings.host == "localhost"
        assert settings.port == 5432
        assert settings.name == "traderlab"
        assert settings.user == "traderlab_app"

    def test_dsn_masks_password(self) -> None:
        settings = DatabaseSettings()
        dsn = settings.dsn
        assert "***" in dsn
        assert "CHANGE_ME" not in dsn

    def test_async_dsn_contains_password(self) -> None:
        settings = DatabaseSettings(password=SecretStr("s3cret"))
        dsn = settings.async_dsn
        assert "s3cret" in dsn


class TestBuildDsn:
    def test_build_dsn(self) -> None:
        settings = DatabaseSettings(
            host="db.example.com",
            port=5433,
            name="mydb",
            user="myuser",
            password=SecretStr("mypass"),
        )
        dsn = build_dsn(settings)
        assert dsn == "postgresql+psycopg://myuser:mypass@db.example.com:5433/mydb"


class TestAppSettings:
    def test_defaults(self) -> None:
        settings = AppSettings()
        assert settings.env == Environment.DEVELOPMENT
        assert settings.log_level == "INFO"

    def test_log_level_normalised(self) -> None:
        settings = AppSettings(log_level="debug")
        assert settings.log_level == "DEBUG"


class TestGetSettings:
    def test_returns_app_settings(self) -> None:
        get_settings.cache_clear()
        settings = get_settings()
        assert isinstance(settings, AppSettings)
        get_settings.cache_clear()

    def test_cached(self) -> None:
        get_settings.cache_clear()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2
        get_settings.cache_clear()


class TestGetDbSettings:
    def test_returns_db_settings(self) -> None:
        get_db_settings.cache_clear()
        settings = get_db_settings()
        assert isinstance(settings, DatabaseSettings)
        get_db_settings.cache_clear()

    def test_cached(self) -> None:
        get_db_settings.cache_clear()
        s1 = get_db_settings()
        s2 = get_db_settings()
        assert s1 is s2
        get_db_settings.cache_clear()
