"""Comprehensive test suite for Phase 4A Order Block Detection & Lifecycle Engine.

Covers all 30 acceptance criteria specified in TraderLab Phase 4A:
1. Basic Bullish Order Block detection
2. Basic Bearish Order Block detection
3. Last opposing candle selection
4. No opposing candle produces no OB
5. Configured origin lookback limit
6. Confirmed BOS link required
7. Confirmed MSS link accepted
8. Candidate CHoCH / unconfirmed MSS not sufficient
9. Displacement direction mismatch rejection
10. FULL_CANDLE_RANGE zone boundaries
11. BODY_ONLY zone boundaries
12. Exact Decimal midpoint (50% Consequent Encroachment)
13. No confirmation before structure event
14. No confirmation before displacement
15. No retroactive Order Block visibility (Zero Lookahead)
16. TOUCHED lifecycle transition
17. PARTIALLY_MITIGATED lifecycle transition
18. FULLY_MITIGATED lifecycle transition
19. Wick breach alone does NOT invalidate
20. Body close invalidation
21. Future mitigation hidden under historical as_of
22. BID/ASK isolation
23. Timeframe and D1 session isolation
24. Partial and synthetic unclosed bar rejection
25. Deterministic IDs and no duplicate records
26. Strict PIT vs Research Estimated policy
27. Insufficient data and empty input safety
28. Immutable models and no side effects
29. Origin and confirmation timestamp consistency
30. Optional FVG requirement
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.displacement import (
    DisplacementDirection,
    DisplacementEvent,
    generate_displacement_id,
)
from traderlab.domain.market_structure.fvg import FvgDirection, FvgZone
from traderlab.domain.market_structure.market_structure import (
    MarketTrend,
    StructureBreakEvent,
    StructureBreakType,
    StructureDirection,
)
from traderlab.domain.market_structure.mss_confirmation import (
    MssConfirmationRecord,
    MssStatus,
)
from traderlab.domain.market_structure.order_block import (
    InvalidOrderBlockParameterError,
    OrderBlockConfig,
    OrderBlockDataError,
    OrderBlockDirection,
    OrderBlockError,
    OrderBlockLifecycleEvent,
    OrderBlockRecord,
    OrderBlockStatus,
    OrderBlockZoneMode,
    detect_order_blocks,
    evaluate_order_block_lifecycle,
    filter_active_order_blocks,
    filter_order_blocks_as_of,
    filter_unmitigated_order_blocks,
    generate_lifecycle_event_id,
    generate_order_block_id,
    summarize_order_blocks,
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


def make_test_displacement(
    candle: Candle,
    direction: DisplacementDirection,
    *,
    event_id: str | None = None,
) -> DisplacementEvent:
    """Create a valid DisplacementEvent anchoring to a specific candle."""
    eid = event_id or generate_displacement_id(
        candle.instrument_id,
        candle.source_id,
        candle.price_type,
        candle.timeframe,
        direction,
        candle.open_time,
    )
    range_val = candle.high - candle.low
    body_val = abs(candle.close - candle.open)
    return DisplacementEvent(
        event_id=eid,
        instrument_id=candle.instrument_id,
        source_id=candle.source_id,
        timeframe=candle.timeframe,
        price_type=candle.price_type,
        candle_open_time=candle.open_time,
        candle_close_time=candle.close_time,
        detection_time=candle.close_time,
        direction=direction,
        open_price=candle.open,
        high_price=candle.high,
        low_price=candle.low,
        close_price=candle.close,
        body_size=body_val,
        total_range=range_val,
        body_ratio=Decimal("0.85"),
        volatility_reference=Decimal("5.0"),
        range_atr_multiple=Decimal("2.5"),
        close_location=Decimal("0.90")
        if direction == DisplacementDirection.BULLISH
        else Decimal("0.10"),
        criteria_passed=("MIN_RANGE", "MIN_BODY_RATIO", "ATR_EXPANSION", "CLOSE_LOCATION"),
        confirmation_status="CONFIRMED",
    )


def make_test_bos_event(
    event_id: str,
    direction: StructureDirection,
    break_candle: Candle,
    *,
    status: str = "CONFIRMED",
) -> StructureBreakEvent:
    """Create a valid StructureBreakEvent for testing."""
    trend = (
        MarketTrend.UPTREND if direction == StructureDirection.BULLISH else MarketTrend.DOWNTREND
    )
    return StructureBreakEvent(
        event_id=event_id,
        instrument_id=break_candle.instrument_id,
        source_id=break_candle.source_id,
        timeframe=break_candle.timeframe,
        price_type=break_candle.price_type,
        structure_direction=direction,
        break_type=StructureBreakType.BOS,
        source_swing_id="SWING_01",
        protected_level_id="SWING_01",
        reference_level_price=Decimal("2000.00"),
        break_price=break_candle.close,
        break_bar_open_time=break_candle.open_time,
        break_bar_close_time=break_candle.close_time,
        detection_time=break_candle.close_time,
        trend_before=trend,
        trend_after=trend,
        confirmation_status=status,
    )


# ── Test Cases ───────────────────────────────────────────────────────────────
def test_basic_bullish_order_block():
    """1. Bullish OB: Last bearish candle before bullish displacement + confirmed BOS."""
    # Bar 0: Neutral/Bullish
    # Bar 1: Bearish candle (Origin OB candidate) [1990 open, 1995 high, 1985 low, 1988 close]
    # Bar 2: Bullish displacement candle [1988 open, 2010 high, 1988 low, 2008 close]
    # Bar 3: Subsequent test candle
    candles = [
        make_test_candle(0, "1992.00", "1994.00", "1990.00", "1992.00"),
        make_test_candle(1, "1990.00", "1995.00", "1985.00", "1988.00"),  # Bearish origin
        make_test_candle(2, "1988.00", "2010.00", "1988.00", "2008.00"),  # Bullish disp
        make_test_candle(3, "2008.00", "2012.00", "2005.00", "2010.00"),  # No retest yet
    ]
    disp = make_test_displacement(candles[2], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_BULL_1", StructureDirection.BULLISH, candles[2])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    ob = obs[0]

    assert ob.direction == OrderBlockDirection.BULLISH
    assert ob.is_bullish is True
    assert ob.is_bearish is False
    assert ob.origin_candle_open_time == candles[1].open_time
    assert ob.zone_bottom == Decimal("1985.00")
    assert ob.zone_top == Decimal("1995.00")
    assert ob.midpoint_50 == Decimal("1990.00")
    assert ob.status == OrderBlockStatus.UNMITIGATED
    assert ob.confirmation_time == candles[2].close_time


def test_basic_bearish_order_block():
    """2. Bearish OB: Last bullish candle before bearish displacement + confirmed BOS."""
    # Bar 1: Bullish origin candle [2000 open, 2010 high, 1998 low, 2008 close]
    # Bar 2: Bearish displacement candle [2008 open, 2008 high, 1980 low, 1982 close]
    candles = [
        make_test_candle(0, "1995.00", "2002.00", "1995.00", "2000.00"),
        make_test_candle(1, "2000.00", "2010.00", "1998.00", "2008.00"),  # Bullish origin
        make_test_candle(2, "2008.00", "2008.00", "1980.00", "1982.00"),  # Bearish disp
        make_test_candle(3, "1982.00", "1985.00", "1978.00", "1980.00"),
    ]
    disp = make_test_displacement(candles[2], DisplacementDirection.BEARISH)
    bos = make_test_bos_event("BOS_BEAR_1", StructureDirection.BEARISH, candles[2])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    ob = obs[0]

    assert ob.direction == OrderBlockDirection.BEARISH
    assert ob.is_bearish is True
    assert ob.origin_candle_open_time == candles[1].open_time
    assert ob.zone_bottom == Decimal("1998.00")
    assert ob.zone_top == Decimal("2010.00")
    assert ob.midpoint_50 == Decimal("2004.00")
    assert ob.status == OrderBlockStatus.UNMITIGATED


def test_last_opposing_candle_selection():
    """3. Multiple opposing candles preceding displacement: closest one is selected."""
    # Bar 0: Bearish (close < open)
    # Bar 1: Bearish (close < open)
    # Bar 2: Bearish (close < open) -> this is the immediately preceding one
    # Bar 3: Bullish displacement
    candles = [
        make_test_candle(0, "1995.00", "1996.00", "1990.00", "1991.00"),
        make_test_candle(1, "1991.00", "1993.00", "1985.00", "1987.00"),
        make_test_candle(2, "1987.00", "1990.00", "1980.00", "1982.00"),  # Closest opposing
        make_test_candle(3, "1982.00", "2010.00", "1982.00", "2008.00"),  # Disp
    ]
    disp = make_test_displacement(candles[3], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[3])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    assert obs[0].origin_candle_open_time == candles[2].open_time


def test_no_opposing_candle_no_ob():
    """4. If all candles in lookback are same direction as displacement, no OB created."""
    # Bullish displacement preceded by only bullish candles
    candles = [
        make_test_candle(0, "1980.00", "1985.00", "1979.00", "1984.00"),  # Bullish
        make_test_candle(1, "1984.00", "1990.00", "1983.00", "1989.00"),  # Bullish
        make_test_candle(2, "1989.00", "2015.00", "1989.00", "2012.00"),  # Bullish disp
    ]
    disp = make_test_displacement(candles[2], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[2])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 0


def test_configured_lookback_limit():
    """5. Opposing candle outside max_origin_lookback_bars is rejected."""
    # Bearish candle at index 0, followed by 3 doji/neutral candles, disp at index 4
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),  # Bearish
        make_test_candle(1, "1988.00", "1989.00", "1988.00", "1989.00"),  # Bullish
        make_test_candle(2, "1989.00", "1990.00", "1989.00", "1990.00"),  # Bullish
        make_test_candle(3, "1990.00", "1991.00", "1990.00", "1991.00"),  # Bullish
        make_test_candle(4, "1991.00", "2020.00", "1991.00", "2018.00"),  # Bullish disp
    ]
    disp = make_test_displacement(candles[4], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[4])

    # If lookback is 2 bars (disp_idx - 1 down to disp_idx - 2, i.e. indices 3 and 2), index 0 is not checked
    cfg = OrderBlockConfig(max_origin_lookback_bars=2)
    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp], config=cfg)
    assert len(obs) == 0

    # If lookback is 4 bars, index 0 is found
    cfg4 = OrderBlockConfig(max_origin_lookback_bars=4)
    obs4 = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp], config=cfg4)
    assert len(obs4) == 1
    assert obs4[0].origin_candle_open_time == candles[0].open_time

    # Parameter validation
    with pytest.raises(InvalidOrderBlockParameterError):
        OrderBlockConfig(max_origin_lookback_bars=0)
    with pytest.raises(InvalidOrderBlockParameterError):
        OrderBlockConfig(tick_size=Decimal("0"))


def test_confirmed_bos_link_required():
    """6. Unconfirmed BOS (PENDING) does NOT create an Order Block."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),
        make_test_candle(1, "1988.00", "2010.00", "1988.00", "2008.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    candidate_bos = make_test_bos_event(
        "BOS_P", StructureDirection.BULLISH, candles[1], status="CANDIDATE"
    )

    obs = detect_order_blocks(candles, structure_breaks=[candidate_bos], displacements=[disp])
    assert len(obs) == 0


