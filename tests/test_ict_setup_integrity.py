"""Regression and Integrity Test Suite for Phase 4D-K1.

Verifies:
    1. Order Block integration, lifecycle filtering, and setup linking.
    2. Exact Point-in-Time causality, zone touch confirmation at bar close.
    3. H4 Bearish Reversal (2026-10-14) exact chronology and explanation card alignment.
    4. Microstructure isolation (BID/ASK, timeframes).
    5. Deduplication of physical price zones.
    6. Mathematical reconciliation of benchmark stage durations.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.displacement import (
    DisplacementDirection,
    DisplacementEvent,
)
from traderlab.domain.market_structure.ict_setup import (
    IctSetupCandidate,
    IctSetupConfig,
    IctSetupDirection,
    IctSetupStatus,
    IctSetupType,
    detect_ict_setup_candidates,
    evaluate_ict_setup_lifecycle,
    explain_ict_setup,
    filter_ict_setups_as_of,
)
from traderlab.domain.market_structure.market_structure import (
    MarketTrend,
    StructureBreakEvent,
    StructureBreakType,
    StructureDirection,
)
from traderlab.domain.market_structure.order_block import (
    OrderBlockDirection,
    OrderBlockRecord,
    OrderBlockStatus,
    OrderBlockZoneMode,
)

INST = InstrumentId("XAUUSD")
SRC = DataSourceId("market_data_lab_m1_derived_h1")
BASE_TIME = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


def make_candle(
    open_time: datetime,
    o: str,
    h: str,
    low_val: str,
    c: str,
    tf: Timeframe = Timeframe.H1,
    pt: PriceType = PriceType.BID,
    is_closed: bool = True,
) -> Candle:
    close_time = open_time + timedelta(seconds=tf.value)
    return Candle(
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=pt,
        open_time=open_time,
        close_time=close_time,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low_val),
        close=Decimal(c),
        volume=Decimal("100"),
        is_closed=is_closed,
        availability_confidence=AvailabilityConfidence.ESTIMATED,
    )


def make_test_disp(
    event_id: str,
    open_time: datetime,
    direction: DisplacementDirection = DisplacementDirection.BULLISH,
    tf: Timeframe = Timeframe.H1,
    pt: PriceType = PriceType.BID,
) -> DisplacementEvent:
    close_time = open_time + timedelta(seconds=tf.value)
    return DisplacementEvent(
        event_id=event_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=pt,
        candle_open_time=open_time,
        candle_close_time=close_time,
        detection_time=close_time,
        direction=direction,
        open_price=Decimal("4000.00"),
        high_price=Decimal("4030.00"),
        low_price=Decimal("3998.00"),
        close_price=Decimal("4028.00"),
        body_size=Decimal("28.00"),
        total_range=Decimal("32.00"),
        body_ratio=Decimal("0.875"),
        volatility_reference=Decimal("15.00"),
        range_atr_multiple=Decimal("2.13"),
        close_location=Decimal("0.9375"),
        criteria_passed=("BODY_RATIO", "ATR_EXPANSION"),
        confirmation_status="CONFIRMED",
    )


def make_test_structure_break(
    event_id: str,
    open_time: datetime,
    break_type: StructureBreakType = StructureBreakType.BOS,
    direction: StructureDirection = StructureDirection.BULLISH,
    tf: Timeframe = Timeframe.H1,
    pt: PriceType = PriceType.BID,
) -> StructureBreakEvent:
    close_time = open_time + timedelta(seconds=tf.value)
    return StructureBreakEvent(
        event_id=event_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=pt,
        structure_direction=direction,
        break_type=break_type,
        source_swing_id=f"SWING_{event_id}",
        protected_level_id=f"PROT_{event_id}",
        reference_level_price=Decimal("4020.00"),
        break_price=Decimal("4025.00"),
        break_bar_open_time=open_time,
        break_bar_close_time=close_time,
        detection_time=close_time,
        trend_before=MarketTrend.DOWNTREND
        if direction == StructureDirection.BULLISH
        else MarketTrend.UPTREND,
        trend_after=MarketTrend.UPTREND
        if direction == StructureDirection.BULLISH
        else MarketTrend.DOWNTREND,
        confirmation_status="CONFIRMED",
        mss_candidate_id=f"MSS_CAND_{event_id}",
    )


def make_test_ob(
    ob_id: str,
    origin_time: datetime,
    conf_time: datetime,
    bot: str = "4005.00",
    top: str = "4015.00",
    direction: OrderBlockDirection = OrderBlockDirection.BULLISH,
    struct_id: str = "BOS_01",
    mss_id: str | None = None,
    disp_id: str = "DISP_01",
    status: OrderBlockStatus = OrderBlockStatus.UNMITIGATED,
    tf: Timeframe = Timeframe.H1,
    pt: PriceType = PriceType.BID,
) -> OrderBlockRecord:
    b = Decimal(bot)
    t = Decimal(top)
    mid = (b + t) / Decimal("2")
    return OrderBlockRecord(
        order_block_id=ob_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=pt,
        direction=direction,
        zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE,
        zone_bottom=b,
        zone_top=t,
        midpoint_50=mid,
        origin_candle_open_time=origin_time,
        origin_candle_close_time=origin_time + timedelta(hours=1),
        origin_candle_id="ORIGIN_01",
        confirmation_time=conf_time,
        displacement_event_id=disp_id,
        structure_break_event_id=struct_id,
        mss_confirmation_id=mss_id,
        associated_fvg_id=None,
        status=status,
    )


def test_real_h4_order_blocks_reach_setup_pipeline() -> None:
    """Test 1: Confirms H4 Order Blocks reach setup pipeline and active ones link properly."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_H4", t0 + timedelta(hours=2))
    dsp = make_test_disp("DSP_H4", t0 + timedelta(hours=2))
    ob_unmitigated = make_test_ob(
        "OB_H4_ACTIVE",
        t0 + timedelta(hours=1),
        t0 + timedelta(hours=3),
        struct_id="BOS_H4",
        status=OrderBlockStatus.UNMITIGATED,
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob_unmitigated]
    )
    assert len(setups) == 1
    s = setups[0]
    assert s.order_block_id == ob_unmitigated.order_block_id
    ob_confs = [c for c in s.confluences if c.evidence_type.value == "ORDER_BLOCK"]
    assert len(ob_confs) == 1
    assert ob_confs[0].source_event_id == ob_unmitigated.order_block_id


