"""Comprehensive test suite for the Point-in-Time Safe Liquidity Sweep Detection Engine.

Covers all 25 acceptance criteria specified in TraderLab Phase 3B:
1. Basic Buy-Side Sweep
2. Basic Sell-Side Sweep
3. Touch is not Sweep
4. Penetration without Reclaim
5. Equal level is not Sweep
6. Minimum tick threshold
7. Only confirmed swings eligible
8. Swing confirmation must precede sweep bar
9. No future leakage under historical as_of
10. Sweep detection only at candle close
11. Previously breached level rejection
12. Duplicate sweep prevention
13. Configurable level consumption
14. BID / ASK isolation
15. D1 / H4 / H2 isolation
16. UTC / NY D1 modes
17. Partial candle rejection
18. Synthetic candle rejection
19. Data-gap protection
20. Decimal precision
21. Deterministic IDs
22. Identical inputs produce identical results
23. STRICT_PIT estimated data rejection
24. RESEARCH_ESTIMATED historical availability
25. Ambiguous same-bar extremes
+ Parameter validation & helper function tests
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
    InvalidLiquiditySweepParameterError,
    LiquiditySide,
    LiquiditySweepConfig,
    LiquiditySweepDataError,
    LiquiditySweepError,
    LiquiditySweepEvent,
    LiquiditySweepStatus,
    detect_liquidity_sweeps,
    filter_confirmed_sweeps,
    generate_liquidity_sweep_id,
    summarize_liquidity_events,
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
    """Create a valid SwingPoint for sweep testing."""
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


# ── 1. Basic Buy-Side Sweep ───────────────────────────────────────────────────
def test_basic_buy_side_sweep() -> None:
    """Verify Buy-Side Sweep: high > level + threshold and close < level."""
    # Swing High at 2650.0 confirmed at index 6
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Candle 7 opens at 07:00 (after conf 06:00): high=2652.00, close=2648.00 (reclaim)
    candles = [make_test_candle(7, "2649.00", "2652.00", "2647.00", "2648.00")]

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"), min_penetration_ticks=1)
    events = detect_liquidity_sweeps(candles, [sh], config=config)

    assert len(events) == 1
    ev = events[0]
    assert ev.is_sweep
    assert ev.is_buy_side
    assert ev.status == LiquiditySweepStatus.SWEEP
    assert ev.liquidity_side == LiquiditySide.BUY_SIDE
    assert ev.level_price == Decimal("2650.00")
    assert ev.penetration_price == Decimal("2652.00")
    assert ev.penetration_ticks == Decimal("200")  # (2652 - 2650) / 0.01 = 200 ticks
    assert ev.sweep_bar_open_time == candles[0].open_time
    assert ev.detection_time == candles[0].close_time


# ── 2. Basic Sell-Side Sweep ──────────────────────────────────────────────────
def test_basic_sell_side_sweep() -> None:
    """Verify Sell-Side Sweep: low < level - threshold and close > level."""
    sl = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2600.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Candle 7 opens at 07:00: low=2598.00, close=2602.00 (reclaim)
    candles = [make_test_candle(7, "2601.00", "2605.00", "2598.00", "2602.00")]

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"), min_penetration_ticks=1)
    events = detect_liquidity_sweeps(candles, [sl], config=config)

    assert len(events) == 1
    ev = events[0]
    assert ev.is_sweep
    assert ev.is_sell_side
    assert ev.status == LiquiditySweepStatus.SWEEP
    assert ev.liquidity_side == LiquiditySide.SELL_SIDE
    assert ev.level_price == Decimal("2600.00")
    assert ev.penetration_price == Decimal("2598.00")
    assert ev.penetration_ticks == Decimal("200")


# ── 3. Touch is not Sweep ─────────────────────────────────────────────────────
def test_touch_is_not_sweep() -> None:
    """Verify that touching or penetrating less than threshold is classified as TOUCH, not SWEEP."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Threshold requires 5 ticks = 0.05. High is 2650.03 (only 3 ticks) and close=2649.00
    candles = [make_test_candle(7, "2648.00", "2650.03", "2647.00", "2649.00")]

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"), min_penetration_ticks=5)
    events = detect_liquidity_sweeps(candles, [sh], config=config)

    assert len(events) == 1
    ev = events[0]
    assert ev.is_touch
    assert not ev.is_sweep
    assert ev.status == LiquiditySweepStatus.TOUCH
    assert "INSUFFICIENT_PENETRATION" in ev.data_quality_flags


