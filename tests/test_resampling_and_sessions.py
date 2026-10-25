"""Tests for trading calendar, bar classification, and multi-timeframe resampling engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    InvalidTimeframeResamplingError,
    MixedPriceTypeError,
    WindowCompleteness,
    filter_candles_by_decision_time,
    get_window_boundaries,
    resample_m1_candles,
)
from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.infrastructure.calendars.forex_session import (
    MarketSessionState,
    XAUUSDTradingCalendar,
)
from traderlab.infrastructure.providers.market_data_lab import (
    BarQualityClassification,
    MarketDataLabBar,
    bar_to_candle,
    classify_m1_series,
    get_market_data_lab_settings,
    read_m1_csv,
)

# ═════════════════════════════════════════════════════════════════════════════
# Helper Fixtures & Builders
# ═════════════════════════════════════════════════════════════════════════════


def make_m1_candle(
    dt_utc: datetime,
    price: str = "2000.00",
    *,
    high_offset: str = "1.00",
    low_offset: str = "1.00",
    close_offset: str = "0.50",
    price_type: PriceType = PriceType.BID,
    is_synthetic: bool = False,
    volume: Decimal | None = None,
) -> Candle:
    """Construct a synthetic domain M1 Candle for testing."""
    p = Decimal(price)
    return Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("synthetic_m1"),
        timeframe=Timeframe.M1,
        open_time=dt_utc,
        close_time=dt_utc + timedelta(minutes=1),
        open=p,
        high=p + Decimal(high_offset),
        low=p - Decimal(low_offset),
        close=p + Decimal(close_offset),
        volume=volume,
        price_type=price_type,
        is_synthetic=is_synthetic,
        is_closed=True,
        availability_confidence=AvailabilityConfidence.UNKNOWN,
    )


def make_flat_m1_bar(
    dt_utc: datetime,
    price: str = "2050.000",
    price_type: PriceType = PriceType.BID,
) -> MarketDataLabBar:
    """Construct an infrastructure flat MarketDataLabBar."""
    p = Decimal(price)
    return MarketDataLabBar(
        timestamp_ms=int(dt_utc.timestamp() * 1000),
        open_time=dt_utc,
        close_time=dt_utc + timedelta(minutes=1),
        open=p,
        high=p,
        low=p,
        close=p,
        price_type=price_type,
        is_flat=True,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1. Trading Calendar & Session State Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestXAUUSDTradingCalendar:
    """Verify session classification, weekend closures, holidays, rollover, and DST awareness."""

    @pytest.fixture
    def calendar(self) -> XAUUSDTradingCalendar:
        return XAUUSDTradingCalendar()

    def test_open_regular_session(self, calendar: XAUUSDTradingCalendar) -> None:
        # Wednesday 14:00 UTC (09:00 NY, active London/NY overlap)
        dt = datetime(2024, 1, 17, 14, 0, tzinfo=UTC)
        assert calendar.get_session_state(dt) == MarketSessionState.OPEN
        assert calendar.is_market_open(dt) is True
        assert calendar.is_verified_closed(dt) is False

    def test_weekend_closure_friday_evening(self, calendar: XAUUSDTradingCalendar) -> None:
        # Friday 17:00 NY = 22:00 UTC in January (EST)
        dt = datetime(2024, 1, 19, 22, 0, tzinfo=UTC)
        assert calendar.get_session_state(dt) == MarketSessionState.CLOSED_WEEKEND
        assert calendar.is_verified_closed(dt) is True

    def test_weekend_closure_saturday(self, calendar: XAUUSDTradingCalendar) -> None:
        dt = datetime(2024, 1, 20, 12, 0, tzinfo=UTC)
        assert calendar.get_session_state(dt) == MarketSessionState.CLOSED_WEEKEND
        assert calendar.is_market_open(dt) is False

    def test_weekend_reopen_sunday(self, calendar: XAUUSDTradingCalendar) -> None:
        # Sunday 17:59 NY (22:59 UTC) -> CLOSED_WEEKEND
        dt_closed = datetime(2024, 1, 21, 22, 59, tzinfo=UTC)
        assert calendar.get_session_state(dt_closed) == MarketSessionState.CLOSED_WEEKEND

        # Sunday 18:00 NY (23:00 UTC) -> OPEN
        dt_open = datetime(2024, 1, 21, 23, 0, tzinfo=UTC)
        assert calendar.get_session_state(dt_open) == MarketSessionState.OPEN

    def test_daily_rollover_break(self, calendar: XAUUSDTradingCalendar) -> None:
        # Tuesday 17:00 to 18:00 NY (22:00 to 23:00 UTC in Jan)
        dt_rollover = datetime(2024, 1, 16, 22, 30, tzinfo=UTC)
        assert calendar.get_session_state(dt_rollover) == MarketSessionState.CLOSED_DAILY_ROLLOVER
        assert calendar.is_verified_closed(dt_rollover) is True

        # Tuesday 18:00 NY (23:00 UTC) -> Market re-opens
        dt_reopen = datetime(2024, 1, 16, 23, 0, tzinfo=UTC)
        assert calendar.get_session_state(dt_reopen) == MarketSessionState.OPEN

    def test_holiday_new_year_day(self, calendar: XAUUSDTradingCalendar) -> None:
        dt_nyd = datetime(2024, 1, 1, 15, 0, tzinfo=UTC)
        assert calendar.get_session_state(dt_nyd) == MarketSessionState.CLOSED_HOLIDAY
        assert calendar.is_verified_closed(dt_nyd) is True

    def test_holiday_christmas_day(self, calendar: XAUUSDTradingCalendar) -> None:
        dt_xmas = datetime(2024, 12, 25, 12, 0, tzinfo=UTC)
        assert calendar.get_session_state(dt_xmas) == MarketSessionState.CLOSED_HOLIDAY

    def test_holiday_mlk_day_early_close(self, calendar: XAUUSDTradingCalendar) -> None:
        # Jan 15, 2024: 14:00 NY (19:00 UTC) is still open
        dt_open = datetime(2024, 1, 15, 19, 0, tzinfo=UTC)
        assert calendar.get_session_state(dt_open) == MarketSessionState.OPEN

        # Jan 15, 2024: 14:30 NY (19:30 UTC) onward is closed for MLK Day
        dt_closed = datetime(2024, 1, 15, 19, 30, tzinfo=UTC)
        assert calendar.get_session_state(dt_closed) == MarketSessionState.CLOSED_HOLIDAY

    def test_dst_awareness(self, calendar: XAUUSDTradingCalendar) -> None:
        # January: Winter (EST, UTC-5), is_dst_active = False
        dt_winter = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
        assert calendar.is_dst_active(dt_winter) is False

        # July: Summer (EDT, UTC-4), is_dst_active = True
        dt_summer = datetime(2024, 7, 15, 12, 0, tzinfo=UTC)
        assert calendar.is_dst_active(dt_summer) is True

        # In July (EDT), rollover 17:00 NY is 21:00 UTC (NOT 22:00 UTC!)
        dt_summer_rollover = datetime(2024, 7, 16, 21, 30, tzinfo=UTC)
        assert (
            calendar.get_session_state(dt_summer_rollover)
            == MarketSessionState.CLOSED_DAILY_ROLLOVER
        )


# ═════════════════════════════════════════════════════════════════════════════
# 2. Bar Quality Classification Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestBarClassifier:
    """Verify distinct separation of active, synthetic carry-forward, and stagnant bars."""

    def test_active_market_bar(self) -> None:
        dt = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
        bar = MarketDataLabBar(
            timestamp_ms=int(dt.timestamp() * 1000),
            open_time=dt,
            close_time=dt + timedelta(minutes=1),
            open=Decimal("2050.00"),
            high=Decimal("2052.00"),
            low=Decimal("2049.00"),
            close=Decimal("2051.50"),
            price_type=PriceType.BID,
            is_flat=False,
        )
        classified = classify_m1_series([bar])
        assert len(classified) == 1
        assert classified[0].classification == BarQualityClassification.ACTIVE_NORMAL
        assert classified[0].session_state == MarketSessionState.OPEN

    def test_synthetic_carry_forward_during_weekend(self) -> None:
        # Saturday flat bar
        dt = datetime(2024, 1, 20, 10, 0, tzinfo=UTC)
        bar = make_flat_m1_bar(dt)
        classified = classify_m1_series([bar])
        assert classified[0].classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD
        assert classified[0].session_state == MarketSessionState.CLOSED_WEEKEND

    def test_synthetic_carry_forward_during_holiday(self) -> None:
        # New Year's Day flat bar
        dt = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
        bar = make_flat_m1_bar(dt)
        classified = classify_m1_series([bar])
        assert classified[0].classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD
        assert classified[0].session_state == MarketSessionState.CLOSED_HOLIDAY

    def test_legitimate_flat_open_single_minute(self) -> None:
        # Wednesday 03:15 UTC (Asian quiet session) isolated flat bar
        dt = datetime(2024, 1, 17, 3, 15, tzinfo=UTC)
        bar = make_flat_m1_bar(dt)
        classified = classify_m1_series([bar])
        assert classified[0].classification == BarQualityClassification.LEGITIMATE_FLAT_OPEN
        assert classified[0].consecutive_flat_count == 1

    def test_uncertain_stagnant_prolonged_run(self) -> None:
        # 10 consecutive flat minutes during daytime open market
        base_dt = datetime(2024, 1, 17, 10, 0, tzinfo=UTC)
        bars = [make_flat_m1_bar(base_dt + timedelta(minutes=i)) for i in range(10)]
        classified = classify_m1_series(bars, max_legitimate_flat_run=5)

        # First 5 are legitimate flat open
        for i in range(5):
            assert classified[i].classification == BarQualityClassification.LEGITIMATE_FLAT_OPEN
        # Next 5 are flagged as uncertain stagnant
        for i in range(5, 10):
            assert classified[i].classification == BarQualityClassification.UNCERTAIN_STAGNANT


# ═════════════════════════════════════════════════════════════════════════════
# 3. Resampling Engine Mathematical & Window Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestResamplingEngine:
    """Verify deterministic resampling, window boundary arithmetic, and OHLC math."""

    def test_m5_resampling_aggregation(self) -> None:
        base_dt = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
        candles = [
            make_m1_candle(
                base_dt + timedelta(minutes=0),
                "2000.00",
                high_offset="2.00",
                low_offset="1.00",
                close_offset="1.50",
            ),
            make_m1_candle(
                base_dt + timedelta(minutes=1),
                "2001.50",
                high_offset="3.00",
                low_offset="0.50",
                close_offset="2.00",
            ),
            make_m1_candle(
                base_dt + timedelta(minutes=2),
                "2003.50",
                high_offset="5.00",
                low_offset="1.00",
                close_offset="-1.00",
            ),
            make_m1_candle(
                base_dt + timedelta(minutes=3),
                "2002.50",
                high_offset="1.00",
                low_offset="3.00",
                close_offset="0.00",
            ),
            make_m1_candle(
                base_dt + timedelta(minutes=4),
                "2002.50",
                high_offset="1.50",
                low_offset="0.50",
                close_offset="1.00",
            ),
        ]

        res = resample_m1_candles(candles, Timeframe.M5)
        assert len(res) == 1
        rc = res[0]

        assert rc.target_timeframe == Timeframe.M5
        assert rc.expected_m1_count == 5
        assert rc.present_m1_count == 5
        assert rc.completeness == WindowCompleteness.COMPLETE
        assert rc.candle.open_time == base_dt
        assert rc.candle.close_time == base_dt + timedelta(minutes=5)

        # Mathematical OHLC: Open is first open, High is max high, Low is min low, Close is last close
        assert rc.candle.open == Decimal("2000.00")
        assert rc.candle.high == Decimal("2008.50")  # bar 2: 2003.50 + 5.00
        assert rc.candle.low == Decimal("1999.00")  # bar 0: 2000.00 - 1.00
        assert rc.candle.close == Decimal("2003.50")  # bar 4: 2002.50 + 1.00
        assert rc.candle.is_closed is True
        assert rc.candle.is_synthetic is False

    def test_m15_and_h1_window_boundaries(self) -> None:
        dt = datetime(2024, 1, 16, 10, 23, 45, tzinfo=UTC)

        m15_open, m15_close = get_window_boundaries(dt, Timeframe.M15)
        assert m15_open == datetime(2024, 1, 16, 10, 15, tzinfo=UTC)
        assert m15_close == datetime(2024, 1, 16, 10, 30, tzinfo=UTC)

        h1_open, h1_close = get_window_boundaries(dt, Timeframe.H1)
        assert h1_open == datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
        assert h1_close == datetime(2024, 1, 16, 11, 0, tzinfo=UTC)

    def test_h4_utc_window_boundaries(self) -> None:
        # 14:15 UTC falls into 12:00-16:00 UTC H4 bucket
        dt = datetime(2024, 1, 16, 14, 15, tzinfo=UTC)
        h4_open, h4_close = get_window_boundaries(dt, Timeframe.H4)
        assert h4_open == datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
        assert h4_close == datetime(2024, 1, 16, 16, 0, tzinfo=UTC)

    def test_d1_utc_midnight_boundaries(self) -> None:
        dt = datetime(2024, 1, 16, 14, 15, tzinfo=UTC)
        d1_open, d1_close = get_window_boundaries(dt, Timeframe.D1, DailyCloseMode.UTC_MIDNIGHT)
        assert d1_open == datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
        assert d1_close == datetime(2024, 1, 17, 0, 0, tzinfo=UTC)

    def test_d1_new_york_1700_boundaries(self) -> None:
        # In January (EST, UTC-5):
        # 2024-01-16 12:00 UTC is 07:00 EST. Session started yesterday 2024-01-15 17:00 EST (22:00 UTC)
        dt = datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
        d1_open, d1_close = get_window_boundaries(dt, Timeframe.D1, DailyCloseMode.NEW_YORK_1700)
        assert d1_open == datetime(2024, 1, 15, 22, 0, tzinfo=UTC)
        assert d1_close == datetime(2024, 1, 16, 22, 0, tzinfo=UTC)

    def test_bid_ask_mixing_raises_error(self) -> None:
        base_dt = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
        bid = make_m1_candle(base_dt, price_type=PriceType.BID)
        ask = make_m1_candle(base_dt + timedelta(minutes=1), price_type=PriceType.ASK)

        with pytest.raises(MixedPriceTypeError) as exc_info:
            resample_m1_candles([bid, ask], Timeframe.M5)
        assert "Cannot resample mixed price types" in str(exc_info.value)
        assert exc_info.value.code == "MIXED_PRICE_TYPE_ERROR"

    def test_partial_window_handling(self) -> None:
        # Provide only 3 bars out of 5 for M5
        base_dt = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
        candles = [
            make_m1_candle(base_dt + timedelta(minutes=0), "2000.00"),
            make_m1_candle(base_dt + timedelta(minutes=1), "2001.00"),
            make_m1_candle(base_dt + timedelta(minutes=2), "2002.00"),
        ]

        res = resample_m1_candles(candles, Timeframe.M5)
        assert len(res) == 1
        rc = res[0]
        assert rc.completeness == WindowCompleteness.PARTIAL
        assert rc.present_m1_count == 3
        assert rc.expected_m1_count == 5
        # Incomplete window is marked NOT closed (cannot be treated as complete closed bar)
        assert rc.candle.is_closed is False

    def test_all_synthetic_window_handling(self) -> None:
        base_dt = datetime(2024, 1, 20, 10, 0, tzinfo=UTC)  # Saturday
        candles = [
            make_m1_candle(
                base_dt + timedelta(minutes=i),
                "2050.00",
                high_offset="0",
                low_offset="0",
                close_offset="0",
                is_synthetic=True,
            )
            for i in range(5)
        ]

        res = resample_m1_candles(candles, Timeframe.M5)
        assert len(res) == 1
        rc = res[0]
        assert rc.completeness == WindowCompleteness.ALL_SYNTHETIC_CLOSED
        assert rc.candle.is_synthetic is True

    def test_invalid_target_timeframe_rejected(self) -> None:
        base_dt = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
        candle = make_m1_candle(base_dt)

        with pytest.raises(InvalidTimeframeResamplingError):
            resample_m1_candles([candle], Timeframe.M1)

    def test_decimal_precision_retained(self) -> None:
        base_dt = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
        candles = [
            make_m1_candle(base_dt + timedelta(minutes=i), f"2040.{i:03d}") for i in range(5)
        ]
        res = resample_m1_candles(candles, Timeframe.M5)
        assert res[0].candle.open == Decimal("2040.000")
        assert res[0].candle.close == Decimal("2040.004") + Decimal("0.50")


# ═════════════════════════════════════════════════════════════════════════════
# 4. Point-in-Time (PIT) & Lookahead Bias Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestPointInTimeLookahead:
    """Ensure future minutes or unclosed bars do not leak into past decision times."""

    def test_filter_candles_by_decision_time(self) -> None:
        base_dt = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
        # H1 bar 1: 10:00 to 11:00 UTC
        # H1 bar 2: 11:00 to 12:00 UTC
        h1_bars = [
            Candle(
                instrument_id=InstrumentId("XAUUSD"),
                source_id=DataSourceId("src"),
                timeframe=Timeframe.H1,
                open_time=base_dt,
                close_time=base_dt + timedelta(hours=1),
                open=Decimal("2000"),
                high=Decimal("2010"),
                low=Decimal("1995"),
                close=Decimal("2005"),
                volume=None,
                price_type=PriceType.BID,
            ),
            Candle(
                instrument_id=InstrumentId("XAUUSD"),
                source_id=DataSourceId("src"),
                timeframe=Timeframe.H1,
                open_time=base_dt + timedelta(hours=1),
                close_time=base_dt + timedelta(hours=2),
                open=Decimal("2005"),
                high=Decimal("2015"),
                low=Decimal("2000"),
                close=Decimal("2010"),
                volume=None,
                price_type=PriceType.BID,
            ),
        ]

        # Decision time 10:30 UTC: Neither H1 bar is closed yet!
        visible_1030 = filter_candles_by_decision_time(
            h1_bars, as_of_utc=datetime(2024, 1, 16, 10, 30, tzinfo=UTC)
        )
        assert len(visible_1030) == 0

        # Decision time 11:00 UTC: First H1 bar is finalized and visible
        visible_1100 = filter_candles_by_decision_time(
            h1_bars, as_of_utc=datetime(2024, 1, 16, 11, 0, tzinfo=UTC)
        )
        assert len(visible_1100) == 1
        assert visible_1100[0].open_time == base_dt

        # Decision time 11:59 UTC: Second H1 bar is STILL not visible
        visible_1159 = filter_candles_by_decision_time(
            h1_bars, as_of_utc=datetime(2024, 1, 16, 11, 59, tzinfo=UTC)
        )
        assert len(visible_1159) == 1

        # Decision time 12:00 UTC: Both H1 bars are visible
        visible_1200 = filter_candles_by_decision_time(
            h1_bars, as_of_utc=datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
        )
        assert len(visible_1200) == 2


# ═════════════════════════════════════════════════════════════════════════════
# 5. Real CSV Pilot Tests (16 January 2024)
# ═════════════════════════════════════════════════════════════════════════════


class TestRealPilotJanuary16:
    """Execute real resampling pipeline on 16 January 2024 trading day."""

    @pytest.fixture(scope="class")
    def jan16_candles(self) -> tuple[list[Candle], list[Candle]]:
        settings = get_market_data_lab_settings()
        bid_path, ask_path = settings.validate_dataset_files(2024, 1)

        t_start = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
        t_end = datetime(2024, 1, 17, 0, 0, tzinfo=UTC)

        bid_m1 = [
            b for b in read_m1_csv(bid_path, PriceType.BID) if t_start <= b.open_time < t_end
        ]
        ask_m1 = [
            b for b in read_m1_csv(ask_path, PriceType.ASK) if t_start <= b.open_time < t_end
        ]

        classified_bid = classify_m1_series(bid_m1)
        bid_candles = [
            bar_to_candle(
                cb.bar,
                is_synthetic=(
                    cb.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD
                ),
            )
            for cb in classified_bid
        ]

        classified_ask = classify_m1_series(ask_m1)
        ask_candles = [
            bar_to_candle(
                cb.bar,
                is_synthetic=(
                    cb.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD
                ),
            )
            for cb in classified_ask
        ]

        return bid_candles, ask_candles

    def test_m1_count_january_16(self, jan16_candles: tuple[list[Candle], list[Candle]]) -> None:
        bid_candles, ask_candles = jan16_candles
        assert len(bid_candles) == 1440
        assert len(ask_candles) == 1440

    @pytest.mark.parametrize(
        ("timeframe", "expected_total", "expected_complete"),
        [
            (Timeframe.M5, 288, 276),
            (Timeframe.M15, 96, 92),
            (Timeframe.H1, 24, 23),
            (Timeframe.H4, 6, 6),
            (Timeframe.D1, 1, 1),
        ],
    )
    def test_resampled_bar_counts(
        self,
        jan16_candles: tuple[list[Candle], list[Candle]],
        timeframe: Timeframe,
        expected_total: int,
        expected_complete: int,
    ) -> None:
        bid_candles, _ = jan16_candles
        res = resample_m1_candles(bid_candles, timeframe)
        assert len(res) == expected_total

        complete_count = sum(1 for r in res if r.completeness == WindowCompleteness.COMPLETE)
        assert complete_count == expected_complete

    def test_m15_mathematical_precision_against_raw_m1(
        self, jan16_candles: tuple[list[Candle], list[Candle]]
    ) -> None:
        """Verify M15 bar (12:00-12:15 UTC) matches manual extraction of 15 raw M1 bars."""
        bid_candles, _ = jan16_candles
        slice_m1 = [
            c
            for c in bid_candles
            if datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
            <= c.open_time
            < datetime(2024, 1, 16, 12, 15, tzinfo=UTC)
        ]
        assert len(slice_m1) == 15

        res = resample_m1_candles(slice_m1, Timeframe.M15)
        assert len(res) == 1
        m15_bar = res[0].candle

        assert m15_bar.open == slice_m1[0].open
        assert m15_bar.high == max(c.high for c in slice_m1)
        assert m15_bar.low == min(c.low for c in slice_m1)
        assert m15_bar.close == slice_m1[-1].close
        assert m15_bar.open == Decimal("2039.775")
        assert m15_bar.high == Decimal("2039.935")
        assert m15_bar.low == Decimal("2035.638")
        assert m15_bar.close == Decimal("2036.165")

    def test_h4_mathematical_precision_against_raw_m1(
        self, jan16_candles: tuple[list[Candle], list[Candle]]
    ) -> None:
        """Verify H4 bar (12:00-16:00 UTC) matches manual extraction of 240 raw M1 bars."""
        bid_candles, _ = jan16_candles
        slice_m1 = [
            c
            for c in bid_candles
            if datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
            <= c.open_time
            < datetime(2024, 1, 16, 16, 0, tzinfo=UTC)
        ]
        assert len(slice_m1) == 240

        res = resample_m1_candles(slice_m1, Timeframe.H4)
        assert len(res) == 1
        h4_bar = res[0].candle

        assert h4_bar.open == slice_m1[0].open
        assert h4_bar.high == max(c.high for c in slice_m1)
        assert h4_bar.low == min(c.low for c in slice_m1)
        assert h4_bar.close == slice_m1[-1].close
        assert h4_bar.open == Decimal("2039.775")
        assert h4_bar.high == Decimal("2044.915")
        assert h4_bar.low == Decimal("2031.275")
        assert h4_bar.close == Decimal("2041.035")

    def test_bid_ask_spread_consistency_across_resampled_bars(
        self, jan16_candles: tuple[list[Candle], list[Candle]]
    ) -> None:
        """Verify ASK close >= BID close across all resampled timeframes."""
        bid_candles, ask_candles = jan16_candles

        for tf in [Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1]:
            res_bid = resample_m1_candles(bid_candles, tf)
            res_ask = resample_m1_candles(ask_candles, tf)

            assert len(res_bid) == len(res_ask)
            for rb, ra in zip(res_bid, res_ask, strict=True):
                assert ra.candle.close >= rb.candle.close
                spread = ra.candle.close - rb.candle.close
                assert spread >= Decimal("0")
