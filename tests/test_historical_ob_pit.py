"""Phase 4D-K2: Historical Order Block Point-in-Time Reconstruction Audit Tests.

Guarantees:
1. Before confirmation_time: Order Block must not be visible in filter_order_blocks_as_of.
2. Between confirmation_time and invalidation_time: Order Block is usable with its valid
   lifecycle state at that point in time (UNMITIGATED, TOUCHED, PARTIALLY_MITIGATED), and is_active is True.
3. After invalidation_time: Order Block has status INVALIDATED, is_active is False, and
   is excluded by filter_active_order_blocks.
4. detect_ict_setup_candidates does not exclude Order Blocks that were active at decision time
   merely because they became invalidated later in the dataset.
5. filter_ict_setups_as_of faithfully reconstructs historical Order Block confluences.
6. End-of-period lifecycle status does not leak into earlier decision points.
7. Verification across real XAUUSD invalidated Order Blocks.
"""

from __future__ import annotations

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
from traderlab.domain.market_structure.fvg import (
    FvgDirection,
    FvgZone,
)
from traderlab.domain.market_structure.ict_setup import (
    IctConfluenceType,
    detect_ict_setup_candidates,
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
    OrderBlockLifecycleEvent,
    OrderBlockRecord,
    OrderBlockStatus,
    OrderBlockZoneMode,
    filter_active_order_blocks,
    filter_order_blocks_as_of,
)

INST = InstrumentId("XAUUSD")
SRC = DataSourceId("market_data_lab_m1_derived_h4")
BASE_TIME = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


def make_candle(
    open_time: datetime,
    o: str,
    h: str,
    low_val: str,
    c: str,
    tf: Timeframe = Timeframe.H4,
    pt: PriceType = PriceType.BID,
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
        is_closed=True,
        availability_confidence=AvailabilityConfidence.ESTIMATED,
    )