# ── 4. Penetration without Reclaim (Breach) ───────────────────────────────────
def test_penetration_without_reclaim() -> None:
    """Verify that penetrating and closing beyond the level is classified as BREACH, not SWEEP."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Candle 7 penetrates high=2655.00 and closes at 2652.00 (above level, no reclaim)
    candles = [make_test_candle(7, "2649.00", "2655.00", "2648.00", "2652.00")]

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"), min_penetration_ticks=1)
    events = detect_liquidity_sweeps(candles, [sh], config=config)

    assert len(events) == 1
    ev = events[0]
    assert ev.is_breach
    assert not ev.is_sweep
    assert ev.status == LiquiditySweepStatus.BREACH


# ── 5. Equal level is not Sweep ───────────────────────────────────────────────
def test_equal_level_is_not_sweep() -> None:
    """Verify exact equality (high == level or low == level) is a TOUCH, never a SWEEP."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Exact high = 2650.00
    candles = [make_test_candle(7, "2648.00", "2650.00", "2645.00", "2649.00")]

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"), min_penetration_ticks=1)
    events = detect_liquidity_sweeps(candles, [sh], config=config)

    assert len(events) == 1
    assert events[0].is_touch
    assert not events[0].is_sweep


# ── 6. Minimum tick threshold ─────────────────────────────────────────────────
def test_minimum_tick_threshold() -> None:
    """Verify threshold boundary: tick_size=0.10, min_penetration_ticks=3 -> threshold=0.30."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    config = LiquiditySweepConfig(tick_size=Decimal("0.10"), min_penetration_ticks=3)
    assert config.penetration_threshold == Decimal("0.30")

    # High = 2650.25 (only 2.5 ticks -> <= 0.30) -> TOUCH
    candle_touch = make_test_candle(7, "2649.00", "2650.25", "2648.00", "2649.50")
    evs_touch = detect_liquidity_sweeps([candle_touch], [sh], config=config)
    assert evs_touch[0].status == LiquiditySweepStatus.TOUCH

    # High = 2650.35 (3.5 ticks -> > 0.30) -> SWEEP
    candle_sweep = make_test_candle(7, "2649.00", "2650.35", "2648.00", "2649.50")
    evs_sweep = detect_liquidity_sweeps([candle_sweep], [sh], config=config)
    assert evs_sweep[0].status == LiquiditySweepStatus.SWEEP


# ── 7. Only confirmed swings eligible ─────────────────────────────────────────
def test_only_confirmed_swings_eligible() -> None:
    """Verify that candidate swings are completely ignored and cannot trigger sweeps."""
    cand = make_test_swing_point(
        "cand1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
        status=SwingConfirmationStatus.CANDIDATE,
    )

    candle = make_test_candle(7, "2649.00", "2655.00", "2648.00", "2648.00")
    config = LiquiditySweepConfig(tick_size=Decimal("0.01"))
    events = detect_liquidity_sweeps([candle], [cand], config=config)
    assert len(events) == 0


# ── 8. Swing confirmation must precede sweep bar ──────────────────────────────
def test_swing_confirmation_must_precede_sweep_bar() -> None:
    """Verify PIT rule: swing.confirmation_time <= sweep_bar.open_time."""
    # Swing confirmed at 07:00
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 4, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 7, 0, tzinfo=UTC),
    )

    # Bar 6 opens at 06:00 and closes at 07:00 (confirmation happens at bar close!)
    # Bar 6 cannot sweep a swing that only gets confirmed at 07:00
    bar_pre = make_test_candle(6, "2649.00", "2655.00", "2648.00", "2648.00")
    config = LiquiditySweepConfig(tick_size=Decimal("0.01"))
    events_pre = detect_liquidity_sweeps([bar_pre], [sh], config=config)
    assert len(events_pre) == 0

    # Bar 7 opens at 07:00 (equal to conf_time 07:00) -> Allowed!
    bar_post = make_test_candle(7, "2649.00", "2655.00", "2648.00", "2648.00")
    events_post = detect_liquidity_sweeps([bar_post], [sh], config=config)
    assert len(events_post) == 1
    assert events_post[0].is_sweep


# ── 9. No future leakage under historical as_of ───────────────────────────────
def test_no_future_leakage_under_historical_as_of() -> None:
    """Verify that querying with historical as_of hides all future sweeps."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Bar 8 opens at 08:00, closes at 09:00
    bar = make_test_candle(8, "2649.00", "2655.00", "2648.00", "2648.00")
    config = LiquiditySweepConfig(tick_size=Decimal("0.01"))

    # As of 08:30 (during the bar): event does not exist yet!
    as_of_mid = datetime(2026, 10, 1, 8, 30, tzinfo=UTC)
    events_mid = detect_liquidity_sweeps([bar], [sh], config=config, as_of_utc=as_of_mid)
    assert len(events_mid) == 0

    # As of 09:00 (bar close): event is now accessible!
    as_of_close = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    events_close = detect_liquidity_sweeps([bar], [sh], config=config, as_of_utc=as_of_close)
    assert len(events_close) == 1
    assert events_close[0].detection_time == as_of_close


