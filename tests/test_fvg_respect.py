"""Deterministic Point-in-Time Safe FVG Respect & Retest Analysis Engine Test Suite.

Contains exhaustive unit tests validating all 38 Phase 4C requirements:
- Touch vs Respect separation
- Single episode aggregation for consecutive bars inside zone
- Departure rules and minimum departure ticks enforcement
- Respect confirmation within reaction window
- Failed reaction on window expiry
- 3 and 4 independent retests / respects
- 7 touches full history preservation with reported retest limits
- Normal FVG vs Inversion FVG (IFVG) role transitions
- Point-in-Time masks (future touches and future reactions masked)
- Market data isolation (BID/ASK, timeframe, session, synthetic, partial rejection)
- Model immutability and deterministic event IDs
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    resample_m1_candles,
)
from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.fvg import (
    FvgDirection,
    FvgZone,
    detect_fair_value_gaps,
    generate_fvg_id,
)
from traderlab.domain.market_structure.fvg_respect import (
    FvgReactionDirection,
    FvgRespectConfig,
    FvgRespectDataError,
    FvgRespectError,
    FvgRetestStatus,
    evaluate_all_fvgs_respect,
    evaluate_fvg_respect,
    filter_fvg_retest_events_as_of,
    generate_fvg_retest_event_id,
)
from traderlab.interfaces.dashboard.app import (
    load_month_m1_candles_cached,
)


# ── Test Helpers ─────────────────────────────────────────────────────────────
def make_candle(
    open_time: datetime,
    open_p: str,
    high_p: str,
    low_p: str,
    close_p: str,
    *,
    instrument_id: str = "XAUUSD",
    source_id: str = "market_data_lab_m1",
    timeframe: Timeframe = Timeframe.H1,
    price_type: PriceType = PriceType.BID,
    is_closed: bool = True,
    is_synthetic: bool = False,
    revision: int = 0,
) -> Candle:
    """Create an immutable domain Candle for testing."""
    o_dt = open_time.replace(tzinfo=UTC) if open_time.tzinfo is None else open_time.astimezone(UTC)
    c_dt = o_dt + timedelta(seconds=timeframe.value)
    return Candle(
        instrument_id=InstrumentId(instrument_id),
        source_id=DataSourceId(source_id),
        price_type=price_type,
        timeframe=timeframe,
        open_time=o_dt,
        close_time=c_dt,
        open=Decimal(open_p),
        high=Decimal(high_p),
        low=Decimal(low_p),
        close=Decimal(close_p),
        volume=Decimal("100"),
        tick_volume=100,
        spread=Decimal("0.20"),
        is_closed=is_closed,
        is_synthetic=is_synthetic,
        revision=revision,
    )


def make_bullish_fvg(
    bottom_price: str = "4000.00",
    top_price: str = "4010.00",
    formation_time: datetime = datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
    *,
    instrument_id: str = "XAUUSD",
    source_id: str = "market_data_lab_m1",
    timeframe: Timeframe = Timeframe.H1,
    price_type: PriceType = PriceType.BID,
) -> FvgZone:
    """Helper to build a valid Bullish FvgZone [bottom, top]."""
    bot = Decimal(bottom_price)
    top = Decimal(top_price)
    width = top - bot
    ce = (top + bot) / Decimal(2)
    f_time = formation_time.replace(tzinfo=UTC)
    z_id = generate_fvg_id(
        instrument_id=instrument_id,
        source_id=source_id,
        price_type=price_type,
        timeframe=timeframe,
        formation_time=f_time,
        direction=FvgDirection.BULLISH,
    )
    return FvgZone(
        id=z_id,
        instrument_id=InstrumentId(instrument_id),
        source_id=DataSourceId(source_id),
        price_type=price_type,
        timeframe=timeframe,
        direction=FvgDirection.BULLISH,
        top_price=top,
        bottom_price=bot,
        ce_price=ce,
        width=width,
        candle_1_open=f_time - timedelta(hours=3),
        candle_2_open=f_time - timedelta(hours=2),
        candle_3_open=f_time - timedelta(hours=1),
        formation_time=f_time,
        computable_at_utc=f_time,
    )


def make_bearish_fvg(
    bottom_price: str = "4000.00",
    top_price: str = "4010.00",
    formation_time: datetime = datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
    *,
    instrument_id: str = "XAUUSD",
    source_id: str = "market_data_lab_m1",
    timeframe: Timeframe = Timeframe.H1,
    price_type: PriceType = PriceType.BID,
) -> FvgZone:
    """Helper to build a valid Bearish FvgZone [bottom, top]."""
    bot = Decimal(bottom_price)
    top = Decimal(top_price)
    width = top - bot
    ce = (top + bot) / Decimal(2)
    f_time = formation_time.replace(tzinfo=UTC)
    z_id = generate_fvg_id(
        instrument_id=instrument_id,
        source_id=source_id,
        price_type=price_type,
        timeframe=timeframe,
        formation_time=f_time,
        direction=FvgDirection.BEARISH,
    )
    return FvgZone(
        id=z_id,
        instrument_id=InstrumentId(instrument_id),
        source_id=DataSourceId(source_id),
        price_type=price_type,
        timeframe=timeframe,
        direction=FvgDirection.BEARISH,
        top_price=top,
        bottom_price=bot,
        ce_price=ce,
        width=width,
        candle_1_open=f_time - timedelta(hours=3),
        candle_2_open=f_time - timedelta(hours=2),
        candle_3_open=f_time - timedelta(hours=1),
        formation_time=f_time,
        computable_at_utc=f_time,
    )


# ── Stage 10 Test Cases (1 to 38) ───────────────────────────────────────────
def test_01_first_touch_detected() -> None:
    """Test 1: First subsequent bar entering the FVG zone registers retest sequence #1."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 1: enters zone [4000, 4010] with low at 4005, closes inside at 4007
        make_candle(t0, "4015.00", "4015.00", "4005.00", "4007.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.total_touch_count == 1
    assert summary.reported_retest_count == 1
    assert summary.remaining_retests == 0
    event = summary.retest_events[0]
    assert event.retest_sequence_number == 1
    assert event.touch_price == Decimal("4005.00")
    assert event.penetration_depth == Decimal("5.00")
    assert event.penetration_percentage == Decimal("50.00")
    assert event.effective_direction == FvgReactionDirection.BULLISH_SUPPORT
    assert event.status == FvgRetestStatus.PENDING


def test_02_no_touch_before_fvg_formation() -> None:
    """Test 2: Pre-formation candles are strictly excluded from retest detection."""
    fvg = make_bullish_fvg(
        "4000.00", "4010.00", formation_time=datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    )
    candles = [
        # Pre-formation candles prior to 10:00 UTC
        make_candle(
            datetime(2026, 10, 1, 7, 0, tzinfo=UTC), "4005.00", "4008.00", "4002.00", "4006.00"
        ),
        make_candle(
            datetime(2026, 10, 1, 8, 0, tzinfo=UTC), "4006.00", "4009.00", "4001.00", "4005.00"
        ),
        make_candle(
            datetime(2026, 10, 1, 9, 0, tzinfo=UTC), "4005.00", "4015.00", "4004.00", "4014.00"
        ),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.total_touch_count == 0
    assert len(summary.retest_events) == 0


def test_03_consecutive_bars_count_as_single_touch() -> None:
    """Test 3: Consecutive bars remaining inside or touching the zone count as 1 retest episode."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 1 touches zone
        make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00"),
        # Bar 2 stays inside zone (penetrates deeper to 4004.00)
        make_candle(t0 + timedelta(hours=1), "4009.00", "4009.00", "4004.00", "4006.00"),
        # Bar 3 still inside zone
        make_candle(t0 + timedelta(hours=2), "4006.00", "4008.00", "4005.00", "4007.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.total_touch_count == 1
    event = summary.retest_events[0]
    assert event.retest_sequence_number == 1
    # Deepest penetration across the episode was 4004.00 (depth 6.00 = 60%)
    assert event.penetration_depth == Decimal("6.00")
    assert event.penetration_percentage == Decimal("60.00")


def test_04_new_touch_requires_departure() -> None:
    """Test 4: A second retest is only counted after price meaningfully departs and returns."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 1: touch #1
        make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00"),
        # Bar 2: departs above zone (low > 4010, close = 4015 >= 4010 + 0.01)
        make_candle(t0 + timedelta(hours=1), "4011.00", "4016.00", "4011.00", "4015.00"),
        # Bar 3: touch #2 (enters zone again)
        make_candle(t0 + timedelta(hours=2), "4015.00", "4015.00", "4007.00", "4008.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.total_touch_count == 2
    assert summary.retest_events[0].retest_sequence_number == 1
    assert summary.retest_events[1].retest_sequence_number == 2


def test_05_min_departure_ticks_enforced() -> None:
    """Test 5: If departure distance is less than min_departure_ticks, no new retest can begin."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    cfg = FvgRespectConfig(
        min_departure_ticks=Decimal("500"), tick_size=Decimal("0.01")
    )  # Requires 5.00 departure
    candles = [
        # Bar 1: touch
        make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00"),
        # Bar 2: outside zone but only closes at 4012.00 (only 2.00 departure < 5.00 required)
        make_candle(t0 + timedelta(hours=1), "4011.00", "4013.00", "4011.00", "4012.00"),
        # Bar 3: enters zone again
        make_candle(t0 + timedelta(hours=2), "4012.00", "4012.00", "4008.00", "4009.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles, config=cfg)
    # Because required departure was not reached, Bar 3 does NOT count as a 2nd retest!
    assert summary.total_touch_count == 1


def test_06_min_bars_outside_enforced() -> None:
    """Test 6: If min_bars_outside_zone = 2, 1 outside bar is insufficient for a new retest."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    cfg = FvgRespectConfig(min_bars_outside_zone=2)
    candles = [
        # Bar 1: touch
        make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00"),
        # Bar 2: only 1 outside bar
        make_candle(t0 + timedelta(hours=1), "4011.00", "4016.00", "4011.00", "4015.00"),
        # Bar 3: returns to zone
        make_candle(t0 + timedelta(hours=2), "4015.00", "4015.00", "4008.00", "4009.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles, config=cfg)
    # Only 1 bar outside when 2 were required -> still 1 retest episode
    assert summary.total_touch_count == 1


def test_07_bullish_respect_confirmation() -> None:
    """Test 7: Bullish FVG touch followed by a closed bar closing above zone top confirms RESPECT."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 1: touch at 4005.00
        make_candle(t0, "4015.00", "4015.00", "4005.00", "4008.00"),
        # Bar 2: closes strongly above top at 4015.00 (top is 4010.00)
        make_candle(t0 + timedelta(hours=1), "4008.00", "4016.00", "4007.00", "4015.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.confirmed_respect_count == 1
    event = summary.retest_events[0]
    assert event.status == FvgRetestStatus.RESPECT_CONFIRMED
    assert event.reaction_confirmation_time == candles[1].close_time
    assert event.reaction_distance == Decimal("5.00")  # 4015 - 4010


def test_08_bearish_respect_confirmation() -> None:
    """Test 8: Bearish FVG touch followed by a closed bar closing below zone bottom confirms RESPECT."""
    fvg = make_bearish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 1: touch at 4005.00 from below
        make_candle(t0, "3995.00", "4005.00", "3995.00", "4002.00"),
        # Bar 2: closes strongly below bottom at 3992.00 (bottom is 4000.00)
        make_candle(t0 + timedelta(hours=1), "4002.00", "4003.00", "3990.00", "3992.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.confirmed_respect_count == 1
    event = summary.retest_events[0]
    assert event.status == FvgRetestStatus.RESPECT_CONFIRMED
    assert event.effective_direction == FvgReactionDirection.BEARISH_RESISTANCE
    assert event.reaction_confirmation_time == candles[1].close_time
    assert event.reaction_distance == Decimal("8.00")  # 4000 - 3992


def test_09_touch_without_reaction_not_respect() -> None:
    """Test 9: Touching the zone without confirming reaction does not count as respect."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 1: touch
        make_candle(t0, "4015.00", "4015.00", "4005.00", "4007.00"),
        # Bar 2: lingers inside zone, close = 4006.00
        make_candle(t0 + timedelta(hours=1), "4007.00", "4008.00", "4004.00", "4006.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.total_touch_count == 1
    assert summary.confirmed_respect_count == 0
    assert summary.retest_events[0].status == FvgRetestStatus.PENDING


def test_10_pending_reaction_before_window_end() -> None:
    """Test 10: Retest remains PENDING while window has not elapsed and no confirmation occurred."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    cfg = FvgRespectConfig(reaction_confirmation_bars=3)
    candles = [
        # Bar 1: touch
        make_candle(t0, "4015.00", "4015.00", "4005.00", "4007.00"),
        # Bar 2: inside (1 bar evaluated < 3)
        make_candle(t0 + timedelta(hours=1), "4007.00", "4008.00", "4004.00", "4006.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles, config=cfg)
    assert summary.retest_events[0].status == FvgRetestStatus.PENDING
    assert summary.pending_reaction_count == 1


def test_11_failed_reaction_after_window_expiry() -> None:
    """Test 11: Retest transitions to FAILED_REACTION when reaction window fully elapses without reaction."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    cfg = FvgRespectConfig(reaction_confirmation_bars=3)
    candles = [
        # Bar 1: touch
        make_candle(t0, "4015.00", "4015.00", "4005.00", "4007.00"),
        # Bar 2: inside zone
        make_candle(t0 + timedelta(hours=1), "4007.00", "4008.00", "4004.00", "4006.00"),
        # Bar 3: inside zone
        make_candle(t0 + timedelta(hours=2), "4006.00", "4008.00", "4003.00", "4005.00"),
        # Bar 4: inside zone (3 bars elapsed without closing above 4010.01)
        make_candle(t0 + timedelta(hours=3), "4005.00", "4008.00", "4003.00", "4006.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles, config=cfg)
    assert summary.retest_events[0].status == FvgRetestStatus.FAILED_REACTION
    assert summary.failed_reaction_count == 1


def test_12_reaction_requires_closed_bar() -> None:
    """Test 12: An unclosed/partial candle cannot be used for reaction confirmation."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        make_candle(t0, "4015.00", "4015.00", "4005.00", "4007.00"),
        make_candle(
            t0 + timedelta(hours=1), "4007.00", "4020.00", "4007.00", "4018.00", is_closed=False
        ),
    ]
    with pytest.raises(FvgRespectDataError, match="Partial unclosed candle"):
        evaluate_fvg_respect(fvg, candles)


def test_13_no_same_bar_intrabar_assumption() -> None:
    """Test 13: Touch bar closing above zone top does not evaluate reaction on itself."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 1: touched zone at low 4005, then closed at 4015.00 on the same bar
        make_candle(t0, "4012.00", "4016.00", "4005.00", "4015.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    # Reaction confirmation must be evaluated on subsequent bars, NOT on the touch bar itself
    assert summary.retest_events[0].status == FvgRetestStatus.PENDING


def test_14_three_independent_respects() -> None:
    """Test 14: Verifies three sequential independent tests and respects of the same zone."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Retest 1: Touch + Respect + Departure
        make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00"),
        make_candle(t0 + timedelta(hours=1), "4009.00", "4018.00", "4009.00", "4017.00"),
        make_candle(t0 + timedelta(hours=2), "4017.00", "4020.00", "4015.00", "4019.00"),
        # Retest 2: Touch + Respect + Departure
        make_candle(t0 + timedelta(hours=3), "4019.00", "4019.00", "4007.00", "4008.00"),
        make_candle(t0 + timedelta(hours=4), "4008.00", "4018.00", "4008.00", "4016.00"),
        make_candle(t0 + timedelta(hours=5), "4016.00", "4020.00", "4014.00", "4018.00"),
        # Retest 3: Touch + Respect
        make_candle(t0 + timedelta(hours=6), "4018.00", "4018.00", "4006.00", "4008.00"),
        make_candle(t0 + timedelta(hours=7), "4008.00", "4017.00", "4008.00", "4015.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.total_touch_count == 3
    assert summary.confirmed_respect_count == 3
    assert summary.consecutive_respect_count == 3
    assert summary.maximum_respect_streak == 3
    for ev in summary.retest_events:
        assert ev.status == FvgRetestStatus.RESPECT_CONFIRMED


def test_15_four_independent_respects() -> None:
    """Test 15: Verifies four sequential independent tests and respects of an H4 FVG zone."""
    fvg = make_bullish_fvg("4000.00", "4010.00", timeframe=Timeframe.H4)
    t0 = fvg.formation_time
    candles: list[Candle] = []
    h = 0
    for _ in range(4):
        # Touch bar
        candles.append(
            make_candle(
                t0 + timedelta(hours=h),
                "4015.00",
                "4015.00",
                "4008.00",
                "4009.00",
                timeframe=Timeframe.H4,
            )
        )
        h += 4
        # Respect confirmation bar
        candles.append(
            make_candle(
                t0 + timedelta(hours=h),
                "4009.00",
                "4020.00",
                "4008.00",
                "4016.00",
                timeframe=Timeframe.H4,
            )
        )
        h += 4
        # Departure bar
        candles.append(
            make_candle(
                t0 + timedelta(hours=h),
                "4016.00",
                "4022.00",
                "4014.00",
                "4018.00",
                timeframe=Timeframe.H4,
            )
        )
        h += 4

    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.total_touch_count == 4
    assert summary.confirmed_respect_count == 4
    assert summary.reported_retest_count == 4
    assert summary.remaining_retests == 0
    assert summary.maximum_respect_streak == 4


def test_16_seven_touches_preserve_full_history() -> None:
    """Test 16: An FVG tested 7 times preserves all 7 events with total_retest_count=7 and remaining=3."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles: list[Candle] = []
    h = 0
    for _ in range(7):
        # Touch
        candles.append(
            make_candle(t0 + timedelta(hours=h), "4015.00", "4015.00", "4007.00", "4008.00")
        )
        h += 1
        # Respect
        candles.append(
            make_candle(t0 + timedelta(hours=h), "4008.00", "4018.00", "4007.00", "4015.00")
        )
        h += 1
        # Departure
        candles.append(
            make_candle(t0 + timedelta(hours=h), "4015.00", "4020.00", "4013.00", "4017.00")
        )
        h += 1

    summary = evaluate_fvg_respect(fvg, candles, config=FvgRespectConfig(max_reported_retests=4))
    assert summary.total_retest_count == 7
    assert summary.total_touch_count == 7
    assert summary.reported_retest_count == 4
    assert summary.remaining_retests == 3
    # Crucial: All 7 events must be preserved in domain memory without deletion
    assert len(summary.retest_events) == 7
    assert len(summary.reported_retest_events) == 4


def test_17_reported_retest_limit_does_not_delete_events() -> None:
    """Test 17: Setting max_reported_retests = 2 preserves all domain events while slicing report."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Retest 1
        make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00"),
        make_candle(t0 + timedelta(hours=1), "4009.00", "4018.00", "4009.00", "4016.00"),
        make_candle(t0 + timedelta(hours=2), "4016.00", "4020.00", "4013.00", "4017.00"),
        # Retest 2
        make_candle(t0 + timedelta(hours=3), "4017.00", "4017.00", "4008.00", "4009.00"),
        make_candle(t0 + timedelta(hours=4), "4009.00", "4018.00", "4009.00", "4016.00"),
        make_candle(t0 + timedelta(hours=5), "4016.00", "4020.00", "4013.00", "4017.00"),
        # Retest 3
        make_candle(t0 + timedelta(hours=6), "4017.00", "4017.00", "4008.00", "4009.00"),
        make_candle(t0 + timedelta(hours=7), "4009.00", "4018.00", "4009.00", "4016.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles, config=FvgRespectConfig(max_reported_retests=2))
    assert summary.total_retest_count == 3
    assert summary.reported_retest_count == 2
    assert summary.remaining_retests == 1
    assert len(summary.retest_events) == 3
    assert len(summary.reported_retest_events) == 2


def test_18_normal_bullish_fvg_role() -> None:
    """Test 18: Normal Bullish FVG has effective direction BULLISH_SUPPORT."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    candles = [make_candle(fvg.formation_time, "4015.00", "4015.00", "4008.00", "4009.00")]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.effective_direction == FvgReactionDirection.BULLISH_SUPPORT
    assert summary.retest_events[0].effective_direction == FvgReactionDirection.BULLISH_SUPPORT


def test_19_normal_bearish_fvg_role() -> None:
    """Test 19: Normal Bearish FVG has effective direction BEARISH_RESISTANCE."""
    fvg = make_bearish_fvg("4000.00", "4010.00")
    candles = [make_candle(fvg.formation_time, "3995.00", "4005.00", "3995.00", "4002.00")]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.effective_direction == FvgReactionDirection.BEARISH_RESISTANCE
    assert summary.retest_events[0].effective_direction == FvgReactionDirection.BEARISH_RESISTANCE


def test_20_inverted_bullish_becomes_resistance() -> None:
    """Test 20: Inverted Bullish FVG transitions to BEARISH_RESISTANCE after inversion."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 1: Closes below bottom price -> invalidates and inverts (track_fvg_lifecycle invert retest)
        make_candle(t0, "4015.00", "4015.00", "3990.00", "3995.00"),
        # Bar 2: Inversion retest (high enters zone to 4005, closes below top at 3998)
        make_candle(t0 + timedelta(hours=1), "3995.00", "4005.00", "3995.00", "3998.00"),
        # Bar 3: Departs downwards (high < 4000, close <= 3990)
        make_candle(t0 + timedelta(hours=2), "3998.00", "3998.00", "3985.00", "3988.00"),
        # Bar 4: Inverted Retest (tests from below as BEARISH_RESISTANCE)
        make_candle(t0 + timedelta(hours=3), "3988.00", "4006.00", "3988.00", "4001.00"),
        # Bar 5: Rejection downwards confirming respect as resistance!
        make_candle(t0 + timedelta(hours=4), "4001.00", "4002.00", "3985.00", "3990.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    inv_events = summary.inverted_retest_events
    assert len(inv_events) >= 1
    retest_inv = inv_events[-1]
    assert retest_inv.effective_direction == FvgReactionDirection.BEARISH_RESISTANCE
    assert retest_inv.lifecycle_phase == "INVERTED"
    assert retest_inv.status == FvgRetestStatus.RESPECT_CONFIRMED


def test_21_inverted_bearish_becomes_support() -> None:
    """Test 21: Inverted Bearish FVG transitions to BULLISH_SUPPORT after inversion."""
    fvg = make_bearish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 1: Invalidation close above top (close = 4015 > 4010)
        make_candle(t0, "3995.00", "4020.00", "3995.00", "4015.00"),
        # Bar 2: Inversion retest holding zone from above
        make_candle(t0 + timedelta(hours=1), "4015.00", "4015.00", "4005.00", "4012.00"),
        # Bar 3: Departs upwards
        make_candle(t0 + timedelta(hours=2), "4012.00", "4025.00", "4012.00", "4022.00"),
        # Bar 4: New retest from above as BULLISH_SUPPORT
        make_candle(t0 + timedelta(hours=3), "4022.00", "4022.00", "4005.00", "4008.00"),
        # Bar 5: Respect bounce upwards
        make_candle(t0 + timedelta(hours=4), "4008.00", "4025.00", "4008.00", "4020.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    inv_events = summary.inverted_retest_events
    assert len(inv_events) >= 1
    retest_inv = inv_events[-1]
    assert retest_inv.effective_direction == FvgReactionDirection.BULLISH_SUPPORT
    assert retest_inv.lifecycle_phase == "INVERTED"
    assert retest_inv.status == FvgRetestStatus.RESPECT_CONFIRMED


def test_22_no_retroactive_inversion_retest() -> None:
    """Test 22: Pre-inversion events keep their original direction; not retroactively reclassified."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 1: Normal Bullish Support test
        make_candle(t0, "4015.00", "4015.00", "4007.00", "4009.00"),
        # Bar 2: Respect bounce
        make_candle(t0 + timedelta(hours=1), "4009.00", "4018.00", "4009.00", "4016.00"),
        # Bar 3: Later breaks down through zone (invalidation)
        make_candle(t0 + timedelta(hours=2), "4016.00", "4016.00", "3990.00", "3992.00"),
        # Bar 4: Inversion retest
        make_candle(t0 + timedelta(hours=3), "3992.00", "4005.00", "3992.00", "3998.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    first_event = summary.retest_events[0]
    # Retest 1 must stay strictly BULLISH_SUPPORT and NORMAL
    assert first_event.effective_direction == FvgReactionDirection.BULLISH_SUPPORT
    assert first_event.lifecycle_phase == "NORMAL"
    assert first_event.status == FvgRetestStatus.RESPECT_CONFIRMED


def test_23_invalidated_zone_rejects_future_respect() -> None:
    """Test 23: An invalidated normal zone does not create new normal respect events."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 1: Invalidates zone by body close below bottom (close = 3995 < 4000)
        make_candle(t0, "4015.00", "4015.00", "3990.00", "3995.00"),
        # Bar 2: Departs below
        make_candle(t0 + timedelta(hours=1), "3995.00", "3995.00", "3980.00", "3985.00"),
        # Bar 3: Re-enters zone (not inverted)
        make_candle(t0 + timedelta(hours=2), "3985.00", "4008.00", "3985.00", "4007.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    # The touch bar itself was invalidated; subsequent bars do not create valid normal respect
    assert summary.confirmed_respect_count == 0


def test_24_fully_filled_semantics_preserved() -> None:
    """Test 24: A wick traversing the full zone reaches FULLY_FILLED but respects if close holds above."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 1: Wick pierces all the way to 3999.00 (fully filled), but body closes at 4008.00 (valid!)
        make_candle(t0, "4015.00", "4015.00", "3999.00", "4008.00"),
        # Bar 2: Closes above top at 4016.00 -> confirms respect!
        make_candle(t0 + timedelta(hours=1), "4008.00", "4018.00", "4008.00", "4016.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.confirmed_respect_count == 1
    assert summary.retest_events[0].status == FvgRetestStatus.RESPECT_CONFIRMED


def test_25_historical_as_of_masks_future_touch() -> None:
    """Test 25: A point-in-time as_of query masks touches that occur after the as_of timestamp."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Touch 1 at t0
        make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00"),
        make_candle(t0 + timedelta(hours=1), "4009.00", "4018.00", "4009.00", "4016.00"),
        make_candle(t0 + timedelta(hours=2), "4016.00", "4020.00", "4014.00", "4018.00"),
        # Touch 2 at t0 + 3h
        make_candle(t0 + timedelta(hours=3), "4018.00", "4018.00", "4007.00", "4008.00"),
    ]
    # Query as of t0 + 2h (Touch 2 has NOT yet occurred in the real historical timeline)
    cutoff = candles[2].close_time
    summary = evaluate_fvg_respect(fvg, candles, as_of_utc=cutoff)
    assert summary.total_touch_count == 1
    assert summary.retest_events[0].retest_sequence_number == 1


def test_26_historical_as_of_masks_future_reaction() -> None:
    """Test 26: A point-in-time as_of query before reaction confirmation keeps status as PENDING."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Touch 1 at t0
        make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00"),
        # Reaction confirmation bar closes at t0 + 1h
        make_candle(t0 + timedelta(hours=1), "4009.00", "4018.00", "4009.00", "4016.00"),
    ]
    # Query as of Touch 1 close (reaction bar has NOT yet formed or closed)
    cutoff = candles[0].close_time
    summary = evaluate_fvg_respect(fvg, candles, as_of_utc=cutoff)
    assert summary.total_touch_count == 1
    assert summary.confirmed_respect_count == 0
    assert summary.retest_events[0].status == FvgRetestStatus.PENDING
    assert summary.retest_events[0].reaction_confirmation_time is None


def test_27_bid_ask_isolation() -> None:
    """Test 27: BID candles and ASK candles cannot be mixed in the same evaluation."""
    fvg = make_bullish_fvg("4000.00", "4010.00", price_type=PriceType.BID)
    t0 = fvg.formation_time
    candles = [
        make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00", price_type=PriceType.BID),
        make_candle(
            t0 + timedelta(hours=1),
            "4009.00",
            "4018.00",
            "4009.00",
            "4016.00",
            price_type=PriceType.ASK,
        ),
    ]
    with pytest.raises(FvgRespectDataError, match="Heterogeneous candle sequence"):
        evaluate_fvg_respect(fvg, candles)


def test_28_timeframe_isolation() -> None:
    """Test 28: Candles from mixed timeframes raise an error."""
    fvg = make_bullish_fvg("4000.00", "4010.00", timeframe=Timeframe.H1)
    t0 = fvg.formation_time
    candles = [
        make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00", timeframe=Timeframe.H1),
        make_candle(
            t0 + timedelta(hours=1),
            "4009.00",
            "4018.00",
            "4009.00",
            "4016.00",
            timeframe=Timeframe.H4,
        ),
    ]
    with pytest.raises(FvgRespectDataError, match="Heterogeneous candle sequence"):
        evaluate_fvg_respect(fvg, candles)


def test_29_d1_session_mode_isolation() -> None:
    """Test 29: Daily candles evaluate respect cleanly under D1 timeframe."""
    fvg = make_bullish_fvg("4000.00", "4050.00", timeframe=Timeframe.D1)
    t0 = fvg.formation_time
    candles = [
        make_candle(t0, "4060.00", "4060.00", "4020.00", "4030.00", timeframe=Timeframe.D1),
        make_candle(
            t0 + timedelta(days=1),
            "4030.00",
            "4080.00",
            "4030.00",
            "4070.00",
            timeframe=Timeframe.D1,
        ),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.timeframe == Timeframe.D1
    assert summary.confirmed_respect_count == 1


def test_30_partial_bar_rejection() -> None:
    """Test 30: Unclosed candles are strictly rejected."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    candles = [
        make_candle(
            fvg.formation_time, "4015.00", "4015.00", "4008.00", "4009.00", is_closed=False
        )
    ]
    with pytest.raises(FvgRespectDataError, match="Partial unclosed candle"):
        evaluate_fvg_respect(fvg, candles)


def test_31_synthetic_bar_rejection() -> None:
    """Test 31: Synthetic carry-forward candles are strictly rejected under STRICT_PIT."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    candles = [
        make_candle(
            fvg.formation_time, "4015.00", "4015.00", "4008.00", "4009.00", is_synthetic=True
        )
    ]
    with pytest.raises(FvgRespectDataError, match="Synthetic candle"):
        evaluate_fvg_respect(fvg, candles, pit_policy=PitPolicy.STRICT_PIT)


def test_32_strict_pit_vs_research_estimated() -> None:
    """Test 32: Validates configuration validation for tick and bar constraints."""
    with pytest.raises(FvgRespectError, match="min_bars_outside_zone"):
        FvgRespectConfig(min_bars_outside_zone=0)
    with pytest.raises(FvgRespectError, match="tick_size"):
        FvgRespectConfig(tick_size=Decimal("0"))


def test_33_deterministic_event_ids() -> None:
    """Test 33: Identical inputs produce deterministic and reproducible event IDs."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    id1 = generate_fvg_retest_event_id(fvg.id, 1, t0)
    id2 = generate_fvg_retest_event_id(fvg.id, 1, t0)
    assert id1 == id2
    assert ":RETEST:1:" in id1


def test_34_immutable_models() -> None:
    """Test 34: Retest models and summaries are frozen dataclasses (FrozenInstanceError on mutation)."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00")]
    summary = evaluate_fvg_respect(fvg, candles)
    with pytest.raises(FrozenInstanceError):
        summary.total_touch_count = 99  # type: ignore[misc]
    if summary.retest_events:
        with pytest.raises(FrozenInstanceError):
            summary.retest_events[0].status = FvgRetestStatus.FAILED_REACTION  # type: ignore[misc]


def test_35_zero_touch_safe_state() -> None:
    """Test 35: FVG that is never touched produces a safe summary with 0 counts."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Price stays completely above zone [4000, 4010]
        make_candle(t0, "4050.00", "4060.00", "4040.00", "4055.00"),
        make_candle(t0 + timedelta(hours=1), "4055.00", "4070.00", "4050.00", "4065.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.total_touch_count == 0
    assert summary.confirmed_respect_count == 0
    assert summary.failed_reaction_count == 0
    assert summary.pending_reaction_count == 0
    assert summary.consecutive_respect_count == 0
    assert summary.maximum_respect_streak == 0
    assert summary.last_touch_time is None
    assert summary.last_respect_time is None


def test_36_empty_input_safe_state() -> None:
    """Test 36: Empty candle sequence returns safe empty summary without raising exception."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    summary = evaluate_fvg_respect(fvg, [])
    assert summary.total_touch_count == 0
    assert len(summary.retest_events) == 0


def test_37_full_domain_pool_preserved() -> None:
    """Test 37: evaluate_all_fvgs_respect preserves all FVG domain items and order."""
    fvg1 = make_bullish_fvg("4000.00", "4010.00")
    fvg2 = make_bearish_fvg("4100.00", "4110.00")
    t0 = fvg1.formation_time
    candles = [make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00")]
    summaries = evaluate_all_fvgs_respect([fvg1, fvg2], candles)
    assert len(summaries) == 2
    assert summaries[0].fvg_id == fvg1.id
    assert summaries[1].fvg_id == fvg2.id


def test_38_multiple_retests_do_not_duplicate_same_episode() -> None:
    """Test 38: Price hovering inside zone across multiple bars does not inflate retest counts."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        make_candle(t0 + timedelta(hours=i), "4005.00", "4008.00", "4002.00", "4006.00")
        for i in range(10)
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.total_touch_count == 1
    assert len(summary.retest_events) == 1


# ── Extra Snapshot Filtering PIT Test ─────────────────────────────────────────
def test_filter_fvg_retest_events_as_of() -> None:
    """Verify filter_fvg_retest_events_as_of reverts in-flight reactions to PENDING."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Bar 0: touch at t0 (detected at close_time t0 + 1h)
        make_candle(t0, "4015.00", "4015.00", "4008.00", "4009.00"),
        # Bar 1: reaction confirmed at close_time t0 + 2h
        make_candle(t0 + timedelta(hours=1), "4009.00", "4018.00", "4009.00", "4016.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.retest_events[0].status == FvgRetestStatus.RESPECT_CONFIRMED

    # Filter snapshot as of t0 + 1h (Touch known, but reaction bar not yet closed)
    as_of = candles[0].close_time
    filtered = filter_fvg_retest_events_as_of(summary.retest_events, as_of)
    assert len(filtered) == 1
    assert filtered[0].status == FvgRetestStatus.PENDING
    assert filtered[0].reaction_confirmation_time is None


# ── Phase 4C-K1 Regression Tests ─────────────────────────────────────────────
def test_invalidated_counter_unit_is_explicit() -> None:
    """4C-K1: Verify FvgRespectSummary distinguishes unique FVGs from invalidated retest episodes."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [make_candle(t0, "4015.00", "4015.00", "3990.00", "3995.00")]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.total_retest_count == 1
    assert summary.invalidated_retest_count == 1
    assert summary.normal_invalidated_retest_count == 1
    assert summary.inverted_invalidated_retest_count == 0
    assert summary.is_normal_invalidated is True


def test_unique_fvg_count_not_confused_with_event_count() -> None:
    """4C-K1: 1 FVG with multiple invalidated episodes produces 1 unique FVG summary."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Retest 1: normal invalidation
        make_candle(t0, "4015.00", "4015.00", "3990.00", "3995.00"),
        # Retest 2: inversion retest (becomes inverted)
        make_candle(t0 + timedelta(hours=1), "3995.00", "4005.00", "3995.00", "3998.00"),
        # Departure downwards
        make_candle(t0 + timedelta(hours=2), "3998.00", "3998.00", "3985.00", "3988.00"),
        # Retest 3: inverted touch that gets invalidated by closing above 4010
        make_candle(t0 + timedelta(hours=3), "3988.00", "4015.00", "3988.00", "4012.00"),
    ]
    summaries = evaluate_all_fvgs_respect([fvg], candles)
    assert len(summaries) == 1  # Exactly 1 unique FVG!
    summary = summaries[0]
    assert summary.invalidated_retest_count == 2  # 2 invalidated retest episodes!
    assert summary.normal_invalidated_retest_count == 1
    assert summary.inverted_invalidated_retest_count == 1
    assert summary.is_inverted is True


def test_no_touch_before_formation() -> None:
    """4C-K1: Proves candle 1, 2, and 3 cannot produce touches before formation_time."""
    fvg = make_bullish_fvg(
        "4000.00", "4010.00", formation_time=datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    )
    pre_formation = [
        make_candle(
            datetime(2026, 10, 1, 0, 0, tzinfo=UTC), "4005.00", "4008.00", "4002.00", "4006.00"
        ),
        make_candle(
            datetime(2026, 10, 1, 4, 0, tzinfo=UTC), "4006.00", "4009.00", "4001.00", "4005.00"
        ),
        make_candle(
            datetime(2026, 10, 1, 8, 0, tzinfo=UTC), "4005.00", "4015.00", "4004.00", "4014.00"
        ),
    ]
    summary = evaluate_fvg_respect(fvg, pre_formation)
    assert summary.total_touch_count == 0
    assert len(summary.retest_events) == 0


def test_touch_open_time_vs_detection_time() -> None:
    """4C-K1: Verifies touch_bar_open_time is candle open and touch_detection_time is candle close."""
    fvg = make_bullish_fvg(
        "4000.00", "4010.00", formation_time=datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    )
    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    c1 = make_candle(t0, "4015.00", "4015.00", "4005.00", "4008.00", timeframe=Timeframe.H4)
    summary = evaluate_fvg_respect(fvg, [c1])
    assert summary.total_touch_count == 1
    ev = summary.retest_events[0]
    assert ev.touch_bar_open_time == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert ev.touch_detection_time == datetime(2026, 10, 1, 16, 0, tzinfo=UTC)
    assert ev.touch_detection_time > ev.touch_bar_open_time


def test_invalidation_detected_only_at_bar_close() -> None:
    """4C-K1: Invalidation on the touch candle is strictly stamped at bar close, never prior."""
    fvg = make_bullish_fvg(
        "4000.00", "4010.00", formation_time=datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    )
    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    c1 = make_candle(t0, "4015.00", "4015.00", "3990.00", "3995.00", timeframe=Timeframe.H4)
    summary = evaluate_fvg_respect(fvg, [c1])
    ev = summary.retest_events[0]
    assert ev.status == FvgRetestStatus.INVALIDATED
    assert ev.touch_bar_open_time == t0
    assert ev.touch_detection_time == datetime(2026, 10, 1, 16, 0, tzinfo=UTC)


def test_no_retroactive_inversion_event() -> None:
    """4C-K1: Historical events occurring before inversion maintain NORMAL phase permanently."""
    fvg = make_bullish_fvg("4000.00", "4010.00")
    t0 = fvg.formation_time
    candles = [
        # Retest 1: Normal respect
        make_candle(t0, "4015.00", "4015.00", "4007.00", "4008.00"),
        make_candle(t0 + timedelta(hours=1), "4008.00", "4018.00", "4007.00", "4015.00"),
        make_candle(t0 + timedelta(hours=2), "4015.00", "4020.00", "4014.00", "4018.00"),
        # Breakdown and inversion
        make_candle(t0 + timedelta(hours=3), "4018.00", "4018.00", "3990.00", "3992.00"),
        make_candle(t0 + timedelta(hours=4), "3992.00", "4005.00", "3992.00", "3998.00"),
    ]
    summary = evaluate_fvg_respect(fvg, candles)
    assert summary.is_inverted is True
    ev1 = summary.retest_events[0]
    assert ev1.lifecycle_phase == "NORMAL"
    assert ev1.effective_direction == FvgReactionDirection.BULLISH_SUPPORT
    assert ev1.status == FvgRetestStatus.RESPECT_CONFIRMED


def test_six_real_h4_retests_have_valid_departures() -> None:
    """4C-K1: Validates that all 6 retests of real H4 Bearish FVG had valid departure bars in between."""
    all_m1 = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(
        2026, 10, PriceType.BID
    )
    res = resample_m1_candles(all_m1, Timeframe.H4, daily_close_mode=DailyCloseMode.NEW_YORK_1700)
    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)
    raw_candles = [
        rc.candle
        for rc in res
        if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
    ]
    fvgs = detect_fair_value_gaps(raw_candles)

    target_id = "XAUUSD:market_data_lab_m1_derived_h4:BID:H4:20260928080000:BEARISH:r0"
    fvg = next(f for f in fvgs if f.id == target_id)
    summary = evaluate_fvg_respect(fvg, raw_candles)
    assert summary.total_touch_count == 6
    assert summary.confirmed_respect_count == 6

    # Verify between each sequential pair of events, at least 1 bar satisfied departure
    dep_dist = Decimal("1") * Decimal("0.01")
    for idx in range(len(summary.retest_events) - 1):
        cur_ev = summary.retest_events[idx]
        nxt_ev = summary.retest_events[idx + 1]
        bars_between = [
            c
            for c in raw_candles
            if cur_ev.touch_bar_open_time < c.open_time < nxt_ev.touch_bar_open_time
        ]
        valid_dep = any(
            (c.high < fvg.bottom_price) and (c.close <= fvg.bottom_price - dep_dist)
            for c in bars_between
        )
        assert valid_dep is True, (
            f"Missing valid departure between retest #{cur_ev.retest_sequence_number} and #{nxt_ev.retest_sequence_number}"
        )


def test_six_real_h4_respects_have_later_confirmation() -> None:
    """4C-K1: Confirms reaction_confirmation_time > touch_detection_time for all 6 real respects."""
    all_m1 = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(
        2026, 10, PriceType.BID
    )
    res = resample_m1_candles(all_m1, Timeframe.H4, daily_close_mode=DailyCloseMode.NEW_YORK_1700)
    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)
    raw_candles = [
        rc.candle
        for rc in res
        if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
    ]
    fvgs = detect_fair_value_gaps(raw_candles)

    target_id = "XAUUSD:market_data_lab_m1_derived_h4:BID:H4:20260928080000:BEARISH:r0"
    fvg = next(f for f in fvgs if f.id == target_id)
    summary = evaluate_fvg_respect(fvg, raw_candles)

    for ev in summary.retest_events:
        assert ev.status == FvgRetestStatus.RESPECT_CONFIRMED
        assert ev.reaction_confirmation_time is not None
        assert ev.reaction_confirmation_time > ev.touch_detection_time


def test_historical_as_of_masks_unconfirmed_reaction() -> None:
    """4C-K1: Historical as_of set to touch candle close keeps reaction status as PENDING."""
    all_m1 = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(
        2026, 10, PriceType.BID
    )
    res = resample_m1_candles(all_m1, Timeframe.H4, daily_close_mode=DailyCloseMode.NEW_YORK_1700)
    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)
    raw_candles = [
        rc.candle
        for rc in res
        if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
    ]
    fvgs = detect_fair_value_gaps(raw_candles)

    target_id = "XAUUSD:market_data_lab_m1_derived_h4:BID:H4:20260928080000:BEARISH:r0"
    fvg = next(f for f in fvgs if f.id == target_id)

    # First touch is at 2026-09-30 04:00 UTC (closes at 08:00 UTC)
    # Reaction confirms at 2026-09-30 12:00 UTC
    as_of = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
    summary = evaluate_fvg_respect(fvg, raw_candles, as_of_utc=as_of)
    assert summary.total_touch_count == 1
    ev = summary.retest_events[0]
    assert ev.status == FvgRetestStatus.PENDING
    assert ev.reaction_confirmation_time is None