def make_disp(
    event_id: str,
    open_time: datetime,
    direction: DisplacementDirection = DisplacementDirection.BEARISH,
    tf: Timeframe = Timeframe.H4,
) -> DisplacementEvent:
    close_time = open_time + timedelta(seconds=tf.value)
    return DisplacementEvent(
        event_id=event_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=PriceType.BID,
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


def make_structure_break(
    event_id: str,
    open_time: datetime,
    break_type: StructureBreakType = StructureBreakType.BOS,
    direction: StructureDirection = StructureDirection.BEARISH,
    tf: Timeframe = Timeframe.H4,
) -> StructureBreakEvent:
    close_time = open_time + timedelta(seconds=tf.value)
    return StructureBreakEvent(
        event_id=event_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=PriceType.BID,
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


def make_synthetic_ob(
    ob_id: str,
    origin_time: datetime,
    conf_time: datetime,
    inval_time: datetime,
    bot: str = "3990.00",
    top: str = "4020.00",
    direction: OrderBlockDirection = OrderBlockDirection.BEARISH,
    struct_id: str = "BOS_01",
) -> OrderBlockRecord:
    b = Decimal(bot)
    t = Decimal(top)
    mid = (b + t) / Decimal("2")
    inval_ev = OrderBlockLifecycleEvent(
        event_id=f"EV_INVAL_{ob_id}",
        order_block_id=ob_id,
        event_time=inval_time,
        bar_open_time=inval_time - timedelta(hours=4),
        bar_close_time=inval_time,
        status_before=OrderBlockStatus.UNMITIGATED,
        status_after=OrderBlockStatus.INVALIDATED,
        trigger_price=t + Decimal("5.00"),
        penetration_depth=Decimal("5.00"),
        penetration_percentage=Decimal("25.00"),
        description=f"Invalidated beyond {top}",
    )
    return OrderBlockRecord(
        order_block_id=ob_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        direction=direction,
        zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE,
        zone_bottom=b,
        zone_top=t,
        midpoint_50=mid,
        origin_candle_open_time=origin_time,
        origin_candle_close_time=origin_time + timedelta(hours=4),
        origin_candle_id=f"ORIGIN_{ob_id}",
        confirmation_time=conf_time,
        displacement_event_id=f"DISP_{ob_id}",
        structure_break_event_id=struct_id,
        mss_confirmation_id=None,
        associated_fvg_id=None,
        status=OrderBlockStatus.INVALIDATED,
        invalidation_time=inval_time,
        lifecycle_events=(inval_ev,),
    )


# ── Test 1: Invisible Prior to Confirmation ───────────────────────────────────
def test_ob_invisible_prior_to_confirmation() -> None:
    """1. Order Block must be strictly invisible prior to confirmation_time."""
    t_origin = BASE_TIME
    t_conf = t_origin + timedelta(hours=8)
    t_inval = t_conf + timedelta(hours=24)
    ob = make_synthetic_ob("OB_TEST_01", t_origin, t_conf, t_inval)

    # 1 second before confirmation
    t_pre = t_conf - timedelta(seconds=1)
    as_of_obs = filter_order_blocks_as_of([ob], t_pre)
    assert len(as_of_obs) == 0
    assert not ob.is_available_at(t_pre)


# ── Test 2: Usable with Active Status Before Invalidation ─────────────────────
def test_ob_usable_with_active_status_before_invalidation() -> None:
    """2. Order Block must be reconstructed with its active point-in-time status before invalidation."""
    t_origin = BASE_TIME
    t_conf = t_origin + timedelta(hours=8)
    t_inval = t_conf + timedelta(hours=24)
    ob = make_synthetic_ob("OB_TEST_02", t_origin, t_conf, t_inval)

    # Midpoint between confirmation and invalidation
    t_mid = t_conf + timedelta(hours=12)
    as_of_obs = filter_order_blocks_as_of([ob], t_mid)
    assert len(as_of_obs) == 1
    reconstructed = as_of_obs[0]

    assert reconstructed.is_available_at(t_mid)
    assert reconstructed.status == OrderBlockStatus.UNMITIGATED
    assert reconstructed.is_active is True
    assert reconstructed.invalidation_time is None
    assert len(reconstructed.lifecycle_events) == 0

    active_obs = filter_active_order_blocks(as_of_obs)
    assert len(active_obs) == 1
    assert active_obs[0].order_block_id == "OB_TEST_02"


# ── Test 3: Not Usable as Active After Invalidation ───────────────────────────
def test_ob_not_usable_as_active_after_invalidation() -> None:
    """3. Order Block must be INVALIDATED after invalidation_time and excluded by filter_active."""
    t_origin = BASE_TIME
    t_conf = t_origin + timedelta(hours=8)
    t_inval = t_conf + timedelta(hours=24)
    ob = make_synthetic_ob("OB_TEST_03", t_origin, t_conf, t_inval)

    # 1 minute after invalidation
    t_post = t_inval + timedelta(minutes=1)
    as_of_obs = filter_order_blocks_as_of([ob], t_post)
    assert len(as_of_obs) == 1
    reconstructed = as_of_obs[0]

    assert reconstructed.status == OrderBlockStatus.INVALIDATED
    assert reconstructed.is_active is False
    assert reconstructed.invalidation_time == t_inval
    assert len(reconstructed.lifecycle_events) == 1

    active_obs = filter_active_order_blocks(as_of_obs)
    assert len(active_obs) == 0


# ── Test 4: Setup Candidate Detection Without Lookahead Exclusion ─────────────
def test_detect_ict_setup_candidates_does_not_exclude_future_invalidated_ob() -> None:
    """4. detect_ict_setup_candidates must not drop OBs whose invalidation occurred after setup decision time."""
    t0 = BASE_TIME
    candles = [
        make_candle(t0, "4000", "4020", "3990", "4015"),
        make_candle(t0 + timedelta(hours=4), "4015", "4015", "3950", "3960"),
        make_candle(t0 + timedelta(hours=8), "3960", "3980", "3955", "3970"),
        make_candle(
            t0 + timedelta(hours=12), "3970", "4035", "3965", "4030"
        ),  # Invalidates at t0+16h
    ]
    t_conf = candles[1].close_time  # t0 + 8h
    t_inval = candles[3].close_time  # t0 + 16h
    t_mid = t_conf + timedelta(hours=2)  # t0 + 10h (before invalidation)

    ob_end_of_period = make_synthetic_ob(
        "OB_SETUP_TEST",
        candles[0].open_time,
        t_conf,
        t_inval,
        struct_id="BOS_01",
    )
    bos = make_structure_break(
        "BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH
    )
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)

    # Query setup candidates as of t_mid (before invalidation)
    setups = detect_ict_setup_candidates(
        candles[:3],
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob_end_of_period],  # Passed with end-of-period status INVALIDATED
        as_of_utc=t_mid,
    )
    assert len(setups) == 1
    s = setups[0]
    assert s.order_block_id == "OB_SETUP_TEST"
    ob_confs = [c for c in s.confluences if c.evidence_type == IctConfluenceType.ORDER_BLOCK]
    assert len(ob_confs) == 1
    assert ob_confs[0].source_event_id == "OB_SETUP_TEST"


