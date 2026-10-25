"""Async database engine and session factory for PostgreSQL.

Ensures strict fail-fast validation of target database names and provides
context-managed async sessions with automatic rollback on failure.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if sys.platform == "win32":
    with suppress(Exception):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from traderlab.core.errors import ConfigurationError
from traderlab.infrastructure.database.config import DatabaseSettings, get_db_settings

# Explicit whitelist of permitted database targets for TraderLab operations.
# Strictly prohibits accidental connection to 'trader_muhendis' or production borsa systems.
PERMITTED_DATABASES: frozenset[str] = frozenset({"traderlab", "traderlab_test"})


def create_db_engine(settings: DatabaseSettings | None = None) -> AsyncEngine:
    """Create an async SQLAlchemy engine with connection safety verification.

    Raises:
        ConfigurationError: If the target database is not explicitly permitted.
    """
    cfg = settings or get_db_settings()

    if cfg.name.lower() not in PERMITTED_DATABASES:
        raise ConfigurationError(
            f"Database '{cfg.name}' is NOT in permitted TraderLab databases: "
            f"{sorted(PERMITTED_DATABASES)}. Operation aborted to protect external databases."
        )

    return create_async_engine(
        cfg.async_dsn,
        connect_args={"options": "-c timezone=utc"},
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the provided engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async session scope.

    Rolls back transaction on any unhandled exception.
    """
    session = session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