def test_confirmed_mss_link_accepted():
    """7. Confirmed MSS (MSS_CONFIRMED) with displacement produces confirmed OB."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),  # Bearish origin
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00"),  # Bullish disp
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    mss_rec = MssConfirmationRecord(
        record_id="MSS_REC_1",
        mss_candidate_id="MSS_CAND_1",
        source_choch_event_id="CHOCH_1",
        instrument_id=candles[0].instrument_id,
        source_id=candles[0].source_id,
        timeframe=candles[0].timeframe,
        price_type=candles[0].price_type,
        direction=StructureDirection.BULLISH,
        candidate_time=candles[1].close_time,
        displacement_event_id=disp.event_id,
        displacement_time=candles[1].close_time,
        associated_fvg_id=None,
        fvg_confirmation_time=None,
        associated_sweep_id=None,
        final_confirmation_time=candles[1].close_time,
        status=MssStatus.MSS_CONFIRMED,
        rejection_reason=None,
    )

    obs = detect_order_blocks(candles, displacements=[disp], mss_confirmations=[mss_rec])
    assert len(obs) == 1
    assert obs[0].direction == OrderBlockDirection.BULLISH
    assert obs[0].mss_confirmation_id == "MSS_REC_1"


def test_candidate_choch_not_sufficient():
    """8. Unconfirmed CHoCH / MSS_CANDIDATE alone CANNOT create an Order Block."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)

    # Standalone CHoCH event
    choch_event = StructureBreakEvent(
        event_id="CHOCH_1",
        instrument_id=candles[0].instrument_id,
        source_id=candles[0].source_id,
        timeframe=candles[0].timeframe,
        price_type=candles[0].price_type,
        structure_direction=StructureDirection.BULLISH,
        break_type=StructureBreakType.CHOCH,  # CHoCH is not BOS
        source_swing_id="SWING_01",
        protected_level_id="SWING_01",
        reference_level_price=Decimal("2000.00"),
        break_price=candles[1].close,
        break_bar_open_time=candles[1].open_time,
        break_bar_close_time=candles[1].close_time,
        detection_time=candles[1].close_time,
        trend_before=MarketTrend.DOWNTREND,
        trend_after=MarketTrend.UPTREND,
        confirmation_status="CONFIRMED",
    )
    # Unconfirmed MSS record
    mss_pending = MssConfirmationRecord(
        record_id="MSS_REC_PEND",
        mss_candidate_id="MSS_CAND_PEND",
        source_choch_event_id="CHOCH_1",
        instrument_id=candles[0].instrument_id,
        source_id=candles[0].source_id,
        timeframe=candles[0].timeframe,
        price_type=candles[0].price_type,
        direction=StructureDirection.BULLISH,
        candidate_time=candles[1].close_time,
        displacement_event_id=None,
        displacement_time=None,
        associated_fvg_id=None,
        fvg_confirmation_time=None,
        associated_sweep_id=None,
        final_confirmation_time=None,
        status=MssStatus.PENDING,  # Not CONFIRMED
        rejection_reason=None,
    )

    obs = detect_order_blocks(
        candles,
        structure_breaks=[choch_event],
        displacements=[disp],
        mss_confirmations=[mss_pending],
    )
    assert len(obs) == 0