# ── Test 5: filter_ict_setups_as_of Reconstructs OB Confluence ────────────────
def test_filter_ict_setups_as_of_reconstructs_ob_confluence() -> None:
    """5. filter_ict_setups_as_of must faithfully reconstruct historical Order Block confluences."""
    t0 = BASE_TIME
    candles = [
        make_candle(t0, "4000", "4020", "3990", "4015"),
        make_candle(t0 + timedelta(hours=4), "4015", "4015", "3950", "3960"),
        make_candle(t0 + timedelta(hours=8), "3960", "3980", "3955", "3970"),
        make_candle(t0 + timedelta(hours=12), "3970", "4035", "3965", "4030"),
    ]
    t_conf = candles[1].close_time
    t_inval = candles[3].close_time
    t_pre = t_conf - timedelta(minutes=1)
    t_mid = candles[2].close_time + timedelta(hours=1)

    ob = make_synthetic_ob(
        "OB_RECONSTRUCT", candles[0].open_time, t_conf, t_inval, struct_id="BOS_01"
    )
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

    # Full period detection: FVG is the entry zone, OB is the verified confluence
    full_setups = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        fvg_zones=[fvg],
        order_blocks=[ob],
    )
    assert len(full_setups) == 1
    assert full_setups[0].order_block_id == "OB_RECONSTRUCT"

    # Filter as of t_pre: setup is not yet available
    pre_setups = filter_ict_setups_as_of(full_setups, t_pre)
    assert len(pre_setups) == 0

    # Filter as of t_mid: setup is available and OB confluence is present
    mid_setups = filter_ict_setups_as_of(full_setups, t_mid)
    assert len(mid_setups) == 1
    s_mid = mid_setups[0]
    assert s_mid.order_block_id == "OB_RECONSTRUCT"
    ob_confs = [c for c in s_mid.confluences if c.evidence_type == IctConfluenceType.ORDER_BLOCK]
    assert len(ob_confs) == 1
    assert ob_confs[0].available_time <= t_mid


# ── Test 6: End-of-Period Lifecycle Does Not Leak Into Earlier Decision Points ──
def test_end_of_period_lifecycle_does_not_leak_into_earlier_as_of() -> None:
    """6. Multi-stage lifecycle events (TOUCHED -> PARTIALLY_MITIGATED -> INVALIDATED) must match exact timestamp."""
    t0 = BASE_TIME
    t_conf = t0 + timedelta(hours=4)
    t_touch = t0 + timedelta(hours=8)
    t_part = t0 + timedelta(hours=12)
    t_inval = t0 + timedelta(hours=16)

    ev_touch = OrderBlockLifecycleEvent(
        event_id="EV_1",
        order_block_id="OB_MULTI",
        event_time=t_touch,
        bar_open_time=t_touch - timedelta(hours=4),
        bar_close_time=t_touch,
        status_before=OrderBlockStatus.UNMITIGATED,
        status_after=OrderBlockStatus.TOUCHED,
        trigger_price=Decimal("4018"),
        penetration_depth=Decimal("2"),
        penetration_percentage=Decimal("10"),
        description="Touched outer boundary",
    )
    ev_part = OrderBlockLifecycleEvent(
        event_id="EV_2",
        order_block_id="OB_MULTI",
        event_time=t_part,
        bar_open_time=t_part - timedelta(hours=4),
        bar_close_time=t_part,
        status_before=OrderBlockStatus.TOUCHED,
        status_after=OrderBlockStatus.PARTIALLY_MITIGATED,
        trigger_price=Decimal("4005"),
        penetration_depth=Decimal("15"),
        penetration_percentage=Decimal("75"),
        description="Partially mitigated",
    )
    ev_inval = OrderBlockLifecycleEvent(
        event_id="EV_3",
        order_block_id="OB_MULTI",
        event_time=t_inval,
        bar_open_time=t_inval - timedelta(hours=4),
        bar_close_time=t_inval,
        status_before=OrderBlockStatus.PARTIALLY_MITIGATED,
        status_after=OrderBlockStatus.INVALIDATED,
        trigger_price=Decimal("4025"),
        penetration_depth=Decimal("25"),
        penetration_percentage=Decimal("125"),
        description="Invalidated",
    )

    ob_full = OrderBlockRecord(
        order_block_id="OB_MULTI",
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        direction=OrderBlockDirection.BEARISH,
        zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE,
        zone_bottom=Decimal("3990"),
        zone_top=Decimal("4020"),
        midpoint_50=Decimal("4005"),
        origin_candle_open_time=t0,
        origin_candle_close_time=t0 + timedelta(hours=4),
        origin_candle_id="ORIGIN_MULTI",
        confirmation_time=t_conf,
        displacement_event_id="DISP_MULTI",
        structure_break_event_id="BOS_MULTI",
        mss_confirmation_id=None,
        associated_fvg_id=None,
        status=OrderBlockStatus.INVALIDATED,
        touch_time=t_touch,
        invalidation_time=t_inval,
        lifecycle_events=(ev_touch, ev_part, ev_inval),
    )

    # 1. At t0 + 6h (between conf and touch): must be UNMITIGATED
    ob_snap1 = filter_order_blocks_as_of([ob_full], t0 + timedelta(hours=6))[0]
    assert ob_snap1.status == OrderBlockStatus.UNMITIGATED
    assert ob_snap1.is_active is True
    assert ob_snap1.touch_time is None

    # 2. At t0 + 10h (between touch and part): must be TOUCHED
    ob_snap2 = filter_order_blocks_as_of([ob_full], t0 + timedelta(hours=10))[0]
    assert ob_snap2.status == OrderBlockStatus.TOUCHED
    assert ob_snap2.is_active is True
    assert ob_snap2.touch_time == t_touch

    # 3. At t0 + 14h (between part and inval): must be PARTIALLY_MITIGATED
    ob_snap3 = filter_order_blocks_as_of([ob_full], t0 + timedelta(hours=14))[0]
    assert ob_snap3.status == OrderBlockStatus.PARTIALLY_MITIGATED
    assert ob_snap3.is_active is True
    assert ob_snap3.invalidation_time is None

    # 4. At t0 + 18h (after inval): must be INVALIDATED
    ob_snap4 = filter_order_blocks_as_of([ob_full], t0 + timedelta(hours=18))[0]
    assert ob_snap4.status == OrderBlockStatus.INVALIDATED
    assert ob_snap4.is_active is False
    assert ob_snap4.invalidation_time == t_inval


