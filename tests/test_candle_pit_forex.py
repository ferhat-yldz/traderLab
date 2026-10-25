"""Comprehensive test suite for Forex Price Model, Point-in-Time, and Bitemporal Candle semantics.

Covers all FAZ 0.5A requirements:
    - GÖREV A1: BID, ASK, MID, UNKNOWN price types, explicit typing, range_midpoint vs true mid, spread handling, synthetic flagging
    - GÖREV A2: Point-in-Time timestamps (open, close, published, received, available, ingested),
      AvailabilityConfidence levels, is_available_at usability, revision tracking
    - GÖREV A3: OHLC consistency, price positivity vs allow_negative_prices, UTC normalization, naive datetime rejection
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from traderlab.core.errors import InvalidCandleError
from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle


# ── Helper Fixtures ──────────────────────────────────────────────────────────
@pytest.fixture
def xau_id() -> InstrumentId:
    return InstrumentId("XAUUSD")


@pytest.fixture
def source_id() -> DataSourceId:
    return DataSourceId("oanda-live")


# ── GÖREV A1: Price Type & Forex Market Semantics ───────────────────────────
class TestPriceTypeSemantics:
    def test_explicit_price_type_required(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """Verify that price_type is mandatory and cannot be omitted (no silent default)."""
        with pytest.raises(TypeError):
            Candle(  # type: ignore[call-arg]
                instrument_id=xau_id,
                source_id=source_id,
                timeframe=Timeframe.M15,
                open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                close_time=datetime(2024, 1, 15, 10, 15, tzinfo=UTC),
                open=Decimal("2030.00"),
                high=Decimal("2035.00"),
                low=Decimal("2029.00"),
                close=Decimal("2034.00"),
                volume=Decimal("500"),
                # price_type missing!
            )

    def test_invalid_price_type_type_rejected(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """Passing a raw string instead of a PriceType enum must be rejected."""
        with pytest.raises(InvalidCandleError, match="PriceType enum"):
            Candle(
                instrument_id=xau_id,
                source_id=source_id,
                timeframe=Timeframe.M15,
                open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                close_time=datetime(2024, 1, 15, 10, 15, tzinfo=UTC),
                open=Decimal("2030.00"),
                high=Decimal("2035.00"),
                low=Decimal("2029.00"),
                close=Decimal("2034.00"),
                volume=Decimal("500"),
                price_type="BID",  # type: ignore[arg-type]  # string instead of enum!
            )

    def test_all_price_types_accepted(self, xau_id: InstrumentId, source_id: DataSourceId) -> None:
        for pt in PriceType:
            c = Candle(
                instrument_id=xau_id,
                source_id=source_id,
                timeframe=Timeframe.M15,
                open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                close_time=datetime(2024, 1, 15, 10, 15, tzinfo=UTC),
                open=Decimal("2030.00"),
                high=Decimal("2035.00"),
                low=Decimal("2029.00"),
                close=Decimal("2034.00"),
                volume=Decimal("500"),
                price_type=pt,
            )
            assert c.price_type == pt

    def test_simultaneous_bid_and_ask_representation(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """Verify representing distinct BID and ASK candles for the same instrument and bar."""
        t_open = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 10, 15, tzinfo=UTC)

        bid_candle = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.M15,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2029.00"),
            close=Decimal("2034.00"),
            volume=Decimal("500"),
            price_type=PriceType.BID,
        )

        ask_candle = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.M15,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.30"),
            high=Decimal("2035.30"),
            low=Decimal("2029.30"),
            close=Decimal("2034.30"),
            volume=Decimal("500"),
            price_type=PriceType.ASK,
        )

        assert bid_candle.price_type == PriceType.BID
        assert ask_candle.price_type == PriceType.ASK
        # Mathematical verification: true market spread and true mid at close
        spread_at_close = ask_candle.close - bid_candle.close
        true_mid_close = (ask_candle.close + bid_candle.close) / 2
        assert spread_at_close == Decimal("0.30")
        assert true_mid_close == Decimal("2034.15")

    def test_range_midpoint_is_not_market_midpoint(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """Verify range_midpoint is (high + low)/2 and distinct from true market midpoint."""
        c = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
            open=Decimal("2030.00"),
            high=Decimal("2040.00"),
            low=Decimal("2020.00"),
            close=Decimal("2035.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
        )
        assert c.range_midpoint == Decimal("2030.00")
        assert c.mid_price == Decimal("2030.00")

    def test_spread_semantics_and_missing_spread(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """When spread is unknown, it must be None, NOT zero."""
        c_no_spread = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            spread=None,
        )
        assert c_no_spread.spread is None
        assert c_no_spread.spread != Decimal("0")

        c_with_spread = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            spread=Decimal("0.25"),
        )
        assert c_with_spread.spread == Decimal("0.25")

    def test_negative_spread_rejected(self, xau_id: InstrumentId, source_id: DataSourceId) -> None:
        with pytest.raises(InvalidCandleError, match="spread cannot be negative"):
            Candle(
                instrument_id=xau_id,
                source_id=source_id,
                timeframe=Timeframe.H1,
                open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
                open=Decimal("2030.00"),
                high=Decimal("2035.00"),
                low=Decimal("2028.00"),
                close=Decimal("2033.00"),
                volume=Decimal("100"),
                price_type=PriceType.BID,
                spread=Decimal("-0.10"),
            )

    def test_synthetic_data_flagging(self, xau_id: InstrumentId, source_id: DataSourceId) -> None:
        c = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.ASK,
            is_synthetic=True,
        )
        assert c.is_synthetic is True

    def test_price_type_unknown_can_be_stored_but_fails_trading_validation(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """UNKNOWN price series can be stored as raw feed, but must be rejected for trading/execution."""
        c = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.UNKNOWN,
        )
        assert c.price_type == PriceType.UNKNOWN
        assert c.is_tradable_price is False
        with pytest.raises(InvalidCandleError, match=r"PriceType\.UNKNOWN"):
            c.validate_for_trading()

        # Contrasting with explicit tradable BID candle:
        c_bid = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
        )
        assert c_bid.is_tradable_price is True
        c_bid.validate_for_trading()  # does not raise


# ── GÖREV A2: Point-in-Time & Bitemporal Tracking ────────────────────────────
class TestPointInTimeAndBitemporal:
    def test_all_six_timestamps_supported(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        t_open = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)
        t_pub = datetime(2024, 1, 15, 11, 0, 1, tzinfo=UTC)
        t_rec = datetime(2024, 1, 15, 11, 0, 2, tzinfo=UTC)
        t_avail = datetime(2024, 1, 15, 11, 0, 3, tzinfo=UTC)
        t_ingest = datetime(2024, 1, 15, 11, 0, 4, tzinfo=UTC)

        c = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            published_at=t_pub,
            received_at=t_rec,
            available_at=t_avail,
            ingested_at=t_ingest,
            availability_confidence=AvailabilityConfidence.VERIFIED,
        )

        assert c.open_time == t_open
        assert c.close_time == t_close
        assert c.published_at == t_pub
        assert c.received_at == t_rec
        assert c.available_at == t_avail
        assert c.ingested_at == t_ingest
        assert c.availability_confidence == AvailabilityConfidence.VERIFIED

    def test_default_ingested_at_is_none_for_in_memory_domain_objects(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """Domain objects have ingested_at=None until stamped by persistence layer."""
        c = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
        )
        assert c.ingested_at is None
        assert c.published_at is None
        assert c.received_at is None
        assert c.available_at is None
        assert c.availability_confidence == AvailabilityConfidence.UNKNOWN

        # When stamped upon database commit:
        c_persisted = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            ingested_at=datetime(2024, 1, 15, 11, 0, 5, tzinfo=UTC),
        )
        assert c_persisted.ingested_at is not None
        assert c_persisted.ingested_at.tzinfo == UTC

    def test_naive_timestamp_in_bitemporal_fields_rejected(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        with pytest.raises(InvalidCandleError, match="published_at invalid"):
            Candle(
                instrument_id=xau_id,
                source_id=source_id,
                timeframe=Timeframe.H1,
                open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
                open=Decimal("2030.00"),
                high=Decimal("2035.00"),
                low=Decimal("2028.00"),
                close=Decimal("2033.00"),
                volume=Decimal("100"),
                price_type=PriceType.BID,
                published_at=datetime(2024, 1, 15, 11, 0, 1),  # naive!
            )

    def test_unclosed_candle_cannot_be_used_for_closed_analysis(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """Incomplete / unclosed candles must never be available for closed-bar analysis."""
        c = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=False,  # Still forming!
        )
        # Even query time far in the future must reject an unclosed candle under any policy
        future_time = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
        assert c.is_available_at(future_time, policy=PitPolicy.STRICT_PIT) is False
        assert c.is_available_at(future_time, policy=PitPolicy.RESEARCH_ESTIMATED) is False

    def test_is_available_at_with_verified_available_time(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        t_open = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)
        t_avail = datetime(2024, 1, 15, 11, 0, 5, tzinfo=UTC)  # 5s network lag

        c = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            available_at=t_avail,
            availability_confidence=AvailabilityConfidence.VERIFIED,
            is_closed=True,
        )

        # Before available_at: unavailable under STRICT_PIT
        assert c.is_available_at(datetime(2024, 1, 15, 11, 0, 4, tzinfo=UTC)) is False
        # At available_at: available
        assert c.is_available_at(datetime(2024, 1, 15, 11, 0, 5, tzinfo=UTC)) is True
        # After available_at: available
        assert c.is_available_at(datetime(2024, 1, 15, 11, 1, 0, tzinfo=UTC)) is True

    def test_is_available_at_historical_backfill_without_available_at(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """Unverified backfills are rejected under STRICT_PIT and allowed under RESEARCH_ESTIMATED."""
        t_open = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)

        c = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            available_at=None,
            availability_confidence=AvailabilityConfidence.UNKNOWN,
            is_closed=True,
        )

        # Under default STRICT_PIT policy: rejected because availability timestamp is unverified!
        assert c.is_available_at(t_close, policy=PitPolicy.STRICT_PIT) is False
        assert c.is_available_at(t_close) is False  # default is STRICT_PIT

        # Under RESEARCH_ESTIMATED policy: allowed once bar close_time has arrived
        assert (
            c.is_available_at(
                datetime(2024, 1, 15, 10, 59, 59, tzinfo=UTC),
                policy=PitPolicy.RESEARCH_ESTIMATED,
            )
            is False
        )
        assert c.is_available_at(t_close, policy=PitPolicy.RESEARCH_ESTIMATED) is True

    def test_unverified_confidence_rejected_under_strict_pit(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """ESTIMATED or UNKNOWN confidence bars cannot masquerade as VERIFIED under STRICT_PIT."""
        t_close = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)
        t_avail = datetime(2024, 1, 15, 11, 0, 5, tzinfo=UTC)

        c_estimated = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            available_at=t_avail,
            availability_confidence=AvailabilityConfidence.ESTIMATED,  # not verified!
        )
        assert (
            c_estimated.is_available_at(
                datetime(2024, 1, 15, 11, 1, 0, tzinfo=UTC),
                policy=PitPolicy.STRICT_PIT,
            )
            is False
        )
        assert (
            c_estimated.is_available_at(
                datetime(2024, 1, 15, 11, 1, 0, tzinfo=UTC),
                policy=PitPolicy.RESEARCH_ESTIMATED,
            )
            is True
        )

    def test_revision_ingested_after_as_of_rejected_to_prevent_leakage(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """A bar or revision committed to DB at 15:00 was not knowable to the live system at 12:00."""
        t_open = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)
        t_avail = datetime(2024, 1, 15, 11, 0, 5, tzinfo=UTC)
        t_ingest = datetime(2024, 1, 15, 15, 0, 0, tzinfo=UTC)  # ingested at 15:00

        c = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            available_at=t_avail,
            ingested_at=t_ingest,
            availability_confidence=AvailabilityConfidence.VERIFIED,
            revision=1,
        )

        # Decision query at 12:00: live system did not have this record yet!
        decision_at_12 = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        # In STRICT_PIT, default knowledge_snapshot_time is as_of (live system invariant)
        assert c.is_available_at(decision_at_12, policy=PitPolicy.STRICT_PIT) is False

        # In RESEARCH_ESTIMATED with snapshot constrained to 12:00, revision is also rejected
        assert (
            c.is_available_at(
                decision_at_12,
                policy=PitPolicy.RESEARCH_ESTIMATED,
                knowledge_snapshot_time=decision_at_12,
            )
            is False
        )

        # In RESEARCH_ESTIMATED without snapshot limit (historical backfill), it is accessible
        assert c.is_available_at(decision_at_12, policy=PitPolicy.RESEARCH_ESTIMATED) is True

        # Decision query after ingestion (15:30): record was present in live system
        decision_at_15_30 = datetime(2024, 1, 15, 15, 30, 0, tzinfo=UTC)
        assert c.is_available_at(decision_at_15_30, policy=PitPolicy.STRICT_PIT) is True

    def test_historical_backfill_research_estimated_allowed(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """Historical backfill downloaded in 2026 must be usable for 2024 backtest in RESEARCH_ESTIMATED."""
        t_open = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)
        # Downloaded years later, so ingested_at is 2026
        t_ingest_later = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)

        c = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            available_at=None,  # Not verified at live arrival
            ingested_at=t_ingest_later,
            availability_confidence=AvailabilityConfidence.ESTIMATED,
        )

        decision_time = datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC)
        # STRICT_PIT rejects because confidence is ESTIMATED and ingested_at > decision_time
        assert c.is_available_at(decision_time, policy=PitPolicy.STRICT_PIT) is False

        # RESEARCH_ESTIMATED allows it because close_time <= decision_time
        assert c.is_available_at(decision_time, policy=PitPolicy.RESEARCH_ESTIMATED) is True

    def test_bitemporal_snapshot_revision_filtering(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """Older research snapshot must reject subsequent data revisions."""
        t_open = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)

        # Revision 0 was ingested on 2026-01-01
        rev0 = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            ingested_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
            revision=0,
        )
        # Revision 1 (correction) was ingested on 2026-02-01
        rev1 = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2036.00"),  # corrected high
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            ingested_at=datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC),
            revision=1,
        )

        decision_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
        snapshot_jan = datetime(2026, 1, 15, 0, 0, 0, tzinfo=UTC)

        # In January snapshot: rev0 is valid, rev1 did not exist yet!
        assert (
            rev0.is_available_at(
                decision_time,
                policy=PitPolicy.RESEARCH_ESTIMATED,
                knowledge_snapshot_time=snapshot_jan,
            )
            is True
        )
        assert (
            rev1.is_available_at(
                decision_time,
                policy=PitPolicy.RESEARCH_ESTIMATED,
                knowledge_snapshot_time=snapshot_jan,
            )
            is False
        )

    def test_revision_tracking(self, xau_id: InstrumentId, source_id: DataSourceId) -> None:
        original = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            revision=0,
        )
        corrected = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
            open=Decimal("2030.00"),
            high=Decimal("2036.00"),  # corrected high
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            revision=1,
        )
        assert original.revision == 0
        assert corrected.revision == 1
        assert corrected.high == Decimal("2036.00")

    def test_negative_revision_rejected(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        with pytest.raises(InvalidCandleError, match="revision cannot be negative"):
            Candle(
                instrument_id=xau_id,
                source_id=source_id,
                timeframe=Timeframe.H1,
                open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
                open=Decimal("2030.00"),
                high=Decimal("2035.00"),
                low=Decimal("2028.00"),
                close=Decimal("2033.00"),
                volume=Decimal("100"),
                price_type=PriceType.BID,
                revision=-1,
            )

    def test_unclosed_higher_timeframe_candle_rejected_at_lower_timeframe_decision(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """An H1 candle (10:00-11:00) must be rejected for an M5 decision at 10:15 under both policies."""
        t_open = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        t_close = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)

        # 1. Forming/streaming candle with is_closed=False
        forming_h1 = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=False,
        )
        decision_at_10_15 = datetime(2024, 1, 15, 10, 15, tzinfo=UTC)
        assert forming_h1.is_available_at(decision_at_10_15, policy=PitPolicy.STRICT_PIT) is False
        assert (
            forming_h1.is_available_at(decision_at_10_15, policy=PitPolicy.RESEARCH_ESTIMATED)
            is False
        )

        # 2. Even if marked is_closed=True in a backfill dataset, close_time > 10:15
        closed_h1 = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            available_at=datetime(2024, 1, 15, 11, 0, 5, tzinfo=UTC),
            availability_confidence=AvailabilityConfidence.VERIFIED,
            is_closed=True,
        )
        assert closed_h1.is_available_at(decision_at_10_15, policy=PitPolicy.STRICT_PIT) is False
        assert (
            closed_h1.is_available_at(decision_at_10_15, policy=PitPolicy.RESEARCH_ESTIMATED)
            is False
        )

        # Accessible only when simulated time reaches or exceeds availability / close
        decision_at_11_01 = datetime(2024, 1, 15, 11, 1, tzinfo=UTC)
        assert closed_h1.is_available_at(decision_at_11_01, policy=PitPolicy.STRICT_PIT) is True
        assert (
            closed_h1.is_available_at(decision_at_11_01, policy=PitPolicy.RESEARCH_ESTIMATED)
            is True
        )


# ── GÖREV A3: Candle Validation & Normalization ─────────────────────────────
class TestCandleValidationAndNormalization:
    def test_utc_normalization_zoneinfo(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """ZoneInfo('UTC') must be cleanly accepted and normalized to datetime.UTC."""
        t_open = datetime(2024, 1, 15, 10, 0, tzinfo=ZoneInfo("UTC"))
        t_close = datetime(2024, 1, 15, 11, 0, tzinfo=ZoneInfo("UTC"))
        c = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
        )
        assert c.open_time.tzinfo == UTC
        assert c.close_time.tzinfo == UTC

    def test_utc_normalization_zero_offset_timezone(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """timezone(timedelta(0)) must be accepted and normalized to datetime.UTC."""
        tz_zero = timezone(timedelta(0))
        t_open = datetime(2024, 1, 15, 10, 0, tzinfo=tz_zero)
        t_close = datetime(2024, 1, 15, 11, 0, tzinfo=tz_zero)
        c = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t_open,
            close_time=t_close,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
        )
        assert c.open_time.tzinfo == UTC
        assert c.close_time.tzinfo == UTC

    def test_non_zero_utc_offset_rejected(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        tz_plus_2 = timezone(timedelta(hours=2))
        with pytest.raises(InvalidCandleError, match="must be in UTC timezone"):
            Candle(
                instrument_id=xau_id,
                source_id=source_id,
                timeframe=Timeframe.H1,
                open_time=datetime(2024, 1, 15, 10, 0, tzinfo=tz_plus_2),
                close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
                open=Decimal("2030.00"),
                high=Decimal("2035.00"),
                low=Decimal("2028.00"),
                close=Decimal("2033.00"),
                volume=Decimal("100"),
                price_type=PriceType.BID,
            )

    def test_zero_or_negative_price_rejected_for_standard_instrument(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        """XAUUSD, EURUSD and standard financial instruments must have strictly positive prices."""
        with pytest.raises(InvalidCandleError, match="strictly positive"):
            Candle(
                instrument_id=xau_id,
                source_id=source_id,
                timeframe=Timeframe.H1,
                open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
                open=Decimal("0.00"),  # zero price!
                high=Decimal("2035.00"),
                low=Decimal("0.00"),
                close=Decimal("2033.00"),
                volume=Decimal("100"),
                price_type=PriceType.BID,
                allow_negative_prices=False,
            )

        with pytest.raises(InvalidCandleError, match="strictly positive"):
            Candle(
                instrument_id=xau_id,
                source_id=source_id,
                timeframe=Timeframe.H1,
                open_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                close_time=datetime(2024, 1, 15, 11, 0, tzinfo=UTC),
                open=Decimal("-10.00"),  # negative price!
                high=Decimal("5.00"),
                low=Decimal("-15.00"),
                close=Decimal("-5.00"),
                volume=Decimal("100"),
                price_type=PriceType.BID,
                allow_negative_prices=False,
            )

    def test_negative_prices_allowed_when_explicitly_configured(
        self, source_id: DataSourceId
    ) -> None:
        """Commodities like WTI (April 2020) can have negative prices when explicitly enabled."""
        oil_id = InstrumentId("WTI_FUTURES")
        c = Candle(
            instrument_id=oil_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=datetime(2020, 4, 20, 14, 0, tzinfo=UTC),
            close_time=datetime(2020, 4, 20, 15, 0, tzinfo=UTC),
            open=Decimal("-10.00"),
            high=Decimal("-5.00"),
            low=Decimal("-40.00"),
            close=Decimal("-37.63"),
            volume=Decimal("10000"),
            price_type=PriceType.MID,
            allow_negative_prices=True,
        )
        assert c.open == Decimal("-10.00")
        assert c.close == Decimal("-37.63")
        assert c.low == Decimal("-40.00")
        assert c.range == Decimal("35.00")

    def test_equal_open_and_close_time_rejected(
        self, xau_id: InstrumentId, source_id: DataSourceId
    ) -> None:
        t = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        with pytest.raises(InvalidCandleError, match="close_time must be after open_time"):
            Candle(
                instrument_id=xau_id,
                source_id=source_id,
                timeframe=Timeframe.H1,
                open_time=t,
                close_time=t,  # equal!
                open=Decimal("2030.00"),
                high=Decimal("2035.00"),
                low=Decimal("2028.00"),
                close=Decimal("2033.00"),
                volume=Decimal("100"),
                price_type=PriceType.BID,
            )
