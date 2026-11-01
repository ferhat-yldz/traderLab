"""Acceptance test suite for FAZ 2B-K3: Session-Aware Data Quality & Minute-Level Integrity.

Validates:
1. Minute-level set-based completeness (missing open-session minute, duplicate minute, closed-market minute).
2. Proof that scalar counts cannot mask missing minutes.
3. Proof that New York D1 PARTIAL bars in Jan 2024 are strictly dataset archive boundary cutoffs.
4. Point-in-Time metadata inheritance (available_at, ingested_at, availability_confidence) and STRICT_PIT adherence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    WindowCompleteness,
    resample_m1_candles,
)
from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.infrastructure.calendars.forex_session import XAUUSDTradingCalendar


def make_m1(
    open_time: datetime,
    *,
    close_price: str = "2000.00",
    is_synthetic: bool = False,
    available_at: datetime | None = None,
    ingested_at: datetime | None = None,
    confidence: AvailabilityConfidence = AvailabilityConfidence.ESTIMATED,
) -> Candle:
    """Helper to create valid M1 Candle."""
    c_price = Decimal(close_price)
    return Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("test_source"),
        timeframe=Timeframe.M1,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=c_price,
        high=c_price + Decimal("0.50"),
        low=c_price - Decimal("0.50"),
        close=c_price,
        volume=Decimal("10"),
        price_type=PriceType.BID,
        available_at=available_at,
        ingested_at=ingested_at,
        availability_confidence=confidence,
        is_closed=True,
        is_synthetic=is_synthetic,
        revision=0,
    )


class TestMinuteLevelDataIntegrity:
    """Requirement 1: Minute-level verification using timestamp set comparison."""

    def test_missing_open_session_minute_masked_by_duplicate_fails_complete(self) -> None:
        """A missing open-session minute must NOT be masked by a duplicate bar elsewhere."""
        # 1-hour window on Tuesday (open market, 60 expected minutes: 12:00 to 13:00 UTC)
        base = datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
        bars: list[Candle] = []

        # Generate 59 distinct minutes (minute 15 is missing)
        for m in range(60):
            if m == 15:
                continue
            bars.append(make_m1(base + timedelta(minutes=m)))

        # Add a duplicate bar for minute 30 -> total bar count is 60!
        bars.append(make_m1(base + timedelta(minutes=30), close_price="2001.00"))

        assert len(bars) == 60  # Scalar count would say present == expected (60 == 60)

        res = resample_m1_candles(bars, Timeframe.H1)
        assert len(res) == 1
        rc = res[0]

        # Must be detected as PARTIAL due to missing minute 15
        assert rc.completeness == WindowCompleteness.PARTIAL
        assert rc.candle.is_closed is False
        assert rc.missing_trading_minutes == 1
        assert rc.duplicate_trading_minutes == 1
        assert rc.unexpected_trading_minutes == 0

    def test_missing_open_session_minute_masked_by_closed_market_bar_fails_complete(self) -> None:
        """A missing open-session minute must NOT be masked by a candle arriving during market closure."""
        # Rollover hour on Tuesday 22:00 to 23:00 UTC (expected_trading_mins = 0 in winter EST)
        # Next hour 23:00 to 00:00 UTC (expected_trading_mins = 60)
        # Resample H2 from 22:00 to 00:00 UTC:
        # Expected open minutes = 60 (23:00 to 00:00)
        # Closed market minutes = 60 (22:00 to 23:00)
        base = datetime(2024, 1, 16, 22, 0, tzinfo=UTC)
        bars: list[Candle] = []

        # 1 active bar in closed rollover period (unexpected active bar at 22:15)
        bars.append(make_m1(base + timedelta(minutes=15)))

        # 59 active bars in open period (23:00 to 00:00, with 23:45 missing)
        for m in range(60, 120):
            if m == 60 + 45:
                continue
            bars.append(make_m1(base + timedelta(minutes=m)))

        # Total active count is 1 + 59 = 60! Exactly equals expected 60 open minutes!
        assert len(bars) == 60

        res = resample_m1_candles(bars, Timeframe.H2)
        assert len(res) == 1
        rc = res[0]

        # Must NOT be marked COMPLETE: 23:45 was missing during open market!
        assert rc.completeness == WindowCompleteness.PARTIAL
        assert rc.candle.is_closed is False
        assert rc.missing_trading_minutes == 1
        assert rc.unexpected_trading_minutes == 1
        assert rc.duplicate_trading_minutes == 0

    def test_duplicate_bar_in_otherwise_complete_session_fails_complete(self) -> None:
        """A window with 60 expected minutes plus 1 duplicate bar (61 total) must be PARTIAL."""
        base = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
        bars = [make_m1(base + timedelta(minutes=m)) for m in range(60)]
        bars.append(make_m1(base + timedelta(minutes=20), close_price="2005.00"))

        res = resample_m1_candles(bars, Timeframe.H1)
        assert len(res) == 1
        rc = res[0]

        assert rc.completeness == WindowCompleteness.PARTIAL
        assert rc.candle.is_closed is False
        assert rc.duplicate_trading_minutes == 1
        assert rc.missing_trading_minutes == 0

    def test_clean_session_is_complete_with_zero_anomalies(self) -> None:
        """A clean trading hour with all 60 minutes present has 0 missing, 0 dups, 0 unexp."""
        base = datetime(2024, 1, 16, 14, 0, tzinfo=UTC)
        bars = [make_m1(base + timedelta(minutes=m)) for m in range(60)]

        res = resample_m1_candles(bars, Timeframe.H1)
        assert len(res) == 1
        rc = res[0]

        assert rc.completeness == WindowCompleteness.COMPLETE
        assert rc.candle.is_closed is True
        assert rc.missing_trading_minutes == 0
        assert rc.duplicate_trading_minutes == 0
        assert rc.unexpected_trading_minutes == 0

    def test_unexpected_trading_minute_with_all_open_minutes_present_fails_complete(self) -> None:
        """An unexpected bar during closed market must fail COMPLETE even if all 60 open minutes exist.

        Also verifies that rogue off-session bar prices do NOT contaminate aggregated OHLC.
        """
        # H2 window from 22:00 to 00:00 UTC on Tuesday:
        # 22:00-23:00 is closed rollover (expected 0)
        # 23:00-00:00 is open market (expected 60)
        base = datetime(2024, 1, 16, 22, 0, tzinfo=UTC)
        bars: list[Candle] = []

        # 1 unexpected bar in closed market at 22:15 with an outlier corrupt price of 1500.00
        bars.append(make_m1(base + timedelta(minutes=15), close_price="1500.00"))

        # All 60 expected open minutes present with normal price 2000.00
        for m in range(60, 120):
            bars.append(make_m1(base + timedelta(minutes=m), close_price="2000.00"))

        res = resample_m1_candles(bars, Timeframe.H2)
        assert len(res) == 1
        rc = res[0]

        # Must NOT be COMPLETE because of the unexpected off-session bar
        assert rc.completeness == WindowCompleteness.PARTIAL
        assert rc.candle.is_closed is False
        assert rc.is_session_complete is False
        assert rc.missing_trading_minutes == 0
        assert rc.unexpected_trading_minutes == 1
        assert rc.duplicate_trading_minutes == 0

        # OHLC must NOT be contaminated by the corrupt 1500.00 price from closed market
        assert rc.candle.low >= Decimal("1999.00")


class TestNewYorkD1BoundaryProof:
    """Requirement 2: Mathematical proof of the 2 PARTIAL bars in NEW_YORK_1700."""

    def test_january_2024_ny_d1_two_partial_bars_are_archive_boundaries(self) -> None:
        """Prove that Bar 0 and Bar 23 are PARTIAL solely due to the monthly dataset boundaries."""
        cal = XAUUSDTradingCalendar()

        # Bar 0: Starts 2023-12-31 22:00 UTC (Sunday 17:00 EST) and closes 2024-01-01 22:00 UTC
        # Inside [2023-12-31 22:00, 2024-01-01 00:00), there are 60 open trading minutes (23:00 to 00:00 UTC)
        # that belong to December 2023!
        t_bar0_open = datetime(2023, 12, 31, 22, 0, tzinfo=UTC)
        t_bar0_close = datetime(2024, 1, 1, 22, 0, tzinfo=UTC)
        total_open_bar0 = cal.count_open_minutes(t_bar0_open, t_bar0_close)
        assert (
            total_open_bar0 == 360
        )  # On New Year's Day, market closes early, expecting 360 open mins

        # Open minutes falling in December 2023:
        t_jan1_start = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        open_in_december = cal.count_open_minutes(t_bar0_open, t_jan1_start)
        assert open_in_december == 60  # Exactly 60 minutes in Dec 31!

        # Open minutes falling in January 2024:
        open_in_january_day1 = cal.count_open_minutes(t_jan1_start, t_bar0_close)
        assert open_in_january_day1 == 300  # Exactly 300 minutes in Jan 1!

        # When January-only data is supplied (starting 2024-01-01 00:00:00), exactly 300 bars exist:
        jan_day1_bars = [
            make_m1(t_jan1_start + timedelta(minutes=m))
            for m in range(1320)
            if cal.is_market_open(t_jan1_start + timedelta(minutes=m))
        ]
        assert len(jan_day1_bars) == 300

        res0 = resample_m1_candles(
            jan_day1_bars, Timeframe.D1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
        )
        assert len(res0) == 1
        assert res0[0].completeness == WindowCompleteness.PARTIAL
        assert res0[0].active_m1_count == 300
        assert res0[0].expected_trading_minutes == 360
        assert res0[0].missing_trading_minutes == 60  # Exactly the 60 Dec 31 minutes!

        # Now supply the missing 60 December 31 minutes -> Bar 0 becomes COMPLETE!
        dec31_bars = [
            make_m1(t_bar0_open + timedelta(minutes=m))
            for m in range(120)
            if cal.is_market_open(t_bar0_open + timedelta(minutes=m))
        ]
        assert len(dec31_bars) == 60

        res0_complete = resample_m1_candles(
            dec31_bars + jan_day1_bars,
            Timeframe.D1,
            daily_close_mode=DailyCloseMode.NEW_YORK_1700,
        )
        assert len(res0_complete) == 1
        assert res0_complete[0].completeness == WindowCompleteness.COMPLETE
        assert res0_complete[0].candle.is_closed is True

    def test_january_2024_ny_d1_last_bar_completes_when_february_data_present(self) -> None:
        """Prove Bar 23 is PARTIAL solely because 1,320 of its minutes fall in February 2024."""
        cal = XAUUSDTradingCalendar()

        # Bar 23: Starts 2024-01-31 22:00 UTC (Wednesday 17:00 EST) and closes 2024-02-01 22:00 UTC
        t_bar23_open = datetime(2024, 1, 31, 22, 0, tzinfo=UTC)
        t_bar23_close = datetime(2024, 2, 1, 22, 0, tzinfo=UTC)
        total_open_bar23 = cal.count_open_minutes(t_bar23_open, t_bar23_close)
        assert total_open_bar23 == 1380  # Full trading day

        # Open minutes in January: 23:00 to 00:00 = 60 minutes
        t_feb1_start = datetime(2024, 2, 1, 0, 0, tzinfo=UTC)
        open_in_jan = cal.count_open_minutes(t_bar23_open, t_feb1_start)
        assert open_in_jan == 60

        # Open minutes in February: 00:00 to 22:00 = 1320 minutes
        open_in_feb = cal.count_open_minutes(t_feb1_start, t_bar23_close)
        assert open_in_feb == 1320

        jan_bars = [
            make_m1(t_bar23_open + timedelta(minutes=m))
            for m in range(120)
            if cal.is_market_open(t_bar23_open + timedelta(minutes=m))
        ]
        assert len(jan_bars) == 60

        res23 = resample_m1_candles(
            jan_bars, Timeframe.D1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
        )
        assert len(res23) == 1
        assert res23[0].completeness == WindowCompleteness.PARTIAL
        assert res23[0].missing_trading_minutes == 1320

        # Adding February 1 data -> Bar 23 becomes COMPLETE!
        feb_bars = [
            make_m1(t_feb1_start + timedelta(minutes=m))
            for m in range(1320)
            if cal.is_market_open(t_feb1_start + timedelta(minutes=m))
        ]
        assert len(feb_bars) == 1320

        res23_complete = resample_m1_candles(
            jan_bars + feb_bars, Timeframe.D1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
        )
        assert len(res23_complete) == 1
        assert res23_complete[0].completeness == WindowCompleteness.COMPLETE
        assert res23_complete[0].candle.is_closed is True


class TestPointInTimeMetadataSemantics:
    """Requirement 3: Verification of PIT and availability metadata inheritance."""

    def test_estimated_historical_data_rejected_under_strict_pit(self) -> None:
        """Constituent M1 bars with ESTIMATED confidence cannot be accessed under STRICT_PIT."""
        base = datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
        m1_bars = [
            make_m1(
                base + timedelta(minutes=m),
                confidence=AvailabilityConfidence.ESTIMATED,
                available_at=None,
            )
            for m in range(60)
        ]

        res = resample_m1_candles(m1_bars, Timeframe.H1)
        assert len(res) == 1
        h1_candle = res[0].candle

        # Resampled candle inherits ESTIMATED confidence and available_at=None
        assert h1_candle.availability_confidence == AvailabilityConfidence.ESTIMATED
        assert h1_candle.available_at is None
        assert h1_candle.is_closed is True

        # STRICT_PIT rejects because availability is not verified!
        decision_time = datetime(2024, 1, 16, 13, 5, tzinfo=UTC)
        assert h1_candle.is_available_at(decision_time, policy=PitPolicy.STRICT_PIT) is False

        # RESEARCH_ESTIMATED permits access once close_time <= decision_time
        assert (
            h1_candle.is_available_at(decision_time, policy=PitPolicy.RESEARCH_ESTIMATED) is True
        )

    def test_verified_live_data_available_under_strict_pit_after_available_at(self) -> None:
        """Constituent M1 bars with VERIFIED confidence inherit max(available_at) under STRICT_PIT."""
        base = datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
        # Each M1 bar is available 5 seconds after close
        m1_bars = [
            make_m1(
                base + timedelta(minutes=m),
                confidence=AvailabilityConfidence.VERIFIED,
                available_at=base + timedelta(minutes=m + 1, seconds=5),
            )
            for m in range(60)
        ]

        res = resample_m1_candles(m1_bars, Timeframe.H1)
        assert len(res) == 1
        h1_candle = res[0].candle

        # H1 bar available_at is exactly the available_at of the 60th bar (13:00:05 UTC)
        expected_available_at = datetime(2024, 1, 16, 13, 0, 5, tzinfo=UTC)
        assert h1_candle.availability_confidence == AvailabilityConfidence.VERIFIED
        assert h1_candle.available_at == expected_available_at

        # Before 13:00:05: not available under STRICT_PIT
        assert (
            h1_candle.is_available_at(
                datetime(2024, 1, 16, 13, 0, 4, tzinfo=UTC), policy=PitPolicy.STRICT_PIT
            )
            is False
        )

        # At or after 13:00:05: available under STRICT_PIT
        assert (
            h1_candle.is_available_at(
                datetime(2024, 1, 16, 13, 0, 5, tzinfo=UTC), policy=PitPolicy.STRICT_PIT
            )
            is True
        )

    def test_ingested_at_snapshot_leakage_rejected(self) -> None:
        """If resampled constituent bars were ingested after knowledge_snapshot_time, reject."""
        base = datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
        ingestion_time = datetime(2024, 1, 16, 14, 0, tzinfo=UTC)
        m1_bars = [
            make_m1(
                base + timedelta(minutes=m),
                confidence=AvailabilityConfidence.ESTIMATED,
                ingested_at=ingestion_time,
            )
            for m in range(60)
        ]

        res = resample_m1_candles(m1_bars, Timeframe.H1)
        h1_candle = res[0].candle
        assert h1_candle.ingested_at == ingestion_time

        # Snapshot taken at 13:30 (before ingestion at 14:00) -> must reject
        snapshot_at_13_30 = datetime(2024, 1, 16, 13, 30, tzinfo=UTC)
        assert (
            h1_candle.is_available_at(
                datetime(2024, 1, 16, 15, 0, tzinfo=UTC),
                policy=PitPolicy.RESEARCH_ESTIMATED,
                knowledge_snapshot_time=snapshot_at_13_30,
            )
            is False
        )

        # Snapshot taken at 14:30 (after ingestion at 14:00) -> allowed
        snapshot_at_14_30 = datetime(2024, 1, 16, 14, 30, tzinfo=UTC)
        assert (
            h1_candle.is_available_at(
                datetime(2024, 1, 16, 15, 0, tzinfo=UTC),
                policy=PitPolicy.RESEARCH_ESTIMATED,
                knowledge_snapshot_time=snapshot_at_14_30,
            )
            is True
        )