# ── Test 7: Real XAUUSD 5 Invalidated OBs PIT Replay Consistency ──────────────
def test_five_real_xauusd_invalidated_obs_pit_consistency() -> None:
    """7. Verifies 5 distinct real XAUUSD invalidated Order Blocks across 3 PIT moments."""
    # Real parameters from Phase 4D-K1/K2 audit
    examples = [
        # (name, conf_time, inval_time, direction, struct_id)
        (
            "H4_BEARISH_OCT14",
            datetime(2026, 10, 14, 20, 0, tzinfo=UTC),
            datetime(2026, 10, 15, 20, 0, tzinfo=UTC),
            OrderBlockDirection.BEARISH,
            "CHOCH_BEARISH_OCT14",
        ),
        (
            "H2_BEARISH_SEP24",
            datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
            datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
            OrderBlockDirection.BEARISH,
            "BOS_BEARISH_SEP24",
        ),
        (
            "H2_BEARISH_SEP30",
            datetime(2026, 9, 30, 16, 0, tzinfo=UTC),
            datetime(2026, 10, 12, 2, 0, tzinfo=UTC),
            OrderBlockDirection.BEARISH,
            "BOS_BEARISH_SEP30",
        ),
        (
            "H2_BULLISH_OCT09",
            datetime(2026, 10, 9, 4, 0, tzinfo=UTC),
            datetime(2026, 10, 14, 14, 0, tzinfo=UTC),
            OrderBlockDirection.BULLISH,
            "BOS_BULLISH_OCT09",
        ),
        (
            "H2_BULLISH_OCT12",
            datetime(2026, 10, 12, 2, 0, tzinfo=UTC),
            datetime(2026, 10, 12, 8, 0, tzinfo=UTC),
            OrderBlockDirection.BULLISH,
            "BOS_BULLISH_OCT12",
        ),
    ]

    for name, conf_dt, inval_dt, direction, struct_id in examples:
        ob = make_synthetic_ob(
            f"OB_{name}",
            conf_dt - timedelta(hours=4),
            conf_dt,
            inval_dt,
            direction=direction,
            struct_id=struct_id,
        )

        t_pre = conf_dt - timedelta(minutes=1)
        t_mid = conf_dt + (inval_dt - conf_dt) / 2
        t_post = inval_dt + timedelta(minutes=1)

        # Instant 1: Pre-confirmation -> Not visible
        res_pre = filter_order_blocks_as_of([ob], t_pre)
        assert len(res_pre) == 0, f"[{name}] Must not be visible before confirmation"

        # Instant 2: Mid-lifecycle -> Active and usable
        res_mid = filter_order_blocks_as_of([ob], t_mid)
        assert len(res_mid) == 1, f"[{name}] Must be visible at t_mid"
        assert res_mid[0].is_active is True, f"[{name}] Must be active at t_mid"
        assert res_mid[0].status != OrderBlockStatus.INVALIDATED, (
            f"[{name}] Must not be INVALIDATED at t_mid"
        )
        assert len(filter_active_order_blocks(res_mid)) == 1

        # Instant 3: Post-invalidation -> Invalidated and not active
        res_post = filter_order_blocks_as_of([ob], t_post)
        assert len(res_post) == 1, f"[{name}] Must be visible at t_post"
        assert res_post[0].status == OrderBlockStatus.INVALIDATED, (
            f"[{name}] Must be INVALIDATED at t_post"
        )
        assert res_post[0].is_active is False, f"[{name}] Must not be active at t_post"
        assert len(filter_active_order_blocks(res_post)) == 0, (
            f"[{name}] Must be excluded from active OBs"
        )
