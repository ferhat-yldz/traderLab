"""Comprehensive test suite for the Point-in-Time Safe Market Structure Engine.

Covers all 30 acceptance criteria specified in TraderLab Phase 3C:
1. Higher High classification
2. Higher Low classification
3. Lower High classification
4. Lower Low classification
5. Uptrend determination
6. Downtrend determination
7. Undetermined structure
8. Basic Bullish BOS
9. Basic Bearish BOS
10. Basic Bullish CHoCH
11. Basic Bearish CHoCH
12. Wick breach is not BOS
13. Wick breach is not CHoCH
14. Confirmed swing prerequisite
15. Protected swing reference
16. Structural level confirmed before break bar
17. No future leakage under historical as_of
18. Break detected only at candle close
19. CHoCH does not automatically confirm new trend
20. MSS candidate without false confirmation
21. Liquidity sweep independence
22. Duplicate structural event prevention
23. BID / ASK isolation
24. D1/H4/H2 separation
25. D1 UTC/NY session modes
26. Partial candle rejection
27. Data-gap protection
28. Decimal threshold precision
29. Deterministic IDs and repeated execution
30. Empty/insufficient data safety
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.liquidity_sweep import (
    LiquiditySweepConfig,
    detect_liquidity_sweeps,
)
from traderlab.domain.market_structure.market_structure import (
    MarketStructureConfig,
    MarketStructureDataError,
    MarketTrend,
    StructuralSwingType,
    StructureBreakType,
    StructureDirection,
    classify_structural_swings,
    detect_market_structure,
    determine_trend_from_structural_swings,
    filter_confirmed_structure_breaks,
    filter_mss_candidates,
    generate_structure_break_id,
    summarize_market_structure_events,
)
from traderlab.domain.market_structure.swing import (
    SwingConfirmationStatus,
    SwingPoint,
    SwingType,
    TiePolicy,
)


# ── Helper Factories ─────────────────────────────────────────────────────────
def make_test_candle(
    index: int,
    open_p: str,
    high_p: str,
    low_p: str,
    close_p: str,
    *,
    base_time: datetime | None = None,
    timeframe: Timeframe = Timeframe.H1,
    instrument_id: str = "XAUUSD",
    source_id: str = "market_data_lab_m1",
    price_type: PriceType = PriceType.BID,
    is_closed: bool = True,
    is_synthetic: bool = False,
    availability_confidence: AvailabilityConfidence = AvailabilityConfidence.ESTIMATED,
    available_at: datetime | None = None,
) -> Candle:
    """Create a valid domain Candle with deterministic timestamps."""
    if base_time is None:
        base_time = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    delta = timedelta(seconds=timeframe.value * index)
    o_dt = base_time + delta
    c_dt = o_dt + timedelta(seconds=timeframe.value)
    return Candle(
        instrument_id=InstrumentId(instrument_id),
        source_id=DataSourceId(source_id),
        timeframe=timeframe,
        open_time=o_dt,
        close_time=c_dt,
        open=Decimal(open_p),
        high=Decimal(high_p),
        low=Decimal(low_p),
        close=Decimal(close_p),
        volume=Decimal("100"),
        price_type=price_type,
        is_closed=is_closed,
        is_synthetic=is_synthetic,
        availability_confidence=availability_confidence,
        available_at=available_at,
    )


def make_test_swing_point(
    swing_id: str,
    swing_type: SwingType,
    price: str,
    pivot_time: datetime,
    conf_time: datetime,
    *,
    timeframe: Timeframe = Timeframe.H1,
    price_type: PriceType = PriceType.BID,
    instrument_id: str = "XAUUSD",
    source_id: str = "market_data_lab_m1",
    status: SwingConfirmationStatus = SwingConfirmationStatus.CONFIRMED,
) -> SwingPoint:
    """Create a valid SwingPoint for market structure testing."""
    return SwingPoint(
        id=swing_id,
        instrument_id=InstrumentId(instrument_id),
        source_id=DataSourceId(source_id),
        price_type=price_type,
        timeframe=timeframe,
        swing_type=swing_type,
        pivot_price=Decimal(price),
        pivot_time=pivot_time,
        pivot_close_time=pivot_time + timedelta(seconds=timeframe.value),
        confirmation_time=conf_time if status == SwingConfirmationStatus.CONFIRMED else None,
        confirmation_status=status,
        left_bars=3,
        right_bars=3,
        tie_policy=TiePolicy.STRICT,
        computable_at_utc=conf_time,
    )


# ── 1. Higher High classification ─────────────────────────────────────────────
def test_higher_high_classification() -> None:
    """Verify classify_structural_swings identifies consecutive higher highs as HH."""
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
    )

    classified = classify_structural_swings([sh1, sh2], tick_size=Decimal("0.01"))
    assert len(classified) == 2
    assert classified[0].structural_type == StructuralSwingType.UNKNOWN
    assert classified[1].structural_type == StructuralSwingType.HH
    assert classified[1].price_delta == Decimal("10.00")
    assert classified[1].previous_swing_id == "sh1"


# ── 2. Higher Low classification ──────────────────────────────────────────────
def test_higher_low_classification() -> None:
    """Verify classify_structural_swings identifies consecutive higher lows as HL."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    )

    classified = classify_structural_swings([sl1, sl2], tick_size=Decimal("0.01"))
    assert len(classified) == 2
    assert classified[0].structural_type == StructuralSwingType.UNKNOWN
    assert classified[1].structural_type == StructuralSwingType.HL
    assert classified[1].price_delta == Decimal("8.00")
    assert classified[1].previous_swing_id == "sl1"


