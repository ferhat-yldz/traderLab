"""Integration test fixtures for PostgreSQL operations.

Guarantees tests run ONLY on the isolated 'traderlab_test' database.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from traderlab.core.errors import ConfigurationError
from traderlab.infrastructure.database.config import DatabaseSettings, get_db_settings
from traderlab.infrastructure.database.session import create_db_engine, get_session_factory


@pytest.fixture(scope="session")
def test_db_settings() -> DatabaseSettings:
    """Provide verified database settings pointing strictly to the test database."""
    base_settings = get_db_settings()
    settings = DatabaseSettings(
        host=base_settings.host,
        port=base_settings.port,
        name="traderlab_test",
        user=base_settings.user,
        password=base_settings.password,
        schema_=base_settings.schema_,
    )
    if settings.name != "traderlab_test":
        raise ConfigurationError("Integration tests must target 'traderlab_test' exclusively.")
    return settings


@pytest_asyncio.fixture(scope="session")
async def test_engine(test_db_settings: DatabaseSettings) -> AsyncGenerator[AsyncEngine, None]:
    """Provide a session-scoped AsyncEngine connected to traderlab_test."""
    engine = create_db_engine(test_db_settings)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def test_session_factory(test_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Provide an async sessionmaker bound to the test database."""
    return get_session_factory(test_engine)


@pytest_asyncio.fixture(autouse=True)
async def clean_candles_table(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[None, None]:
    """Truncate the candles table before every test to ensure strict test isolation."""
    async with test_session_factory() as session:
        # Check that we are indeed connected to traderlab_test before truncating!
        res = await session.execute(text("SELECT current_database()"))
        current_db = res.scalar_one()
        if current_db != "traderlab_test":
            raise ConfigurationError(
                f"SAFETY VIOLATION: Attempted to run test on database '{current_db}' instead of 'traderlab_test'!"
            )
        await session.execute(text("TRUNCATE TABLE candles CASCADE;"))
        await session.commit()
    yield
