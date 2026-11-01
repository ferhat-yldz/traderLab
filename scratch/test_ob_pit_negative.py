"""Demonstrate the point-in-time leak of end-of-period OrderBlockStatus.INVALIDATED into historical as_of queries."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from traderlab.core.types import AvailabilityConfidence, PriceType, Timeframe
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.displacement import (
    DisplacementDirection,
    DisplacementEvent,
)
from traderlab.domain.market_structure.fvg import FvgDirection, FvgZone
from traderlab.domain.market_structure.ict_setup import (
    IctConfluenceType,
    IctSetupConfig,
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

BASE = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)

def make_c(i: int, o: str, h: str, l: str, c: str) -> Candle:
    t_open = BASE + timedelta(hours=4 * i)
    t_close = t_open + timedelta(hours=4)
    return Candle(
        instrument_id="XAUUSD",
        source_id="TEST",
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        open_time=t_open,
        close_time=t_close,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal("100"),
        is_closed=True,
    )

def test_pit_reconstruction():
    # Candle 0: Bullish origin candle [4000, 4020]
    # Candle 1: Bearish BOS candle -> Confirms BOS at c1.close_time (08:00)
    # Candle 2: Free candle (stays below 4020) (12:00)
    # Candle 3: Invalidation candle (closes above 4020 at 16:00)
    candles = [
        make_c(0, "4000", "4020", "3990", "4015"),
        make_c(1, "4015", "4015", "3950", "3960"),
        make_c(2, "3960", "3980", "3955", "3970"),
        make_c(3, "3970", "4030", "3965", "4025"), # Closes above 4020 -> Invalidated at c3.close_time (16:00)
    ]
    
    t_conf = candles[1].close_time # 08:00 UTC
    t_inval = candles[3].close_time # 16:00 UTC
    
    # Create the OrderBlockRecord as it looks at the end of the period (INVALIDATED at 16:00 UTC)
    ev_inval = OrderBlockLifecycleEvent(
        event_id="EV_INVAL",
        order_block_id="OB_01",
        event_time=t_inval,
        bar_open_time=candles[3].open_time,
        bar_close_time=t_inval,
        status_before=OrderBlockStatus.UNMITIGATED,
        status_after=OrderBlockStatus.INVALIDATED,
        trigger_price=Decimal("4025"),
        penetration_depth=Decimal("5"),
        penetration_percentage=Decimal("25"),
        description="Invalidated: Bullish close above 4020",
    )
    
    ob_record = OrderBlockRecord(
        order_block_id="OB_01",
        instrument_id="XAUUSD",
        source_id="TEST",
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        direction=OrderBlockDirection.BEARISH,
        zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE,
        zone_bottom=Decimal("3990"),
        zone_top=Decimal("4020"),
        midpoint_50=Decimal("4005"),
        origin_candle_open_time=candles[0].open_time,
        origin_candle_close_time=candles[0].close_time,
        origin_candle_id="C0",
        confirmation_time=t_conf,
        displacement_event_id="DISP_01",
        structure_break_event_id="BOS_01",
        mss_confirmation_id=None,
        associated_fvg_id=None,
        status=OrderBlockStatus.INVALIDATED,
        invalidation_time=t_inval,
        lifecycle_events=(ev_inval,),
    )
    
    # 1. Test filter_order_blocks_as_of at t_pre (before confirmation)
    t_pre = t_conf - timedelta(minutes=1)
    obs_pre = filter_order_blocks_as_of([ob_record], t_pre)
    print("obs_pre len:", len(obs_pre))
    assert len(obs_pre) == 0, "OB should not be visible before confirmation_time"
    
    # 2. Test filter_order_blocks_as_of at t_mid (after confirmation, before invalidation)
    t_mid = t_conf + timedelta(hours=2) # 10:00 UTC
    obs_mid = filter_order_blocks_as_of([ob_record], t_mid)
    print("obs_mid len:", len(obs_mid))
    assert len(obs_mid) == 1, "OB must be visible at t_mid"
    print("obs_mid[0].status:", obs_mid[0].status)
    print("obs_mid[0].is_active:", obs_mid[0].is_active)
    # 3. Test setup candidate detection at t_mid passing the end-of-period ob_record
    from tests.test_ict_setup_integrity import make_test_disp, make_test_structure_break
    bos = make_test_structure_break("BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH, Timeframe.H4)
    disp = make_test_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH, Timeframe.H4)
    setups_at_mid = detect_ict_setup_candidates(
        candles[:3],
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob_record], # Pass ob_record with status=INVALIDATED (from end-of-period)
        as_of_utc=t_mid,
    )
    print("setups_at_mid len:", len(setups_at_mid))
    if setups_at_mid:
        print("setup order_block_id:", setups_at_mid[0].order_block_id)
        ob_confs = [c for c in setups_at_mid[0].confluences if c.evidence_type == IctConfluenceType.ORDER_BLOCK]
        print("setup OB confluences:", len(ob_confs))
        assert setups_at_mid[0].order_block_id == "OB_01", f"Expected OB_01, got {setups_at_mid[0].order_block_id}"

    # 4. Test full dataset detection and then filter_ict_setups_as_of
    full_setups = detect_ict_setup_candidates(
        candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob_record],
    )
    print("full_setups len:", len(full_setups))
    assert len(full_setups) == 1
    assert full_setups[0].order_block_id == "OB_01"
    
    # Filter as of t_mid:
    setups_filtered_mid = filter_ict_setups_as_of(full_setups, t_mid)
    assert len(setups_filtered_mid) == 1
    assert setups_filtered_mid[0].order_block_id == "OB_01"
    ob_confs_filtered = [c for c in setups_filtered_mid[0].confluences if c.evidence_type == IctConfluenceType.ORDER_BLOCK]
    assert len(ob_confs_filtered) == 1, "ORDER_BLOCK confluence must be present as of t_mid"
    print("Filtered setup OB confluences as of t_mid:", len(ob_confs_filtered))
    
    # Filter as of t_pre:
    setups_filtered_pre = filter_ict_setups_as_of(full_setups, t_pre)
    assert len(setups_filtered_pre) == 0, "Setup must not be available before setup_available_time"
    print("Filtered setup count as of t_pre:", len(setups_filtered_pre))

if __name__ == "__main__":
    test_pit_reconstruction()