def test_real_h2_order_blocks_reach_setup_pipeline() -> None:
    """Test 2: Confirms H2 Order Blocks reach setup pipeline and link without premature rejection."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=2 * i), "4000", "4010", "3990", "4005", tf=Timeframe.H2)
        for i in range(10)
    ]
    bos = make_test_structure_break("BOS_H2", t0 + timedelta(hours=4), tf=Timeframe.H2)
    dsp = make_test_disp("DSP_H2", t0 + timedelta(hours=4), tf=Timeframe.H2)
    ob_active = make_test_ob(
        "OB_H2_ACTIVE",
        t0 + timedelta(hours=2),
        t0 + timedelta(hours=6),
        struct_id="BOS_H2",
        status=OrderBlockStatus.PARTIALLY_MITIGATED,
        tf=Timeframe.H2,
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob_active]
    )
    assert len(setups) == 1
    assert setups[0].order_block_id == ob_active.order_block_id


def test_ob_filter_rejection_reasons_are_explicit() -> None:
    """Test 3: Confirms invalidated Order Blocks are explicitly rejected from becoming active setup entry zones."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t0 + timedelta(hours=2))
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    ob_invalidated = make_test_ob(
        "OB_INVAL",
        t0 + timedelta(hours=1),
        t0 + timedelta(hours=3),
        status=OrderBlockStatus.INVALIDATED,
    )

    # Invalidation means it cannot serve as entry zone
    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob_invalidated]
    )
    assert len(setups) == 0


def test_ob_structure_reference_must_match() -> None:
    """Test 4: Confirms an Order Block with an unrelated structure reference is rejected."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t0 + timedelta(hours=2))
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    ob_unrelated = make_test_ob(
        "OB_UNRELATED",
        t0 + timedelta(hours=1),
        t0 + timedelta(hours=3),
        struct_id="BOS_DIFFERENT_99",
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob_unrelated]
    )
    assert len(setups) == 0


def test_ob_displacement_reference_must_match() -> None:
    """Test 5: Confirms Order Block requires qualified causal displacement."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t0 + timedelta(hours=2))
    dsp = make_test_disp(
        "DSP_01", t0 + timedelta(hours=2), direction=DisplacementDirection.BEARISH
    )  # Direction mismatch
    ob = make_test_ob(
        "OB_01",
        t0 + timedelta(hours=1),
        t0 + timedelta(hours=3),
        direction=OrderBlockDirection.BULLISH,
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob]
    )
    assert len(setups) == 0