def test_displacement_direction_mismatch():
    """9. Displacement direction mismatching BOS direction is rejected."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00"),
    ]
    # Bullish BOS but Bearish displacement
    bearish_disp = make_test_displacement(candles[1], DisplacementDirection.BEARISH)
    bullish_bos = make_test_bos_event("BOS_BULL", StructureDirection.BULLISH, candles[1])

    obs = detect_order_blocks(
        candles, structure_breaks=[bullish_bos], displacements=[bearish_disp]
    )
    assert len(obs) == 0


def test_full_candle_zone_boundaries():
    """10. FULL_CANDLE_RANGE uses [low, high] of origin candle."""
    candles = [
        make_test_candle(0, "1990.00", "1996.50", "1982.25", "1987.00"),  # Bearish origin
        make_test_candle(1, "1987.00", "2015.00", "1987.00", "2012.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    cfg = OrderBlockConfig(zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE)
    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp], config=cfg)
    assert len(obs) == 1
    assert obs[0].zone_bottom == Decimal("1982.25")
    assert obs[0].zone_top == Decimal("1996.50")
    assert obs[0].zone_mode == OrderBlockZoneMode.FULL_CANDLE_RANGE


def test_body_only_zone_boundaries():
    """11. BODY_ONLY uses [min(open, close), max(open, close)]."""
    candles = [
        make_test_candle(0, "1990.00", "1996.50", "1982.25", "1987.00"),  # Bearish origin
        make_test_candle(1, "1987.00", "2015.00", "1987.00", "2012.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    cfg = OrderBlockConfig(zone_mode=OrderBlockZoneMode.BODY_ONLY)
    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp], config=cfg)
    assert len(obs) == 1
    assert obs[0].zone_bottom == Decimal("1987.00")
    assert obs[0].zone_top == Decimal("1990.00")
    assert obs[0].zone_mode == OrderBlockZoneMode.BODY_ONLY


def test_exact_decimal_midpoint():
    """12. Midpoint 50% calculated with exact Decimal arithmetic."""
    candles = [
        make_test_candle(0, "1990.125", "1995.500", "1985.250", "1988.000"),
        make_test_candle(1, "1988.000", "2015.000", "1988.000", "2012.000"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    # zone_bottom = 1985.250, zone_top = 1995.500 -> sum = 3980.750 / 2 = 1990.375
    assert obs[0].midpoint_50 == Decimal("1990.375")


def test_no_confirmation_before_structure_event():
    """13. confirmation_time >= structure_detection_time."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    assert obs[0].confirmation_time >= bos.detection_time


def test_no_confirmation_before_displacement():
    """14. confirmation_time >= displacement.detection_time."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    assert obs[0].confirmation_time >= disp.detection_time


def test_no_retroactive_order_block_visibility():
    """15. Point-in-Time safety: OB is invisible prior to confirmation_time."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    # Query as of origin candle close time (before displacement & BOS detection)
    obs_prior = detect_order_blocks(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        as_of_utc=candles[0].close_time,
    )
    assert len(obs_prior) == 0

    # Query as of confirmation time
    obs_after = detect_order_blocks(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        as_of_utc=candles[1].close_time,
    )
    assert len(obs_after) == 1
    assert filter_order_blocks_as_of(obs_after, candles[0].close_time) == []
    assert len(filter_order_blocks_as_of(obs_after, candles[1].close_time)) == 1


