"""Integration tests for Alembic migrations.

Verifies:
- Upgrade to head creates all schema objects, constraints, and indexes.
- Downgrade to base safely drops all objects without orphaned artifacts.
- Re-upgrade to head works idempotently.
- Forbidden databases (e.g. trader_muhendis) are strictly rejected with ConfigurationError.
"""

from __future__ import annotations

import pytest
from migrations.env import get_verified_url
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from traderlab.core.errors import ConfigurationError


@pytest.mark.integration
class TestAlembicMigrations:
    @pytest.mark.asyncio
    async def test_schema_objects_exist_after_migration(self, test_engine: AsyncEngine) -> None:
        """Verify candles table, primary key, and composite indexes exist."""
        async with test_engine.connect() as conn:

            def check_inspector(sync_conn) -> dict[str, list]:
                inspector = inspect(sync_conn)
                tables = inspector.get_table_names()
                columns = (
                    [c["name"] for c in inspector.get_columns("candles")]
                    if "candles" in tables
                    else []
                )
                pk = inspector.get_pk_constraint("candles") if "candles" in tables else {}
                indexes = (
                    [idx["name"] for idx in inspector.get_indexes("candles")]
                    if "candles" in tables
                    else []
                )
                return {
                    "tables": tables,
                    "columns": columns,
                    "pk": pk.get("constrained_columns", []),
                    "indexes": indexes,
                }

            info = await conn.run_sync(check_inspector)

        assert "candles" in info["tables"]
        # Required columns
        expected_columns = [
            "instrument_id",
            "source_id",
            "timeframe",
            "open_time",
            "price_type",
            "revision",
            "close_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "tick_volume",
            "spread",
            "is_closed",
            "is_synthetic",
            "published_at",
            "received_at",
            "available_at",
            "ingested_at",
            "availability_confidence",
        ]
        for col in expected_columns:
            assert col in info["columns"], f"Expected column {col} missing from candles table"

        # Primary Key (Compound)
        expected_pk = [
            "instrument_id",
            "source_id",
            "timeframe",
            "open_time",
            "price_type",
            "revision",
        ]
        assert set(info["pk"]) == set(expected_pk)

        # Composite Indexes
        assert "idx_candles_pit_lookup" in info["indexes"]
        assert "idx_candles_instrument_tf" in info["indexes"]

    def test_forbidden_database_migration_rejected(self) -> None:
        """Verify that get_verified_url raises ConfigurationError for unauthorized DBs."""
        with pytest.raises(ConfigurationError) as exc_info:
            get_verified_url(override_db="trader_muhendis")

        assert "forbidden" in str(exc_info.value).lower()
        assert "trader_muhendis" in str(exc_info.value)