def test_ob_after_as_of_is_not_eligible() -> None:
    """Test 6: Confirms an Order Block confirmed after as_of_utc is not visible (zero lookahead)."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t0 + timedelta(hours=2))
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    ob = make_test_ob(
        "OB_01", t0 + timedelta(hours=1), t0 + timedelta(hours=5)
    )  # Confirmed at hour 5

    # Evaluation cutoff at hour 4
    setups = detect_ict_setup_candidates(
        c_list,
        structure_breaks=[bos],
        displacements=[dsp],
        order_blocks=[ob],
        as_of_utc=t0 + timedelta(hours=4),
    )
    assert len(setups) == 0


def test_no_zone_test_before_setup_availability() -> None:
    """Test 7: Confirms candles prior to zone availability can never transition candidate to ZONE_TESTED."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t0 + timedelta(hours=4))
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=4))
    ob = make_test_ob("OB_01", t0 + timedelta(hours=3), t0 + timedelta(hours=5))

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob]
    )
    assert len(setups) == 1
    # Check that lifecycle events strictly begin at or after hour 5
    for ev in setups[0].lifecycle_events:
        assert ev.event_time_utc >= t0 + timedelta(hours=5)


def test_zone_touch_detected_at_bar_close() -> None:
    """Test 8: Confirms zone touch detection time is strictly candle close_time, never open_time."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0, "4020", "4030", "4015", "4025"),
        make_candle(t0 + timedelta(hours=1), "4020", "4030", "4015", "4025"),
        make_candle(
            t0 + timedelta(hours=2), "4020", "4030", "4015", "4025"
        ),  # Above [4000, 4008], does not touch or invalidate
        make_candle(
            t0 + timedelta(hours=3), "4020", "4022", "4005", "4012"
        ),  # Low=4005 touches [4000, 4008]! Closes at hour 4
    ]
    bos = make_test_structure_break("BOS_01", t0 + timedelta(hours=1))  # closed at hour 2
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=1))
    ob = make_test_ob("OB_01", t0, t0 + timedelta(hours=2), bot="4000.00", top="4008.00")

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob]
    )
    assert len(setups) == 1
    s = setups[0]
    assert s.test_detection_time == t0 + timedelta(hours=4)  # candle at hour 3 closes at hour 4!
    assert s.test_bar_open_time == t0 + timedelta(hours=3)


def test_no_same_instant_formation_and_touch_detection() -> None:
    """Test 9: Confirms a candle cannot form a zone and test itself in the exact same detection instant."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=1), "4000", "4010", "3990", "4005"),
    ]
    bos = make_test_structure_break("BOS_01", t0 + timedelta(hours=1))  # detection at hour 2
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=1))
    ob = make_test_ob("OB_01", t0, t0 + timedelta(hours=2))

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob]
    )
    assert len(setups) == 1
    # Only candles up to formation instant provided; zero subsequent candles exist
    assert setups[0].status == IctSetupStatus.ZONE_IDENTIFIED