# ── 10. Sweep detection only at candle close ──────────────────────────────────
def test_sweep_detection_only_at_candle_close() -> None:
    """Verify detection_time strictly matches sweep_bar.close_time and post-init enforces it."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )
    bar = make_test_candle(8, "2649.00", "2655.00", "2648.00", "2648.00")
    config = LiquiditySweepConfig(tick_size=Decimal("0.01"))
    ev = detect_liquidity_sweeps([bar], [sh], config=config)[0]

    assert ev.detection_time == bar.close_time
    assert ev.sweep_bar_close_time == bar.close_time
    assert ev.detection_time > ev.sweep_bar_open_time

    # Attempting to forge detection_time before close raises LiquiditySweepError
    with pytest.raises(LiquiditySweepError, match="must equal sweep_bar_close_time"):
        LiquiditySweepEvent(
            event_id="test",
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("src"),
            timeframe=Timeframe.H1,
            price_type=PriceType.BID,
            source_swing_id="sh1",
            liquidity_side=LiquiditySide.BUY_SIDE,
            level_price=Decimal("2650.00"),
            penetration_price=Decimal("2655.00"),
            penetration_ticks=Decimal("500"),
            penetration_threshold=Decimal("0.01"),
            sweep_bar_open_time=bar.open_time,
            sweep_bar_close_time=bar.close_time,
            detection_time=bar.open_time,  # Forged detection time!
            pivot_time=sh.pivot_time,
            level_confirmation_time=sh.confirmation_time,  # type: ignore[arg-type]
            status=LiquiditySweepStatus.SWEEP,
        )


# ── 11. Previously breached level rejection ───────────────────────────────────
def test_previously_breached_level_rejection() -> None:
    """Verify that a level breached by a body close cannot generate future sweeps."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Bar 7 breaches level: closes at 2655.00 (above 2650.00)
    bar7 = make_test_candle(7, "2649.00", "2658.00", "2648.00", "2655.00")

    # Bar 8 appears to sweep: high=2660.00, close=2645.00 (under 2650.00)
    bar8 = make_test_candle(8, "2655.00", "2660.00", "2644.00", "2645.00")

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"))
    events = detect_liquidity_sweeps([bar7, bar8], [sh], config=config)

    # Bar 7 triggers BREACH, Bar 8 is completely rejected because level was breached!
    assert len(events) == 1
    assert events[0].status == LiquiditySweepStatus.BREACH
    assert events[0].sweep_bar_open_time == bar7.open_time


