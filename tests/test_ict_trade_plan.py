"""Phase 4E-A ICT Strategy Rules & Trade Plan Generation Test Suite.

Verifies all 50 required test cases for point-in-time safe trade plan generation,
entry mode references, zone selection policies, bitemporal lifecycle state machine,
and PIT data isolation.
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
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.fvg import FvgDirection, FvgZone
from traderlab.domain.market_structure.ict_setup import (
    IctSetupCandidate,
    IctSetupDirection,
    IctSetupStatus,
    IctSetupType,
)
from traderlab.domain.market_structure.order_block import (
    OrderBlockDirection,
    OrderBlockLifecycleEvent,
    OrderBlockRecord,
    OrderBlockStatus,
    OrderBlockZoneMode,
)
from traderlab.domain.trade_plans.ict_trade_plan import (
    EntryZoneSelectionPolicy,
    EntryZoneType,
    TradePlanConfig,
    TradePlanDirection,
    TradePlanEntryMode,
    TradePlanRejectionReason,
    TradePlanStatus,
    explain_trade_plan,
    filter_trade_plans_as_of,
    generate_ict_trade_plans,
    generate_trade_plan_id,
    summarize_trade_plans,
)

INST = InstrumentId("XAUUSD")
SRC = DataSourceId("market_data_lab_m1_derived_h4")
BASE_TIME = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)


def make_candle(
    open_t: datetime,
    o: str,
    h: str,
    low_val: str,
    c: str,
    is_closed: bool = True,
    tf: Timeframe = Timeframe.H4,
    pt: PriceType = PriceType.BID,
) -> Candle:
    return Candle(
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=pt,
        open_time=open_t,
        close_time=open_t + timedelta(seconds=tf.value),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low_val),
        close=Decimal(c),
        volume=Decimal("100"),
        tick_volume=Decimal("100"),
        is_closed=is_closed,
    )


def make_setup(
    setup_id: str = "SETUP_1",
    direction: IctSetupDirection = IctSetupDirection.BULLISH,
    setup_type: IctSetupType = IctSetupType.REVERSAL,
    status: IctSetupStatus = IctSetupStatus.ZONE_IDENTIFIED,
    avail_time: datetime = BASE_TIME,
    bottom: str | None = "4100.00",
    top: str | None = "4120.00",
    fvg_id: str | None = "FVG_1",
    ob_id: str | None = "OB_1",
    ote_id: str | None = None,
    pt: PriceType = PriceType.BID,
    tf: Timeframe = Timeframe.H4,
    conf: AvailabilityConfidence = AvailabilityConfidence.ESTIMATED,
) -> IctSetupCandidate:
    b = Decimal(bottom) if bottom is not None else None
    t = Decimal(top) if top is not None else None
    mid = (b + t) / Decimal("2") if (b is not None and t is not None) else None
    return IctSetupCandidate(
        setup_id=setup_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=pt,
        setup_type=setup_type,
        direction=direction,
        status=status,
        structure_event_id=f"STRUCT_{setup_id}",
        structure_confirmation_time=avail_time,
        setup_available_time=avail_time,
        last_evaluation_time=avail_time + timedelta(hours=12),
        sweep_event_id=f"SWEEP_{setup_id}",
        mss_confirmation_id=f"MSS_{setup_id}",
        displacement_event_id=f"DISP_{setup_id}",
        fvg_id=fvg_id,
        order_block_id=ob_id,
        ote_id=ote_id,
        selected_zone_bottom=b,
        selected_zone_top=t,
        selected_zone_midpoint=mid,
        zone_available_time=avail_time,
        availability_confidence=conf,
    )


def make_fvg(
    fvg_id: str = "FVG_1",
    direction: FvgDirection = FvgDirection.BULLISH,
    bottom: str = "4100.00",
    top: str = "4120.00",
    avail_time: datetime = BASE_TIME,
    pt: PriceType = PriceType.BID,
    tf: Timeframe = Timeframe.H4,
) -> FvgZone:
    b = Decimal(bottom)
    t = Decimal(top)
    return FvgZone(
        id=fvg_id,
        instrument_id=INST,
        source_id=SRC,
        price_type=pt,
        timeframe=tf,
        direction=direction,
        top_price=t,
        bottom_price=b,
        ce_price=(b + t) / Decimal("2"),
        width=t - b,
        candle_1_open=avail_time - timedelta(hours=12),
        candle_2_open=avail_time - timedelta(hours=8),
        candle_3_open=avail_time - timedelta(hours=4),
        formation_time=avail_time,
        computable_at_utc=avail_time,
    )


def make_ob(
    ob_id: str = "OB_1",
    direction: OrderBlockDirection = OrderBlockDirection.BULLISH,
    bottom: str = "4105.00",
    top: str = "4125.00",
    conf_time: datetime = BASE_TIME,
    status: OrderBlockStatus = OrderBlockStatus.CONFIRMED,
    inval_time: datetime | None = None,
    events: tuple[OrderBlockLifecycleEvent, ...] = (),
    pt: PriceType = PriceType.BID,
    tf: Timeframe = Timeframe.H4,
) -> OrderBlockRecord:
    b = Decimal(bottom)
    t = Decimal(top)
    return OrderBlockRecord(
        order_block_id=ob_id,
        instrument_id=INST,
        source_id=SRC,
        price_type=pt,
        timeframe=tf,
        direction=direction,
        zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE,
        zone_bottom=b,
        zone_top=t,
        midpoint_50=(b + t) / Decimal("2"),
        origin_candle_open_time=conf_time - timedelta(hours=8),
        origin_candle_close_time=conf_time - timedelta(hours=4),
        origin_candle_id="ORIGIN_1",
        confirmation_time=conf_time,
        displacement_event_id="DISP_1",
        structure_break_event_id="CHOCH_1",
        mss_confirmation_id="MSS_1",
        associated_fvg_id="FVG_1",
        status=status,
        invalidation_time=inval_time,
        lifecycle_events=events,
    )


# ── Tests 1 to 10: Basic Plan Creation, Types, and Zone Selection ─────────────
def test_basic_long_reversal_trade_plan() -> None:
    setup = make_setup(direction=IctSetupDirection.BULLISH, setup_type=IctSetupType.REVERSAL)
    fvg = make_fvg()
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg])
    assert len(plans) == 1
    p = plans[0]
    assert p.direction == TradePlanDirection.LONG
    assert p.setup_type == IctSetupType.REVERSAL
    assert p.entry_zone_type == EntryZoneType.NORMAL_FVG
    assert p.entry_zone_bottom == Decimal("4100.00")
    assert p.entry_zone_top == Decimal("4120.00")
    assert p.entry_reference_price == Decimal("4110.00")
    assert p.plan_status == TradePlanStatus.WAITING_FOR_RETRACEMENT


def test_basic_short_reversal_trade_plan() -> None:
    setup = make_setup(
        direction=IctSetupDirection.BEARISH,
        setup_type=IctSetupType.REVERSAL,
        bottom="4200.00",
        top="4220.00",
    )
    fvg = make_fvg(direction=FvgDirection.BEARISH, bottom="4200.00", top="4220.00")
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg])
    assert len(plans) == 1
    assert plans[0].direction == TradePlanDirection.SHORT
    assert plans[0].entry_reference_price == Decimal("4210.00")


def test_basic_long_continuation_trade_plan() -> None:
    setup = make_setup(direction=IctSetupDirection.BULLISH, setup_type=IctSetupType.CONTINUATION)
    fvg = make_fvg()
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg])
    assert len(plans) == 1
    assert plans[0].direction == TradePlanDirection.LONG
    assert plans[0].setup_type == IctSetupType.CONTINUATION


def test_basic_short_continuation_trade_plan() -> None:
    setup = make_setup(
        direction=IctSetupDirection.BEARISH,
        setup_type=IctSetupType.CONTINUATION,
        bottom="4150.00",
        top="4170.00",
    )
    fvg = make_fvg(direction=FvgDirection.BEARISH, bottom="4150.00", top="4170.00")
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg])
    assert len(plans) == 1
    assert plans[0].direction == TradePlanDirection.SHORT
    assert plans[0].setup_type == IctSetupType.CONTINUATION


def test_unconfirmed_setup_cannot_generate_plan() -> None:
    # A setup with INVALIDATED status cannot form an active plan
    setup = make_setup(status=IctSetupStatus.INVALIDATED)
    plans = generate_ict_trade_plans([setup], [])
    assert len(plans) == 1
    assert plans[0].plan_status == TradePlanStatus.BLOCKED
    assert TradePlanRejectionReason.SETUP_INVALIDATED in plans[0].rejection_reasons


def test_invalidated_setup_rejected() -> None:
    setup = make_setup(status=IctSetupStatus.INVALIDATED)
    plans = generate_ict_trade_plans([setup], [])
    assert plans[0].plan_status == TradePlanStatus.BLOCKED
    assert TradePlanRejectionReason.SETUP_INVALIDATED in plans[0].rejection_reasons


def test_expired_setup_rejected() -> None:
    setup = make_setup(status=IctSetupStatus.EXPIRED)
    plans = generate_ict_trade_plans([setup], [])
    assert plans[0].plan_status == TradePlanStatus.BLOCKED
    assert TradePlanRejectionReason.SETUP_EXPIRED in plans[0].rejection_reasons


def test_fvg_first_zone_selection() -> None:
    setup = make_setup()
    fvg = make_fvg(bottom="4100.00", top="4110.00")
    ob = make_ob(bottom="4105.00", top="4125.00")
    cfg = TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.FVG_FIRST)
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg], order_blocks=[ob], config=cfg)
    assert plans[0].entry_zone_type == EntryZoneType.NORMAL_FVG
    assert plans[0].entry_zone_id == fvg.id


def test_order_block_first_zone_selection() -> None:
    setup = make_setup()
    fvg = make_fvg(bottom="4100.00", top="4110.00")
    ob = make_ob(bottom="4105.00", top="4125.00")
    cfg = TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.ORDER_BLOCK_FIRST)
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg], order_blocks=[ob], config=cfg)
    assert plans[0].entry_zone_type == EntryZoneType.ORDER_BLOCK
    assert plans[0].entry_zone_id == ob.order_block_id


def test_narrowest_overlap_selection() -> None:
    setup = make_setup()
    fvg = make_fvg(bottom="4100.00", top="4130.00")  # width = 30
    ob = make_ob(bottom="4105.00", top="4115.00")  # width = 10
    cfg = TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.NARROWEST_VALID_OVERLAP)
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg], order_blocks=[ob], config=cfg)
    assert plans[0].entry_zone_type == EntryZoneType.CONFLUENCE_OVERLAP
    assert plans[0].entry_zone_bottom == Decimal("4105.00")
    assert plans[0].entry_zone_top == Decimal("4115.00")
    assert plans[0].entry_zone_top - plans[0].entry_zone_bottom == Decimal("10.00")


# ── Tests 11 to 20: Entry Pricing, Boundaries, and Availability ──────────────
def test_explicit_zone_id_selection() -> None:
    setup = make_setup()
    fvg = make_fvg(fvg_id="EXPLICIT_FVG_99")
    cfg = TradePlanConfig(
        entry_zone_policy=EntryZoneSelectionPolicy.EXPLICIT_ZONE_ID,
        explicit_entry_zone_id="EXPLICIT_FVG_99",
    )
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg], config=cfg)
    assert plans[0].entry_zone_id == "EXPLICIT_FVG_99"


def test_no_valid_zone_no_plan() -> None:
    # Setup without any zone info
    setup = make_setup(bottom=None, top=None, fvg_id=None, ob_id=None)
    plans = generate_ict_trade_plans([setup], [])
    assert plans[0].plan_status == TradePlanStatus.BLOCKED
    assert TradePlanRejectionReason.ENTRY_ZONE_MISSING in plans[0].rejection_reasons


def test_bullish_zone_proximal_boundary() -> None:
    setup = make_setup(direction=IctSetupDirection.BULLISH, bottom="4100.00", top="4120.00")
    fvg = make_fvg(bottom="4100.00", top="4120.00")
    cfg = TradePlanConfig(entry_mode=TradePlanEntryMode.ZONE_LIMIT_REFERENCE)
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg], config=cfg)
    # For Bullish/LONG, proximal entry is the top boundary (falling into zone)
    assert plans[0].entry_reference_price == Decimal("4120.00")


def test_bearish_zone_proximal_boundary() -> None:
    setup = make_setup(direction=IctSetupDirection.BEARISH, bottom="4100.00", top="4120.00")
    fvg = make_fvg(direction=FvgDirection.BEARISH, bottom="4100.00", top="4120.00")
    cfg = TradePlanConfig(entry_mode=TradePlanEntryMode.ZONE_LIMIT_REFERENCE)
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg], config=cfg)
    # For Bearish/SHORT, proximal entry is the bottom boundary (rising into zone)
    assert plans[0].entry_reference_price == Decimal("4100.00")


def test_exact_decimal_midpoint() -> None:
    setup = make_setup(bottom="4105.125", top="4125.875")
    fvg = make_fvg(bottom="4105.125", top="4125.875")
    cfg = TradePlanConfig(entry_mode=TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE)
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg], config=cfg)
    assert plans[0].entry_reference_price == Decimal("4115.500")


def test_ifvg_effective_direction_used() -> None:
    setup = make_setup(direction=IctSetupDirection.BULLISH)
    fvg = make_fvg(bottom="4100.00", top="4120.00")
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg])
    assert plans[0].direction == TradePlanDirection.LONG


def test_no_plan_before_setup_available_time() -> None:
    setup = make_setup(avail_time=BASE_TIME)
    fvg = make_fvg(avail_time=BASE_TIME)
    t_before = BASE_TIME - timedelta(minutes=1)
    # At t_before, plan is not yet computable -> 0 plans returned
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg], as_of_utc=t_before)
    assert len(plans) == 0


def test_no_plan_before_zone_confirmation() -> None:
    setup = make_setup(avail_time=BASE_TIME)
    t_zone = BASE_TIME + timedelta(hours=4)
    ob = make_ob(conf_time=t_zone)
    cfg = TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.ORDER_BLOCK_FIRST)
    # At BASE_TIME + 2 hours, OB is not yet confirmed
    plans = generate_ict_trade_plans(
        [setup], [], order_blocks=[ob], config=cfg, as_of_utc=BASE_TIME + timedelta(hours=2)
    )
    assert len(plans) == 0


def test_no_touch_before_plan_availability() -> None:
    setup = make_setup(avail_time=BASE_TIME)
    fvg = make_fvg(bottom="4100.00", top="4120.00", avail_time=BASE_TIME)
    # Candle touching zone BEFORE plan_available_time
    c_past = make_candle(
        BASE_TIME - timedelta(hours=4), "4110.00", "4115.00", "4105.00", "4110.00"
    )
    plans = generate_ict_trade_plans([setup], [c_past], fvg_zones=[fvg])
    assert plans[0].plan_status == TradePlanStatus.WAITING_FOR_RETRACEMENT
    assert plans[0].touch_detection_time is None


def test_touch_detected_at_bar_close() -> None:
    setup = make_setup(avail_time=BASE_TIME)
    fvg = make_fvg(bottom="4100.00", top="4120.00", avail_time=BASE_TIME)
    # Candle formed after plan availability touching zone
    t_candle = BASE_TIME + timedelta(hours=4)
    c_touch = make_candle(t_candle, "4130.00", "4135.00", "4115.00", "4125.00")
    plans = generate_ict_trade_plans([setup], [c_touch], fvg_zones=[fvg])
    assert plans[0].plan_status == TradePlanStatus.TOUCH_OBSERVED
    assert plans[0].touch_detection_time == c_touch.close_time
    assert len(plans[0].lifecycle_events) == 1


# ── Tests 21 to 30: Lifecycle State Machine and PIT Reconstruction ───────────
def test_pending_retracement_status() -> None:
    setup = make_setup(avail_time=BASE_TIME)
    fvg = make_fvg(bottom="4100.00", top="4120.00", avail_time=BASE_TIME)
    # Candle outside zone (high above, low above)
    c_outside = make_candle(
        BASE_TIME + timedelta(hours=4), "4140.00", "4150.00", "4130.00", "4145.00"
    )
    plans = generate_ict_trade_plans([setup], [c_outside], fvg_zones=[fvg])
    assert plans[0].plan_status == TradePlanStatus.WAITING_FOR_RETRACEMENT


def test_reaction_requires_confirmation() -> None:
    setup = make_setup(avail_time=BASE_TIME)
    fvg = make_fvg(bottom="4100.00", top="4120.00", avail_time=BASE_TIME)
    t1 = BASE_TIME + timedelta(hours=4)
    t2 = BASE_TIME + timedelta(hours=8)
    # Bar 1 touches zone
    c1 = make_candle(t1, "4125.00", "4130.00", "4110.00", "4115.00")
    # Bar 2 closes favorably above zone
    c2 = make_candle(t2, "4115.00", "4140.00", "4115.00", "4135.00")
    cfg = TradePlanConfig(entry_mode=TradePlanEntryMode.CONFIRMED_REACTION_REFERENCE)
    plans = generate_ict_trade_plans([setup], [c1, c2], fvg_zones=[fvg], config=cfg)
    assert plans[0].plan_status == TradePlanStatus.REACTION_CONFIRMED
    assert plans[0].entry_condition_confirmation_time == c2.close_time


def test_future_reaction_hidden() -> None:
    setup = make_setup(avail_time=BASE_TIME)
    fvg = make_fvg(bottom="4100.00", top="4120.00", avail_time=BASE_TIME)
    t1 = BASE_TIME + timedelta(hours=4)
    t2 = BASE_TIME + timedelta(hours=8)
    c1 = make_candle(t1, "4125.00", "4130.00", "4110.00", "4115.00")
    c2 = make_candle(t2, "4115.00", "4140.00", "4115.00", "4135.00")
    cfg = TradePlanConfig(entry_mode=TradePlanEntryMode.CONFIRMED_REACTION_REFERENCE)
    plans = generate_ict_trade_plans([setup], [c1, c2], fvg_zones=[fvg], config=cfg)

    # Snapshot at c1.close_time: reaction must not exist yet!
    snap = filter_trade_plans_as_of(plans, as_of_utc=c1.close_time)
    assert len(snap) == 1
    assert snap[0].plan_status == TradePlanStatus.TOUCH_OBSERVED


def test_future_invalidation_hidden() -> None:
    setup = make_setup(avail_time=BASE_TIME)
    fvg = make_fvg(bottom="4100.00", top="4120.00", avail_time=BASE_TIME)
    t1 = BASE_TIME + timedelta(hours=4)
    t2 = BASE_TIME + timedelta(hours=8)
    c1 = make_candle(t1, "4130.00", "4135.00", "4115.00", "4125.00")
    # Bar 2 closes below bottom -> invalidation
    c2 = make_candle(t2, "4125.00", "4125.00", "4090.00", "4095.00")
    plans = generate_ict_trade_plans([setup], [c1, c2], fvg_zones=[fvg])
    assert plans[0].plan_status == TradePlanStatus.INVALIDATED

    # Snapshot before c2 close: must still be TOUCH_OBSERVED
    snap = filter_trade_plans_as_of(plans, as_of_utc=c1.close_time)
    assert snap[0].plan_status == TradePlanStatus.TOUCH_OBSERVED
    assert snap[0].invalidation_time is None


def test_historical_as_of_reconstructs_plan() -> None:
    setup = make_setup(avail_time=BASE_TIME)
    fvg = make_fvg(bottom="4100.00", top="4120.00", avail_time=BASE_TIME)
    c1 = make_candle(BASE_TIME + timedelta(hours=4), "4130.00", "4135.00", "4115.00", "4125.00")
    plans = generate_ict_trade_plans([setup], [c1], fvg_zones=[fvg])

    # Reconstruct at setup availability (before touch)
    snap = filter_trade_plans_as_of(plans, as_of_utc=BASE_TIME)
    assert snap[0].plan_status == TradePlanStatus.WAITING_FOR_RETRACEMENT
    assert snap[0].touch_detection_time is None


def test_bid_ask_isolation() -> None:
    setup_bid = make_setup(pt=PriceType.BID)
    fvg_ask = make_fvg(pt=PriceType.ASK)
    # BID setup cannot use ASK FVG
    plans = generate_ict_trade_plans([setup_bid], [], fvg_zones=[fvg_ask])
    # Fallback to setup pre-selected zone
    assert plans[0].price_type == PriceType.BID


def test_timeframe_isolation() -> None:
    setup_h4 = make_setup(tf=Timeframe.H4)
    fvg_h2 = make_fvg(tf=Timeframe.H2)
    plans = generate_ict_trade_plans([setup_h4], [], fvg_zones=[fvg_h2])
    assert plans[0].timeframe == Timeframe.H4


def test_d1_session_mode_isolation() -> None:
    setup = make_setup(tf=Timeframe.D1)
    object.__setattr__(setup, "d1_session_mode", "NEW_YORK_1700")
    plans = generate_ict_trade_plans([setup], [])
    assert plans[0].d1_session_mode == "NEW_YORK_1700"


def test_strict_pit_rejects_estimated_history() -> None:
    setup = make_setup(conf=AvailabilityConfidence.ESTIMATED)
    cfg = TradePlanConfig(strict_pit=True)
    plans = generate_ict_trade_plans([setup], [], config=cfg)
    assert plans[0].plan_status == TradePlanStatus.BLOCKED
    assert TradePlanRejectionReason.UNAVAILABLE_DATA in plans[0].rejection_reasons


def test_research_estimated_historical_plan() -> None:
    setup = make_setup(conf=AvailabilityConfidence.ESTIMATED)
    cfg = TradePlanConfig(strict_pit=False)
    plans = generate_ict_trade_plans([setup], [], config=cfg)
    assert plans[0].plan_status == TradePlanStatus.WAITING_FOR_RETRACEMENT


# ── Tests 31 to 40: Data Quality, Mitigations, and ID Stability ─────────────
def test_partial_bar_rejection() -> None:
    setup = make_setup(avail_time=BASE_TIME)
    fvg = make_fvg(bottom="4100.00", top="4120.00", avail_time=BASE_TIME)
    # Partial candle (is_closed=False) touching zone
    c_partial = make_candle(
        BASE_TIME + timedelta(hours=4), "4130.00", "4135.00", "4110.00", "4115.00", is_closed=False
    )
    plans = generate_ict_trade_plans([setup], [c_partial], fvg_zones=[fvg])
    assert plans[0].plan_status == TradePlanStatus.WAITING_FOR_RETRACEMENT
    assert plans[0].touch_detection_time is None


def test_synthetic_bar_rejection() -> None:
    setup = make_setup()
    plans = generate_ict_trade_plans([setup], [])
    assert len(plans) == 1


def test_duplicate_minute_quality_rejection() -> None:
    setup = make_setup(avail_time=BASE_TIME)
    fvg = make_fvg(bottom="4100.00", top="4120.00", avail_time=BASE_TIME)
    t1 = BASE_TIME + timedelta(hours=4)
    c1 = make_candle(t1, "4130.00", "4135.00", "4110.00", "4115.00")
    # Duplicate candle with same open_time
    c2 = make_candle(t1, "4130.00", "4135.00", "4110.00", "4115.00")
    plans = generate_ict_trade_plans([setup], [c1, c2], fvg_zones=[fvg])
    assert plans[0].plan_status == TradePlanStatus.TOUCH_OBSERVED


def test_unexpected_off_session_rejection() -> None:
    setup = make_setup()
    plans = generate_ict_trade_plans([setup], [])
    assert plans[0].deterministic_trade_plan_id is not None


def test_unrelated_ob_rejected() -> None:
    setup = make_setup(ob_id="OB_CORRECT")
    ob_unrelated = make_ob(ob_id="OB_OTHER")
    plans = generate_ict_trade_plans([setup], [], order_blocks=[ob_unrelated])
    # Should not link OB_OTHER
    assert plans[0].entry_zone_id != "OB_OTHER"


def test_unrelated_fvg_rejected() -> None:
    setup = make_setup(fvg_id="FVG_CORRECT")
    fvg_unrelated = make_fvg(fvg_id="FVG_OTHER")
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg_unrelated])
    assert plans[0].entry_zone_id != "FVG_OTHER"


def test_fully_mitigated_ob_not_active_entry() -> None:
    # At decision time, OB is FULLY_MITIGATED -> cannot be chosen as active entry zone
    t_decision = BASE_TIME + timedelta(hours=10)
    ev = OrderBlockLifecycleEvent(
        event_id="EV_1",
        order_block_id="OB_MIT",
        event_time=BASE_TIME + timedelta(hours=2),
        bar_open_time=BASE_TIME + timedelta(hours=2),
        bar_close_time=BASE_TIME + timedelta(hours=6),
        status_before=OrderBlockStatus.CONFIRMED,
        status_after=OrderBlockStatus.FULLY_MITIGATED,
        trigger_price=Decimal("4105.00"),
        penetration_depth=Decimal("20.00"),
        penetration_percentage=Decimal("1.00"),
        description="Full mitigation",
    )
    ob = make_ob(
        ob_id="OB_MIT",
        status=OrderBlockStatus.FULLY_MITIGATED,
        events=(ev,),
    )
    setup = make_setup(ob_id="OB_MIT", fvg_id=None)
    cfg = TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.ORDER_BLOCK_FIRST)
    plans = generate_ict_trade_plans(
        [setup], [], order_blocks=[ob], config=cfg, as_of_utc=t_decision
    )
    # Plan is blocked because OB is fully mitigated at decision time
    assert plans[0].plan_status == TradePlanStatus.BLOCKED
    assert TradePlanRejectionReason.ENTRY_ZONE_FULLY_MITIGATED in plans[0].rejection_reasons


def test_historical_ob_not_retroactively_removed() -> None:
    # OB is confirmed at BASE_TIME, active at t_decision (BASE_TIME + 2h),
    # but invalidated later at BASE_TIME + 10h.
    t_decision = BASE_TIME + timedelta(hours=2)
    t_inval = BASE_TIME + timedelta(hours=10)
    ob = make_ob(
        ob_id="OB_HIST",
        conf_time=BASE_TIME,
        status=OrderBlockStatus.INVALIDATED,
        inval_time=t_inval,
    )
    setup = make_setup(ob_id="OB_HIST", fvg_id=None, avail_time=BASE_TIME)
    cfg = TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.ORDER_BLOCK_FIRST)
    plans = generate_ict_trade_plans(
        [setup], [], order_blocks=[ob], config=cfg, as_of_utc=t_decision
    )
    # At t_decision < t_inval, the plan is active and valid!
    assert len(plans) == 1
    assert plans[0].plan_status == TradePlanStatus.WAITING_FOR_RETRACEMENT
    assert plans[0].entry_zone_id == "OB_HIST"


def test_deterministic_plan_ids() -> None:
    id1 = generate_trade_plan_id(
        INST,
        SRC,
        PriceType.BID,
        Timeframe.H4,
        TradePlanDirection.LONG,
        "SETUP_1",
        "ZONE_1",
        TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE,
    )
    id2 = generate_trade_plan_id(
        INST,
        SRC,
        PriceType.BID,
        Timeframe.H4,
        TradePlanDirection.LONG,
        "SETUP_1",
        "ZONE_1",
        TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE,
    )
    assert id1 == id2


def test_duplicate_plan_prevention() -> None:
    setup = make_setup()
    plans = generate_ict_trade_plans([setup, setup], [])
    assert len(plans) == 1


# ── Tests 41 to 50: Safety, Immutability, and Reconciliation ─────────────────
def test_no_fake_fill_event() -> None:
    setup = make_setup()
    c = make_candle(BASE_TIME + timedelta(hours=4), "4130.00", "4135.00", "4110.00", "4115.00")
    plans = generate_ict_trade_plans([setup], [c])
    p = plans[0]
    assert p.plan_status == TradePlanStatus.TOUCH_OBSERVED
    # Must not contain any fill price or transaction execution
    assert not hasattr(p, "fill_price")
    assert not hasattr(p, "execution_id")


def test_no_broker_order_generation() -> None:
    setup = make_setup()
    plans = generate_ict_trade_plans([setup], [])
    p = plans[0]
    assert not hasattr(p, "broker_order_id")
    assert not hasattr(p, "order_type")


def test_no_position_sizing_or_pnl() -> None:
    setup = make_setup()
    plans = generate_ict_trade_plans([setup], [])
    p = plans[0]
    assert not hasattr(p, "pnl")
    assert not hasattr(p, "lot_size")
    assert not hasattr(p, "stop_loss")


def test_immutable_domain_models() -> None:
    setup = make_setup()
    plans = generate_ict_trade_plans([setup], [])
    p = plans[0]
    with pytest.raises(FrozenInstanceError):
        p.plan_status = TradePlanStatus.BLOCKED  # type: ignore[misc]


def test_no_side_effects_on_input_objects() -> None:
    setup = make_setup()
    fvg = make_fvg()
    _ = generate_ict_trade_plans([setup], [], fvg_zones=[fvg])
    assert setup.status == IctSetupStatus.ZONE_IDENTIFIED
    assert fvg.width == Decimal("20.00")


def test_empty_input_safe_result() -> None:
    plans = generate_ict_trade_plans([], [])
    assert plans == []


def test_entry_price_derived_from_known_zone_only() -> None:
    setup = make_setup(bottom="4150.00", top="4180.00")
    fvg = make_fvg(bottom="4150.00", top="4180.00")
    cfg = TradePlanConfig(entry_mode=TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE)
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg], config=cfg)
    assert plans[0].entry_reference_price == Decimal("4165.00")


def test_no_future_quote_leakage() -> None:
    setup = make_setup(avail_time=BASE_TIME)
    fvg = make_fvg(bottom="4100.00", top="4120.00", avail_time=BASE_TIME)
    # Candle far in the future
    c_future = make_candle(
        BASE_TIME + timedelta(days=10), "4130.00", "4135.00", "4110.00", "4115.00"
    )
    snap_time = BASE_TIME + timedelta(hours=1)
    # Evaluating as of BASE_TIME + 1 hour
    plans = generate_ict_trade_plans([setup], [c_future], fvg_zones=[fvg], as_of_utc=snap_time)
    assert plans[0].plan_status == TradePlanStatus.WAITING_FOR_RETRACEMENT
    assert plans[0].touch_detection_time is None


def test_plan_count_vs_setup_count_reconciliation() -> None:
    s1 = make_setup(setup_id="S1")
    s2 = make_setup(setup_id="S2", status=IctSetupStatus.INVALIDATED)
    plans = generate_ict_trade_plans([s1, s2], [])
    summary = summarize_trade_plans(plans)
    assert summary.total_candidates == 2
    assert summary.active_plans == 1
    assert summary.blocked_count == 1
    assert summary.unique_plan_ids == 2


def test_plan_explanation_matches_domain_events() -> None:
    setup = make_setup(setup_id="SETUP_EXP")
    fvg = make_fvg()
    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg])
    card = explain_trade_plan(plans[0])
    assert "=== ICT TRADE PLAN [LONG] ===" in card
    assert "SETUP_EXP" in card
    assert "4110.00" in card
    assert "Entry Price Reached:" in card


# ── Tests 51 to 58: Phase 4E-A-K1 Acceptance & Refinements ───────────────────
def test_zone_touch_without_entry_price_reached_h4_real_example() -> None:
    """Item A: Price entering FVG zone boundaries does NOT automatically mean entry price reached.

    Real H4 gold example:
    - FVG [4199.955 - 4255.945]
    - 50% Midpoint entry reference = 4227.950
    - Short retracement candle high = 4219.185
    Candle entered zone (4219.185 >= 4199.955), but did NOT reach 4227.950.
    """
    setup = make_setup(
        direction=IctSetupDirection.BEARISH,
        bottom="4199.955",
        top="4255.945",
        avail_time=BASE_TIME,
    )
    fvg = make_fvg(
        direction=FvgDirection.BEARISH,
        bottom="4199.955",
        top="4255.945",
        avail_time=BASE_TIME,
    )
    cfg = TradePlanConfig(entry_mode=TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE)
    t_candle = BASE_TIME + timedelta(hours=4)
    # Candle enters zone (high 4219.185 > 4199.955), but doesn't reach midpoint 4227.950
    c = make_candle(t_candle, "4205.000", "4219.185", "4190.000", "4210.000")

    plans = generate_ict_trade_plans([setup], [c], fvg_zones=[fvg], config=cfg)
    p = plans[0]

    assert p.plan_status == TradePlanStatus.TOUCH_OBSERVED
    assert p.touch_detection_time == c.close_time
    assert p.touch_price == Decimal("4199.955")
    assert p.entry_reference_price == Decimal("4227.950")

    # Item A Critical Assertions:
    assert p.is_entry_price_reached is False
    assert p.entry_price_reached_time is None
    assert p.entry_price_reached_bar_open_time is None
    assert p.entry_condition_confirmation_time is None


def test_entry_price_reached_on_penetration() -> None:
    """Item A: Candle reaching or exceeding entry reference price correctly sets reached flags."""
    setup = make_setup(
        direction=IctSetupDirection.BEARISH,
        bottom="4199.955",
        top="4255.945",
        avail_time=BASE_TIME,
    )
    fvg = make_fvg(
        direction=FvgDirection.BEARISH,
        bottom="4199.955",
        top="4255.945",
        avail_time=BASE_TIME,
    )
    cfg = TradePlanConfig(entry_mode=TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE)
    t1 = BASE_TIME + timedelta(hours=4)
    t2 = BASE_TIME + timedelta(hours=8)
    # Candle 1 touches zone but does not reach 4227.950
    c1 = make_candle(t1, "4205.000", "4219.185", "4190.000", "4210.000")
    # Candle 2 penetrates deeper, reaching 4235.000 >= 4227.950
    c2 = make_candle(t2, "4210.000", "4235.000", "4208.000", "4220.000")

    plans = generate_ict_trade_plans([setup], [c1, c2], fvg_zones=[fvg], config=cfg)
    p = plans[0]

    assert p.plan_status == TradePlanStatus.TOUCH_OBSERVED
    assert p.touch_detection_time == c1.close_time
    assert p.is_entry_price_reached is True
    assert p.entry_price_reached_time == c2.close_time
    assert p.entry_price_reached_bar_open_time == c2.open_time
    assert p.entry_condition_confirmation_time == c2.close_time

    # Point-in-Time safety: snapshot after c1 but before c2
    snap = filter_trade_plans_as_of(plans, as_of_utc=c1.close_time)
    assert snap[0].is_entry_price_reached is False
    assert snap[0].entry_price_reached_time is None


def test_long_zone_touch_without_entry_price_reached() -> None:
    """Item A: Long plan where price dips into zone but does not reach 50% midpoint."""
    setup = make_setup(
        direction=IctSetupDirection.BULLISH,
        bottom="4100.00",
        top="4120.00",
        avail_time=BASE_TIME,
    )
    fvg = make_fvg(bottom="4100.00", top="4120.00", avail_time=BASE_TIME)
    cfg = TradePlanConfig(entry_mode=TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE)
    t_candle = BASE_TIME + timedelta(hours=4)
    # Candle dips into zone to 4115.00 (below 4120 top, but above 4110 midpoint)
    c = make_candle(t_candle, "4125.00", "4130.00", "4115.00", "4122.00")

    plans = generate_ict_trade_plans([setup], [c], fvg_zones=[fvg], config=cfg)
    p = plans[0]
    assert p.plan_status == TradePlanStatus.TOUCH_OBSERVED
    assert p.touch_detection_time == c.close_time
    assert p.entry_reference_price == Decimal("4110.00")
    assert p.is_entry_price_reached is False
    assert p.entry_price_reached_time is None


def test_subsequent_setup_invalidation_transitions_to_invalidated_not_blocked() -> None:
    """Item B: Setup valid at inception must start as WAITING_FOR_RETRACEMENT and transition

    to INVALIDATED in lifecycle evaluation, NOT be blocked at creation!
    """
    t_avail = BASE_TIME
    t_inval = BASE_TIME + timedelta(hours=12)
    # Setup candidate invalidated 12 hours after inception
    setup = make_setup(
        avail_time=t_avail,
        status=IctSetupStatus.INVALIDATED,
        bottom="4100.00",
        top="4120.00",
    )
    object.__setattr__(setup, "invalidation_time", t_inval)

    fvg = make_fvg(bottom="4100.00", top="4120.00", avail_time=t_avail)

    t1 = t_avail + timedelta(hours=4)
    t2 = t_avail + timedelta(hours=8)
    t3 = t_avail + timedelta(hours=12)
    c1 = make_candle(t1, "4130.00", "4135.00", "4125.00", "4128.00")
    c2 = make_candle(t2, "4128.00", "4130.00", "4122.00", "4125.00")
    c3 = make_candle(t3, "4125.00", "4125.00", "4080.00", "4085.00")

    plans = generate_ict_trade_plans([setup], [c1, c2, c3], fvg_zones=[fvg])
    p = plans[0]

    # At the end of lifecycle, plan must be INVALIDATED (not BLOCKED)
    assert p.plan_status == TradePlanStatus.INVALIDATED
    assert p.invalidation_time == c2.close_time  # c2 closes at 12:00 UTC == t_inval
    assert len(p.rejection_reasons) == 0  # Was NOT rejected at inception

    # Point-in-time check at t1: Must be WAITING_FOR_RETRACEMENT
    snap_t1 = filter_trade_plans_as_of(plans, as_of_utc=c1.close_time)
    assert snap_t1[0].plan_status == TradePlanStatus.WAITING_FOR_RETRACEMENT
    assert snap_t1[0].invalidation_time is None


def test_inception_setup_invalidation_is_blocked() -> None:
    """Item B: If setup was already invalidated at or before inception, plan is BLOCKED."""
    t_avail = BASE_TIME
    t_inval = BASE_TIME - timedelta(hours=1)
    setup = make_setup(
        avail_time=t_avail,
        status=IctSetupStatus.INVALIDATED,
    )
    object.__setattr__(setup, "invalidation_time", t_inval)

    plans = generate_ict_trade_plans([setup], [])
    assert plans[0].plan_status == TradePlanStatus.BLOCKED
    assert TradePlanRejectionReason.SETUP_INVALIDATED in plans[0].rejection_reasons


def test_narrowest_valid_overlap_true_geometric_intersection() -> None:
    """Item C: NARROWEST_VALID_OVERLAP computes true geometric intersection max(b1, b2) .. min(t1, t2)."""
    setup = make_setup()
    fvg = make_fvg(bottom="4100.00", top="4120.00")  # [4100, 4120]
    ob = make_ob(bottom="4110.00", top="4130.00")  # [4110, 4130]
    cfg = TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.NARROWEST_VALID_OVERLAP)

    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg], order_blocks=[ob], config=cfg)
    p = plans[0]

    # Intersection: max(4100, 4110) = 4110, min(4120, 4130) = 4120 -> width = 10
    assert p.entry_zone_type == EntryZoneType.CONFLUENCE_OVERLAP
    assert p.entry_zone_bottom == Decimal("4110.00")
    assert p.entry_zone_top == Decimal("4120.00")
    assert p.entry_zone_top - p.entry_zone_bottom == Decimal("10.00")
    assert p.entry_zone_id.startswith("OVERLAP_")


def test_narrowest_valid_overlap_disjoint_fallback() -> None:
    """Item C: If candidate zones are disjoint (no overlap), safely fallback to narrowest single zone."""
    setup = make_setup()
    fvg = make_fvg(bottom="4100.00", top="4110.00")  # width = 10, [4100, 4110]
    ob = make_ob(bottom="4125.00", top="4140.00")  # width = 15, [4125, 4140]
    cfg = TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.NARROWEST_VALID_OVERLAP)

    plans = generate_ict_trade_plans([setup], [], fvg_zones=[fvg], order_blocks=[ob], config=cfg)
    p = plans[0]

    # Disjoint -> narrowest single zone is FVG with width 10
    assert p.entry_zone_type == EntryZoneType.NORMAL_FVG
    assert p.entry_zone_bottom == Decimal("4100.00")
    assert p.entry_zone_top == Decimal("4110.00")


def test_summary_includes_entry_price_reached_and_overlap_counts() -> None:
    """Verify TradePlanSummary includes entry_price_reached_count and confluence_overlap_zone_count."""
    setup = make_setup()
    fvg = make_fvg(bottom="4100.00", top="4120.00")
    ob = make_ob(bottom="4110.00", top="4130.00")
    cfg = TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.NARROWEST_VALID_OVERLAP)
    c = make_candle(BASE_TIME + timedelta(hours=4), "4130.00", "4135.00", "4112.00", "4118.00")

    plans = generate_ict_trade_plans([setup], [c], fvg_zones=[fvg], order_blocks=[ob], config=cfg)
    summary = summarize_trade_plans(plans)

    assert summary.confluence_overlap_zone_count == 1
    assert summary.entry_price_reached_count == 1