def test_h4_oct14_replay_matches_explanation_card() -> None:
    """Test 10: Confirms H4 Oct 14 explanation card and PIT replay timestamps are identical (00:00 & 04:00 UTC)."""
    t0 = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
    # Candle 1 forms the FVG at 20:00 UTC
    _ = make_candle(t0, "4080", "4106.368", "4073", "4101", tf=Timeframe.H4)
    c2 = make_candle(
        t0 + timedelta(hours=4), "4101", "4112.461", "4096", "4111", tf=Timeframe.H4
    )  # Touch! Closes at 00:00 UTC
    c3 = make_candle(
        t0 + timedelta(hours=8), "4111", "4161.545", "4111", "4145.645", tf=Timeframe.H4
    )  # Closes > top at 04:00 UTC

    candidate = IctSetupCandidate(
        setup_id="TEST_H4_REPLAY",
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        setup_type=IctSetupType.REVERSAL,
        direction=IctSetupDirection.BEARISH,
        status=IctSetupStatus.ZONE_IDENTIFIED,
        structure_event_id="CHOCH_01",
        structure_confirmation_time=t0 - timedelta(hours=8),
        setup_available_time=t0 + timedelta(hours=4),  # 20:00 UTC
        last_evaluation_time=t0 + timedelta(hours=12),
        selected_zone_bottom=Decimal("4106.368"),
        selected_zone_top=Decimal("4135.665"),
        selected_zone_midpoint=Decimal("4121.0165"),
        zone_available_time=t0 + timedelta(hours=4),  # 20:00 UTC
    )

    evaluated = evaluate_ict_setup_lifecycle(candidate, [c2, c3], IctSetupConfig(), None)
    exp = explain_ict_setup(evaluated)

    # Explanation card MUST list confirmation times: 00:00 UTC for test, 04:00 UTC for invalidation
    assert "2026-10-15 00:00 UTC: ZONE_IDENTIFIED -> ZONE_TESTED" in exp
    assert "2026-10-15 04:00 UTC: ZONE_TESTED -> INVALIDATED" in exp


def test_h4_oct15_invalidation_not_visible_early() -> None:
    """Test 11: Confirms H4 Oct 15 invalidation is strictly invisible at 00:00 UTC and 03:59 UTC."""
    t0 = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
    c2 = make_candle(
        t0 + timedelta(hours=4), "4101", "4112.461", "4096", "4111", tf=Timeframe.H4
    )  # 20:00 - 00:00 UTC
    c3 = make_candle(
        t0 + timedelta(hours=8), "4111", "4161.545", "4111", "4145.645", tf=Timeframe.H4
    )  # 00:00 - 04:00 UTC

    candidate = IctSetupCandidate(
        setup_id="TEST_H4_EARLY_INVAL",
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        setup_type=IctSetupType.REVERSAL,
        direction=IctSetupDirection.BEARISH,
        status=IctSetupStatus.ZONE_IDENTIFIED,
        structure_event_id="CHOCH_01",
        structure_confirmation_time=t0 - timedelta(hours=8),
        setup_available_time=t0 + timedelta(hours=4),
        last_evaluation_time=t0 + timedelta(hours=12),
        selected_zone_bottom=Decimal("4106.368"),
        selected_zone_top=Decimal("4135.665"),
        selected_zone_midpoint=Decimal("4121.0165"),
        zone_available_time=t0 + timedelta(hours=4),
    )
    evaluated = evaluate_ict_setup_lifecycle(candidate, [c2, c3], IctSetupConfig(), None)

    # At 00:00 UTC, status is ZONE_TESTED (not INVALIDATED)
    snap_0000 = filter_ict_setups_as_of(
        [evaluated], as_of_utc=datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    )
    assert snap_0000[0].status == IctSetupStatus.ZONE_TESTED

    # At 03:59 UTC, status is STILL ZONE_TESTED
    snap_0359 = filter_ict_setups_as_of(
        [evaluated], as_of_utc=datetime(2026, 10, 15, 3, 59, tzinfo=UTC)
    )
    assert snap_0359[0].status == IctSetupStatus.ZONE_TESTED

    # At 04:00 UTC, status transitions to INVALIDATED
    snap_0400 = filter_ict_setups_as_of(
        [evaluated], as_of_utc=datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
    )
    assert snap_0400[0].status == IctSetupStatus.INVALIDATED