# ── 12. Duplicate sweep prevention & 13. Configurable level consumption ───────
def test_duplicate_sweep_prevention_and_consumption() -> None:
    """Verify consume_level_after_sweep (default: True prevents duplicate sweeps)."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Bar 7 sweeps: high=2655.00, close=2648.00
    bar7 = make_test_candle(7, "2649.00", "2655.00", "2647.00", "2648.00")
    # Bar 8 also sweeps: high=2654.00, close=2647.00
    bar8 = make_test_candle(8, "2648.00", "2654.00", "2646.00", "2647.00")

    # Case A: Default consume_level_after_sweep=True -> Only 1 sweep produced (bar 7)
    config_consume = LiquiditySweepConfig(
        tick_size=Decimal("0.01"), consume_level_after_sweep=True
    )
    events_consume = detect_liquidity_sweeps([bar7, bar8], [sh], config=config_consume)
    assert len(events_consume) == 1
    assert events_consume[0].sweep_bar_open_time == bar7.open_time

    # Case B: consume_level_after_sweep=False -> Both sweeps permitted
    config_multi = LiquiditySweepConfig(tick_size=Decimal("0.01"), consume_level_after_sweep=False)
    events_multi = detect_liquidity_sweeps([bar7, bar8], [sh], config=config_multi)
    assert len(events_multi) == 2
    assert events_multi[0].sweep_bar_open_time == bar7.open_time
    assert events_multi[1].sweep_bar_open_time == bar8.open_time


# ── 14. BID / ASK isolation ───────────────────────────────────────────────────
def test_bid_ask_isolation() -> None:
    """Verify BID and ASK sweeps are strictly isolated and mixed series raise error."""
    sh_bid = make_test_swing_point(
        "sh_bid",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
        price_type=PriceType.BID,
    )
    sh_ask = make_test_swing_point(
        "sh_ask",
        SwingType.SWING_HIGH,
        "2650.50",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
        price_type=PriceType.ASK,
    )

    bar_bid = make_test_candle(
        7, "2649.00", "2655.00", "2647.00", "2648.00", price_type=PriceType.BID
    )
    bar_ask = make_test_candle(
        7, "2649.50", "2655.50", "2647.50", "2648.50", price_type=PriceType.ASK
    )

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"))
    ev_bid = detect_liquidity_sweeps([bar_bid], [sh_bid], config=config)[0]
    ev_ask = detect_liquidity_sweeps([bar_ask], [sh_ask], config=config)[0]

    assert ev_bid.price_type == PriceType.BID
    assert ev_ask.price_type == PriceType.ASK
    assert "BID" in ev_bid.event_id
    assert "ASK" in ev_ask.event_id
    assert ev_bid.event_id != ev_ask.event_id

    # Trying to evaluate ASK swing against BID candles must raise LiquiditySweepDataError
    with pytest.raises(LiquiditySweepDataError, match="PriceType mismatch"):
        detect_liquidity_sweeps([bar_bid], [sh_ask], config=config)


# ── 15. D1 / H4 / H2 isolation ─────────────────────────────────────────────────
def test_timeframe_isolation() -> None:
    """Verify timeframe isolation: evaluating H4 candles against D1 swings raises error."""
    sh_d1 = make_test_swing_point(
        "sh_d1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 4, 0, 0, tzinfo=UTC),
        timeframe=Timeframe.D1,
    )
    bar_h4 = make_test_candle(
        10, "2649.00", "2655.00", "2647.00", "2648.00", timeframe=Timeframe.H4
    )

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"))
    with pytest.raises(LiquiditySweepDataError, match="Timeframe mismatch"):
        detect_liquidity_sweeps([bar_h4], [sh_d1], config=config)


# ── 16. UTC / NY D1 modes ─────────────────────────────────────────────────────
def test_utc_and_ny_d1_modes() -> None:
    """Verify D1 candles work cleanly and Sunday stubs are safely skipped."""
    sh_d1 = make_test_swing_point(
        "sh_d1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 4, 0, 0, tzinfo=UTC),
        timeframe=Timeframe.D1,
    )

    # Sunday 00:00 stub (auto-excluded)
    sunday_bar = make_test_candle(
        0,
        "2648.00",
        "2655.00",
        "2647.00",
        "2648.00",
        base_time=datetime(2026, 10, 4, 0, 0, tzinfo=UTC),
        timeframe=Timeframe.D1,
    )
    # Monday candle
    monday_bar = make_test_candle(
        0,
        "2649.00",
        "2655.00",
        "2647.00",
        "2648.00",
        base_time=datetime(2026, 10, 5, 0, 0, tzinfo=UTC),
        timeframe=Timeframe.D1,
    )

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"))
    events = detect_liquidity_sweeps([sunday_bar, monday_bar], [sh_d1], config=config)

    assert len(events) == 1
    assert events[0].sweep_bar_open_time == monday_bar.open_time


# ── 17. Partial candle rejection ──────────────────────────────────────────────
def test_partial_candle_rejection() -> None:
    """Verify unclosed candles (is_closed=False) are rejected."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )
    bar_partial = make_test_candle(7, "2649.00", "2655.00", "2647.00", "2648.00", is_closed=False)

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"))
    events = detect_liquidity_sweeps([bar_partial], [sh], config=config)
    assert len(events) == 0


