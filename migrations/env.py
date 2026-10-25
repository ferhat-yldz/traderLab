"""Alembic environment configuration with strict safety checks for TraderLab.

Guarantees that migrations run ONLY against authorized TraderLab databases
(e.g., 'traderlab' or 'traderlab_test') and NEVER touch external databases.
"""

import asyncio
from logging.config import fileConfig
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from traderlab.core.errors import ConfigurationError
from traderlab.infrastructure.database.config import get_db_settings
from traderlab.infrastructure.database.models import Base
from traderlab.infrastructure.database.session import PERMITTED_DATABASES

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
try:
    config = context.config
except AttributeError:
    config = None

# Interpret the config file for Python logging.
if config is not None and config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_verified_url(override_db: str | None = None) -> str:
    """Retrieve and verify target database URL against strict whitelist."""
    # Check if a custom db override was provided via -x db=traderlab_test
    if override_db is None:
        try:
            x_args = context.get_x_argument(as_dictionary=True)
            override_db = x_args.get("db")
        except Exception:
            override_db = None

    settings = get_db_settings()
    target_db = override_db or settings.name

    if target_db.lower() not in PERMITTED_DATABASES:
        raise ConfigurationError(
            f"Alembic target database '{target_db}' is forbidden! "
            f"Must be one of {sorted(PERMITTED_DATABASES)}."
        )

    # Construct the async connection DSN
    pw = settings.password.get_secret_value()
    return f"postgresql+psycopg://{settings.user}:{pw}@{settings.host}:{settings.port}/{target_db}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_verified_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    url = get_verified_url()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = url

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if config is not None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