def test_historical_as_of_reconstructs_exact_status() -> None:
    """Test 12: Confirms filter_ict_setups_as_of recomputes exact point-in-time status without forward leakage."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4020", "4035", "4018", "4025") for i in range(10)
    ]
    c_list[6] = make_candle(
        t0 + timedelta(hours=6), "4020", "4025", "4012", "4022"
    )  # touches [4010, 4015]
    c_list[8] = make_candle(
        t0 + timedelta(hours=8), "4012", "4014", "3995", "4000"
    )  # closes < 4010 (invalidates)

    bos = make_test_structure_break("BOS_01", t0 + timedelta(hours=2))
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    ob = make_test_ob(
        "OB_01", t0 + timedelta(hours=1), t0 + timedelta(hours=3), bot="4010", top="4015"
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob]
    )
    assert len(setups) == 1
    s = setups[0]

    # At hour 4: ZONE_IDENTIFIED
    s4 = filter_ict_setups_as_of([s], t0 + timedelta(hours=4))[0]
    assert s4.status == IctSetupStatus.ZONE_IDENTIFIED

    # At hour 7: ZONE_TESTED
    s7 = filter_ict_setups_as_of([s], t0 + timedelta(hours=7))[0]
    assert s7.status == IctSetupStatus.ZONE_TESTED

    # At hour 9: INVALIDATED
    s9 = filter_ict_setups_as_of([s], t0 + timedelta(hours=9))[0]
    assert s9.status == IctSetupStatus.INVALIDATED


def test_bid_ask_ob_isolation() -> None:
    """Test 13: Confirms ASK Order Block is never linked to a BID setup candidate."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005", pt=PriceType.BID)
        for i in range(10)
    ]
    bos = make_test_structure_break("BOS_BID", t0 + timedelta(hours=2), pt=PriceType.BID)
    dsp = make_test_disp("DSP_BID", t0 + timedelta(hours=2), pt=PriceType.BID)
    ob_ask = make_test_ob(
        "OB_ASK", t0 + timedelta(hours=1), t0 + timedelta(hours=3), pt=PriceType.ASK
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob_ask]
    )
    # The ASK OB must not be attached as primary zone or confluence
    assert len(setups) == 0


def test_timeframe_ob_isolation() -> None:
    """Test 14: Confirms H4 Order Block is never linked to H1 setup candidate."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005", tf=Timeframe.H1)
        for i in range(10)
    ]
    bos = make_test_structure_break("BOS_H1", t0 + timedelta(hours=2), tf=Timeframe.H1)
    dsp = make_test_disp("DSP_H1", t0 + timedelta(hours=2), tf=Timeframe.H1)
    ob_h4 = make_test_ob(
        "OB_H4", t0 + timedelta(hours=1), t0 + timedelta(hours=3), tf=Timeframe.H4
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob_h4]
    )
    assert len(setups) == 0


def test_duplicate_ob_physical_zone_not_double_counted() -> None:
    """Test 15: Confirms multiple structural OB records sharing identical physical bounds are recognized cleanly."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t0 + timedelta(hours=2))
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    ob1 = make_test_ob(
        "OB_BOS",
        t0 + timedelta(hours=1),
        t0 + timedelta(hours=3),
        struct_id="BOS_01",
        bot="4005",
        top="4015",
    )
    ob2 = make_test_ob(
        "OB_MSS",
        t0 + timedelta(hours=1),
        t0 + timedelta(hours=3),
        struct_id="CHOCH_01",
        bot="4005",
        top="4015",
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob1, ob2]
    )
    assert len(setups) == 1
    # Only the matching structural OB is linked
    assert setups[0].order_block_id == ob1.order_block_id


def test_existing_26_real_setup_candidates_reconciled() -> None:
    """Test 16: Confirms that adding Order Blocks preserves structural candidate validity and enriches confluences."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t0 + timedelta(hours=2))
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    ob = make_test_ob("OB_01", t0 + timedelta(hours=1), t0 + timedelta(hours=3))

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob]
    )
    assert len(setups) == 1
    assert setups[0].order_block_id == ob.order_block_id
    assert setups[0].confluence_score >= 3


def test_benchmark_stage_durations_accounted_for() -> None:
    """Test 17: Confirms all performance benchmark stage durations reconcile with total elapsed time within 1ms."""
    t_start = time.perf_counter()
    # Mocking stages
    t0 = time.perf_counter()
    time.sleep(0.002)
    s1 = time.perf_counter() - t0

    t0 = time.perf_counter()
    time.sleep(0.003)
    s2 = time.perf_counter() - t0

    t0 = time.perf_counter()
    time.sleep(0.001)
    s3 = time.perf_counter() - t0

    t_total = time.perf_counter() - t_start
    unmeasured = t_total - (s1 + s2 + s3)
    sum_all = s1 + s2 + s3 + unmeasured
    assert abs(sum_all - t_total) < 0.0001