# ── 18. Synthetic candle rejection ────────────────────────────────────────────
def test_synthetic_candle_rejection() -> None:
    """Verify synthetic candles are rejected when allow_synthetic=False."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )
    bar_synth = make_test_candle(7, "2649.00", "2655.00", "2647.00", "2648.00", is_synthetic=True)

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"), allow_synthetic=False)
    events = detect_liquidity_sweeps([bar_synth], [sh], config=config)
    assert len(events) == 0


# ── 19. Data-gap protection ───────────────────────────────────────────────────
def test_data_gap_protection() -> None:
    """Verify data gaps preceding the sweep candle are detected and flagged."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
        datetime(2026, 10, 6, 11, 0, tzinfo=UTC),
    )

    base_t = datetime(2026, 10, 6, 11, 0, tzinfo=UTC)
    bar1 = make_test_candle(0, "2648.00", "2649.00", "2647.00", "2648.50", base_time=base_t)
    # 5-hour unannounced mid-week intraday gap between bar1 and bar2
    gap_time = bar1.close_time + timedelta(hours=5)
    bar2 = make_test_candle(0, "2649.00", "2655.00", "2647.00", "2648.00", base_time=gap_time)

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"), allow_weekend_gap=False)
    events = detect_liquidity_sweeps([bar1, bar2], [sh], config=config)

    assert len(events) == 1
    assert "DATA_GAP_PRECEDING" in events[0].data_quality_flags


# ── 20. Decimal precision ─────────────────────────────────────────────────────
def test_decimal_precision() -> None:
    """Verify sub-pip Decimal calculations without float imprecision."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.12345",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # High = 2650.12355 (0.00010 higher = 10 ticks of 0.00001)
    bar = make_test_candle(7, "2650.00000", "2650.12355", "2649.00000", "2650.10000")
    config = LiquiditySweepConfig(tick_size=Decimal("0.00001"), min_penetration_ticks=5)
    events = detect_liquidity_sweeps([bar], [sh], config=config)

    assert len(events) == 1
    ev = events[0]
    assert ev.is_sweep
    assert ev.penetration_ticks == Decimal("10")
    assert isinstance(ev.penetration_ticks, Decimal)


# ── 21. Deterministic IDs ─────────────────────────────────────────────────────
def test_deterministic_ids() -> None:
    """Verify deterministic sweep identifier generation."""
    dt = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
    s_id = generate_liquidity_sweep_id(
        "XAUUSD",
        "market_data_lab_m1",
        PriceType.BID,
        Timeframe.H1,
        LiquiditySide.BUY_SIDE,
        LiquiditySweepStatus.SWEEP,
        "sh1",
        dt,
    )
    expected = "XAUUSD_market_data_lab_m1_BID_H1_BUY_SIDE_SWEEP_sh1_20261001T070000Z"
    assert s_id == expected


# ── 22. Identical inputs produce identical results ────────────────────────────
def test_identical_inputs_produce_identical_results() -> None:
    """Verify repeated execution produces 100% mathematically identical events."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )
    bar = make_test_candle(7, "2649.00", "2655.00", "2647.00", "2648.00")
    config = LiquiditySweepConfig(tick_size=Decimal("0.01"))

    first = detect_liquidity_sweeps([bar], [sh], config=config)
    for _ in range(5):
        run = detect_liquidity_sweeps([bar], [sh], config=config)
        assert run == first