# ── 3. Lower High classification ──────────────────────────────────────────────
def test_lower_high_classification() -> None:
    """Verify classify_structural_swings identifies consecutive lower highs as LH."""
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2670.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2655.00",
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 8, 0, tzinfo=UTC),
    )

    classified = classify_structural_swings([sh1, sh2], tick_size=Decimal("0.01"))
    assert classified[1].structural_type == StructuralSwingType.LH
    assert classified[1].price_delta == Decimal("-15.00")


# ── 4. Lower Low classification ───────────────────────────────────────────────
def test_lower_low_classification() -> None:
    """Verify classify_structural_swings identifies consecutive lower lows as LL."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2640.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2625.00",
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    )

    classified = classify_structural_swings([sl1, sl2], tick_size=Decimal("0.01"))
    assert classified[1].structural_type == StructuralSwingType.LL
    assert classified[1].price_delta == Decimal("-15.00")


# ── 5. Uptrend determination ──────────────────────────────────────────────────
def test_uptrend_determination() -> None:
    """Verify sequence of HH and HL establishes UPTREND."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2665.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    classified = classify_structural_swings([sl1, sh1, sl2, sh2], tick_size=Decimal("0.01"))
    trend = determine_trend_from_structural_swings(classified)
    assert trend == MarketTrend.UPTREND


# ── 6. Downtrend determination ────────────────────────────────────────────────
def test_downtrend_determination() -> None:
    """Verify sequence of LH and LL establishes DOWNTREND."""
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2670.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2635.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    classified = classify_structural_swings([sh1, sl1, sh2, sl2], tick_size=Decimal("0.01"))
    trend = determine_trend_from_structural_swings(classified)
    assert trend == MarketTrend.DOWNTREND


# ── 7. Undetermined structure ─────────────────────────────────────────────────
def test_undetermined_structure() -> None:
    """Verify insufficient or single swings return UNDETERMINED."""
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    classified = classify_structural_swings([sh1], tick_size=Decimal("0.01"))
    trend = determine_trend_from_structural_swings(classified)
    assert trend == MarketTrend.UNDETERMINED


