"""Database infrastructure — SQLAlchemy engine, session management.

No schema or models are defined in this phase.  This module only provides
the database engine factory and session management utilities that will be
used by repository adapters in later phases.

Security:
    - Connection string uses SecretStr from config (never logged).
    - Pool settings are conservative defaults suitable for research workloads.
"""

from __future__ import annotations

from traderlab.infrastructure.database.config import DatabaseSettings, build_dsn, get_db_settings
from traderlab.infrastructure.database.models import Base, CandleModel
from traderlab.infrastructure.database.repositories import PostgresCandleRepository
from traderlab.infrastructure.database.session import (
    PERMITTED_DATABASES,
    create_db_engine,
    get_session_factory,
    session_scope,
)

__all__ = [
    "PERMITTED_DATABASES",
    "Base",
    "CandleModel",
    "DatabaseSettings",
    "PostgresCandleRepository",
    "build_dsn",
    "create_db_engine",
    "get_db_settings",
    "get_session_factory",
    "session_scope",
]