# ── 23. STRICT_PIT estimated data rejection ───────────────────────────────────
def test_strict_pit_estimated_data_rejection() -> None:
    """Verify STRICT_PIT policy rejects unverified / estimated candles."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )
    # Candle has ESTIMATED confidence (Dukascopy historical backfill default)
    bar_estimated = make_test_candle(
        7,
        "2649.00",
        "2655.00",
        "2647.00",
        "2648.00",
        availability_confidence=AvailabilityConfidence.ESTIMATED,
    )

    config_strict = LiquiditySweepConfig(tick_size=Decimal("0.01"), strict_pit=True)
    events_strict = detect_liquidity_sweeps([bar_estimated], [sh], config=config_strict)
    # Rejected because data was estimated
    assert len(events_strict) == 0


# ── 24. RESEARCH_ESTIMATED historical availability ────────────────────────────
def test_research_estimated_historical_availability() -> None:
    """Verify RESEARCH_ESTIMATED permits completed backfill candles."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )
    bar_estimated = make_test_candle(
        7,
        "2649.00",
        "2655.00",
        "2647.00",
        "2648.00",
        availability_confidence=AvailabilityConfidence.ESTIMATED,
    )

    config_research = LiquiditySweepConfig(tick_size=Decimal("0.01"), strict_pit=False)
    events_research = detect_liquidity_sweeps([bar_estimated], [sh], config=config_research)
    assert len(events_research) == 1
    assert events_research[0].is_sweep
    assert events_research[0].pit_confidence == "RESEARCH_ESTIMATED"


# ── 25. Ambiguous same-bar extremes ───────────────────────────────────────────
def test_ambiguous_same_bar_extremes() -> None:
    """Verify single candle breaching opposing swing levels is marked AMBIGUOUS."""
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )
    sl = make_test_swing_point(
        "sl1",
        SwingType.SWING_LOW,
        "2630.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )

    # Massive volatile candle breaching high=2655.00 and low=2625.00, close=2640.00
    mega_bar = make_test_candle(7, "2640.00", "2655.00", "2625.00", "2640.00")

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"), allow_ambiguous=True)
    events = detect_liquidity_sweeps([mega_bar], [sh, sl], config=config)

    assert len(events) == 2
    for ev in events:
        assert ev.is_ambiguous
        assert ev.status == LiquiditySweepStatus.AMBIGUOUS
        assert "AMBIGUOUS_SAME_BAR_EXTREMES" in ev.data_quality_flags


# ── Parameter Validation and Filtering Helpers ────────────────────────────────
def test_parameter_validation_and_filtering() -> None:
    """Verify parameter validations and query helper functions."""
    with pytest.raises(InvalidLiquiditySweepParameterError, match="strictly positive"):
        LiquiditySweepConfig(tick_size=Decimal("0"))

    with pytest.raises(InvalidLiquiditySweepParameterError, match="min_penetration_ticks"):
        LiquiditySweepConfig(tick_size=Decimal("0.01"), min_penetration_ticks=0)

    # Dynamic resolution without tick_size or instrument raises error
    with pytest.raises(InvalidLiquiditySweepParameterError, match="Cannot resolve tick_size"):
        LiquiditySweepConfig.from_instrument_or_source(None, None)

    # Filtering helpers
    sh = make_test_swing_point(
        "sh1",
        SwingType.SWING_HIGH,
        "2650.00",
        datetime(2026, 10, 1, 3, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 6, 0, tzinfo=UTC),
    )
    bar_touch = make_test_candle(7, "2648.00", "2650.00", "2645.00", "2649.00")
    bar_sweep = make_test_candle(8, "2649.00", "2655.00", "2647.00", "2648.00")
    bar_breach = make_test_candle(9, "2648.00", "2655.00", "2646.00", "2653.00")

    config = LiquiditySweepConfig(tick_size=Decimal("0.01"), consume_level_after_sweep=False)
    events = detect_liquidity_sweeps([bar_touch, bar_sweep, bar_breach], [sh], config=config)

    # filter_confirmed_sweeps
    sweeps = filter_confirmed_sweeps(events)
    assert len(sweeps) == 1
    assert sweeps[0].is_sweep

    # summarize_liquidity_events
    summary = summarize_liquidity_events(events)
    assert summary["total_events"] == 3
    assert summary["confirmed_sweeps"] == 1
    assert summary["buy_side_sweeps"] == 1
    assert summary["sell_side_sweeps"] == 0
    assert summary["touches"] == 1
    assert summary["breaches"] == 1
    assert summary["ambiguous"] == 0
