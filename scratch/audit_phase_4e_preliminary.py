"""Preliminary Stage 0 Audit Script for Phase 4E-A.

Tests:
0.1. Order block invalidated later in the period when querying cutoff=None vs as_of.
0.2. Order block that is FULLY_MITIGATED at decision time.
"""
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
from traderlab.domain.market_structure.ict_setup import (
    IctSetupConfig,
    detect_ict_setup_candidates,
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
)

INST = InstrumentId("XAUUSD")
SRC = DataSourceId("market_data_lab_m1_derived_h4")


def make_candle(open_t: datetime, o: str, h: str, l: str, c: str) -> Candle:
    close_t = open_t + timedelta(hours=4)
    return Candle(
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        open_time=open_t,
        close_time=close_t,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
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


def test_audit():
    t0 = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
    t_conf = t0 + timedelta(hours=8)
    t_inval = t0 + timedelta(hours=24)

    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
        make_candle(t0 + timedelta(hours=12), "3955", "3970", "3950", "3960"),
        make_candle(t0 + timedelta(hours=16), "3960", "3975", "3955", "3965"),
        make_candle(t0 + timedelta(hours=20), "3965", "4020", "3960", "4015"),
    ]

    bos = make_structure_break("BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH)
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)

    ob_inval = make_synthetic_ob("OB_TEST_01", t0, t_conf, t_inval)

    print("--- 0.1 Audit: Cutoff=None with prefer_order_block_over_fvg=True ---")
    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    setups_no_cutoff = detect_ict_setup_candidates(
        candles=candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob_inval],
        fvg_zones=[],
        config=cfg,
        as_of_utc=None,
    )
    print(f"Setups found with as_of_utc=None: {len(setups_no_cutoff)}")
    if setups_no_cutoff:
        print(f"Zone ID: {setups_no_cutoff[0].entry_zone_id}, Zone Type: {setups_no_cutoff[0].entry_zone_type}")
    else:
        print("RESULT: Setup was DROPPED when as_of_utc=None because ob_inval.status == INVALIDATED was checked at line 1081!")

    print("\n--- 0.2 Audit: FULLY_MITIGATED OB passed to detect_ict_setup_candidates ---")
    ob_fully_mit = OrderBlockRecord(
        order_block_id="OB_FULLY_MIT",
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        direction=OrderBlockDirection.BEARISH,
        zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE,
        zone_bottom=Decimal("3990.00"),
        zone_top=Decimal("4020.00"),
        midpoint_50=Decimal("4005.00"),
        origin_candle_open_time=t0,
        origin_candle_close_time=t0 + timedelta(hours=4),
        origin_candle_id="C0",
        confirmation_time=t_conf,
        displacement_event_id="DISP_01",
        structure_break_event_id="BOS_01",
        mss_confirmation_id=None,
        associated_fvg_id=None,
        status=OrderBlockStatus.FULLY_MITIGATED,
        full_mitigation_time=t0 + timedelta(hours=12),
        lifecycle_events=(),
    )
    setups_fully_mit = detect_ict_setup_candidates(
        candles=candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob_fully_mit],
        fvg_zones=[],
        config=cfg,
        as_of_utc=t0 + timedelta(hours=14),
    )
    print(f"Setups found with FULLY_MITIGATED OB at as_of_utc=t0+14h: {len(setups_fully_mit)}")
    if setups_fully_mit:
        print(f"OB ID in candidate: {setups_fully_mit[0].order_block_id}, zone_bottom: {setups_fully_mit[0].selected_zone_bottom}")
        print(f"is_active property of OB: {ob_fully_mit.is_active}")
        print("RESULT: A FULLY_MITIGATED OB was chosen as primary zone because line 1081 checked `status != INVALIDATED`!")


if __name__ == "__main__":
    test_audit()