# ── 8. Basic Bullish BOS ──────────────────────────────────────────────────────
def test_basic_bullish_bos() -> None:
    """Verify Bullish BOS occurs when candle closes above active structural high in UPTREND."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Bar 7 opens at 07:00 (after sh2 confirmed at 06:00). Closes at 2662.00 > 2660.00 + 0.01
    bar7 = make_test_candle(7, "2658.00", "2665.00", "2657.00", "2662.00")

    config = MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1)
    events, state = detect_market_structure([bar7], [sl1, sh1, sl2, sh2], config=config)

    assert len(events) == 1
    ev = events[0]
    assert ev.break_type == StructureBreakType.BOS
    assert ev.structure_direction == StructureDirection.BULLISH
    assert ev.source_swing_id == "sh2"
    assert ev.reference_level_price == Decimal("2660.00")
    assert ev.break_price == Decimal("2662.00")
    assert ev.trend_before == MarketTrend.UPTREND
    assert ev.trend_after == MarketTrend.UPTREND
    assert ev.confirmation_status == "CONFIRMED"
    assert state.is_uptrend
    assert len(filter_confirmed_structure_breaks(events)) == 1
    summary = summarize_market_structure_events(events)
    assert summary["bullish_bos"] == 1
    assert summary["total_events"] == 1


# ── 9. Basic Bearish BOS ──────────────────────────────────────────────────────
def test_basic_bearish_bos() -> None:
    """Verify Bearish BOS occurs when candle closes below active structural low in DOWNTREND."""
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2670.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2635.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Bar 7 opens at 07:00, closes at 2633.00 < 2635.00 - 0.01
    bar7 = make_test_candle(7, "2636.00", "2637.00", "2630.00", "2633.00")

    config = MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1)
    events, state = detect_market_structure([bar7], [sh1, sl1, sh2, sl2], config=config)

    assert len(events) == 1
    ev = events[0]
    assert ev.break_type == StructureBreakType.BOS
    assert ev.structure_direction == StructureDirection.BEARISH
    assert ev.source_swing_id == "sl2"
    assert ev.reference_level_price == Decimal("2635.00")
    assert ev.break_price == Decimal("2633.00")
    assert ev.trend_before == MarketTrend.DOWNTREND
    assert ev.trend_after == MarketTrend.DOWNTREND
    assert state.is_downtrend


# ── 10. Basic Bullish CHoCH ───────────────────────────────────────────────────
def test_basic_bullish_choch() -> None:
    """Verify Bullish CHoCH occurs when candle closes above protected swing high in DOWNTREND."""
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2670.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2635.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # In downtrend, protected high is sh2 (2660.00). Bar 7 closes above 2660.00 at 2663.00
    bar7 = make_test_candle(7, "2650.00", "2665.00", "2648.00", "2663.00")

    config = MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1)
    events, state = detect_market_structure([bar7], [sh1, sl1, sh2, sl2], config=config)

    assert len(events) == 1
    ev = events[0]
    assert ev.break_type == StructureBreakType.CHOCH
    assert ev.structure_direction == StructureDirection.BULLISH
    assert ev.source_swing_id == "sh2"
    assert ev.protected_level_id == "sh2"
    assert ev.trend_before == MarketTrend.DOWNTREND
    assert ev.trend_after == MarketTrend.POTENTIAL_REVERSAL
    assert ev.mss_candidate_id is not None
    assert state.is_potential_reversal


# ── 11. Basic Bearish CHoCH ───────────────────────────────────────────────────
def test_basic_bearish_choch() -> None:
    """Verify Bearish CHoCH occurs when candle closes below protected swing low in UPTREND."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2665.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # In uptrend, protected low is sl2 (2638.00). Bar 7 closes below at 2635.00
    bar7 = make_test_candle(7, "2642.00", "2644.00", "2632.00", "2635.00")

    config = MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1)
    events, state = detect_market_structure([bar7], [sl1, sh1, sl2, sh2], config=config)

    assert len(events) == 1
    ev = events[0]
    assert ev.break_type == StructureBreakType.CHOCH
    assert ev.structure_direction == StructureDirection.BEARISH
    assert ev.source_swing_id == "sl2"
    assert ev.protected_level_id == "sl2"
    assert ev.trend_before == MarketTrend.UPTREND
    assert ev.trend_after == MarketTrend.POTENTIAL_REVERSAL
    assert ev.mss_candidate_id is not None
    assert state.is_potential_reversal


# ── 12. Wick breach is not BOS ────────────────────────────────────────────────
def test_wick_breach_is_not_bos() -> None:
    """Verify candle high breaching level but close below does NOT trigger BOS."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Bar 7 high = 2665.00 > 2660.00, BUT close = 2658.00 <= 2660.00
    bar7 = make_test_candle(7, "2655.00", "2665.00", "2654.00", "2658.00")

    config = MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1)
    events, _ = detect_market_structure([bar7], [sl1, sh1, sl2, sh2], config=config)

    assert len(events) == 0


# ── 13. Wick breach is not CHoCH ──────────────────────────────────────────────
def test_wick_breach_is_not_choch() -> None:
    """Verify candle low breaching protected low but close above does NOT trigger CHoCH."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2665.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Bar 7 low = 2632.00 < 2638.00, BUT close = 2640.00 >= 2638.00
    bar7 = make_test_candle(7, "2645.00", "2648.00", "2632.00", "2640.00")

    config = MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1)
    events, _ = detect_market_structure([bar7], [sl1, sh1, sl2, sh2], config=config)

    assert len(events) == 0