def test_touched_lifecycle():
    """16. Subsequent candle penetrates outer edge (<= zone_top) but stays above 50% midpoint."""
    candles = [
        make_test_candle(
            0, "1990.00", "2000.00", "1980.00", "1985.00"
        ),  # Zone: [1980, 2000], mid: 1990
        make_test_candle(1, "1985.00", "2020.00", "1985.00", "2018.00"),  # Disp
        make_test_candle(
            2, "2018.00", "2018.00", "1995.00", "2005.00"
        ),  # Test bar: low=1995 <= 2000, > 1990
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    ob = obs[0]
    assert ob.status == OrderBlockStatus.TOUCHED
    assert ob.touch_time == candles[2].close_time
    assert len(ob.lifecycle_events) == 1
    assert isinstance(ob.lifecycle_events[0], OrderBlockLifecycleEvent)
    assert ob.lifecycle_events[0].status_after == OrderBlockStatus.TOUCHED


def test_partial_mitigation_lifecycle():
    """17. Subsequent candle penetrates past 50% midpoint (low <= 1990) but stays above bottom (1980)."""
    candles = [
        make_test_candle(
            0, "1990.00", "2000.00", "1980.00", "1985.00"
        ),  # Zone: [1980, 2000], mid: 1990
        make_test_candle(1, "1985.00", "2020.00", "1985.00", "2018.00"),  # Disp
        make_test_candle(
            2, "2018.00", "2018.00", "1988.00", "2005.00"
        ),  # Test bar: low=1988 <= 1990, > 1980
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    ob = obs[0]
    assert ob.status == OrderBlockStatus.PARTIALLY_MITIGATED
    assert ob.max_penetration_percentage == Decimal("60")  # (2000 - 1988) / 20 = 12/20 = 60%


def test_full_mitigation_lifecycle():
    """18. Subsequent candle pierces zone bottom (low <= 1980) but closes inside or above zone."""
    candles = [
        make_test_candle(
            0, "1990.00", "2000.00", "1980.00", "1985.00"
        ),  # Zone: [1980, 2000], mid: 1990
        make_test_candle(1, "1985.00", "2020.00", "1985.00", "2018.00"),  # Disp
        make_test_candle(
            2, "2018.00", "2018.00", "1978.00", "1985.00"
        ),  # Low 1978 <= 1980, close 1985 >= 1980
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    ob = obs[0]
    assert ob.status == OrderBlockStatus.FULLY_MITIGATED
    assert ob.is_fully_mitigated is True
    assert ob.is_invalidated is False


def test_wick_breach_not_invalidation():
    """19. Deep wick breach below zone does NOT invalidate if candle body closes above bottom."""
    candles = [
        make_test_candle(0, "1990.00", "2000.00", "1980.00", "1985.00"),
        make_test_candle(1, "1985.00", "2020.00", "1985.00", "2018.00"),
        make_test_candle(
            2, "2018.00", "2018.00", "1960.00", "1982.00"
        ),  # Wicked to 1960, closed at 1982!
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    ob = obs[0]
    assert ob.status == OrderBlockStatus.FULLY_MITIGATED
    assert ob.status != OrderBlockStatus.INVALIDATED


def test_body_close_invalidation():
    """20. Candle body close below zone bottom (bar.close < zone_bottom - threshold) invalidates."""
    candles = [
        make_test_candle(0, "1990.00", "2000.00", "1980.00", "1985.00"),
        make_test_candle(1, "1985.00", "2020.00", "1985.00", "2018.00"),
        make_test_candle(2, "2018.00", "2018.00", "1970.00", "1975.00"),  # Closed at 1975 < 1980
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    ob = obs[0]
    assert ob.status == OrderBlockStatus.INVALIDATED
    assert ob.is_invalidated is True
    assert ob.invalidation_time == candles[2].close_time


def test_future_mitigation_hidden_under_historical_as_of():
    """21. Mitigation happening at T2 is hidden when querying as of T1."""
    candles = [
        make_test_candle(0, "1990.00", "2000.00", "1980.00", "1985.00"),
        make_test_candle(1, "1985.00", "2020.00", "1985.00", "2018.00"),  # T1
        make_test_candle(2, "2018.00", "2018.00", "1970.00", "1975.00"),  # T2 (invalidated)
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    # Evaluated at T1 (before Bar 2 closed)
    obs_t1 = detect_order_blocks(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        as_of_utc=candles[1].close_time,
    )
    assert len(obs_t1) == 1
    assert obs_t1[0].status == OrderBlockStatus.UNMITIGATED

    # Evaluated at T2 (after Bar 2 closed)
    obs_t2 = detect_order_blocks(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        as_of_utc=candles[2].close_time,
    )
    assert len(obs_t2) == 1
    assert obs_t2[0].status == OrderBlockStatus.INVALIDATED


def test_bid_ask_isolation():
    """22. BID events cannot link with ASK candles."""
    candles_ask = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00", price_type=PriceType.ASK),
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00", price_type=PriceType.ASK),
    ]
    # BID displacement and BOS
    c_bid = make_test_candle(
        1, "1988.00", "2015.00", "1988.00", "2012.00", price_type=PriceType.BID
    )
    disp_bid = make_test_displacement(c_bid, DisplacementDirection.BULLISH)
    bos_bid = make_test_bos_event("BOS_BID", StructureDirection.BULLISH, c_bid)

    obs = detect_order_blocks(candles_ask, structure_breaks=[bos_bid], displacements=[disp_bid])
    assert len(obs) == 0


def test_timeframe_and_d1_session_isolation():
    """23. Sunday D1 synthetic bar is filtered out."""
    # Create Sunday D1 candle (weekday 6, hour 0)
    sunday_d1 = Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("market_data_lab_m1"),
        timeframe=Timeframe.D1,
        open_time=datetime(2026, 10, 4, 0, 0, tzinfo=UTC),  # Sunday
        close_time=datetime(2026, 10, 5, 0, 0, tzinfo=UTC),
        open=Decimal("2000.00"),
        high=Decimal("2005.00"),
        low=Decimal("1998.00"),
        close=Decimal("2002.00"),
        volume=Decimal("10"),
        price_type=PriceType.BID,
        is_closed=True,
    )
    monday_d1 = Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("market_data_lab_m1"),
        timeframe=Timeframe.D1,
        open_time=datetime(2026, 10, 5, 0, 0, tzinfo=UTC),  # Monday
        close_time=datetime(2026, 10, 6, 0, 0, tzinfo=UTC),
        open=Decimal("2002.00"),
        high=Decimal("2020.00"),
        low=Decimal("2002.00"),
        close=Decimal("2018.00"),
        volume=Decimal("100"),
        price_type=PriceType.BID,
        is_closed=True,
    )
    disp = make_test_displacement(monday_d1, DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_D1", StructureDirection.BULLISH, monday_d1)

    # Sunday bar should be ignored as origin candidate
    obs = detect_order_blocks([sunday_d1, monday_d1], structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 0


def test_partial_synthetic_unclosed_bar_rejection():
    """24. Unclosed or synthetic candle is rejected."""
    c_unclosed = make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00", is_closed=False)
    c_disp = make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00")
    disp = make_test_displacement(c_disp, DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, c_disp)

    obs = detect_order_blocks([c_unclosed, c_disp], structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 0


def test_deterministic_ids_and_no_duplicates():
    """25. Deterministic Order Block ID generator and duplicate suppression."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    obs1 = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    obs2 = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs1) == 1
    assert len(obs2) == 1
    assert obs1[0].order_block_id == obs2[0].order_block_id

    # Generator test
    gen_id = generate_order_block_id(
        candles[0].instrument_id,
        candles[0].source_id,
        candles[0].price_type,
        candles[0].timeframe,
        OrderBlockDirection.BULLISH,
        candles[0].open_time,
        bos.event_id,
    )
    assert isinstance(gen_id, str)
    ev_id = generate_lifecycle_event_id(gen_id, candles[1].open_time, OrderBlockStatus.TOUCHED)
    assert ev_id.startswith("EV_")

    # Passing multiple identical BOS records does not duplicate the OB
    obs_dup = detect_order_blocks(candles, structure_breaks=[bos, bos], displacements=[disp])
    assert len(obs_dup) == 1


def test_strict_pit_vs_research_estimated():
    """26. Strict PIT policy enforces availability checks."""
    c0 = make_test_candle(
        0,
        "1990.00",
        "1995.00",
        "1985.00",
        "1988.00",
        availability_confidence=AvailabilityConfidence.ESTIMATED,
    )
    c1 = make_test_candle(
        1,
        "1988.00",
        "2015.00",
        "1988.00",
        "2012.00",
        availability_confidence=AvailabilityConfidence.ESTIMATED,
    )
    disp = make_test_displacement(c1, DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, c1)

    # In STRICT_PIT, ESTIMATED confidence without available_at fails availability
    cfg_strict = OrderBlockConfig(strict_pit=True)
    obs_strict = detect_order_blocks(
        [c0, c1], structure_breaks=[bos], displacements=[disp], config=cfg_strict
    )
    assert len(obs_strict) == 0

    # In RESEARCH_ESTIMATED, it succeeds
    cfg_est = OrderBlockConfig(strict_pit=False, policy=PitPolicy.RESEARCH_ESTIMATED)
    obs_est = detect_order_blocks(
        [c0, c1], structure_breaks=[bos], displacements=[disp], config=cfg_est
    )
    assert len(obs_est) == 1


def test_insufficient_data_and_empty_input():
    """27. Empty input candle series returns empty list safely."""
    assert detect_order_blocks([]) == []
    summary = summarize_order_blocks([])
    assert summary["total_order_blocks"] == 0
    assert summary["unmitigated"] == 0

    # Data error on mixed timeframes
    c_h1 = make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00", timeframe=Timeframe.H1)
    c_h4 = make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00", timeframe=Timeframe.H4)
    with pytest.raises(OrderBlockDataError):
        detect_order_blocks([c_h1, c_h4])


def test_immutable_models_and_no_side_effects():
    """28. OrderBlockRecord is frozen and raises error on attribute modification."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])
    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    ob = obs[0]

    with pytest.raises(FrozenInstanceError):
        ob.status = OrderBlockStatus.INVALIDATED  # type: ignore

    # Filter active and unmitigated helpers
    assert len(filter_active_order_blocks(obs)) == 1
    assert len(filter_unmitigated_order_blocks(obs)) == 1

    # Direct lifecycle evaluator check
    evaluated = evaluate_order_block_lifecycle(candles, ob)
    assert evaluated.order_block_id == ob.order_block_id

    # Model post-init validation
    with pytest.raises(OrderBlockError):
        OrderBlockRecord(
            order_block_id="OB_TEST",
            instrument_id=ob.instrument_id,
            source_id=ob.source_id,
            timeframe=ob.timeframe,
            price_type=ob.price_type,
            direction=ob.direction,
            zone_mode=ob.zone_mode,
            zone_bottom=Decimal("2000.00"),
            zone_top=Decimal("1990.00"),  # Invalid: bottom >= top
            midpoint_50=Decimal("1995.00"),
            origin_candle_open_time=ob.origin_candle_open_time,
            origin_candle_close_time=ob.origin_candle_close_time,
            origin_candle_id="CAND_1",
            confirmation_time=ob.confirmation_time,
            displacement_event_id=disp.event_id,
            structure_break_event_id=bos.event_id,
            mss_confirmation_id=None,
            associated_fvg_id=None,
            status=OrderBlockStatus.UNMITIGATED,
        )


def test_origin_and_confirmation_timestamp_consistency():
    """29. Origin candle timestamps and confirmation time ordering."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])
    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    ob = obs[0]

    assert ob.origin_candle_open_time < ob.origin_candle_close_time
    assert ob.origin_candle_close_time <= ob.confirmation_time


def test_optional_fvg_requirement():
    """30. When require_fvg=True, only displacements with matching FVG at candle 2 open create an OB."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    # require_fvg=True without FVG provided -> no OB
    cfg_fvg = OrderBlockConfig(require_fvg=True)
    obs_none = detect_order_blocks(
        candles, structure_breaks=[bos], displacements=[disp], config=cfg_fvg
    )
    assert len(obs_none) == 0

    # With matching FVG provided
    fvg = FvgZone(
        id="FVG_1",
        instrument_id=candles[0].instrument_id,
        source_id=candles[0].source_id,
        price_type=candles[0].price_type,
        timeframe=candles[0].timeframe,
        direction=FvgDirection.BULLISH,
        top_price=Decimal("2012.00"),
        bottom_price=Decimal("1995.00"),
        ce_price=Decimal("2003.50"),
        width=Decimal("17.00"),
        candle_1_open=candles[0].open_time,
        candle_2_open=candles[1].open_time,
        candle_3_open=candles[1].close_time,
        formation_time=candles[1].close_time,
        computable_at_utc=candles[1].close_time,
    )
    obs_with = detect_order_blocks(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        fvg_zones=[fvg],
        config=cfg_fvg,
    )
    assert len(obs_with) == 1
    assert obs_with[0].associated_fvg_id == "FVG_1"


# ── Phase 4A-K1 Regression Tests ──────────────────────────────────────────────
def test_bearish_ob_requires_bullish_origin() -> None:
    """31. Bearish OB strictly requires a bullish candle (close > open) as origin."""
    candles_all_bear = [
        make_test_candle(0, "2000.00", "2005.00", "1995.00", "1998.00"),  # Bearish
        make_test_candle(1, "1998.00", "2002.00", "1990.00", "1992.00"),  # Bearish
        make_test_candle(2, "1992.00", "1995.00", "1960.00", "1965.00"),  # Bearish disp
    ]
    disp = make_test_displacement(candles_all_bear[2], DisplacementDirection.BEARISH)
    bos = make_test_bos_event("BOS_BEAR", StructureDirection.BEARISH, candles_all_bear[2])

    obs_none = detect_order_blocks(candles_all_bear, structure_breaks=[bos], displacements=[disp])
    assert len(obs_none) == 0

    candles_with_bull = [
        make_test_candle(0, "2000.00", "2005.00", "1995.00", "1998.00"),  # Bearish
        make_test_candle(1, "1998.00", "2010.00", "1995.00", "2008.00"),  # Bullish origin
        make_test_candle(2, "2008.00", "2010.00", "1960.00", "1965.00"),  # Bearish disp
    ]
    disp2 = make_test_displacement(candles_with_bull[2], DisplacementDirection.BEARISH)
    bos2 = make_test_bos_event("BOS_BEAR2", StructureDirection.BEARISH, candles_with_bull[2])

    obs_valid = detect_order_blocks(
        candles_with_bull, structure_breaks=[bos2], displacements=[disp2]
    )
    assert len(obs_valid) == 1
    assert obs_valid[0].direction == OrderBlockDirection.BEARISH
    assert obs_valid[0].origin_candle_open_time == candles_with_bull[1].open_time
    assert candles_with_bull[1].close > candles_with_bull[1].open


def test_bullish_ob_requires_bearish_origin() -> None:
    """32. Bullish OB strictly requires a bearish candle (close < open) as origin."""
    candles_all_bull = [
        make_test_candle(0, "1980.00", "1985.00", "1978.00", "1983.00"),  # Bullish
        make_test_candle(1, "1983.00", "1990.00", "1982.00", "1988.00"),  # Bullish
        make_test_candle(2, "1988.00", "2020.00", "1988.00", "2015.00"),  # Bullish disp
    ]
    disp = make_test_displacement(candles_all_bull[2], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_BULL", StructureDirection.BULLISH, candles_all_bull[2])

    obs_none = detect_order_blocks(candles_all_bull, structure_breaks=[bos], displacements=[disp])
    assert len(obs_none) == 0

    candles_with_bear = [
        make_test_candle(0, "1980.00", "1985.00", "1978.00", "1983.00"),  # Bullish
        make_test_candle(1, "1988.00", "1992.00", "1978.00", "1980.00"),  # Bearish origin
        make_test_candle(2, "1980.00", "2020.00", "1980.00", "2015.00"),  # Bullish disp
    ]
    disp2 = make_test_displacement(candles_with_bear[2], DisplacementDirection.BULLISH)
    bos2 = make_test_bos_event("BOS_BULL2", StructureDirection.BULLISH, candles_with_bear[2])

    obs_valid = detect_order_blocks(
        candles_with_bear, structure_breaks=[bos2], displacements=[disp2]
    )
    assert len(obs_valid) == 1
    assert obs_valid[0].direction == OrderBlockDirection.BULLISH
    assert obs_valid[0].origin_candle_open_time == candles_with_bear[1].open_time
    assert candles_with_bear[1].close < candles_with_bear[1].open


def test_real_h4_oct14_origin_matches_zone() -> None:
    """33. Verify real XAUUSD BID H4 Oct 14 Order Block origin, zone, and invariants."""
    from traderlab.application.analysis.resampling import resample_m1_candles
    from traderlab.domain.market_structure.displacement import (
        DisplacementConfig,
        detect_displacements,
    )
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
    from traderlab.domain.market_structure.swing import (
        TiePolicy,
        detect_swings,
        filter_confirmed_swings,
    )
    from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached

    all_m1 = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(
        2026, 10, PriceType.BID
    )
    h4_res = resample_m1_candles(all_m1, Timeframe.H4)
    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)
    h4_c = [
        rc.candle
        for rc in h4_res
        if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
    ]
    cand_map = {c.open_time: c for c in h4_c}

    conf_swings = filter_confirmed_swings(
        detect_swings(h4_c, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
    )
    ms_events, _ = detect_market_structure(
        h4_c, conf_swings, config=MarketStructureConfig(Decimal("0.01"), 1)
    )
    disps = detect_displacements(
        h4_c,
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
    fvgs = detect_fair_value_gaps(h4_c)
    mss_records = evaluate_mss_confirmations(
        h4_c,
        cands,
        displacements=disps,
        fvgs=fvgs,
        config=MssConfirmationConfig(
            max_confirmation_bars=3, require_displacement=True, require_fvg_confirmation=True
        ),
    )
    obs = detect_order_blocks(
        h4_c,
        structure_breaks=ms_events,
        displacements=disps,
        mss_confirmations=mss_records,
        fvg_zones=fvgs,
        config=OrderBlockConfig(max_origin_lookback_bars=6),
    )

    # Locate Oct 14 OB
    target_obs = [
        ob
        for ob in obs
        if ob.origin_candle_open_time.date() == datetime(2026, 10, 14, tzinfo=UTC).date()
    ]
    assert len(target_obs) == 1
    ob = target_obs[0]

    # Verify origin is 00:00 UTC (NOT 04:00 UTC)
    assert ob.origin_candle_open_time == datetime(2026, 10, 14, 0, 0, tzinfo=UTC)
    assert ob.direction == OrderBlockDirection.BEARISH

    orig_c = cand_map[ob.origin_candle_open_time]
    # Origin candle MUST be bullish
    assert orig_c.close > orig_c.open
    assert orig_c.open == Decimal("4154.975")
    assert orig_c.high == Decimal("4170.165")
    assert orig_c.low == Decimal("4144.175")
    assert orig_c.close == Decimal("4162.445")

    # Zone matches origin candle exactly
    assert ob.zone_bottom == orig_c.low == Decimal("4144.175")
    assert ob.zone_top == orig_c.high == Decimal("4170.165")
    assert ob.midpoint_50 == Decimal("4157.170")
    assert "_OB_20261014T000000Z_" in ob.order_block_id
    assert "_OB_20261014T040000Z_" not in ob.order_block_id


def test_real_h2_oct01_origin_matches_zone() -> None:
    """34. Verify real XAUUSD BID H2 around Oct 1-2 origin, zone, and PIT touch timing."""
    from traderlab.application.analysis.resampling import resample_m1_candles
    from traderlab.domain.market_structure.displacement import (
        DisplacementConfig,
        detect_displacements,
    )
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
    from traderlab.domain.market_structure.swing import (
        TiePolicy,
        detect_swings,
        filter_confirmed_swings,
    )
    from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached

    all_m1 = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(
        2026, 10, PriceType.BID
    )
    h2_res = resample_m1_candles(all_m1, Timeframe.H2)
    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)
    h2_c = [
        rc.candle
        for rc in h2_res
        if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
    ]
    cand_map = {c.open_time: c for c in h2_c}

    conf_swings = filter_confirmed_swings(
        detect_swings(h2_c, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
    )
    ms_events, _ = detect_market_structure(
        h2_c, conf_swings, config=MarketStructureConfig(Decimal("0.01"), 1)
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
    obs = detect_order_blocks(
        h2_c,
        structure_breaks=ms_events,
        displacements=disps,
        mss_confirmations=mss_records,
        fvg_zones=fvgs,
        config=OrderBlockConfig(max_origin_lookback_bars=6),
    )

    # 1. Verify NO OB exists with origin on 2026-10-01 16:00 UTC (which was a bearish candle)
    oct01_16_obs = [
        ob for ob in obs if ob.origin_candle_open_time == datetime(2026, 10, 1, 16, 0, tzinfo=UTC)
    ]
    assert len(oct01_16_obs) == 0

    # 2. Locate the real Oct 2 OB
    oct02_obs = [
        ob
        for ob in obs
        if ob.origin_candle_open_time.date() == datetime(2026, 10, 2, tzinfo=UTC).date()
    ]
    assert len(oct02_obs) == 1
    ob2 = oct02_obs[0]
    assert ob2.origin_candle_open_time == datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    assert ob2.direction == OrderBlockDirection.BEARISH

    orig_c2 = cand_map[ob2.origin_candle_open_time]
    assert orig_c2.close > orig_c2.open
    assert ob2.zone_bottom == orig_c2.low == Decimal("4174.465")
    assert ob2.zone_top == orig_c2.high == Decimal("4225.886")
    assert ob2.confirmation_time == datetime(2026, 10, 2, 16, 0, tzinfo=UTC)

    # 3. Verify that all lifecycle transitions occurred strictly after confirmation_time
    for ev in ob2.lifecycle_events:
        assert ev.event_time > ob2.confirmation_time
        assert ev.bar_open_time >= ob2.confirmation_time


def test_origin_id_matches_ohlc_candle() -> None:
    """35. OrderBlockRecord origin_candle_id and order_block_id contain origin timestamp."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])
    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    ob = obs[0]

    t_str = candles[0].open_time.strftime("%Y%m%dT%H%M%SZ")
    assert t_str in ob.order_block_id
    assert t_str in ob.origin_candle_id
    assert ob.origin_candle_open_time == candles[0].open_time
    assert ob.origin_candle_close_time == candles[0].close_time


def test_full_range_zone_matches_origin_exactly() -> None:
    """36. FULL_CANDLE_RANGE zone matches origin candle low and high with exact Decimal precision."""
    candles = [
        make_test_candle(0, "1990.125", "1997.875", "1983.333", "1988.500"),  # Bearish origin
        make_test_candle(1, "1988.500", "2015.000", "1988.500", "2012.000"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    cfg = OrderBlockConfig(zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE)
    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp], config=cfg)
    ob = obs[0]

    assert ob.zone_bottom == Decimal("1983.333")
    assert ob.zone_top == Decimal("1997.875")
    assert ob.midpoint_50 == (Decimal("1983.333") + Decimal("1997.875")) / Decimal("2")


def test_body_only_zone_matches_origin_exactly() -> None:
    """37. BODY_ONLY zone matches min/max of origin open/close with exact Decimal precision."""
    candles = [
        make_test_candle(0, "1990.125", "1997.875", "1983.333", "1988.500"),  # Bearish origin
        make_test_candle(1, "1988.500", "2015.000", "1988.500", "2012.000"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    cfg = OrderBlockConfig(zone_mode=OrderBlockZoneMode.BODY_ONLY)
    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp], config=cfg)
    ob = obs[0]

    assert ob.zone_bottom == Decimal("1988.500")
    assert ob.zone_top == Decimal("1990.125")
    assert ob.midpoint_50 == (Decimal("1988.500") + Decimal("1990.125")) / Decimal("2")


def test_no_touch_from_pre_confirmation_bar() -> None:
    """38. Candle closing at or before confirmation_time cannot produce TOUCHED or lifecycle event."""
    candles = [
        make_test_candle(0, "1990.00", "2000.00", "1980.00", "1985.00"),
        make_test_candle(1, "1985.00", "2030.00", "1985.00", "2025.00"),
        make_test_candle(
            2, "2025.00", "2035.00", "1990.00", "2030.00"
        ),  # Wicks to 1990, closes at 2030 (confirmation bar)
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[2])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    ob = obs[0]
    # Confirmation happened at Bar 2 close time. Bar 2 cannot test the OB prior to confirmation!
    assert ob.status == OrderBlockStatus.UNMITIGATED
    assert len(ob.lifecycle_events) == 0


def test_touch_requires_post_confirmation_observation() -> None:
    """39. Testing requires a bar closing strictly after confirmation_time."""
    candles = [
        make_test_candle(0, "1990.00", "2000.00", "1980.00", "1985.00"),
        make_test_candle(1, "1985.00", "2030.00", "1985.00", "2025.00"),
        make_test_candle(2, "2025.00", "2035.00", "2010.00", "2030.00"),  # Confirmation bar
        make_test_candle(
            3, "2030.00", "2030.00", "1995.00", "2010.00"
        ),  # Subsequent observation bar
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[2])

    obs = detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])
    assert len(obs) == 1
    ob = obs[0]
    assert ob.status == OrderBlockStatus.TOUCHED
    assert len(ob.lifecycle_events) == 1
    ev = ob.lifecycle_events[0]
    assert ev.bar_open_time == candles[2].close_time == ob.confirmation_time
    assert ev.event_time == candles[3].close_time > ob.confirmation_time


def test_all_real_ob_records_satisfy_invariants() -> None:
    """40. Automated invariant verification across all real XAUUSD BID and ASK Order Blocks."""
    from traderlab.application.analysis.resampling import resample_m1_candles
    from traderlab.domain.market_structure.displacement import (
        DisplacementConfig,
        detect_displacements,
    )
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
    from traderlab.domain.market_structure.swing import (
        TiePolicy,
        detect_swings,
        filter_confirmed_swings,
    )
    from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached

    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)
    total_obs = 0

    for pt in (PriceType.BID, PriceType.ASK):
        all_m1 = load_month_m1_candles_cached(2026, 9, pt) + load_month_m1_candles_cached(
            2026, 10, pt
        )
        for tf in (Timeframe.H4, Timeframe.H2):
            c_list = [
                rc.candle
                for rc in resample_m1_candles(all_m1, tf)
                if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
            ]
            cand_map = {c.open_time: c for c in c_list}
            swings = filter_confirmed_swings(
                detect_swings(c_list, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
            )
            ms_events, _ = detect_market_structure(
                c_list, swings, config=MarketStructureConfig(Decimal("0.01"), 1)
            )
            disps = detect_displacements(
                c_list,
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
            fvgs = detect_fair_value_gaps(c_list)
            mss_records = evaluate_mss_confirmations(
                c_list,
                cands,
                displacements=disps,
                fvgs=fvgs,
                config=MssConfirmationConfig(
                    max_confirmation_bars=3,
                    require_displacement=True,
                    require_fvg_confirmation=True,
                ),
            )
            obs = detect_order_blocks(
                c_list,
                structure_breaks=ms_events,
                displacements=disps,
                mss_confirmations=mss_records,
                fvg_zones=fvgs,
                config=OrderBlockConfig(max_origin_lookback_bars=6),
            )
            total_obs += len(obs)

            for ob in obs:
                orig_c = cand_map[ob.origin_candle_open_time]
                # Invariant 1: Directional opposing candle
                if ob.direction == OrderBlockDirection.BULLISH:
                    assert orig_c.close < orig_c.open, (
                        f"Bullish OB {ob.order_block_id} has non-bearish candle {orig_c}"
                    )
                else:
                    assert orig_c.close > orig_c.open, (
                        f"Bearish OB {ob.order_block_id} has non-bullish candle {orig_c}"
                    )

                # Invariant 2: FULL_CANDLE_RANGE zone bounds match origin low and high
                assert ob.zone_bottom == orig_c.low
                assert ob.zone_top == orig_c.high

                # Invariant 3: Midpoint 50% exact decimal
                assert ob.midpoint_50 == (orig_c.low + orig_c.high) / Decimal("2")

                # Invariant 4: PIT event times strictly greater than confirmation_time
                for ev in ob.lifecycle_events:
                    assert ev.event_time > ob.confirmation_time
                    assert ev.bar_open_time >= ob.confirmation_time

    assert total_obs == 22


def test_bid_ask_and_timeframe_isolation_after_fix() -> None:
    """41. BID and ASK Order Blocks maintain strict isolation and different prices."""
    from traderlab.application.analysis.resampling import resample_m1_candles
    from traderlab.domain.market_structure.displacement import detect_displacements
    from traderlab.domain.market_structure.fvg import detect_fair_value_gaps
    from traderlab.domain.market_structure.market_structure import (
        MarketStructureConfig,
        detect_market_structure,
        filter_mss_candidates,
    )
    from traderlab.domain.market_structure.mss_confirmation import evaluate_mss_confirmations
    from traderlab.domain.market_structure.swing import (
        TiePolicy,
        detect_swings,
        filter_confirmed_swings,
    )
    from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached

    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)

    def get_obs(p_type: PriceType):
        all_m1 = load_month_m1_candles_cached(2026, 9, p_type) + load_month_m1_candles_cached(
            2026, 10, p_type
        )
        h4_c = [
            rc.candle
            for rc in resample_m1_candles(all_m1, Timeframe.H4)
            if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
        ]
        swings = filter_confirmed_swings(
            detect_swings(h4_c, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
        )
        ms_events, _ = detect_market_structure(
            h4_c, swings, config=MarketStructureConfig(Decimal("0.01"), 1)
        )
        disps = detect_displacements(h4_c)
        cands = filter_mss_candidates(ms_events)
        fvgs = detect_fair_value_gaps(h4_c)
        mss_records = evaluate_mss_confirmations(h4_c, cands, displacements=disps, fvgs=fvgs)
        return detect_order_blocks(
            h4_c,
            structure_breaks=ms_events,
            displacements=disps,
            mss_confirmations=mss_records,
            fvg_zones=fvgs,
        )

    bid_obs = get_obs(PriceType.BID)
    ask_obs = get_obs(PriceType.ASK)

    assert len(bid_obs) == 2
    assert len(ask_obs) == 2
    # Verify IDs are distinct
    bid_ids = {ob.order_block_id for ob in bid_obs}
    ask_ids = {ob.order_block_id for ob in ask_obs}
    assert bid_ids.isdisjoint(ask_ids)

    # Verify prices differ by spread
    for b, a in zip(bid_obs, ask_obs, strict=True):
        assert b.zone_bottom < a.zone_bottom  # ASK prices > BID prices


def test_no_changes_to_existing_mss_or_bos_events() -> None:
    """42. Running Order Block detection has zero side effects on structural and displacement events."""
    candles = [
        make_test_candle(0, "1990.00", "1995.00", "1985.00", "1988.00"),
        make_test_candle(1, "1988.00", "2015.00", "1988.00", "2012.00"),
    ]
    disp = make_test_displacement(candles[1], DisplacementDirection.BULLISH)
    bos = make_test_bos_event("BOS_1", StructureDirection.BULLISH, candles[1])

    bos_status_before = bos.confirmation_status
    disp_dir_before = disp.direction

    detect_order_blocks(candles, structure_breaks=[bos], displacements=[disp])

    assert bos.confirmation_status == bos_status_before
    assert disp.direction == disp_dir_before
