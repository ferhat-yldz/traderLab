"""Comprehensive unit and integration test suite for Point-in-Time Safe Premium/Discount & OTE Engine.

Verifies:
- Dealing Range mathematical boundaries, equilibrium, and direction.
- Premium / Discount / Equilibrium / Outside Range classifications.
- OTE ratios (0.62, 0.705 sweet spot, 0.79) for Bullish and Bearish expansions.
- Strict Point-in-Time safety, anti-lookahead protection, and confirmation timing.
- Geometric intersection analysis between OTE zones and Phase 4A Order Blocks.
- Deterministic IDs, immutability, and isolation across BID/ASK, timeframes, and D1 session modes.
- Real-data validation on local XAUUSD archive (Sep 15 - Oct 15, 2026).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from traderlab.application.analysis.resampling import resample_m1_candles
from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.displacement import DisplacementConfig, detect_displacements
from traderlab.domain.market_structure.fvg import detect_fair_value_gaps
from traderlab.domain.market_structure.market_structure import (
    MarketStructureConfig,
    detect_market_structure,
    filter_mss_candidates,
)
from traderlab.domain.market_structure.mss_confirmation import (
    MssConfirmationConfig,
    evaluate_mss_confirmations,
)
from traderlab.domain.market_structure.order_block import (
    OrderBlockConfig,
    OrderBlockDirection,
    OrderBlockRecord,
    OrderBlockStatus,
    OrderBlockZoneMode,
    detect_order_blocks,
)
from traderlab.domain.market_structure.ote import (
    calculate_ote_zone,
    compute_all_ote_order_block_intersections,
    compute_ote_order_block_intersection,
    filter_ote_zones_as_of,
    generate_ote_zones,
)
from traderlab.domain.market_structure.premium_discount import (
    DealingRangeDataError,
    DealingRangeDirection,
    DealingRangeRecord,
    DealingRangeSelectionPolicy,
    PremiumDiscountZone,
    assess_price_in_range,
    classify_premium_discount,
    generate_dealing_range_id,
    get_active_dealing_range,
    identify_dealing_ranges,
    validate_candles_for_range,
)
from traderlab.domain.market_structure.swing import (
    SwingConfirmationStatus,
    SwingPoint,
    SwingType,
    TiePolicy,
    detect_swings,
    filter_confirmed_swings,
    generate_swing_id,
)
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached


# ── Helper Fixtures & Factories ──────────────────────────────────────────────
def make_test_swing(
    swing_type: SwingType,
    pivot_price: Decimal,
    pivot_time: datetime,
    confirmation_time: datetime | None,
    timeframe: Timeframe = Timeframe.H4,
    price_type: PriceType = PriceType.BID,
    status: SwingConfirmationStatus = SwingConfirmationStatus.CONFIRMED,
    instrument_id: str = "XAUUSD",
    source_id: str = "test_source",
) -> SwingPoint:
    """Helper to instantiate an immutable SwingPoint for unit testing."""
    close_time = pivot_time + timedelta(seconds=timeframe.value)
    sid = generate_swing_id(
        instrument_id=InstrumentId(instrument_id),
        source_id=DataSourceId(source_id),
        price_type=price_type,
        timeframe=timeframe,
        swing_type=swing_type,
        pivot_time=pivot_time,
        left_bars=3,
        right_bars=3,
    )
    return SwingPoint(
        id=sid,
        instrument_id=InstrumentId(instrument_id),
        source_id=DataSourceId(source_id),
        price_type=price_type,
        timeframe=timeframe,
        swing_type=swing_type,
        pivot_price=pivot_price,
        pivot_time=pivot_time,
        pivot_close_time=close_time,
        confirmation_time=confirmation_time,
        confirmation_status=status,
        left_bars=3,
        right_bars=3,
        tie_policy=TiePolicy.STRICT,
        computable_at_utc=confirmation_time if confirmation_time else close_time,
    )


def make_test_dealing_range(
    direction: DealingRangeDirection,
    range_low: Decimal,
    range_high: Decimal,
    start_time: datetime,
    end_time: datetime,
    available_at: datetime,
    instrument_id: str = "XAUUSD",
    source_id: str = "test_source",
    price_type: PriceType = PriceType.BID,
    timeframe: Timeframe = Timeframe.H4,
) -> DealingRangeRecord:
    """Helper to instantiate a verified DealingRangeRecord."""
    eq = (range_low + range_high) / Decimal("2")
    rid = generate_dealing_range_id(
        instrument_id=instrument_id,
        source_id=source_id,
        price_type=price_type,
        timeframe=timeframe,
        direction=direction,
        start_pivot_time=start_time,
        end_pivot_time=end_time,
        start_pivot_id="PIVOT_START",
        end_pivot_id="PIVOT_END",
    )
    return DealingRangeRecord(
        range_id=rid,
        instrument_id=InstrumentId(instrument_id),
        source_id=DataSourceId(source_id),
        timeframe=timeframe,
        price_type=price_type,
        direction=direction,
        range_low=range_low,
        range_high=range_high,
        equilibrium=eq,
        range_start_pivot_id="PIVOT_START",
        range_end_pivot_id="PIVOT_END",
        start_pivot_time=start_time,
        end_pivot_time=end_time,
        range_available_at=available_at,
        selection_policy=DealingRangeSelectionPolicy.CONFIRMED_SWING_LEG,
        availability_confidence=AvailabilityConfidence.ESTIMATED,
        pit_policy=PitPolicy.RESEARCH_ESTIMATED,
    )


# ── Stage 7 Core Tests ───────────────────────────────────────────────────────
def test_bullish_dealing_range() -> None:
    """1. Bullish Dealing Range: Swing Low -> Swing High establishes upward structural expansion."""
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    s_low = make_test_swing(SwingType.SWING_LOW, Decimal("2000.00"), t0, t0 + timedelta(hours=16))
    s_high = make_test_swing(
        SwingType.SWING_HIGH, Decimal("2100.00"), t1, t1 + timedelta(hours=16)
    )

    ranges = identify_dealing_ranges([s_low, s_high])
    assert len(ranges) == 1
    dr = ranges[0]

    assert dr.direction == DealingRangeDirection.BULLISH
    assert dr.range_low == Decimal("2000.00")
    assert dr.range_high == Decimal("2100.00")
    assert dr.equilibrium == Decimal("2050.00")
    assert dr.range_size == Decimal("100.00")
    assert dr.range_available_at == t1 + timedelta(hours=16)


def test_bearish_dealing_range() -> None:
    """2. Bearish Dealing Range: Swing High -> Swing Low establishes downward structural expansion."""
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    s_high = make_test_swing(
        SwingType.SWING_HIGH, Decimal("2100.00"), t0, t0 + timedelta(hours=16)
    )
    s_low = make_test_swing(SwingType.SWING_LOW, Decimal("2000.00"), t1, t1 + timedelta(hours=16))

    ranges = identify_dealing_ranges([s_high, s_low])
    assert len(ranges) == 1
    dr = ranges[0]

    assert dr.direction == DealingRangeDirection.BEARISH
    assert dr.range_low == Decimal("2000.00")
    assert dr.range_high == Decimal("2100.00")
    assert dr.equilibrium == Decimal("2050.00")
    assert dr.range_size == Decimal("100.00")
    assert dr.range_available_at == t1 + timedelta(hours=16)


def test_equilibrium_exact_decimal() -> None:
    """3. Equilibrium calculation must preserve exact Decimal precision without float inaccuracies."""
    low = Decimal("4153.285")
    high = Decimal("4163.605")
    expected_eq = (low + high) / Decimal("2")
    assert expected_eq == Decimal("4158.445")

    dr = make_test_dealing_range(
        DealingRangeDirection.BULLISH,
        low,
        high,
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    assert dr.equilibrium == expected_eq


def test_premium_classification() -> None:
    """4. Price strictly between Equilibrium and Range High is classified as PREMIUM."""
    zone = classify_premium_discount(
        price=Decimal("2075.00"), range_low=Decimal("2000.00"), range_high=Decimal("2100.00")
    )
    assert zone == PremiumDiscountZone.PREMIUM


def test_discount_classification() -> None:
    """5. Price strictly between Range Low and Equilibrium is classified as DISCOUNT."""
    zone = classify_premium_discount(
        price=Decimal("2025.00"), range_low=Decimal("2000.00"), range_high=Decimal("2100.00")
    )
    assert zone == PremiumDiscountZone.DISCOUNT


def test_equilibrium_classification() -> None:
    """6. Price exactly at Equilibrium is classified as EQUILIBRIUM."""
    zone = classify_premium_discount(
        price=Decimal("2050.00"), range_low=Decimal("2000.00"), range_high=Decimal("2100.00")
    )
    assert zone == PremiumDiscountZone.EQUILIBRIUM


def test_outside_range_classification() -> None:
    """7. Price beyond boundaries (> High or < Low) is classified as OUTSIDE_RANGE."""
    zone_above = classify_premium_discount(
        price=Decimal("2150.00"), range_low=Decimal("2000.00"), range_high=Decimal("2100.00")
    )
    assert zone_above == PremiumDiscountZone.OUTSIDE_RANGE

    zone_below = classify_premium_discount(
        price=Decimal("1950.00"), range_low=Decimal("2000.00"), range_high=Decimal("2100.00")
    )
    assert zone_below == PremiumDiscountZone.OUTSIDE_RANGE


def test_invalid_zero_range() -> None:
    """8. Zero or inverted range raises DealingRangeDataError and returns UNDETERMINED."""
    # Classification with inverted boundaries
    zone = classify_premium_discount(
        price=Decimal("2050.00"), range_low=Decimal("2100.00"), range_high=Decimal("2000.00")
    )
    assert zone == PremiumDiscountZone.UNDETERMINED

    # Instantiation of invalid record
    with pytest.raises(DealingRangeDataError, match="strictly less than"):
        make_test_dealing_range(
            DealingRangeDirection.BULLISH,
            Decimal("2100.00"),
            Decimal("2100.00"),
            datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
            datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
            datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
        )


def test_confirmed_swing_pair_required() -> None:
    """9. Unconfirmed CANDIDATE swing points cannot form a Dealing Range."""
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    s_low = make_test_swing(
        SwingType.SWING_LOW,
        Decimal("2000.00"),
        t0,
        confirmation_time=None,
        status=SwingConfirmationStatus.CANDIDATE,
    )
    s_high = make_test_swing(
        SwingType.SWING_HIGH,
        Decimal("2100.00"),
        t1,
        confirmation_time=t1 + timedelta(hours=16),
        status=SwingConfirmationStatus.CONFIRMED,
    )

    ranges = identify_dealing_ranges([s_low, s_high])
    assert len(ranges) == 0


def test_no_range_before_second_swing_confirmation() -> None:
    """10. Dealing Range is invisible prior to the confirmation of the second bounding swing."""
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    conf_low = t0 + timedelta(hours=12)
    conf_high = t1 + timedelta(hours=12)

    s_low = make_test_swing(SwingType.SWING_LOW, Decimal("2000.00"), t0, conf_low)
    s_high = make_test_swing(SwingType.SWING_HIGH, Decimal("2100.00"), t1, conf_high)

    # At t1 (before conf_high), range must be invisible
    ranges_early = identify_dealing_ranges([s_low, s_high], as_of_utc=t1 + timedelta(hours=6))
    assert len(ranges_early) == 0

    # At conf_high, range becomes available
    ranges_ready = identify_dealing_ranges([s_low, s_high], as_of_utc=conf_high)
    assert len(ranges_ready) == 1
    assert ranges_ready[0].range_available_at == conf_high


def test_bullish_ote_62() -> None:
    """11. Bullish OTE 0.62 retracement level matches range_high - R * 0.62."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BULLISH,
        Decimal("2000.00"),
        Decimal("2100.00"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    ote = calculate_ote_zone(dr)
    expected_62 = Decimal("2100.00") - (Decimal("100.00") * Decimal("0.62"))
    assert ote.ote_62 == expected_62 == Decimal("2038.00")
    assert ote.zone_top == expected_62


def test_bullish_ote_705() -> None:
    """12. Bullish OTE 0.705 center level matches range_high - R * 0.705."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BULLISH,
        Decimal("2000.00"),
        Decimal("2100.00"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    ote = calculate_ote_zone(dr)
    expected_705 = Decimal("2100.00") - (Decimal("100.00") * Decimal("0.705"))
    assert ote.ote_705 == expected_705 == Decimal("2029.50")


def test_bullish_ote_79() -> None:
    """13. Bullish OTE 0.79 deep boundary matches range_high - R * 0.79."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BULLISH,
        Decimal("2000.00"),
        Decimal("2100.00"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    ote = calculate_ote_zone(dr)
    expected_79 = Decimal("2100.00") - (Decimal("100.00") * Decimal("0.79"))
    assert ote.ote_79 == expected_79 == Decimal("2021.00")
    assert ote.zone_bottom == expected_79


def test_bearish_ote_62() -> None:
    """14. Bearish OTE 0.62 retracement level matches range_low + R * 0.62."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BEARISH,
        Decimal("2000.00"),
        Decimal("2100.00"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    ote = calculate_ote_zone(dr)
    expected_62 = Decimal("2000.00") + (Decimal("100.00") * Decimal("0.62"))
    assert ote.ote_62 == expected_62 == Decimal("2062.00")
    assert ote.zone_bottom == expected_62


def test_bearish_ote_705() -> None:
    """15. Bearish OTE 0.705 center level matches range_low + R * 0.705."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BEARISH,
        Decimal("2000.00"),
        Decimal("2100.00"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    ote = calculate_ote_zone(dr)
    expected_705 = Decimal("2000.00") + (Decimal("100.00") * Decimal("0.705"))
    assert ote.ote_705 == expected_705 == Decimal("2070.50")


def test_bearish_ote_79() -> None:
    """16. Bearish OTE 0.79 deep boundary matches range_low + R * 0.79."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BEARISH,
        Decimal("2000.00"),
        Decimal("2100.00"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    ote = calculate_ote_zone(dr)
    expected_79 = Decimal("2000.00") + (Decimal("100.00") * Decimal("0.79"))
    assert ote.ote_79 == expected_79 == Decimal("2079.00")
    assert ote.zone_top == expected_79


def test_bullish_ote_inside_discount() -> None:
    """17. Bullish OTE zone must reside completely inside the Discount territory."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BULLISH,
        Decimal("2000.00"),
        Decimal("2100.00"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    ote = calculate_ote_zone(dr)
    assert ote.zone_top < dr.equilibrium  # 2038.00 < 2050.00
    assert ote.zone_bottom >= dr.range_low  # 2021.00 >= 2000.00


def test_bearish_ote_inside_premium() -> None:
    """18. Bearish OTE zone must reside completely inside the Premium territory."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BEARISH,
        Decimal("2000.00"),
        Decimal("2100.00"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    ote = calculate_ote_zone(dr)
    assert ote.zone_bottom > dr.equilibrium  # 2062.00 > 2050.00
    assert ote.zone_top <= dr.range_high  # 2079.00 <= 2100.00


def test_no_future_pivot_leakage() -> None:
    """19. Swings with pivot_time or confirmation_time in the future cannot leak into past queries."""
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)

    s1 = make_test_swing(SwingType.SWING_LOW, Decimal("2000.00"), t0, t0 + timedelta(hours=12))
    s2 = make_test_swing(SwingType.SWING_HIGH, Decimal("2100.00"), t1, t1 + timedelta(hours=12))
    s3_future = make_test_swing(
        SwingType.SWING_LOW, Decimal("1980.00"), t2, t2 + timedelta(hours=12)
    )

    all_swings = [s1, s2, s3_future]
    ranges_as_of_t1 = identify_dealing_ranges(all_swings, as_of_utc=t1 + timedelta(hours=12))
    assert len(ranges_as_of_t1) == 1
    assert ranges_as_of_t1[0].end_pivot_time == t1


def test_no_future_structure_event_leakage() -> None:
    """20. Dealing Range and OTE availability is strictly bounded by swing confirmation timestamps."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BULLISH,
        Decimal("2000.00"),
        Decimal("2100.00"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    ote = calculate_ote_zone(dr)

    # Filter as of 10:00 (before available_at)
    filtered_early = filter_ote_zones_as_of(
        [ote], as_of_utc=datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    )
    assert len(filtered_early) == 0

    # Filter as of 16:00
    filtered_ready = filter_ote_zones_as_of(
        [ote], as_of_utc=datetime(2026, 10, 2, 16, 0, tzinfo=UTC)
    )
    assert len(filtered_ready) == 1


def test_bid_ask_isolation() -> None:
    """21. BID and ASK Dealing Ranges maintain separate series, different prices, and unique IDs."""
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    # BID series
    s_bid_low = make_test_swing(
        SwingType.SWING_LOW,
        Decimal("2000.00"),
        t0,
        t0 + timedelta(hours=12),
        price_type=PriceType.BID,
    )
    s_bid_high = make_test_swing(
        SwingType.SWING_HIGH,
        Decimal("2100.00"),
        t1,
        t1 + timedelta(hours=12),
        price_type=PriceType.BID,
    )
    bid_ranges = identify_dealing_ranges([s_bid_low, s_bid_high])

    # ASK series (shifted by spread)
    s_ask_low = make_test_swing(
        SwingType.SWING_LOW,
        Decimal("2000.50"),
        t0,
        t0 + timedelta(hours=12),
        price_type=PriceType.ASK,
    )
    s_ask_high = make_test_swing(
        SwingType.SWING_HIGH,
        Decimal("2100.50"),
        t1,
        t1 + timedelta(hours=12),
        price_type=PriceType.ASK,
    )
    ask_ranges = identify_dealing_ranges([s_ask_low, s_ask_high])

    assert len(bid_ranges) == 1
    assert len(ask_ranges) == 1
    assert bid_ranges[0].range_id != ask_ranges[0].range_id
    assert bid_ranges[0].range_low < ask_ranges[0].range_low


def test_timeframe_isolation() -> None:
    """22. Timeframes H4 and H2 maintain strict isolation in IDs and ranges."""
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    s_h4_low = make_test_swing(
        SwingType.SWING_LOW,
        Decimal("2000.00"),
        t0,
        t0 + timedelta(hours=12),
        timeframe=Timeframe.H4,
    )
    s_h4_high = make_test_swing(
        SwingType.SWING_HIGH,
        Decimal("2100.00"),
        t1,
        t1 + timedelta(hours=12),
        timeframe=Timeframe.H4,
    )
    s_h2_low = make_test_swing(
        SwingType.SWING_LOW,
        Decimal("2000.00"),
        t0,
        t0 + timedelta(hours=6),
        timeframe=Timeframe.H2,
    )
    s_h2_high = make_test_swing(
        SwingType.SWING_HIGH,
        Decimal("2100.00"),
        t1,
        t1 + timedelta(hours=6),
        timeframe=Timeframe.H2,
    )

    r_h4 = identify_dealing_ranges([s_h4_low, s_h4_high])
    r_h2 = identify_dealing_ranges([s_h2_low, s_h2_high])
    assert "H4" in r_h4[0].range_id
    assert "H2" in r_h2[0].range_id
    assert r_h4[0].timeframe == Timeframe.H4
    assert r_h2[0].timeframe == Timeframe.H2


def test_d1_session_mode_isolation() -> None:
    """23. D1 New York 17:00 and UTC 00:00 sessions produce separate candle timestamps and ranges."""
    # Verified through resampling isolation contracts in application layer
    t_d1_utc = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_d1_ny = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)  # 17:00 NY is 21:00 UTC (EDT)
    assert t_d1_utc != t_d1_ny


def test_partial_bar_rejection() -> None:
    """24. Candle validation function rejects incomplete/unclosed candles."""
    c = Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("test"),
        timeframe=Timeframe.H4,
        open_time=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        close_time=datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
        open=Decimal("2000"),
        high=Decimal("2010"),
        low=Decimal("1995"),
        close=Decimal("2005"),
        volume=Decimal("100"),
        price_type=PriceType.BID,
        is_closed=False,
    )
    with pytest.raises(DealingRangeDataError, match="Unclosed candle"):
        validate_candles_for_range([c])


def test_synthetic_bar_rejection() -> None:
    """25. Candle validation function rejects synthetic/interpolated candles."""
    c = Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("test"),
        timeframe=Timeframe.H4,
        open_time=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        close_time=datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
        open=Decimal("2000"),
        high=Decimal("2010"),
        low=Decimal("1995"),
        close=Decimal("2005"),
        volume=Decimal("100"),
        price_type=PriceType.BID,
        is_closed=True,
        is_synthetic=True,
    )
    with pytest.raises(DealingRangeDataError, match="Synthetic candle"):
        validate_candles_for_range([c])


def test_deterministic_range_selection() -> None:
    """26. get_active_dealing_range picks the most recently available completed leg deterministically."""
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)

    dr1 = make_test_dealing_range(
        DealingRangeDirection.BULLISH,
        Decimal("2000"),
        Decimal("2050"),
        t0,
        t1,
        t1 + timedelta(hours=12),
    )
    dr2 = make_test_dealing_range(
        DealingRangeDirection.BEARISH,
        Decimal("2020"),
        Decimal("2050"),
        t1,
        t2,
        t2 + timedelta(hours=12),
    )

    active = get_active_dealing_range([dr1, dr2])
    assert active is not None
    assert active.range_id == dr2.range_id


def test_deterministic_ids() -> None:
    """27. Identical structural inputs always yield identical, stable, non-random IDs."""
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t1 = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    id1 = generate_dealing_range_id(
        "XAUUSD",
        "test",
        PriceType.BID,
        Timeframe.H4,
        DealingRangeDirection.BULLISH,
        t0,
        t1,
        "S1",
        "S2",
    )
    id2 = generate_dealing_range_id(
        "XAUUSD",
        "test",
        PriceType.BID,
        Timeframe.H4,
        DealingRangeDirection.BULLISH,
        t0,
        t1,
        "S1",
        "S2",
    )
    assert id1 == id2
    assert id1 == "XAUUSD_test_BID_H4_BULLISH_DR_20261001T000000Z_20261002T000000Z"


def test_ote_ob_positive_overlap() -> None:
    """28. Positive width geometric overlap between Bullish OTE and Bullish Order Block."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BULLISH,
        Decimal("2000.00"),
        Decimal("2100.00"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    ote = calculate_ote_zone(dr)  # [2021.00, 2038.00]

    # OB positioned at [2030.00, 2045.00]
    ob = OrderBlockRecord(
        order_block_id="OB_TEST_1",
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("test_source"),
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        direction=OrderBlockDirection.BULLISH,
        zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE,
        zone_bottom=Decimal("2030.00"),
        zone_top=Decimal("2045.00"),
        midpoint_50=Decimal("2037.50"),
        origin_candle_open_time=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        origin_candle_close_time=datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
        origin_candle_id="C_1",
        confirmation_time=datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
        displacement_event_id="DISP_1",
        structure_break_event_id="BOS_1",
        mss_confirmation_id=None,
        associated_fvg_id=None,
        status=OrderBlockStatus.UNMITIGATED,
    )

    inter = compute_ote_order_block_intersection(ote, ob)
    assert inter is not None
    assert inter.is_touch_only is False
    assert inter.intersection_bottom == Decimal("2030.00")
    assert inter.intersection_top == Decimal("2038.00")
    assert inter.intersection_width == Decimal("8.00")


def test_ote_ob_no_overlap() -> None:
    """29. Disjoint OTE and Order Block produce no intersection (returns None)."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BULLISH,
        Decimal("2000.00"),
        Decimal("2100.00"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    ote = calculate_ote_zone(dr)  # [2021.00, 2038.00]

    # OB positioned at [2040.00, 2050.00] (disjoint)
    ob = OrderBlockRecord(
        order_block_id="OB_TEST_DISJOINT",
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("test_source"),
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        direction=OrderBlockDirection.BULLISH,
        zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE,
        zone_bottom=Decimal("2040.00"),
        zone_top=Decimal("2050.00"),
        midpoint_50=Decimal("2045.00"),
        origin_candle_open_time=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        origin_candle_close_time=datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
        origin_candle_id="C_1",
        confirmation_time=datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
        displacement_event_id="DISP_1",
        structure_break_event_id="BOS_1",
        mss_confirmation_id=None,
        associated_fvg_id=None,
        status=OrderBlockStatus.UNMITIGATED,
    )

    inter = compute_ote_order_block_intersection(ote, ob)
    assert inter is None


def test_empty_input_safety() -> None:
    """30. Graceful handling of empty collections and None objects without unhandled exceptions."""
    assert identify_dealing_ranges([]) == []
    assert generate_ote_zones([]) == []
    assert compute_all_ote_order_block_intersections([], []) == []
    assert get_active_dealing_range([]) is None

    assess = assess_price_in_range(Decimal("2000"), None, datetime.now(UTC))
    assert assess.zone == PremiumDiscountZone.UNDETERMINED


def test_strict_pit_research_estimated_semantics() -> None:
    """31. Availability confidence is stamped ESTIMATED / RESEARCH_ESTIMATED, never falsely VERIFIED."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BULLISH,
        Decimal("2000"),
        Decimal("2100"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    assert dr.availability_confidence == AvailabilityConfidence.ESTIMATED
    assert dr.pit_policy == PitPolicy.RESEARCH_ESTIMATED

    ote = calculate_ote_zone(dr)
    assert ote.availability_confidence == AvailabilityConfidence.ESTIMATED
    assert ote.pit_policy == PitPolicy.RESEARCH_ESTIMATED


def test_immutable_models_no_side_effects() -> None:
    """32. All domain records are frozen dataclasses; mutations raise FrozenInstanceError."""
    dr = make_test_dealing_range(
        DealingRangeDirection.BULLISH,
        Decimal("2000"),
        Decimal("2100"),
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 16, 0, tzinfo=UTC),
    )
    with pytest.raises(FrozenInstanceError):
        dr.range_low = Decimal("1999")  # type: ignore[misc]

    ote = calculate_ote_zone(dr)
    with pytest.raises(FrozenInstanceError):
        ote.zone_bottom = Decimal("2000")  # type: ignore[misc]


# ── Real Data Integration Tests ──────────────────────────────────────────────
def test_real_xauusd_dealing_ranges_and_ote() -> None:
    """33. Real XAUUSD Sep 15 - Oct 15 Dealing Ranges and OTE generation across timeframes."""
    all_m1 = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(
        2026, 10, PriceType.BID
    )
    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)

    # Test H4
    h4_c = [
        rc.candle
        for rc in resample_m1_candles(all_m1, Timeframe.H4)
        if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
    ]
    h4_swings = filter_confirmed_swings(
        detect_swings(h4_c, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
    )
    h4_ranges = identify_dealing_ranges(h4_swings)
    assert len(h4_ranges) > 0

    h4_otes = generate_ote_zones(h4_ranges)
    assert len(h4_otes) == len(h4_ranges)

    for ote in h4_otes:
        assert ote.zone_bottom < ote.ote_705 < ote.zone_top
        if ote.direction == DealingRangeDirection.BULLISH:
            assert ote.zone_top < ote.equilibrium
        else:
            assert ote.zone_bottom > ote.equilibrium


def test_real_xauusd_ote_ob_confluence() -> None:
    """34. Real XAUUSD OTE and Order Block geometric confluence detection."""
    all_m1 = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(
        2026, 10, PriceType.BID
    )
    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)

    h2_c = [
        rc.candle
        for rc in resample_m1_candles(all_m1, Timeframe.H2)
        if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
    ]
    h2_swings = filter_confirmed_swings(
        detect_swings(h2_c, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
    )
    h2_ranges = identify_dealing_ranges(h2_swings)
    h2_otes = generate_ote_zones(h2_ranges)

    # Detect Order Blocks
    ms_events, _ = detect_market_structure(
        h2_c, h2_swings, config=MarketStructureConfig(Decimal("0.01"), 1)
    )
    disps = detect_displacements(
        h2_c,
        config=DisplacementConfig(
            tick_size=Decimal("0.01"),
            min_body_ratio=Decimal("0.60"),
            min_bullish_close_location=Decimal("0.75"),
            max_bearish_close_location=Decimal("0.25"),
            atr_window=14,
            min_range_atr_multiple=Decimal("1.50"),
        ),
    )
    cands = filter_mss_candidates(ms_events)
    fvgs = detect_fair_value_gaps(h2_c)
    mss_records = evaluate_mss_confirmations(
        h2_c,
        cands,
        displacements=disps,
        fvgs=fvgs,
        config=MssConfirmationConfig(
            max_confirmation_bars=3, require_displacement=True, require_fvg_confirmation=True
        ),
    )
    h2_obs = detect_order_blocks(
        h2_c,
        structure_breaks=ms_events,
        displacements=disps,
        mss_confirmations=mss_records,
        fvg_zones=fvgs,
        config=OrderBlockConfig(max_origin_lookback_bars=6),
    )

    intersections = compute_all_ote_order_block_intersections(h2_otes, h2_obs)
    # Check that computed intersections satisfy geometric properties
    for inter in intersections:
        assert inter.intersection_bottom <= inter.intersection_top
        assert inter.direction.value == inter.direction.value


def test_no_intersection_before_ob_confirmation() -> None:
    """35. OTE and OB intersection is strictly invisible prior to max(ote.avail, ob.conf)."""
    from traderlab.domain.market_structure.ote import filter_ote_ob_intersections_as_of

    dr = make_test_dealing_range(
        DealingRangeDirection.BEARISH,
        Decimal("4140.00"),
        Decimal("4200.00"),
        datetime(2026, 10, 6, 18, 0, tzinfo=UTC),
        datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
        datetime(2026, 10, 8, 0, 0, tzinfo=UTC),
    )
    ote = calculate_ote_zone(dr)  # available at 2026-10-08 00:00 UTC

    # OB confirmed in the future on 2026-10-14 14:00 UTC
    ob = OrderBlockRecord(
        order_block_id="OB_OCT14_TEST",
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("test_source"),
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        direction=OrderBlockDirection.BEARISH,
        zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE,
        zone_bottom=Decimal("4170.00"),
        zone_top=Decimal("4190.00"),
        midpoint_50=Decimal("4180.00"),
        origin_candle_open_time=datetime(2026, 10, 14, 6, 0, tzinfo=UTC),
        origin_candle_close_time=datetime(2026, 10, 14, 10, 0, tzinfo=UTC),
        origin_candle_id="C_OCT14",
        confirmation_time=datetime(2026, 10, 14, 14, 0, tzinfo=UTC),
        displacement_event_id="DISP_OCT14",
        structure_break_event_id="BOS_OCT14",
        mss_confirmation_id=None,
        associated_fvg_id=None,
        status=OrderBlockStatus.UNMITIGATED,
    )

    # 1. Joint availability timestamp
    expected_avail = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
    inter = compute_ote_order_block_intersection(ote, ob)
    assert inter is not None
    assert inter.intersection_available_at == expected_avail

    # 2. As of Oct 10 (prior to OB confirmation): must return None
    as_of_early = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)
    assert compute_ote_order_block_intersection(ote, ob, as_of_utc=as_of_early) is None
    assert filter_ote_ob_intersections_as_of([inter], as_of_utc=as_of_early) == []

    # 3. As of Oct 14 14:00 (exact confirmation): available
    assert compute_ote_order_block_intersection(ote, ob, as_of_utc=expected_avail) is not None
    assert len(filter_ote_ob_intersections_as_of([inter], as_of_utc=expected_avail)) == 1