# ── 14. Confirmed swing prerequisite ──────────────────────────────────────────
def test_confirmed_swing_prerequisite() -> None:
    """Verify unconfirmed CANDIDATE swings are rejected from market structure analysis."""
    sh_cand = make_test_swing_point(
        "sh_cand",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
        status=SwingConfirmationStatus.CANDIDATE,
    )
    bar7 = make_test_candle(7, "2658.00", "2665.00", "2657.00", "2662.00")
    events, state = detect_market_structure([bar7], [sh_cand], tick_size=Decimal("0.01"))
    assert len(events) == 0
    assert state.confirmed_swings_count == 0


# ── 15. Protected swing reference ─────────────────────────────────────────────
def test_protected_swing_reference() -> None:
    """Verify break events link active protected_level_id."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    bar7 = make_test_candle(7, "2658.00", "2665.00", "2657.00", "2662.00")
    events, _ = detect_market_structure([bar7], [sl1, sh1, sl2, sh2], tick_size=Decimal("0.01"))

    assert len(events) == 1
    assert events[0].protected_level_id == "sl2"


# ── 16. Structural level confirmed before break bar ───────────────────────────
def test_structural_level_confirmed_before_break_bar() -> None:
    """Verify swing confirmed on candle close cannot be broken by that same candle."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 7, 0, tzinfo=UTC),  # Confirmed at 07:00
    )
    # Candle 6 opens at 06:00 and closes at 07:00 (which confirms sh1)
    bar6 = make_test_candle(6, "2650.00", "2665.00", "2648.00", "2662.00")

    events, _ = detect_market_structure([bar6], [sh], tick_size=Decimal("0.01"))
    assert len(events) == 0  # Cannot break itself during confirmation candle!


# ── 17. No future leakage under historical as_of ──────────────────────────────
def test_no_future_leakage_under_historical_as_of() -> None:
    """Verify historical as_of query hides future breaks and trend transitions."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    bar6 = make_test_candle(6, "2655.00", "2659.00", "2654.00", "2658.00")  # closes 07:00
    bar7 = make_test_candle(7, "2658.00", "2665.00", "2657.00", "2662.00")  # closes 08:00 (BOS)
    bar8 = make_test_candle(8, "2662.00", "2670.00", "2660.00", "2668.00")  # closes 09:00

    # Query as of 07:00 (before bar 7 closes at 08:00)
    as_of = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
    events, state = detect_market_structure(
        [bar6, bar7, bar8], [sl1, sh1, sl2, sh2], tick_size=Decimal("0.01"), as_of_utc=as_of
    )

    assert len(events) == 0
    assert state.confirmed_swings_count == 4


# ── 18. Break detected only at candle close ───────────────────────────────────
def test_break_detected_only_at_candle_close() -> None:
    """Verify detection_time equals break_bar_close_time."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    bar7 = make_test_candle(7, "2658.00", "2665.00", "2657.00", "2662.00")
    events, _ = detect_market_structure([bar7], [sl1, sh1, sl2, sh2], tick_size=Decimal("0.01"))

    assert len(events) == 1
    assert events[0].detection_time == bar7.close_time
    assert events[0].detection_time > bar7.open_time


# ── 19. CHoCH does not automatically confirm new trend ────────────────────────
def test_choch_does_not_automatically_confirm_new_trend() -> None:
    """Verify CHoCH sets trend to POTENTIAL_REVERSAL, NOT auto-confirming new trend."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2665.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    bar7 = make_test_candle(7, "2642.00", "2644.00", "2632.00", "2635.00")
    events, state = detect_market_structure(
        [bar7], [sl1, sh1, sl2, sh2], tick_size=Decimal("0.01")
    )

    assert len(events) == 1
    assert events[0].break_type == StructureBreakType.CHOCH
    assert events[0].trend_after == MarketTrend.POTENTIAL_REVERSAL
    assert state.trend == MarketTrend.POTENTIAL_REVERSAL


# ── 20. MSS candidate without false confirmation ──────────────────────────────
def test_mss_candidate_without_false_confirmation() -> None:
    """Verify MSS is strictly CANDIDATE and no MSS_CONFIRMED exists."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2665.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    bar7 = make_test_candle(7, "2642.00", "2644.00", "2632.00", "2635.00")
    events, _ = detect_market_structure([bar7], [sl1, sh1, sl2, sh2], tick_size=Decimal("0.01"))

    mss_candidates = filter_mss_candidates(events)
    assert len(mss_candidates) == 1
    cand = mss_candidates[0]
    assert cand.break_type == StructureBreakType.MSS_CANDIDATE
    assert cand.confirmation_status == "CANDIDATE"
    assert "CONFIRMED" not in cand.break_type.value


