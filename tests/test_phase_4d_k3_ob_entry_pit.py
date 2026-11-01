"""Phase 4D-K3 Order Block Entry Eligibility and Historical PIT Tests.

Verifies:
1. test_historical_invalidated_ob_remains_valid_at_setup_time
2. test_historical_ob_not_removed_by_future_invalidation
3. test_historical_ob_not_removed_by_future_full_mitigation
4. test_fully_mitigated_ob_cannot_be_active_entry
5. test_invalidated_ob_cannot_be_current_entry
6. test_reversal_uses_pit_reconstructed_ob
7. test_continuation_uses_pit_reconstructed_ob
8. test_pre_confirmation_ob_is_rejected
9. test_as_of_none_historical_scan_is_deterministic
10. test_historical_confluence_survives_future_invalidation
11. test_entry_zone_selection_matches_as_of_snapshot
12. test_no_bid_ask_or_timeframe_cross_contamination
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from traderlab.core.types import (
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
from traderlab.domain.market_structure.fvg import (
    FvgDirection,
    FvgZone,
)
from traderlab.domain.market_structure.ict_setup import (
    IctConfluenceType,
    IctSetupConfig,
    IctSetupStatus,
    IctSetupType,
    detect_ict_setup_candidates,
    filter_ict_setups_as_of,
)
from traderlab.domain.market_structure.liquidity_sweep import (
    LiquiditySide,
    LiquiditySweepEvent,
    LiquiditySweepStatus,
)
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
    OrderBlockDirection,
    OrderBlockLifecycleEvent,
    OrderBlockRecord,
    OrderBlockStatus,
    OrderBlockZoneMode,
)

INST = InstrumentId("XAUUSD")
SRC = DataSourceId("market_data_lab_m1_derived_h4")
BASE_TIME = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)


def make_candle(
    open_t: datetime,
    o: str,
    h: str,
    low_p: str,
    c: str,
    tf: Timeframe = Timeframe.H4,
    pt: PriceType = PriceType.BID,
) -> Candle:
    close_t = open_t + timedelta(seconds=tf.value)
    return Candle(
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=pt,
        open_time=open_t,
        close_time=close_t,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low_p),
        close=Decimal(c),
        volume=Decimal("100"),
        tick_volume=Decimal("100"),
        is_closed=True,
    )


def make_structure_break(
    event_id: str,
    open_time: datetime,
    break_type: StructureBreakType = StructureBreakType.BOS,
    direction: StructureDirection = StructureDirection.BEARISH,
    tf: Timeframe = Timeframe.H4,
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
        reference_level_price=Decimal("4000.00"),
        break_price=Decimal("3960.00"),
        break_bar_open_time=open_time,
        break_bar_close_time=close_time,
        detection_time=close_time,
        trend_before=MarketTrend.UPTREND
        if direction == StructureDirection.BEARISH
        else MarketTrend.DOWNTREND,
        trend_after=MarketTrend.DOWNTREND
        if direction == StructureDirection.BEARISH
        else MarketTrend.UPTREND,
        confirmation_status="CONFIRMED",
        mss_candidate_id=f"MSS_CAND_{event_id}",
    )


def make_disp(
    event_id: str,
    open_time: datetime,
    direction: DisplacementDirection = DisplacementDirection.BEARISH,
    tf: Timeframe = Timeframe.H4,
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
        low_price=Decimal("3950.00"),
        close_price=Decimal("3960.00"),
        body_size=Decimal("40.00"),
        total_range=Decimal("80.00"),
        body_ratio=Decimal("0.80"),
        volatility_reference=Decimal("15.00"),
        range_atr_multiple=Decimal("2.50"),
        close_location=Decimal("0.125"),
        criteria_passed=("BODY_RATIO", "ATR_EXPANSION"),
        confirmation_status="CONFIRMED",
    )


def make_ob(
    ob_id: str,
    origin_time: datetime,
    conf_time: datetime,
    bot: str = "3990.00",
    top: str = "4020.00",
    direction: OrderBlockDirection = OrderBlockDirection.BEARISH,
    struct_id: str | None = "BOS_01",
    mss_id: str | None = None,
    status: OrderBlockStatus = OrderBlockStatus.UNMITIGATED,
    inval_time: datetime | None = None,
    full_mit_time: datetime | None = None,
    lifecycle_events: tuple[OrderBlockLifecycleEvent, ...] = (),
    tf: Timeframe = Timeframe.H4,
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
        origin_candle_close_time=origin_time + timedelta(seconds=tf.value),
        origin_candle_id=f"ORIGIN_{ob_id}",
        confirmation_time=conf_time,
        displacement_event_id=f"DISP_{ob_id}",
        structure_break_event_id=struct_id,
        mss_confirmation_id=mss_id,
        associated_fvg_id=None,
        status=status,
        invalidation_time=inval_time,
        full_mitigation_time=full_mit_time,
        lifecycle_events=lifecycle_events,
    )


# ── 1. Historical Invalidated OB Remains Valid at Setup Time ──────────────────
def test_historical_invalidated_ob_remains_valid_at_setup_time() -> None:
    """1. An OB invalidated at period-end must be usable at setup formation time."""
    t0 = BASE_TIME
    t_conf = t0 + timedelta(hours=8)
    t_inval = t0 + timedelta(hours=24)
    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
        make_candle(t0 + timedelta(hours=12), "3955", "3970", "3950", "3960"),
    ]
    bos = make_structure_break(
        "BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)

    inval_ev = OrderBlockLifecycleEvent(
        event_id="EV_INVAL_01",
        order_block_id="OB_01",
        event_time=t_inval,
        bar_open_time=t_inval - timedelta(hours=4),
        bar_close_time=t_inval,
        status_before=OrderBlockStatus.UNMITIGATED,
        status_after=OrderBlockStatus.INVALIDATED,
        trigger_price=Decimal("4025.00"),
        penetration_depth=Decimal("5.00"),
        penetration_percentage=Decimal("25.00"),
        description="Invalidated",
    )
    ob = make_ob(
        "OB_01",
        t0,
        t_conf,
        status=OrderBlockStatus.INVALIDATED,
        inval_time=t_inval,
        lifecycle_events=(inval_ev,),
    )

    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    # Query at setup formation time (t0 + 8h)
    setups = detect_ict_setup_candidates(
        candles[:3],
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob],
        fvg_zones=[],
        config=cfg,
        as_of_utc=t_conf,
    )
    assert len(setups) == 1
    assert setups[0].order_block_id == "OB_01"
    assert setups[0].selected_zone_bottom == Decimal("3990.00")


# ── 2. Historical OB Not Removed by Future Invalidation ───────────────────────
def test_historical_ob_not_removed_by_future_invalidation() -> None:
    """2. Full period historical scan (as_of_utc=None) must not drop OBs due to future invalidation."""
    t0 = BASE_TIME
    t_conf = t0 + timedelta(hours=8)
    t_inval = t0 + timedelta(hours=24)
    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
        make_candle(t0 + timedelta(hours=12), "3955", "3970", "3950", "3960"),
        make_candle(t0 + timedelta(hours=16), "3960", "3975", "3955", "3965"),
        make_candle(t0 + timedelta(hours=20), "3965", "4025", "3960", "4022"),
    ]
    bos = make_structure_break(
        "BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)
    inval_ev = OrderBlockLifecycleEvent(
        event_id="EV_INVAL_01",
        order_block_id="OB_01",
        event_time=t_inval,
        bar_open_time=t_inval - timedelta(hours=4),
        bar_close_time=t_inval,
        status_before=OrderBlockStatus.UNMITIGATED,
        status_after=OrderBlockStatus.INVALIDATED,
        trigger_price=Decimal("4025.00"),
        penetration_depth=Decimal("5.00"),
        penetration_percentage=Decimal("25.00"),
        description="Invalidated",
    )
    ob = make_ob(
        "OB_01",
        t0,
        t_conf,
        status=OrderBlockStatus.INVALIDATED,
        inval_time=t_inval,
        lifecycle_events=(inval_ev,),
    )

    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    setups = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob],
        fvg_zones=[],
        config=cfg,
        as_of_utc=None,
    )
    assert len(setups) == 1
    assert setups[0].order_block_id == "OB_01"


# ── 3. Historical OB Not Removed by Future Full Mitigation ────────────────────
def test_historical_ob_not_removed_by_future_full_mitigation() -> None:
    """3. An OB that was active at setup time must not be removed by a future FULL_MITIGATION."""
    t0 = BASE_TIME
    t_conf = t0 + timedelta(hours=8)
    t_full_mit = t0 + timedelta(hours=20)
    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
        make_candle(t0 + timedelta(hours=12), "3955", "3970", "3950", "3960"),
        make_candle(t0 + timedelta(hours=16), "3960", "4019", "3955", "4000"),
    ]
    bos = make_structure_break(
        "BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)
    mit_ev = OrderBlockLifecycleEvent(
        event_id="EV_MIT_01",
        order_block_id="OB_01",
        event_time=t_full_mit,
        bar_open_time=t_full_mit - timedelta(hours=4),
        bar_close_time=t_full_mit,
        status_before=OrderBlockStatus.UNMITIGATED,
        status_after=OrderBlockStatus.FULLY_MITIGATED,
        trigger_price=Decimal("4019.00"),
        penetration_depth=Decimal("29.00"),
        penetration_percentage=Decimal("96.67"),
        description="Fully mitigated",
    )
    ob = make_ob(
        "OB_01",
        t0,
        t_conf,
        status=OrderBlockStatus.FULLY_MITIGATED,
        full_mit_time=t_full_mit,
        lifecycle_events=(mit_ev,),
    )

    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    setups = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob],
        fvg_zones=[],
        config=cfg,
        as_of_utc=None,
    )
    assert len(setups) == 1
    assert setups[0].order_block_id == "OB_01"


# ── 4. FULLY_MITIGATED OB Cannot Be Active Entry ─────────────────────────────
def test_fully_mitigated_ob_cannot_be_active_entry() -> None:
    """4. An OB that was already FULLY_MITIGATED at setup time must be rejected as an active entry zone."""
    t0 = BASE_TIME
    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
    ]
    # BOS occurs at t0+8h
    bos = make_structure_break(
        "BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)

    # OB confirmed at t0+4h and became FULLY_MITIGATED at t0+6h (BEFORE BOS at t0+8h)
    ob_fully_mit = make_ob(
        "OB_FULLY_MIT",
        t0,
        t0 + timedelta(hours=4),
        status=OrderBlockStatus.FULLY_MITIGATED,
        full_mit_time=t0 + timedelta(hours=6),
    )

    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    setups = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob_fully_mit],
        fvg_zones=[],
        config=cfg,
    )
    assert len(setups) == 0


# ── 5. INVALIDATED OB Cannot Be Current Entry ────────────────────────────────
def test_invalidated_ob_cannot_be_current_entry() -> None:
    """5. An OB that was already INVALIDATED at setup time must be rejected as an active entry zone."""
    t0 = BASE_TIME
    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
    ]
    bos = make_structure_break(
        "BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)

    # OB invalidated at t0+6h (before BOS at t0+8h)
    ob_inval = make_ob(
        "OB_INVAL",
        t0,
        t0 + timedelta(hours=4),
        status=OrderBlockStatus.INVALIDATED,
        inval_time=t0 + timedelta(hours=6),
    )

    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    setups = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob_inval],
        fvg_zones=[],
        config=cfg,
    )
    assert len(setups) == 0


# ── 6. Reversal Uses PIT Reconstructed OB ────────────────────────────────────
def test_reversal_uses_pit_reconstructed_ob() -> None:
    """6. Reversal setups evaluate OB eligibility using PIT reconstruction at eval_t."""
    t0 = BASE_TIME
    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
        make_candle(t0 + timedelta(hours=12), "3955", "3970", "3950", "3960"),
    ]
    choch = make_structure_break(
        "CHOCH_01", candles[1].open_time, StructureBreakType.CHOCH, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)
    sweep = LiquiditySweepEvent(
        event_id="SWEEP_01",
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        source_swing_id="SWING_PREV",
        liquidity_side=LiquiditySide.BUY_SIDE,
        level_price=Decimal("4020.00"),
        penetration_price=Decimal("4025.00"),
        penetration_ticks=Decimal("500"),
        penetration_threshold=Decimal("0.01"),
        sweep_bar_open_time=candles[0].open_time,
        sweep_bar_close_time=candles[0].close_time,
        detection_time=candles[0].close_time,
        pivot_time=candles[0].open_time - timedelta(hours=4),
        level_confirmation_time=candles[0].open_time,
        status=LiquiditySweepStatus.SWEEP,
    )
    mss = MssConfirmationRecord(
        record_id="MSS_01",
        mss_candidate_id=f"MSS_CAND_{choch.event_id}",
        source_choch_event_id=choch.event_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        direction=StructureDirection.BEARISH,
        candidate_time=candles[1].open_time,
        displacement_event_id=disp.event_id,
        displacement_time=disp.candle_open_time,
        associated_fvg_id="FVG_01",
        fvg_confirmation_time=candles[1].close_time,
        associated_sweep_id=sweep.event_id,
        final_confirmation_time=candles[1].close_time,
        status=MssStatus.MSS_CONFIRMED,
    )
    fvg = FvgZone(
        id="FVG_01",
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        direction=FvgDirection.BEARISH,
        candle_1_open=candles[0].open_time,
        candle_2_open=candles[1].open_time,
        candle_3_open=candles[2].open_time,
        formation_time=candles[2].close_time,
        top_price=Decimal("3990.00"),
        bottom_price=Decimal("3980.00"),
        ce_price=Decimal("3985.00"),
        width=Decimal("10.00"),
        computable_at_utc=candles[2].close_time,
    )
    # OB that was active at setup time, but invalidated later at t0+24h
    ob = make_ob(
        "OB_REV",
        t0,
        candles[1].close_time,
        mss_id="MSS_01",
        struct_id=None,
        status=OrderBlockStatus.INVALIDATED,
        inval_time=t0 + timedelta(hours=24),
    )

    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    setups = detect_ict_setup_candidates(
        candles,
        structure_breaks=[choch],
        displacements=[disp],
        sweeps=[sweep],
        mss_records=[mss],
        fvg_zones=[fvg],
        order_blocks=[ob],
        config=cfg,
    )
    assert len(setups) == 1
    assert setups[0].setup_type == IctSetupType.REVERSAL
    assert setups[0].order_block_id == "OB_REV"


# ── 7. Continuation Uses PIT Reconstructed OB ────────────────────────────────
def test_continuation_uses_pit_reconstructed_ob() -> None:
    """7. Continuation setups evaluate OB eligibility using PIT reconstruction at bos.detection_time."""
    t0 = BASE_TIME
    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
    ]
    bos = make_structure_break(
        "BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)
    ob = make_ob(
        "OB_CONT",
        t0,
        candles[1].close_time,
        struct_id="BOS_01",
        status=OrderBlockStatus.INVALIDATED,
        inval_time=t0 + timedelta(hours=20),
    )

    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    setups = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob],
        fvg_zones=[],
        config=cfg,
    )
    assert len(setups) == 1
    assert setups[0].setup_type == IctSetupType.CONTINUATION
    assert setups[0].order_block_id == "OB_CONT"


# ── 8. Pre-Confirmation OB Is Rejected ───────────────────────────────────────
def test_pre_confirmation_ob_is_rejected() -> None:
    """8. An OB whose confirmation_time is strictly after eval_t cannot be selected."""
    t0 = BASE_TIME
    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
    ]
    # BOS at candles[1].close_time = t0 + 8h
    bos = make_structure_break(
        "BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)

    # OB confirmed in the future at t0 + 12h
    ob_future_conf = make_ob(
        "OB_FUTURE",
        t0,
        t0 + timedelta(hours=12),
        struct_id="BOS_01",
        status=OrderBlockStatus.UNMITIGATED,
    )

    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    setups = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob_future_conf],
        fvg_zones=[],
        config=cfg,
    )
    assert len(setups) == 0


# ── 9. as_of_none Historical Scan Is Deterministic ───────────────────────────
def test_as_of_none_historical_scan_is_deterministic() -> None:
    """9. Calling detect_ict_setup_candidates with as_of_utc=None produces deterministic, identical output."""
    t0 = BASE_TIME
    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
    ]
    bos = make_structure_break(
        "BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)
    ob = make_ob(
        "OB_DET",
        t0,
        candles[1].close_time,
        struct_id="BOS_01",
        status=OrderBlockStatus.UNMITIGATED,
    )

    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    run1 = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob],
        fvg_zones=[],
        config=cfg,
    )
    run2 = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob],
        fvg_zones=[],
        config=cfg,
    )
    assert len(run1) == len(run2) == 1
    assert run1[0].setup_id == run2[0].setup_id
    assert run1[0].selected_zone_bottom == run2[0].selected_zone_bottom


# ── 10. Historical Confluence Survives Future Invalidation ─────────────────────
def test_historical_confluence_survives_future_invalidation() -> None:
    """10. An FVG-based setup with an active OB at setup time preserves its ORDER_BLOCK confluence."""
    t0 = BASE_TIME
    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
        make_candle(t0 + timedelta(hours=12), "3955", "3970", "3950", "3960"),
    ]
    bos = make_structure_break(
        "BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)
    fvg = FvgZone(
        id="FVG_01",
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        direction=FvgDirection.BEARISH,
        candle_1_open=candles[0].open_time,
        candle_2_open=candles[1].open_time,
        candle_3_open=candles[2].open_time,
        formation_time=candles[2].close_time,
        top_price=Decimal("3990.00"),
        bottom_price=Decimal("3980.00"),
        ce_price=Decimal("3985.00"),
        width=Decimal("10.00"),
        computable_at_utc=candles[2].close_time,
    )
    # OB active at setup time, invalidated at t0+24h
    ob = make_ob(
        "OB_CONF",
        t0,
        candles[1].close_time,
        struct_id="BOS_01",
        status=OrderBlockStatus.INVALIDATED,
        inval_time=t0 + timedelta(hours=24),
    )

    # prefer_order_block_over_fvg=False: FVG is the primary entry zone, OB is confluence
    cfg = IctSetupConfig(prefer_order_block_over_fvg=False)
    setups = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        fvg_zones=[fvg],
        order_blocks=[ob],
        config=cfg,
        as_of_utc=None,
    )
    assert len(setups) == 1
    assert setups[0].fvg_id == "FVG_01"
    assert setups[0].order_block_id == "OB_CONF"
    ob_confs = [
        c for c in setups[0].confluences if c.evidence_type == IctConfluenceType.ORDER_BLOCK
    ]
    assert len(ob_confs) == 1


# ── 11. Entry Zone Selection Matches as_of Snapshot ───────────────────────────
def test_entry_zone_selection_matches_as_of_snapshot() -> None:
    """11. filter_ict_setups_as_of correctly tracks state transitions across pre, mid, and post snapshots."""
    t0 = BASE_TIME
    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
        make_candle(t0 + timedelta(hours=12), "3955", "3970", "3950", "3960"),
        make_candle(
            t0 + timedelta(hours=16), "3960", "4030", "3955", "4025"
        ),  # invalidates zone at t0+20h
    ]
    bos = make_structure_break(
        "BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)
    ob = make_ob(
        "OB_SNAP",
        t0,
        candles[1].close_time,
        struct_id="BOS_01",
        status=OrderBlockStatus.UNMITIGATED,
    )

    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    full_setups = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob],
        fvg_zones=[],
        config=cfg,
    )
    assert len(full_setups) == 1

    t_pre = candles[1].close_time - timedelta(minutes=1)
    t_mid = candles[2].close_time
    t_post = candles[4].close_time

    assert len(filter_ict_setups_as_of(full_setups, t_pre)) == 0
    snap_mid = filter_ict_setups_as_of(full_setups, t_mid)
    assert len(snap_mid) == 1
    assert snap_mid[0].status == IctSetupStatus.ZONE_IDENTIFIED

    snap_post = filter_ict_setups_as_of(full_setups, t_post)
    assert len(snap_post) == 1
    assert snap_post[0].status == IctSetupStatus.INVALIDATED


# ── 12. No BID/ASK or Timeframe Cross-Contamination ───────────────────────────
def test_no_bid_ask_or_timeframe_cross_contamination() -> None:
    """12. An OB on ASK or H2 cannot be linked to a BID H4 setup candidate."""
    t0 = BASE_TIME
    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
    ]
    bos = make_structure_break(
        "BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)

    # OB on ASK price type
    ob_ask = make_ob(
        "OB_ASK",
        t0,
        candles[1].close_time,
        struct_id="BOS_01",
        status=OrderBlockStatus.UNMITIGATED,
        pt=PriceType.ASK,
    )
    # OB on H2 timeframe
    ob_h2 = make_ob(
        "OB_H2",
        t0,
        candles[1].close_time,
        struct_id="BOS_01",
        status=OrderBlockStatus.UNMITIGATED,
        tf=Timeframe.H2,
    )

    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    setups = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob_ask, ob_h2],
        fvg_zones=[],
        config=cfg,
    )
    # Neither ASK nor H2 OB can be linked to BID H4 setup; with no FVG, 0 setups generated
    assert len(setups) == 0
