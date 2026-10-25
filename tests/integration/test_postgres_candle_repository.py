"""Integration tests for PostgresCandleRepository on real PostgreSQL.

Tests run against the isolated 'traderlab_test' database.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from traderlab.core.errors import ConfigurationError
from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.infrastructure.database.config import DatabaseSettings
from traderlab.infrastructure.database.models import CandleModel
from traderlab.infrastructure.database.repositories.candle_repository import (
    PostgresCandleRepository,
)
from traderlab.infrastructure.database.session import create_db_engine, session_scope


@pytest.mark.integration
class TestPostgresCandleRepository:
    @pytest.mark.asyncio
    async def test_save_and_retrieve_single_candle(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify basic persistence, UTC awareness, and exact Decimal round-trip."""
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

        candle = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("oanda-live"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2035.12345678"),
            high=Decimal("2040.87654321"),
            low=Decimal("2030.00000000"),
            close=Decimal("2038.55555555"),
            volume=Decimal("1250.5000"),
            price_type=PriceType.BID,
            published_at=t_close,
            received_at=t_close + timedelta(milliseconds=150),
            available_at=t_close + timedelta(milliseconds=200),
            ingested_at=t_close + timedelta(milliseconds=300),
            availability_confidence=AvailabilityConfidence.VERIFIED,
            spread=Decimal("0.25000000"),
            is_closed=True,
        )

        saved = await repo.save_candles([candle])
        assert saved == 1

        results = await repo.get_candles(
            source_id=DataSourceId("oanda-live"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            as_of_utc=t_close + timedelta(minutes=5),
            policy=PitPolicy.STRICT_PIT,
        )

        assert len(results) == 1
        retrieved = results[0]
        assert retrieved.instrument_id == InstrumentId("XAUUSD")
        assert retrieved.open == Decimal("2035.12345678")
        assert retrieved.high == Decimal("2040.87654321")
        assert retrieved.low == Decimal("2030.00000000")
        assert retrieved.close == Decimal("2038.55555555")
        assert retrieved.volume == Decimal("1250.5000")
        assert retrieved.spread == Decimal("0.25000000")
        assert retrieved.price_type == PriceType.BID
        assert retrieved.open_time.tzinfo is not None
        assert retrieved.ingested_at is not None

    @pytest.mark.asyncio
    async def test_multi_instrument_and_source_isolation(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify data from different instruments and sources do not collide or leak."""
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

        c1 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-alpha"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2025.00"),
            close=Decimal("2032.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
        )

        c2 = Candle(
            instrument_id=InstrumentId("EURUSD"),
            source_id=DataSourceId("feed-beta"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("1.08500"),
            high=Decimal("1.08700"),
            low=Decimal("1.08400"),
            close=Decimal("1.08600"),
            volume=Decimal("500"),
            price_type=PriceType.BID,
            is_closed=True,
        )

        await repo.save_candles([c1, c2])

        # Query feed-alpha
        alpha_res = await repo.get_candles(
            source_id=DataSourceId("feed-alpha"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(alpha_res) == 1
        assert alpha_res[0].instrument_id == InstrumentId("XAUUSD")

        # Query feed-beta
        beta_res = await repo.get_candles(
            source_id=DataSourceId("feed-beta"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(beta_res) == 1
        assert beta_res[0].instrument_id == InstrumentId("EURUSD")

    @pytest.mark.asyncio
    async def test_simultaneous_bid_and_ask_storage(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify storing BID and ASK candles at identical open_time without collision."""
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

        bid_candle = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-quotes"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2025.00"),
            close=Decimal("2032.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
        )

        ask_candle = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-quotes"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.30"),
            high=Decimal("2035.30"),
            low=Decimal("2025.30"),
            close=Decimal("2032.30"),
            volume=Decimal("100"),
            price_type=PriceType.ASK,
            is_closed=True,
        )

        saved = await repo.save_candles([bid_candle, ask_candle])
        assert saved == 2

        # Filter by BID
        bids = await repo.get_candles(
            source_id=DataSourceId("feed-quotes"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            price_type=PriceType.BID,
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(bids) == 1
        assert bids[0].price_type == PriceType.BID
        assert bids[0].open == Decimal("2030.00")

        # Filter by ASK
        asks = await repo.get_candles(
            source_id=DataSourceId("feed-quotes"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            price_type=PriceType.ASK,
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(asks) == 1
        assert asks[0].price_type == PriceType.ASK
        assert asks[0].open == Decimal("2030.30")

    @pytest.mark.asyncio
    async def test_idempotent_duplicate_storage(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify that persisting the exact same candle twice is a safe no-op."""
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

        candle = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-idempotent"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2025.00"),
            close=Decimal("2032.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
        )

        saved_first = await repo.save_candles([candle])
        assert saved_first == 1

        saved_second = await repo.save_candles([candle])
        assert saved_second == 0  # Idempotent: 0 new rows written

        res = await repo.get_candles(
            source_id=DataSourceId("feed-idempotent"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(res) == 1

    @pytest.mark.asyncio
    async def test_data_correction_as_new_revision_and_snapshot_query(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify data revisions, append-only retention, and bitemporal snapshot queries."""
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

        t_ingest_rev0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        t_ingest_rev1 = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)

        # Revision 0 (original report)
        rev0_candle = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-revisions"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2025.00"),
            close=Decimal("2032.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            ingested_at=t_ingest_rev0,
            revision=0,
            is_closed=True,
        )

        # Revision 1 (corrected close price)
        rev1_candle = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-revisions"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2025.00"),
            close=Decimal("2034.50"),  # Corrected close
            volume=Decimal("105"),
            price_type=PriceType.BID,
            ingested_at=t_ingest_rev1,
            revision=1,
            is_closed=True,
        )

        await repo.save_candles([rev0_candle])
        await repo.save_candles([rev1_candle])

        # Query with snapshot time BEFORE revision 1 was ingested (e.g. 2026-01-15)
        snap_prior = datetime(2026, 1, 15, 0, 0, 0, tzinfo=UTC)
        prior_res = await repo.get_candles(
            source_id=DataSourceId("feed-revisions"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            knowledge_snapshot_time=snap_prior,
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(prior_res) == 1
        assert prior_res[0].revision == 0
        assert prior_res[0].close == Decimal("2032.00")

        # Query with snapshot time AFTER revision 1 was ingested (e.g. 2026-02-15)
        snap_after = datetime(2026, 2, 15, 0, 0, 0, tzinfo=UTC)
        after_res = await repo.get_candles(
            source_id=DataSourceId("feed-revisions"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            knowledge_snapshot_time=snap_after,
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(after_res) == 1
        assert after_res[0].revision == 1
        assert after_res[0].close == Decimal("2034.50")

    @pytest.mark.asyncio
    async def test_strict_pit_verified_confidence_required(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify STRICT_PIT rejects ESTIMATED or UNKNOWN bars."""
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

        candle_estimated = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-pit-test"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2025.00"),
            close=Decimal("2032.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            available_at=t_close,
            availability_confidence=AvailabilityConfidence.ESTIMATED,
            is_closed=True,
        )
        await repo.save_candles([candle_estimated])

        # STRICT_PIT query -> must return empty
        res_strict = await repo.get_candles(
            source_id=DataSourceId("feed-pit-test"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            as_of_utc=t_close + timedelta(minutes=10),
            policy=PitPolicy.STRICT_PIT,
        )
        assert len(res_strict) == 0

        # RESEARCH_ESTIMATED query -> returns the candle
        res_research = await repo.get_candles(
            source_id=DataSourceId("feed-pit-test"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            as_of_utc=t_close + timedelta(minutes=10),
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(res_research) == 1

    @pytest.mark.asyncio
    async def test_research_estimated_historical_backfill(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify RESEARCH_ESTIMATED permits bulk historical data where available_at is None."""
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

        # Historical backfilled candle without real-time available_at
        candle_backfill = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-backfill"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2025.00"),
            close=Decimal("2032.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            available_at=None,
            ingested_at=datetime(2026, 10, 20, 0, 0, 0, tzinfo=UTC),
            is_closed=True,
        )
        await repo.save_candles([candle_backfill])

        # Evaluated as of historical decision time 11:05
        decision_time = t_close + timedelta(minutes=5)
        res = await repo.get_candles(
            source_id=DataSourceId("feed-backfill"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            as_of_utc=decision_time,
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(res) == 1
        assert res[0].open == Decimal("2030.00")

    @pytest.mark.asyncio
    async def test_rejection_of_unclosed_candle_at_decision_time(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify that an H1 candle (10:00-11:00) is rejected at decision time 10:15."""
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

        candle = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-unclosed"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2025.00"),
            close=Decimal("2032.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
        )
        await repo.save_candles([candle])

        # Decision time 10:15 is BEFORE bar close (11:00) -> MUST be rejected
        decision_time = t_open + timedelta(minutes=15)
        res = await repo.get_candles(
            source_id=DataSourceId("feed-unclosed"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            as_of_utc=decision_time,
            policy=PitPolicy.RESEARCH_ESTIMATED,
            only_closed=True,
        )
        assert len(res) == 0

    @pytest.mark.asyncio
    async def test_null_spread_and_unknown_volume(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify distinguishing between unknown volume (None) vs zero volume (Decimal(0))."""
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

        candle_unknown_vol = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-nulls"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2025.00"),
            close=Decimal("2032.00"),
            volume=None,  # Unknown volume!
            price_type=PriceType.BID,
            spread=None,  # Unknown spread!
            is_closed=True,
        )

        candle_zero_vol = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-nulls"),
            timeframe=Timeframe.H1,
            open_time=t_open + timedelta(hours=1),
            close_time=t_close + timedelta(hours=1),
            open=Decimal("2032.00"),
            high=Decimal("2034.00"),
            low=Decimal("2031.00"),
            close=Decimal("2033.00"),
            volume=Decimal("0"),  # Explicit zero volume!
            price_type=PriceType.BID,
            spread=Decimal("0.20"),  # Explicit spread!
            is_closed=True,
        )

        await repo.save_candles([candle_unknown_vol, candle_zero_vol])

        res = await repo.get_candles(
            source_id=DataSourceId("feed-nulls"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=2),
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )

        assert len(res) == 2
        # Bar 1: volume and spread are None
        assert res[0].volume is None
        assert res[0].spread is None

        # Bar 2: volume is Decimal(0), spread is Decimal("0.20")
        assert res[1].volume == Decimal("0")
        assert res[1].spread == Decimal("0.20")

    @pytest.mark.asyncio
    async def test_transaction_rollback(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify that an exception inside a transactional session rolls back cleanly."""
        t_open = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)

        repo = PostgresCandleRepository(test_session_factory)

        # Simulate exception during custom transaction
        with pytest.raises(RuntimeError):
            async with session_scope(test_session_factory) as session:
                await session.execute(
                    text(
                        "INSERT INTO candles (instrument_id, source_id, timeframe, open_time, close_time, price_type, "
                        "revision, open, high, low, close, availability_confidence) "
                        "VALUES ('XAUUSD', 'feed-rollback', 3600, '2024-01-15 10:00:00+00', '2024-01-15 11:00:00+00', "
                        "'BID', 0, 2030, 2035, 2025, 2032, 'UNKNOWN')"
                    )
                )
                raise RuntimeError("Simulated failure")

        # Verify nothing was committed
        res = await repo.get_candles(
            source_id=DataSourceId("feed-rollback"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(res) == 0

    def test_fail_fast_on_forbidden_database(self) -> None:
        """Verify targeting an unauthorized database immediately fails fast."""
        with pytest.raises(ValueError) as exc_info:
            DatabaseSettings(
                name="trader_muhendis",
            )
        assert "strictly prohibited" in str(exc_info.value).lower()

        # Also verify create_db_engine check when bypassing pydantic validator
        fake_settings = DatabaseSettings()
        object.__setattr__(fake_settings, "name", "trader_muhendis")
        with pytest.raises(ConfigurationError) as exc_info2:
            create_db_engine(fake_settings)
        assert "not in permitted" in str(exc_info2.value).lower()

    @pytest.mark.asyncio
    async def test_concurrent_revisions_no_silent_loss(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify concurrent async sessions saving distinct corrections never suffer silent data loss.

        Two concurrent tasks attempt to append different price revisions for the same bar.
        PostgreSQL transactional advisory lock ensures both serialize cleanly into rev 1 and rev 2.
        """
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 6, 1, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 6, 1, 11, 0, 0, tzinfo=UTC)

        # 1. Base revision 0
        base_candle = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-race"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2300.00"),
            high=Decimal("2310.00"),
            low=Decimal("2295.00"),
            close=Decimal("2305.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
        )
        saved_base = await repo.save_candles([base_candle])
        assert saved_base == 1

        # 2. Prepare two conflicting corrections
        correction_a = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-race"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2300.00"),
            high=Decimal("2315.00"),
            low=Decimal("2295.00"),
            close=Decimal("2312.00"),  # Correction A
            volume=Decimal("120"),
            price_type=PriceType.BID,
            is_closed=True,
        )

        correction_b = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-race"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2300.00"),
            high=Decimal("2318.00"),
            low=Decimal("2295.00"),
            close=Decimal("2316.00"),  # Correction B
            volume=Decimal("130"),
            price_type=PriceType.BID,
            is_closed=True,
        )

        # 3. Execute concurrently
        async def worker_save(candle: Candle) -> int:
            return await repo.save_candles([candle])

        results = await asyncio.gather(
            worker_save(correction_a),
            worker_save(correction_b),
        )

        # Both workers must succeed in persisting their new revision
        assert results == [1, 1]

        # 4. Verify all 3 revisions exist in database (rev 0, rev 1, rev 2)
        async with session_scope(test_session_factory) as session:
            rows = await session.execute(
                text(
                    "SELECT revision, close FROM candles "
                    "WHERE source_id = 'feed-race' AND open_time = '2024-06-01 10:00:00+00' "
                    "ORDER BY revision ASC"
                )
            )
            saved_revisions = [(r[0], Decimal(str(r[1]))) for r in rows.all()]

        assert len(saved_revisions) == 3
        assert [r[0] for r in saved_revisions] == [0, 1, 2]
        closes = {r[1] for r in saved_revisions}
        assert Decimal("2305.00") in closes
        assert Decimal("2312.00") in closes
        assert Decimal("2316.00") in closes

        # 5. Query latest revision returns the highest revision (rev 2)
        latest_candles = await repo.get_candles(
            source_id=DataSourceId("feed-race"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(latest_candles) == 1
        assert latest_candles[0].revision == 2

    @pytest.mark.asyncio
    async def test_repeated_identical_data_does_not_create_redundant_revision(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify re-ingesting the exact same candle data is idempotent and creates NO redundant revisions."""
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 7, 1, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 7, 1, 11, 0, 0, tzinfo=UTC)

        base_candle = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-idemp"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2350.00"),
            high=Decimal("2355.00"),
            low=Decimal("2345.00"),
            close=Decimal("2352.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
        )

        # 1. First save: revision 0
        saved_1 = await repo.save_candles([base_candle])
        assert saved_1 == 1

        # 2. Correction: revision 1
        corrected_candle = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-idemp"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2350.00"),
            high=Decimal("2358.00"),
            low=Decimal("2345.00"),
            close=Decimal("2356.00"),
            volume=Decimal("110"),
            price_type=PriceType.BID,
            is_closed=True,
        )
        saved_2 = await repo.save_candles([corrected_candle])
        assert saved_2 == 1

        # 3. Re-save identical corrected candle again (should be idempotent no-op)
        identical_again = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-idemp"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2350.00"),
            high=Decimal("2358.00"),
            low=Decimal("2345.00"),
            close=Decimal("2356.00"),
            volume=Decimal("110"),
            price_type=PriceType.BID,
            is_closed=True,
        )
        saved_3 = await repo.save_candles([identical_again])
        assert saved_3 == 0  # No new row written

        # 4. Check total row count in DB: MUST remain exactly 2
        async with session_scope(test_session_factory) as session:
            rows = await session.execute(
                text(
                    "SELECT revision, close FROM candles "
                    "WHERE source_id = 'feed-idemp' AND open_time = '2024-07-01 10:00:00+00' "
                    "ORDER BY revision ASC"
                )
            )
            saved_rows = rows.all()

        assert len(saved_rows) == 2
        assert [r[0] for r in saved_rows] == [0, 1]

    @pytest.mark.asyncio
    async def test_instrument_and_source_isolation_and_distinct_on(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify strict instrument isolation: DataSource cannot be shared across instruments,

        and DISTINCT ON includes instrument_id to prevent cross-instrument clobbering.
        """
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 8, 1, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 8, 1, 11, 0, 0, tzinfo=UTC)

        c_xau = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-xau-unique"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2400.00"),
            high=Decimal("2410.00"),
            low=Decimal("2390.00"),
            close=Decimal("2405.00"),
            volume=Decimal("200"),
            price_type=PriceType.BID,
            is_closed=True,
        )
        await repo.save_candles([c_xau])

        # Attempt to save EURUSD candle using the same source_id: must raise ValueError
        c_eur_invalid = Candle(
            instrument_id=InstrumentId("EURUSD"),
            source_id=DataSourceId("feed-xau-unique"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("1.0900"),
            high=Decimal("1.0950"),
            low=Decimal("1.0850"),
            close=Decimal("1.0920"),
            volume=Decimal("300"),
            price_type=PriceType.BID,
            is_closed=True,
        )
        with pytest.raises(ValueError) as exc_info:
            await repo.save_candles([c_eur_invalid])
        assert "already bound to instrument 'XAUUSD'" in str(exc_info.value)

        # Batch with mixed instruments for same source_id must also be rejected
        c_batch_1 = Candle(
            instrument_id=InstrumentId("USDJPY"),
            source_id=DataSourceId("feed-shared-batch"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("150.00"),
            high=Decimal("151.00"),
            low=Decimal("149.00"),
            close=Decimal("150.50"),
            volume=Decimal("50"),
            price_type=PriceType.BID,
            is_closed=True,
        )
        c_batch_2 = Candle(
            instrument_id=InstrumentId("GBPUSD"),
            source_id=DataSourceId("feed-shared-batch"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("1.2700"),
            high=Decimal("1.2750"),
            low=Decimal("1.2650"),
            close=Decimal("1.2720"),
            volume=Decimal("80"),
            price_type=PriceType.BID,
            is_closed=True,
        )
        with pytest.raises(ValueError) as exc_batch:
            await repo.save_candles([c_batch_1, c_batch_2])
        assert "bound to multiple instruments in batch" in str(exc_batch.value)

        # Test instrument_id query filter
        c_eur = Candle(
            instrument_id=InstrumentId("EURUSD"),
            source_id=DataSourceId("feed-eur-unique"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("1.0850"),
            high=Decimal("1.0890"),
            low=Decimal("1.0820"),
            close=Decimal("1.0860"),
            volume=Decimal("500"),
            price_type=PriceType.BID,
            is_closed=True,
        )
        await repo.save_candles([c_eur])

        # Query XAUUSD specifically
        xau_res = await repo.get_candles(
            source_id=DataSourceId("feed-xau-unique"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            instrument_id=InstrumentId("XAUUSD"),
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(xau_res) == 1
        assert xau_res[0].instrument_id == InstrumentId("XAUUSD")

        # Querying XAUUSD with EURUSD instrument_id filter returns empty
        mismatch_res = await repo.get_candles(
            source_id=DataSourceId("feed-xau-unique"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            instrument_id=InstrumentId("EURUSD"),
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(mismatch_res) == 0

    @pytest.mark.asyncio
    async def test_bitemporal_pit_scenarios_comprehensive(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Test Scenarios 1-7 for bitemporal point-in-time and snapshot accuracy on PostgreSQL."""
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2024, 9, 1, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2024, 9, 1, 11, 0, 0, tzinfo=UTC)
        t_avail_v0 = datetime(2024, 9, 1, 11, 0, 5, tzinfo=UTC)
        t_avail_v1 = datetime(2024, 9, 1, 11, 0, 10, tzinfo=UTC)

        t_ingest_v0 = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        t_ingest_v1 = datetime(2026, 3, 2, 12, 0, 0, tzinfo=UTC)
        snapshot_t0 = datetime(2026, 3, 1, 18, 0, 0, tzinfo=UTC)
        snapshot_t1 = datetime(2026, 3, 2, 18, 0, 0, tzinfo=UTC)

        # 1. Save initial version (v0)
        c_v0 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-pit-7"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2500.00"),
            high=Decimal("2510.00"),
            low=Decimal("2490.00"),
            close=Decimal("2502.00"),
            volume=Decimal("150"),
            price_type=PriceType.BID,
            available_at=t_avail_v0,
            availability_confidence=AvailabilityConfidence.VERIFIED,
            ingested_at=t_ingest_v0,
            is_closed=True,
        )
        await repo.save_candles([c_v0])

        # 2. Save corrected version (v1)
        c_v1 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-pit-7"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2500.00"),
            high=Decimal("2515.00"),
            low=Decimal("2490.00"),
            close=Decimal("2508.00"),  # Corrected close
            volume=Decimal("160"),
            price_type=PriceType.BID,
            available_at=t_avail_v1,
            availability_confidence=AvailabilityConfidence.VERIFIED,
            ingested_at=t_ingest_v1,
            is_closed=True,
        )
        await repo.save_candles([c_v1])

        # 3. Query with old snapshot (t0): must return initial version (v0, revision=0, close=2502.00)
        res_old = await repo.get_candles(
            source_id=DataSourceId("feed-pit-7"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            as_of_utc=t_close + timedelta(minutes=10),
            knowledge_snapshot_time=snapshot_t0,
            policy=PitPolicy.STRICT_PIT,
        )
        assert len(res_old) == 1
        assert res_old[0].revision == 0
        assert res_old[0].close == Decimal("2502.00")

        # 4. Query with new snapshot (t1): must return corrected version (v1, revision=1, close=2508.00)
        res_new = await repo.get_candles(
            source_id=DataSourceId("feed-pit-7"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            as_of_utc=t_close + timedelta(minutes=10),
            knowledge_snapshot_time=snapshot_t1,
            policy=PitPolicy.STRICT_PIT,
        )
        assert len(res_new) == 1
        assert res_new[0].revision == 1
        assert res_new[0].close == Decimal("2508.00")

        # 5. STRICT_PIT rejects unverified availability confidence
        c_unverified = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-pit-unverified"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2500.00"),
            high=Decimal("2510.00"),
            low=Decimal("2490.00"),
            close=Decimal("2505.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            available_at=t_avail_v0,
            availability_confidence=AvailabilityConfidence.ESTIMATED,  # Not VERIFIED
            is_closed=True,
        )
        await repo.save_candles([c_unverified])

        strict_res = await repo.get_candles(
            source_id=DataSourceId("feed-pit-unverified"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            as_of_utc=t_close + timedelta(minutes=10),
            policy=PitPolicy.STRICT_PIT,
        )
        assert len(strict_res) == 0  # Rejected by STRICT_PIT

        # 6. RESEARCH_ESTIMATED permits historical backfill candles without explicit available_at
        c_backfill = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-pit-backfill"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2500.00"),
            high=Decimal("2510.00"),
            low=Decimal("2490.00"),
            close=Decimal("2505.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            available_at=None,  # Historical backfill
            availability_confidence=AvailabilityConfidence.UNKNOWN,
            is_closed=True,
        )
        await repo.save_candles([c_backfill])

        res_est = await repo.get_candles(
            source_id=DataSourceId("feed-pit-backfill"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            as_of_utc=t_close + timedelta(minutes=10),
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(res_est) == 1
        assert res_est[0].close == Decimal("2505.00")

        # 7. Unclosed candle at decision time must NEVER be returned
        res_unclosed = await repo.get_candles(
            source_id=DataSourceId("feed-pit-7"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            as_of_utc=t_open + timedelta(minutes=30),  # Decision at 10:30, but bar closes at 11:00
            policy=PitPolicy.STRICT_PIT,
        )
        assert len(res_unclosed) == 0

    @pytest.mark.asyncio
    async def test_concurrent_batch_deadlock_prevention(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify concurrent batch writes in opposite order do not deadlock due to deterministic lock sorting."""
        repo = PostgresCandleRepository(test_session_factory)
        t_a = datetime(2024, 10, 1, 10, 0, 0, tzinfo=UTC)
        t_b = datetime(2024, 10, 1, 11, 0, 0, tzinfo=UTC)

        bar_a1 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-deadlock"),
            timeframe=Timeframe.H1,
            open_time=t_a,
            close_time=t_a + timedelta(hours=1),
            open=Decimal("2600.00"),
            high=Decimal("2605.00"),
            low=Decimal("2595.00"),
            close=Decimal("2602.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
        )
        bar_b1 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-deadlock"),
            timeframe=Timeframe.H1,
            open_time=t_b,
            close_time=t_b + timedelta(hours=1),
            open=Decimal("2602.00"),
            high=Decimal("2608.00"),
            low=Decimal("2598.00"),
            close=Decimal("2604.00"),
            volume=Decimal("120"),
            price_type=PriceType.BID,
            is_closed=True,
        )

        bar_a2 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-deadlock"),
            timeframe=Timeframe.H1,
            open_time=t_a,
            close_time=t_a + timedelta(hours=1),
            open=Decimal("2600.00"),
            high=Decimal("2606.00"),
            low=Decimal("2595.00"),
            close=Decimal("2603.00"),
            volume=Decimal("105"),
            price_type=PriceType.BID,
            is_closed=True,
        )
        bar_b2 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-deadlock"),
            timeframe=Timeframe.H1,
            open_time=t_b,
            close_time=t_b + timedelta(hours=1),
            open=Decimal("2602.00"),
            high=Decimal("2609.00"),
            low=Decimal("2598.00"),
            close=Decimal("2605.00"),
            volume=Decimal("125"),
            price_type=PriceType.BID,
            is_closed=True,
        )

        # Worker 1 saves [bar_a1, bar_b1]
        # Worker 2 saves [bar_b2, bar_a2] in REVERSE order
        async def task_1() -> int:
            return await repo.save_candles([bar_a1, bar_b1])

        async def task_2() -> int:
            return await repo.save_candles([bar_b2, bar_a2])

        res1, res2 = await asyncio.gather(task_1(), task_2())
        assert res1 == 2
        assert res2 == 2

    @pytest.mark.asyncio
    async def test_cross_instrument_concurrent_source_atomic_isolation(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify concurrent sessions saving different instruments with the same source_id serialize atomically.

        DataSource represents an instrument-specific feed. When two workers attempt to register
        the same source_id for XAUUSD and EURUSD simultaneously, transactional advisory locking
        guarantees one succeeds and the other is rejected with ValueError. No corrupt cross-mapping occurs.
        """
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2025, 4, 1, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2025, 4, 1, 11, 0, 0, tzinfo=UTC)

        c_xau = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-cross-atomic-test"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2000.00"),
            high=Decimal("2010.00"),
            low=Decimal("1990.00"),
            close=Decimal("2005.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
        )

        c_eur = Candle(
            instrument_id=InstrumentId("EURUSD"),
            source_id=DataSourceId("feed-cross-atomic-test"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("1.0800"),
            high=Decimal("1.0900"),
            low=Decimal("1.0700"),
            close=Decimal("1.0850"),
            volume=Decimal("200"),
            price_type=PriceType.BID,
            is_closed=True,
        )

        async def worker(candle: Candle) -> str:
            try:
                cnt = await repo.save_candles([candle])
                return f"SUCCESS:{cnt}"
            except ValueError as e:
                return f"ERROR:{e}"

        res_xau, res_eur = await asyncio.gather(worker(c_xau), worker(c_eur))
        results = [res_xau, res_eur]

        # Exactly one worker must succeed, and the other must be rejected with ValueError
        success_count = sum(1 for r in results if r.startswith("SUCCESS"))
        error_count = sum(
            1 for r in results if r.startswith("ERROR") and "already bound to instrument" in r
        )
        assert success_count == 1
        assert error_count == 1

        # Verify in database: exactly ONE instrument exists for this source_id
        async with session_scope(test_session_factory) as session:
            rows = await session.execute(
                text(
                    "SELECT DISTINCT instrument_id FROM candles WHERE source_id = 'feed-cross-atomic-test'"
                )
            )
            saved_instruments = [r[0] for r in rows.all()]

        assert len(saved_instruments) == 1
        assert saved_instruments[0] in ("XAUUSD", "EURUSD")

    @pytest.mark.asyncio
    async def test_metadata_aware_idempotency_transitions(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify that changes to metadata fields (is_closed, confidence, available_at, tick_volume, spread, is_synthetic)

        reliably bump revisions without losing updates, while identical retries remain idempotent no-ops.
        """
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2025, 5, 1, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2025, 5, 1, 11, 0, 0, tzinfo=UTC)

        # 1. Base open candle (is_closed=False) -> rev 0
        c0 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-meta-test"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2300.00"),
            high=Decimal("2310.00"),
            low=Decimal("2290.00"),
            close=Decimal("2305.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=False,
            availability_confidence=AvailabilityConfidence.ESTIMATED,
        )
        s0 = await repo.save_candles([c0])
        assert s0 == 1

        # 2. Bar completes and closes (is_closed: False -> True) -> rev 1
        c1 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-meta-test"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2300.00"),
            high=Decimal("2310.00"),
            low=Decimal("2290.00"),
            close=Decimal("2305.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
            availability_confidence=AvailabilityConfidence.ESTIMATED,
        )
        s1 = await repo.save_candles([c1])
        assert s1 == 1

        # 3. Availability confidence updated (ESTIMATED -> VERIFIED) -> rev 2
        c2 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-meta-test"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2300.00"),
            high=Decimal("2310.00"),
            low=Decimal("2290.00"),
            close=Decimal("2305.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
            availability_confidence=AvailabilityConfidence.VERIFIED,
        )
        s2 = await repo.save_candles([c2])
        assert s2 == 1

        # 4. available_at timestamp assigned -> rev 3
        t_avail = t_close + timedelta(seconds=5)
        c3 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-meta-test"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2300.00"),
            high=Decimal("2310.00"),
            low=Decimal("2290.00"),
            close=Decimal("2305.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
            availability_confidence=AvailabilityConfidence.VERIFIED,
            available_at=t_avail,
        )
        s3 = await repo.save_candles([c3])
        assert s3 == 1

        # 5. tick_volume microstructure updated -> rev 4
        c4 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-meta-test"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2300.00"),
            high=Decimal("2310.00"),
            low=Decimal("2290.00"),
            close=Decimal("2305.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
            availability_confidence=AvailabilityConfidence.VERIFIED,
            available_at=t_avail,
            tick_volume=Decimal("150"),
        )
        s4 = await repo.save_candles([c4])
        assert s4 == 1

        # 6. spread updated -> rev 5
        c5 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-meta-test"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2300.00"),
            high=Decimal("2310.00"),
            low=Decimal("2290.00"),
            close=Decimal("2305.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
            availability_confidence=AvailabilityConfidence.VERIFIED,
            available_at=t_avail,
            tick_volume=Decimal("150"),
            spread=Decimal("0.25"),
        )
        s5 = await repo.save_candles([c5])
        assert s5 == 1

        # 7. is_synthetic updated -> rev 6
        c6 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-meta-test"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2300.00"),
            high=Decimal("2310.00"),
            low=Decimal("2290.00"),
            close=Decimal("2305.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
            availability_confidence=AvailabilityConfidence.VERIFIED,
            available_at=t_avail,
            tick_volume=Decimal("150"),
            spread=Decimal("0.25"),
            is_synthetic=True,
        )
        s6 = await repo.save_candles([c6])
        assert s6 == 1

        # 8. Identical retry (all 11 domain fields match): MUST be idempotent no-op (0 new rows)
        c7 = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-meta-test"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2300.00"),
            high=Decimal("2310.00"),
            low=Decimal("2290.00"),
            close=Decimal("2305.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
            availability_confidence=AvailabilityConfidence.VERIFIED,
            available_at=t_avail,
            tick_volume=Decimal("150"),
            spread=Decimal("0.25"),
            is_synthetic=True,
            ingested_at=datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC),  # Technical field changed
        )
        s7 = await repo.save_candles([c7])
        assert s7 == 0

        # Verify total rows and revisions in database
        async with session_scope(test_session_factory) as session:
            rows = await session.execute(
                text(
                    "SELECT revision, is_closed, availability_confidence FROM candles WHERE source_id = 'feed-meta-test' ORDER BY revision ASC"
                )
            )
            saved_rows = rows.all()

        assert len(saved_rows) == 7
        assert [r[0] for r in saved_rows] == [0, 1, 2, 3, 4, 5, 6]

    @pytest.mark.asyncio
    async def test_in_flight_commit_visibility_lag(
        self, test_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Verify empirical commit visibility lag: clock_timestamp() reflects statement execution, not commit time.

        Demonstrates that uncommitted rows are invisible to concurrent MVCC readers during in-flight period,
        but retrospective queries executed after commit will see rows within that window.
        """
        repo = PostgresCandleRepository(test_session_factory)
        t_open = datetime(2025, 6, 1, 10, 0, 0, tzinfo=UTC)
        t_close = datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC)

        c = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("feed-inflight-vis"),
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2350.00"),
            high=Decimal("2360.00"),
            low=Decimal("2340.00"),
            close=Decimal("2355.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
        )

        # 1. Session 1: Insert candle and flush to obtain database clock_timestamp() without committing
        session1 = test_session_factory()
        model = CandleModel.from_domain(c)
        session1.add(model)
        await session1.flush()

        t_statement_executed = model.ingested_at
        assert t_statement_executed is not None

        # Snapshot timestamp chosen 50ms after statement execution
        snapshot_time = t_statement_executed + timedelta(milliseconds=50)

        # 2. Session 2: Read while Session 1 is UNCOMMITTED -> MVCC isolation blocks visibility
        res_uncommitted = await repo.get_candles(
            source_id=DataSourceId("feed-inflight-vis"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            knowledge_snapshot_time=snapshot_time,
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(res_uncommitted) == 0

        # 3. Session 1 commits
        await asyncio.sleep(0.1)
        await session1.commit()
        await session1.close()

        # 4. Session 3: Read after commit with the EXACT SAME snapshot timestamp -> Row is now visible!
        res_committed = await repo.get_candles(
            source_id=DataSourceId("feed-inflight-vis"),
            timeframe=Timeframe.H1,
            start_utc=t_open,
            end_utc=t_close + timedelta(hours=1),
            knowledge_snapshot_time=snapshot_time,
            policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        assert len(res_committed) == 1
        assert res_committed[0].close == Decimal("2355.00")