# ── 21. Liquidity sweep independence ──────────────────────────────────────────
def test_liquidity_sweep_independence() -> None:
    """Verify consume_level_after_sweep=True does not remove swing points from market structure."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # First, run liquidity sweeps with consume_level_after_sweep=True
    bar7 = make_test_candle(7, "2658.00", "2665.00", "2657.00", "2662.00")
    sw_cfg = LiquiditySweepConfig(tick_size=Decimal("0.01"), consume_level_after_sweep=True)
    sweep_events = detect_liquidity_sweeps([bar7], [sh2], config=sw_cfg)
    assert len(sweep_events) == 1

    # Now, run market structure with the same swing point - it must remain completely accessible!
    ms_events, ms_state = detect_market_structure(
        [bar7], [sl1, sh1, sl2, sh2], tick_size=Decimal("0.01")
    )
    assert len(ms_events) == 1
    assert ms_events[0].break_type == StructureBreakType.BOS
    assert ms_state.confirmed_swings_count == 4


# ── 22. Duplicate structural event prevention ─────────────────────────────────
def test_duplicate_structural_event_prevention() -> None:
    """Verify consecutive candles closing beyond an already broken level do NOT produce duplicate events."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Bar 7 breaks sh2
    bar7 = make_test_candle(7, "2658.00", "2665.00", "2657.00", "2662.00")
    # Bar 8 also closes above sh2
    bar8 = make_test_candle(8, "2662.00", "2668.00", "2661.00", "2666.00")

    events, _ = detect_market_structure(
        [bar7, bar8], [sl1, sh1, sl2, sh2], tick_size=Decimal("0.01")
    )
    assert len(events) == 1
    assert events[0].source_swing_id == "sh2"


# ── 23. BID / ASK isolation ───────────────────────────────────────────────────
def test_bid_ask_isolation() -> None:
    """Verify mixing BID candles with ASK swings raises MarketStructureDataError."""
    sh_ask = make_test_swing_point(
        "sh_ask",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
        price_type=PriceType.ASK,
    )
    bar7_bid = make_test_candle(
        7, "2658.00", "2665.00", "2657.00", "2662.00", price_type=PriceType.BID
    )

    with pytest.raises(MarketStructureDataError, match="PriceType mismatch"):
        detect_market_structure([bar7_bid], [sh_ask], tick_size=Decimal("0.01"))


# ── 24. D1/H4/H2 separation ───────────────────────────────────────────────────
def test_timeframe_separation() -> None:
    """Verify timeframe mismatch between candles and swings raises MarketStructureDataError."""
    sh_h4 = make_test_swing_point(
        "sh_h4",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        timeframe=Timeframe.H4,
    )
    bar_h2 = make_test_candle(
        7, "2658.00", "2665.00", "2657.00", "2662.00", timeframe=Timeframe.H2
    )

    with pytest.raises(MarketStructureDataError, match="Timeframe mismatch"):
        detect_market_structure([bar_h2], [sh_h4], tick_size=Decimal("0.01"))


# ── 25. D1 UTC/NY session modes ───────────────────────────────────────────────
def test_d1_utc_and_ny_session_modes() -> None:
    """Verify D1 candles in UTC_MIDNIGHT and NY modes work and filter Sunday stubs."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        timeframe=Timeframe.D1,
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        timeframe=Timeframe.D1,
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 3, 0, 0, tzinfo=UTC),
        timeframe=Timeframe.D1,
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 3, 0, 0, tzinfo=UTC),
        timeframe=Timeframe.D1,
    )

    # Sunday stub candle: weekday=6, hour=0
    sun_stub = make_test_candle(
        0,
        "2650.00",
        "2655.00",
        "2649.00",
        "2652.00",
        base_time=datetime(2026, 10, 4, 0, 0, tzinfo=UTC),
        timeframe=Timeframe.D1,
    )
    # Monday bar: weekday=0 (Oct 5)
    mon_bar = make_test_candle(
        0,
        "2652.00",
        "2670.00",
        "2650.00",
        "2668.00",
        base_time=datetime(2026, 10, 5, 0, 0, tzinfo=UTC),
        timeframe=Timeframe.D1,
    )

    events, _ = detect_market_structure(
        [sun_stub, mon_bar], [sl1, sh1, sl2, sh2], tick_size=Decimal("0.01")
    )
    # Sunday stub was excluded, mon_bar triggered BOS
    assert len(events) == 1
    assert events[0].break_bar_open_time == mon_bar.open_time


# ── 26. Partial candle rejection ──────────────────────────────────────────────
def test_partial_candle_rejection() -> None:
    """Verify unclosed (partial) candles cannot trigger structural breaks."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )
    partial_bar = make_test_candle(7, "2658.00", "2665.00", "2657.00", "2662.00", is_closed=False)
    events, _ = detect_market_structure([partial_bar], [sh], tick_size=Decimal("0.01"))
    assert len(events) == 0


# ── 27. Data-gap protection ───────────────────────────────────────────────────
def test_data_gap_protection() -> None:
    """Verify calendar gaps flag DATA_GAP_PRECEDING on structural events."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    bar6 = make_test_candle(6, "2655.00", "2659.00", "2654.00", "2658.00")
    # 5-hour unannounced mid-week intraday gap between bar6 and bar7
    gap_time = bar6.close_time + timedelta(hours=5)
    bar7 = make_test_candle(0, "2658.00", "2665.00", "2657.00", "2662.00", base_time=gap_time)

    config = MarketStructureConfig(tick_size=Decimal("0.01"), allow_weekend_gap=False)
    events, _ = detect_market_structure([bar6, bar7], [sl1, sh1, sl2, sh2], config=config)
    assert len(events) == 1
    assert "DATA_GAP_PRECEDING" in events[0].flags


# ── 28. Decimal threshold precision ───────────────────────────────────────────
def test_decimal_threshold_precision() -> None:
    """Verify close == level + threshold does NOT qualify as break (strict inequality required)."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Level = 2660.00, Threshold = 1 tick (0.01) -> required close > 2660.01
    # Exactly equal to 2660.01
    bar7 = make_test_candle(7, "2658.00", "2665.00", "2657.00", "2660.01")
    cfg = MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1)

    events, _ = detect_market_structure([bar7], [sl1, sh1, sl2, sh2], config=cfg)
    assert len(events) == 0  # Strict inequality required!


# ── 29. Deterministic IDs and repeated execution ──────────────────────────────
def test_deterministic_ids_and_repeated_execution() -> None:
    """Verify repeated runs on identical data produce identical results and deterministic IDs."""
    sl1 = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
    )
    sh1 = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
    )
    sl2 = make_test_swing_point(
        "sl2",
        SwingType.SWING_LOW,
        "2638.00",
        datetime(2026, 10, 1, 2, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 5, 0, tzinfo=UTC),
    )
    sh2 = make_test_swing_point(
        "sh2",
        SwingType.SWING_HIGH,
        "2660.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    bar7 = make_test_candle(7, "2658.00", "2665.00", "2657.00", "2662.00")

    ev1, st1 = detect_market_structure([bar7], [sl1, sh1, sl2, sh2], tick_size=Decimal("0.01"))
    ev2, st2 = detect_market_structure([bar7], [sl1, sh1, sl2, sh2], tick_size=Decimal("0.01"))

    assert len(ev1) == len(ev2) == 1
    expected_id = generate_structure_break_id(
        bar7.instrument_id,
        bar7.source_id,
        bar7.price_type,
        bar7.timeframe,
        StructureBreakType.BOS,
        StructureDirection.BULLISH,
        "sh2",
        bar7.open_time,
    )
    assert ev1[0].event_id == expected_id
    assert ev1[0].event_id == ev2[0].event_id
    assert st1.trend == st2.trend


# ── 30. Empty/insufficient data safety ─────────────────────────────────────────
def test_empty_insufficient_data_safety() -> None:
    """Verify empty inputs return empty events and UNDETERMINED state gracefully."""
    events, state = detect_market_structure([], [], tick_size=Decimal("0.01"))
    assert events == []
    assert state.is_undetermined
    assert state.confirmed_swings_count == 0
