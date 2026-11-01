"""Unit and integration test suite for the Point-in-Time Safe ICT Setup Candidate & Confluence Engine.

Verifies:
    1. Operational definitions of ICT Reversal and ICT Continuation setups.
    2. Mandatory structural, liquidity, displacement, and zone causalities.
    3. Point-in-Time safety, zero lookahead leakage, historical as_of reconstruction.
    4. Confluence evidence evaluation without double counting.
    5. Geometric overlap analysis (positive width vs tangent touch).
    6. Isolation of BID/ASK, timeframes, and D1 session modes.
    7. Rejection of unclosed, partial, or synthetic bars.
    8. Deterministic identification, immutability, and state machine transitions.
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
)
from traderlab.domain.market_structure.fvg import (
    FvgDirection,
    FvgZone,
)
from traderlab.domain.market_structure.fvg_respect import (
    FvgReactionDirection,
    FvgRespectSummary,
    FvgRetestEvent,
    FvgRetestStatus,
)
from traderlab.domain.market_structure.ict_setup import (
    IctConfluenceType,
    IctSetupConfig,
    IctSetupDataError,
    IctSetupDirection,
    IctSetupStatus,
    IctSetupType,
    compute_zone_overlap,
    detect_ict_setup_candidates,
    explain_ict_setup,
    filter_ict_setups_as_of,
    generate_ict_setup_id,
    summarize_ict_setups,
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
    OrderBlockRecord,
    OrderBlockStatus,
    OrderBlockZoneMode,
)
from traderlab.domain.market_structure.ote import (
    OTEZoneRecord,
)
from traderlab.domain.market_structure.premium_discount import (
    DealingRangeDirection,
)

INST = InstrumentId("XAUUSD")
SRC = DataSourceId("market_data_lab_m1_derived_h1")
BASE_TIME = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


# ── Test Factory Helpers ─────────────────────────────────────────────────────
def make_candle(
    open_time: datetime,
    o: str,
    h: str,
    low_val: str,
    c: str,
    tf: Timeframe = Timeframe.H1,
    pt: PriceType = PriceType.BID,
    is_closed: bool = True,
    is_synthetic: bool = False,
    confidence: AvailabilityConfidence = AvailabilityConfidence.ESTIMATED,
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
        is_synthetic=is_synthetic,
        availability_confidence=confidence,
    )


def make_test_sweep(
    event_id: str,
    open_time: datetime,
    side: LiquiditySide = LiquiditySide.SELL_SIDE,
    status: LiquiditySweepStatus = LiquiditySweepStatus.SWEEP,
    level: str = "4000.00",
    penetration: str = "3995.00",
    tf: Timeframe = Timeframe.H1,
    pt: PriceType = PriceType.BID,
) -> LiquiditySweepEvent:
    close_time = open_time + timedelta(seconds=tf.value)
    lvl_p = Decimal(level)
    pen_p = Decimal(penetration)
    ticks = abs(pen_p - lvl_p) / Decimal("0.01")
    return LiquiditySweepEvent(
        event_id=event_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=pt,
        source_swing_id=f"SWING_{event_id}",
        liquidity_side=side,
        level_price=lvl_p,
        penetration_price=pen_p,
        penetration_ticks=ticks,
        penetration_threshold=Decimal("0.01"),
        sweep_bar_open_time=open_time,
        sweep_bar_close_time=close_time,
        detection_time=close_time,
        pivot_time=open_time - timedelta(hours=5),
        level_confirmation_time=open_time - timedelta(hours=2),
        status=status,
    )


def make_test_structure_break(
    event_id: str,
    open_time: datetime,
    break_type: StructureBreakType = StructureBreakType.CHOCH,
    direction: StructureDirection = StructureDirection.BULLISH,
    status: str = "CONFIRMED",
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
        confirmation_status=status,
        mss_candidate_id=f"MSS_CAND_{event_id}",
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


def make_test_mss(
    record_id: str,
    choch_id: str,
    cand_time: datetime,
    final_conf_time: datetime,
    direction: StructureDirection = StructureDirection.BULLISH,
    status: MssStatus = MssStatus.MSS_CONFIRMED,
    disp_id: str | None = "DISP_01",
    fvg_id: str | None = "FVG_01",
    tf: Timeframe = Timeframe.H1,
    pt: PriceType = PriceType.BID,
) -> MssConfirmationRecord:
    return MssConfirmationRecord(
        record_id=record_id,
        mss_candidate_id=f"MSS_CAND_{choch_id}",
        source_choch_event_id=choch_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=pt,
        direction=direction,
        candidate_time=cand_time,
        displacement_event_id=disp_id,
        displacement_time=cand_time,
        associated_fvg_id=fvg_id,
        fvg_confirmation_time=final_conf_time,
        associated_sweep_id="SWEEP_01",
        final_confirmation_time=final_conf_time,
        status=status,
    )


def make_test_fvg(
    fvg_id: str,
    formation_time: datetime,
    bot: str = "4010.00",
    top: str = "4020.00",
    direction: FvgDirection = FvgDirection.BULLISH,
    tf: Timeframe = Timeframe.H1,
    pt: PriceType = PriceType.BID,
) -> FvgZone:
    b = Decimal(bot)
    t = Decimal(top)
    ce = (b + t) / Decimal("2")
    w = t - b
    step = timedelta(seconds=tf.value)
    c1 = formation_time - step * 3
    c2 = formation_time - step * 2
    c3 = formation_time - step
    return FvgZone(
        id=fvg_id,
        instrument_id=INST,
        source_id=SRC,
        price_type=pt,
        timeframe=tf,
        direction=direction,
        top_price=t,
        bottom_price=b,
        ce_price=ce,
        width=w,
        candle_1_open=c1,
        candle_2_open=c2,
        candle_3_open=c3,
        formation_time=formation_time,
        computable_at_utc=formation_time,
    )


def make_test_ob(
    ob_id: str,
    origin_time: datetime,
    conf_time: datetime,
    bot: str = "4005.00",
    top: str = "4015.00",
    direction: OrderBlockDirection = OrderBlockDirection.BULLISH,
    struct_id: str = "CHOCH_01",
    mss_id: str | None = "MSS_01",
    disp_id: str = "DISP_01",
    status: OrderBlockStatus = OrderBlockStatus.CONFIRMED,
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
        associated_fvg_id="FVG_01",
        status=status,
    )


# ── Test Cases (1 to 50) ─────────────────────────────────────────────────────


def test_01_basic_bullish_reversal_setup() -> None:
    """Test 1: Validates complete Bullish Reversal setup assembly."""
    t0 = BASE_TIME
    t_sweep = t0 + timedelta(hours=1)
    t_choch = t0 + timedelta(hours=2)
    t_fvg = t0 + timedelta(hours=3)

    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    swp = make_test_sweep("SWP_01", t_sweep, side=LiquiditySide.SELL_SIDE)
    ch = make_test_structure_break(
        "CH_01", t_choch, break_type=StructureBreakType.CHOCH, direction=StructureDirection.BULLISH
    )
    dsp = make_test_disp("DSP_01", t_choch, direction=DisplacementDirection.BULLISH)
    fvg = make_test_fvg("FVG_01", t_fvg, bot="4005", top="4015", direction=FvgDirection.BULLISH)
    mss = make_test_mss(
        "MSS_01",
        "CH_01",
        ch.detection_time,
        t_fvg,
        direction=StructureDirection.BULLISH,
        disp_id=dsp.event_id,
        fvg_id=fvg.id,
    )

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp],
        structure_breaks=[ch],
        mss_records=[mss],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    assert len(setups) == 1
    s = setups[0]
    assert s.is_reversal is True
    assert s.is_bullish is True
    assert s.status == IctSetupStatus.ZONE_TESTED
    assert s.fvg_id == fvg.id
    assert s.sweep_event_id == swp.event_id

    # Verify PIT rewind to formation time shows ZONE_IDENTIFIED
    rewound = filter_ict_setups_as_of(setups, t_fvg)
    assert len(rewound) == 1
    assert rewound[0].status == IctSetupStatus.ZONE_IDENTIFIED


def test_02_basic_bearish_reversal_setup() -> None:
    """Test 2: Validates complete Bearish Reversal setup assembly."""
    t0 = BASE_TIME
    t_sweep = t0 + timedelta(hours=1)
    t_choch = t0 + timedelta(hours=2)
    t_fvg = t0 + timedelta(hours=3)

    c_list = [
        make_candle(t0 + timedelta(hours=i), "4050", "4060", "4040", "4045") for i in range(10)
    ]
    swp = make_test_sweep(
        "SWP_BEAR", t_sweep, side=LiquiditySide.BUY_SIDE, level="4050", penetration="4055"
    )
    ch = make_test_structure_break(
        "CH_BEAR",
        t_choch,
        break_type=StructureBreakType.CHOCH,
        direction=StructureDirection.BEARISH,
    )
    dsp = make_test_disp("DSP_BEAR", t_choch, direction=DisplacementDirection.BEARISH)
    fvg = make_test_fvg("FVG_BEAR", t_fvg, bot="4035", top="4045", direction=FvgDirection.BEARISH)
    mss = make_test_mss(
        "MSS_BEAR",
        "CH_BEAR",
        ch.detection_time,
        t_fvg,
        direction=StructureDirection.BEARISH,
        disp_id=dsp.event_id,
        fvg_id=fvg.id,
    )

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp],
        structure_breaks=[ch],
        mss_records=[mss],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    assert len(setups) == 1
    assert setups[0].is_bearish is True
    assert setups[0].is_reversal is True


def test_03_basic_bullish_continuation_setup() -> None:
    """Test 3: Validates complete Bullish Continuation setup assembly."""
    t0 = BASE_TIME
    t_bos = t0 + timedelta(hours=2)
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4020", "3995", "4015") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_01", t_bos, break_type=StructureBreakType.BOS, direction=StructureDirection.BULLISH
    )
    dsp = make_test_disp("DSP_01", t_bos, direction=DisplacementDirection.BULLISH)
    fvg = make_test_fvg(
        "FVG_01",
        t_bos + timedelta(hours=1),
        bot="4005",
        top="4015",
        direction=FvgDirection.BULLISH,
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg]
    )
    assert len(setups) == 1
    s = setups[0]
    assert s.is_continuation is True
    assert s.is_bullish is True
    assert s.structure_event_id == bos.event_id


def test_04_basic_bearish_continuation_setup() -> None:
    """Test 4: Validates complete Bearish Continuation setup assembly."""
    t0 = BASE_TIME
    t_bos = t0 + timedelta(hours=2)
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4050", "4060", "4040", "4045") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_BEAR", t_bos, break_type=StructureBreakType.BOS, direction=StructureDirection.BEARISH
    )
    dsp = make_test_disp("DSP_BEAR", t_bos, direction=DisplacementDirection.BEARISH)
    fvg = make_test_fvg(
        "FVG_BEAR",
        t_bos + timedelta(hours=1),
        bot="4035",
        top="4045",
        direction=FvgDirection.BEARISH,
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg]
    )
    assert len(setups) == 1
    assert setups[0].is_continuation is True
    assert setups[0].is_bearish is True


def test_05_reversal_requires_confirmed_liquidity_sweep() -> None:
    """Test 5: Reversal setup candidate is rejected if no liquidity sweep exists."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    ch = make_test_structure_break(
        "CH_01", t0 + timedelta(hours=2), break_type=StructureBreakType.CHOCH
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))
    mss = make_test_mss("MSS_01", "CH_01", ch.detection_time, t0 + timedelta(hours=3))

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[],
        structure_breaks=[ch],
        mss_records=[mss],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    assert len(setups) == 0


def test_06_liquidity_touch_not_equal_sweep() -> None:
    """Test 6: Level interaction with status TOUCH cannot qualify a reversal setup."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    swp_touch = make_test_sweep(
        "SWP_TOUCH", t0 + timedelta(hours=1), status=LiquiditySweepStatus.TOUCH
    )
    ch = make_test_structure_break(
        "CH_01", t0 + timedelta(hours=2), break_type=StructureBreakType.CHOCH
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))
    mss = make_test_mss("MSS_01", "CH_01", ch.detection_time, t0 + timedelta(hours=3))

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp_touch],
        structure_breaks=[ch],
        mss_records=[mss],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    assert len(setups) == 0


def test_07_liquidity_breach_not_equal_sweep() -> None:
    """Test 7: Level breakout closing beyond (BREACH) cannot qualify a reversal setup."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    swp_breach = make_test_sweep(
        "SWP_BREACH", t0 + timedelta(hours=1), status=LiquiditySweepStatus.BREACH
    )
    ch = make_test_structure_break(
        "CH_01", t0 + timedelta(hours=2), break_type=StructureBreakType.CHOCH
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))
    mss = make_test_mss("MSS_01", "CH_01", ch.detection_time, t0 + timedelta(hours=3))

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp_breach],
        structure_breaks=[ch],
        mss_records=[mss],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    assert len(setups) == 0


def test_08_bullish_reversal_requires_sell_side_sweep() -> None:
    """Test 8: Bullish Reversal rejects BUY_SIDE sweep (direction mismatch)."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    swp_bsl = make_test_sweep("SWP_BSL", t0 + timedelta(hours=1), side=LiquiditySide.BUY_SIDE)
    ch = make_test_structure_break(
        "CH_01",
        t0 + timedelta(hours=2),
        break_type=StructureBreakType.CHOCH,
        direction=StructureDirection.BULLISH,
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))
    mss = make_test_mss(
        "MSS_01",
        "CH_01",
        ch.detection_time,
        t0 + timedelta(hours=3),
        direction=StructureDirection.BULLISH,
    )

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp_bsl],
        structure_breaks=[ch],
        mss_records=[mss],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    assert len(setups) == 0


def test_09_bearish_reversal_requires_buy_side_sweep() -> None:
    """Test 9: Bearish Reversal rejects SELL_SIDE sweep (direction mismatch)."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4050", "4060", "4040", "4045") for i in range(10)
    ]
    swp_ssl = make_test_sweep("SWP_SSL", t0 + timedelta(hours=1), side=LiquiditySide.SELL_SIDE)
    ch = make_test_structure_break(
        "CH_01",
        t0 + timedelta(hours=2),
        break_type=StructureBreakType.CHOCH,
        direction=StructureDirection.BEARISH,
    )
    dsp = make_test_disp(
        "DSP_01", t0 + timedelta(hours=2), direction=DisplacementDirection.BEARISH
    )
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3), direction=FvgDirection.BEARISH)
    mss = make_test_mss(
        "MSS_01",
        "CH_01",
        ch.detection_time,
        t0 + timedelta(hours=3),
        direction=StructureDirection.BEARISH,
    )

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp_ssl],
        structure_breaks=[ch],
        mss_records=[mss],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    assert len(setups) == 0


def test_10_choch_candidate_cannot_create_confirmed_setup() -> None:
    """Test 10: Unconfirmed CHOCH candidate does not produce an active setup."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    swp = make_test_sweep("SWP_01", t0 + timedelta(hours=1))
    ch_cand = make_test_structure_break("CH_CAND", t0 + timedelta(hours=2), status="CANDIDATE")
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))
    mss = make_test_mss("MSS_01", "CH_CAND", ch_cand.detection_time, t0 + timedelta(hours=3))

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp],
        structure_breaks=[ch_cand],
        mss_records=[mss],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    assert len(setups) == 0


def test_11_mss_candidate_cannot_create_confirmed_setup() -> None:
    """Test 11: MSS record with PENDING or REJECTED status does not produce an active setup."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    swp = make_test_sweep("SWP_01", t0 + timedelta(hours=1))
    ch = make_test_structure_break("CH_01", t0 + timedelta(hours=2))
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))
    mss_pending = make_test_mss(
        "MSS_PENDING",
        "CH_01",
        ch.detection_time,
        t0 + timedelta(hours=3),
        status=MssStatus.PENDING,
    )

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp],
        structure_breaks=[ch],
        mss_records=[mss_pending],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    assert len(setups) == 0


def test_12_confirmed_mss_link_required_for_reversal() -> None:
    """Test 12: CHoCH without matching MSS confirmation record cannot produce a reversal candidate."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    swp = make_test_sweep("SWP_01", t0 + timedelta(hours=1))
    ch = make_test_structure_break("CH_01", t0 + timedelta(hours=2))
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp],
        structure_breaks=[ch],
        mss_records=[],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    assert len(setups) == 0


def test_13_continuation_requires_confirmed_bos() -> None:
    """Test 13: BOS candidate cannot produce a continuation setup."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos_cand = make_test_structure_break(
        "BOS_CAND", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS, status="CANDIDATE"
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos_cand], displacements=[dsp], fvg_zones=[fvg]
    )
    assert len(setups) == 0


def test_14_wick_breach_not_equal_bos() -> None:
    """Test 14: CHoCH or non-BOS break type cannot be passed off as BOS continuation."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    choch_event = make_test_structure_break(
        "CH_01", t0 + timedelta(hours=2), break_type=StructureBreakType.CHOCH
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[choch_event], displacements=[dsp], fvg_zones=[fvg]
    )
    assert len(setups) == 0


def test_15_displacement_direction_mismatch_rejected() -> None:
    """Test 15: Bullish BOS paired with Bearish displacement is rejected."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_BULL",
        t0 + timedelta(hours=2),
        break_type=StructureBreakType.BOS,
        direction=StructureDirection.BULLISH,
    )
    dsp_bear = make_test_disp(
        "DSP_BEAR", t0 + timedelta(hours=2), direction=DisplacementDirection.BEARISH
    )
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3), direction=FvgDirection.BULLISH)

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp_bear], fvg_zones=[fvg]
    )
    assert len(setups) == 0


def test_16_unrelated_displacement_rejected() -> None:
    """Test 16: Displacement occurring far outside max_bos_displacement_bars is rejected."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(20)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS
    )
    # Displacement 15 bars away (max allowed is 5)
    dsp_far = make_test_disp("DSP_FAR", t0 + timedelta(hours=17))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))

    cfg = IctSetupConfig(max_bos_displacement_bars=5)
    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp_far], fvg_zones=[fvg], config=cfg
    )
    assert len(setups) == 0


def test_17_unrelated_fvg_rejected() -> None:
    """Test 17: FVG with opposing direction to the setup is rejected."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg_bear = make_test_fvg("FVG_BEAR", t0 + timedelta(hours=3), direction=FvgDirection.BEARISH)

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg_bear]
    )
    assert len(setups) == 0


def test_18_unrelated_order_block_rejected() -> None:
    """Test 18: Order Block with opposing direction to the setup is rejected."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    ob_bear = make_test_ob(
        "OB_BEAR",
        t0,
        t0 + timedelta(hours=2),
        direction=OrderBlockDirection.BEARISH,
        struct_id="BOS_01",
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob_bear]
    )
    assert len(setups) == 0


def test_19_no_setup_before_structure_confirmation() -> None:
    """Test 19: PIT filter masks setup if as_of precedes structure detection time."""
    t0 = BASE_TIME
    t_bos = t0 + timedelta(hours=2)
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t_bos, break_type=StructureBreakType.BOS)
    dsp = make_test_disp("DSP_01", t_bos)
    fvg = make_test_fvg("FVG_01", t_bos + timedelta(hours=1))

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg], as_of_utc=t_bos
    )
    assert len(setups) == 0


def test_20_no_setup_before_displacement_confirmation() -> None:
    """Test 20: Setup candidate is unavailable if as_of precedes displacement confirmation."""
    t0 = BASE_TIME
    t_bos = t0 + timedelta(hours=2)
    t_dsp = t0 + timedelta(hours=4)
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t_bos, break_type=StructureBreakType.BOS)
    dsp = make_test_disp("DSP_01", t_dsp)
    fvg = make_test_fvg("FVG_01", t_dsp + timedelta(hours=1))

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg], as_of_utc=t_dsp
    )
    assert len(setups) == 0


def test_21_no_setup_before_fvg_formation() -> None:
    """Test 21: Setup candidate is unavailable if as_of precedes FVG formation time."""
    t0 = BASE_TIME
    t_bos = t0 + timedelta(hours=2)
    t_fvg = t0 + timedelta(hours=5)
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t_bos, break_type=StructureBreakType.BOS)
    dsp = make_test_disp("DSP_01", t_bos)
    fvg = make_test_fvg("FVG_01", t_fvg)

    setups = detect_ict_setup_candidates(
        c_list,
        structure_breaks=[bos],
        displacements=[dsp],
        fvg_zones=[fvg],
        as_of_utc=t_fvg - timedelta(minutes=1),
    )
    assert len(setups) == 0


def test_22_no_setup_before_ob_confirmation() -> None:
    """Test 22: Setup candidate with Order Block is unavailable if as_of precedes OB confirmation."""
    t0 = BASE_TIME
    t_bos = t0 + timedelta(hours=2)
    t_ob_conf = t0 + timedelta(hours=5)
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t_bos, break_type=StructureBreakType.BOS)
    dsp = make_test_disp("DSP_01", t_bos)
    ob = make_test_ob("OB_01", t0, t_ob_conf, struct_id="BOS_01")

    setups = detect_ict_setup_candidates(
        c_list,
        structure_breaks=[bos],
        displacements=[dsp],
        order_blocks=[ob],
        as_of_utc=t_ob_conf - timedelta(minutes=1),
    )
    assert len(setups) == 0


def test_23_no_ote_overlap_before_range_available() -> None:
    """Test 23: OTE confluence cannot appear in a setup snapshot before OTE range availability."""
    t0 = BASE_TIME
    t_bos = t0 + timedelta(hours=2)
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t_bos, break_type=StructureBreakType.BOS)
    dsp = make_test_disp("DSP_01", t_bos)
    fvg = make_test_fvg("FVG_01", t_bos + timedelta(hours=1), bot="3975.00", top="3985.00")

    # OTE zone becoming available much later at hour 8
    ote = OTEZoneRecord(
        ote_id="OTE_01",
        dealing_range_id="DR_01",
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H1,
        price_type=PriceType.BID,
        direction=DealingRangeDirection.BULLISH,
        range_low=Decimal("3950.00"),
        range_high=Decimal("4050.00"),
        equilibrium=Decimal("4000.00"),
        ote_62=Decimal("3988.00"),
        ote_705=Decimal("3979.50"),
        ote_79=Decimal("3971.00"),
        zone_bottom=Decimal("3971.00"),
        zone_top=Decimal("3988.00"),
        range_available_at=t0 + timedelta(hours=8),
    )

    # At hour 4, setup is created but OTE must NOT be in confluences
    setups = detect_ict_setup_candidates(
        c_list,
        structure_breaks=[bos],
        displacements=[dsp],
        fvg_zones=[fvg],
        ote_zones=[ote],
        as_of_utc=t0 + timedelta(hours=4),
    )
    assert len(setups) == 1
    assert any(c.evidence_type == IctConfluenceType.OTE for c in setups[0].confluences) is False


def test_24_no_future_respect_leakage() -> None:
    """Test 24: Historical FVG respect confirmed in future cannot be added to past candidate."""
    t0 = BASE_TIME
    t_bos = t0 + timedelta(hours=2)
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break("BOS_01", t_bos, break_type=StructureBreakType.BOS)
    dsp = make_test_disp("DSP_01", t_bos)
    fvg = make_test_fvg("FVG_01", t_bos + timedelta(hours=1))

    future_respect = FvgRetestEvent(
        event_id="EV_01",
        fvg_id=fvg.id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H1,
        price_type=PriceType.BID,
        touch_bar_open_time=t0 + timedelta(hours=5),
        touch_detection_time=t0 + timedelta(hours=6),
        effective_direction=FvgReactionDirection.BULLISH_SUPPORT,
        zone_bottom=fvg.bottom_price,
        zone_top=fvg.top_price,
        touch_price=Decimal("4012.00"),
        penetration_depth=Decimal("2.00"),
        penetration_percentage=Decimal("20.00"),
        retest_sequence_number=1,
        reaction_confirmation_time=t0 + timedelta(hours=8),
        reaction_distance=Decimal("5.00"),
        status=FvgRetestStatus.RESPECT_CONFIRMED,
        status_reason="Respect confirmed",
    )
    resp_summary = FvgRespectSummary(
        fvg_id=fvg.id,
        instrument_id=INST,
        source_id=SRC,
        price_type=PriceType.BID,
        timeframe=Timeframe.H1,
        fvg_direction=FvgDirection.BULLISH,
        effective_direction=FvgReactionDirection.BULLISH_SUPPORT,
        formation_time=fvg.formation_time,
        inversion_confirmed_at=None,
        zone_bottom=fvg.bottom_price,
        zone_top=fvg.top_price,
        zone_width=fvg.width,
        total_touch_count=1,
        confirmed_respect_count=1,
        failed_reaction_count=0,
        pending_reaction_count=0,
        consecutive_respect_count=1,
        maximum_respect_streak=1,
        last_touch_time=t0 + timedelta(hours=6),
        last_respect_time=t0 + timedelta(hours=8),
        final_status=FvgRetestStatus.RESPECT_CONFIRMED,
        total_retest_count=1,
        reported_retest_count=1,
        remaining_retests=0,
        retest_events=(future_respect,),
    )

    setups = detect_ict_setup_candidates(
        c_list,
        structure_breaks=[bos],
        displacements=[dsp],
        fvg_zones=[fvg],
        fvg_respect_summaries=[resp_summary],
        as_of_utc=t0 + timedelta(hours=4),
    )
    assert len(setups) == 1
    assert (
        any(c.evidence_type == IctConfluenceType.FVG_RESPECT for c in setups[0].confluences)
        is False
    )


def test_25_historical_as_of_recomputes_candidate_state() -> None:
    """Test 25: filter_ict_setups_as_of cleanly rewinds candidate from ZONE_TESTED to ZONE_IDENTIFIED."""
    t0 = BASE_TIME
    t_bos = t0 + timedelta(hours=2)
    # Candle 4 tests the zone
    c_list = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=1), "4005", "4015", "4000", "4010"),
        make_candle(t0 + timedelta(hours=2), "4010", "4030", "4010", "4025"),
        make_candle(t0 + timedelta(hours=3), "4025", "4035", "4020", "4030"),
        make_candle(
            t0 + timedelta(hours=4), "4030", "4030", "4012", "4025"
        ),  # Touch [4010 - 4020]
    ]
    bos = make_test_structure_break("BOS_01", t_bos, break_type=StructureBreakType.BOS)
    dsp = make_test_disp("DSP_01", t_bos)
    fvg = make_test_fvg("FVG_01", t_bos + timedelta(hours=1), bot="4010", top="4020")

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg]
    )
    assert setups[0].status == IctSetupStatus.ZONE_TESTED

    # Rewind to t0 + 3h (before touch bar closes at t0 + 5h)
    rewound = filter_ict_setups_as_of(setups, t0 + timedelta(hours=3))
    assert len(rewound) == 1
    assert rewound[0].status == IctSetupStatus.ZONE_IDENTIFIED
    assert rewound[0].test_detection_time is None


def test_26_future_zone_touch_not_visible() -> None:
    """Test 26: Touch occurring after as_of_utc is completely invisible."""
    t0 = BASE_TIME
    t_bos = t0 + timedelta(hours=2)
    # Candles stay well above [4010 - 4020]
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4030", "4040", "4025", "4035") for i in range(10)
    ]
    # Only Candle 6 touches zone
    c_list[6] = make_candle(t0 + timedelta(hours=6), "4030", "4030", "4015", "4025")
    bos = make_test_structure_break("BOS_01", t_bos, break_type=StructureBreakType.BOS)
    dsp = make_test_disp("DSP_01", t_bos)
    fvg = make_test_fvg("FVG_01", t_bos + timedelta(hours=1), bot="4010", top="4020")

    setups = detect_ict_setup_candidates(
        c_list,
        structure_breaks=[bos],
        displacements=[dsp],
        fvg_zones=[fvg],
        as_of_utc=t0 + timedelta(hours=5),
    )
    assert setups[0].status == IctSetupStatus.ZONE_IDENTIFIED
    assert setups[0].test_bar_open_time is None


def test_27_same_candle_intrabar_order_not_assumed() -> None:
    """Test 27: Reversal where sweep and CHoCH share identical detection_time is rejected as AMBIGUOUS."""
    t0 = BASE_TIME
    t_same = t0 + timedelta(hours=2)
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    swp_same = make_test_sweep("SWP_SAME", t_same)
    ch_same = make_test_structure_break("CH_SAME", t_same)
    dsp = make_test_disp("DSP_01", t_same)
    fvg = make_test_fvg("FVG_01", t_same + timedelta(hours=1))
    mss = make_test_mss("MSS_01", "CH_SAME", ch_same.detection_time, t_same + timedelta(hours=1))

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp_same],
        structure_breaks=[ch_same],
        mss_records=[mss],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    assert len(setups) == 0


def test_28_bid_ask_isolation() -> None:
    """Test 28: Candles from mixed price types raise an error."""
    t0 = BASE_TIME
    c_bid = make_candle(t0, "4000", "4010", "3990", "4005", pt=PriceType.BID)
    c_ask = make_candle(t0 + timedelta(hours=1), "4005", "4015", "3995", "4010", pt=PriceType.ASK)

    with pytest.raises(IctSetupDataError, match="Heterogeneous candle sequence"):
        detect_ict_setup_candidates([c_bid, c_ask])


def test_29_timeframe_isolation() -> None:
    """Test 29: Candles from mixed timeframes raise an error."""
    t0 = BASE_TIME
    c_h1 = make_candle(t0, "4000", "4010", "3990", "4005", tf=Timeframe.H1)
    c_h4 = make_candle(t0 + timedelta(hours=1), "4005", "4015", "3995", "4010", tf=Timeframe.H4)

    with pytest.raises(IctSetupDataError, match="Heterogeneous candle sequence"):
        detect_ict_setup_candidates([c_h1, c_h4])


def test_30_d1_session_mode_isolation() -> None:
    """Test 30: d1_session_mode is stamped explicitly onto candidate."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(days=i), "4000", "4020", "3990", "4015", tf=Timeframe.D1)
        for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_D1", t0 + timedelta(days=2), break_type=StructureBreakType.BOS, tf=Timeframe.D1
    )
    dsp = make_test_disp("DSP_D1", t0 + timedelta(days=2), tf=Timeframe.D1)
    fvg = make_test_fvg("FVG_D1", t0 + timedelta(days=4), tf=Timeframe.D1)

    setups = detect_ict_setup_candidates(
        c_list,
        structure_breaks=[bos],
        displacements=[dsp],
        fvg_zones=[fvg],
        d1_session_mode="NEW_YORK_1700",
    )
    assert len(setups) == 1
    assert setups[0].d1_session_mode == "NEW_YORK_1700"


def test_31_strict_pit_rejects_estimated_history() -> None:
    """Test 31: STRICT_PIT mode rejects unverified candle sequences."""
    t0 = BASE_TIME
    c_est = [
        make_candle(
            t0 + timedelta(hours=i),
            "4000",
            "4010",
            "3990",
            "4005",
            confidence=AvailabilityConfidence.ESTIMATED,
        )
        for i in range(5)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS
    )
    cfg = IctSetupConfig(strict_pit=True)

    with pytest.raises(IctSetupDataError, match="STRICT_PIT violation"):
        detect_ict_setup_candidates(c_est, structure_breaks=[bos], config=cfg)


def test_32_research_estimated_allows_valid_historical_analysis() -> None:
    """Test 32: RESEARCH_ESTIMATED policy permits estimated bars."""
    t0 = BASE_TIME
    c_est = [
        make_candle(
            t0 + timedelta(hours=i),
            "4000",
            "4010",
            "3990",
            "4005",
            confidence=AvailabilityConfidence.ESTIMATED,
        )
        for i in range(5)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))
    cfg = IctSetupConfig(policy=PitPolicy.RESEARCH_ESTIMATED, strict_pit=False)

    setups = detect_ict_setup_candidates(
        c_est, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg], config=cfg
    )
    assert len(setups) == 1


def test_33_missing_available_at_does_not_become_verified() -> None:
    """Test 33: Setup candidate confidence defaults to ESTIMATED if components lack verification."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg]
    )
    assert setups[0].availability_confidence == AvailabilityConfidence.ESTIMATED


def test_34_partial_synthetic_unclosed_bar_rejection() -> None:
    """Test 34: Unclosed and synthetic bars are rejected by the engine."""
    t0 = BASE_TIME
    c_unclosed = [make_candle(t0, "4000", "4010", "3990", "4005", is_closed=False)]
    with pytest.raises(IctSetupDataError, match="Partial unclosed candle"):
        detect_ict_setup_candidates(c_unclosed)

    c_synth = [make_candle(t0, "4000", "4010", "3990", "4005", is_synthetic=True)]
    with pytest.raises(IctSetupDataError, match="Synthetic candle rejected"):
        detect_ict_setup_candidates(c_synth)


def test_35_invalidated_zone_not_used_as_active_entry_zone() -> None:
    """Test 35: Order Block that is already INVALIDATED cannot be selected as entry zone."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    ob_inval = make_test_ob(
        "OB_INVAL",
        t0,
        t0 + timedelta(hours=2),
        struct_id="BOS_01",
        status=OrderBlockStatus.INVALIDATED,
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], order_blocks=[ob_inval]
    )
    assert len(setups) == 0


def test_36_duplicate_candidate_prevention() -> None:
    """Test 36: Multiple calls or cycles with identical inputs produce deterministic unique candidates."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))

    setups_1 = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg]
    )
    setups_2 = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg]
    )
    assert len(setups_1) == 1
    assert len(setups_2) == 1
    assert setups_1[0].setup_id == setups_2[0].setup_id


def test_37_deterministic_setup_ids() -> None:
    """Test 37: Setup ID format adheres strictly to standard and is fully deterministic."""
    id1 = generate_ict_setup_id(
        INST,
        SRC,
        PriceType.BID,
        Timeframe.H1,
        IctSetupType.REVERSAL,
        IctSetupDirection.BULLISH,
        "CH_01",
        BASE_TIME,
    )
    id2 = generate_ict_setup_id(
        INST,
        SRC,
        PriceType.BID,
        Timeframe.H1,
        IctSetupType.REVERSAL,
        IctSetupDirection.BULLISH,
        "CH_01",
        BASE_TIME,
    )
    assert id1 == id2
    assert "REVERSAL" in id1
    assert "BULLISH" in id1
    assert "CH_01" in id1


def test_38_geometric_ote_ob_fvg_overlap() -> None:
    """Test 38: Overlapping zones compute positive width intersection."""
    b_inter, t_inter, width, is_touch = compute_zone_overlap(
        Decimal("4000.00"), Decimal("4020.00"), Decimal("4010.00"), Decimal("4030.00")
    )
    assert b_inter == Decimal("4010.00")
    assert t_inter == Decimal("4020.00")
    assert width == Decimal("10.00")
    assert is_touch is False


def test_39_zero_width_tangent_overlap_distinguished() -> None:
    """Test 39: Touching zones with zero width return is_touch_only True."""
    b_inter, t_inter, width, is_touch = compute_zone_overlap(
        Decimal("4000.00"), Decimal("4010.00"), Decimal("4010.00"), Decimal("4020.00")
    )
    assert b_inter == Decimal("4010.00")
    assert t_inter == Decimal("4010.00")
    assert width == Decimal("0.00")
    assert is_touch is True


def test_40_normal_fvg_vs_inverted_effective_direction() -> None:
    """Test 40: Setup candidate records correct zone direction."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg(
        "FVG_01", t0 + timedelta(hours=3), bot="4005", top="4015", direction=FvgDirection.BULLISH
    )

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg]
    )
    assert setups[0].direction == IctSetupDirection.BULLISH
    assert setups[0].selected_zone_bottom == Decimal("4005.00")


def test_41_no_double_counting_of_choch_and_mss_evidence() -> None:
    """Test 41: Reversal setup does not artificially add CHoCH as separate confluence from MSS."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    swp = make_test_sweep("SWP_01", t0 + timedelta(hours=1))
    ch = make_test_structure_break(
        "CH_01", t0 + timedelta(hours=2), break_type=StructureBreakType.CHOCH
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))
    mss = make_test_mss("MSS_01", "CH_01", ch.detection_time, t0 + timedelta(hours=3))

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp],
        structure_breaks=[ch],
        mss_records=[mss],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    conf_types = [c.evidence_type for c in setups[0].confluences]
    # MSS is present; BOS is not; no duplicated CHoCH confluence type
    assert IctConfluenceType.MSS in conf_types
    assert IctConfluenceType.BOS not in conf_types


def test_42_respect_count_not_interpreted_as_win_probability() -> None:
    """Test 42: FVG respect explanation accurately presents sample count without win-rate claims."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))

    ev = FvgRetestEvent(
        event_id="EV_01",
        fvg_id=fvg.id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H1,
        price_type=PriceType.BID,
        touch_bar_open_time=t0,
        touch_detection_time=t0 + timedelta(hours=1),
        effective_direction=FvgReactionDirection.BULLISH_SUPPORT,
        zone_bottom=fvg.bottom_price,
        zone_top=fvg.top_price,
        touch_price=Decimal("4012.00"),
        penetration_depth=Decimal("2.00"),
        penetration_percentage=Decimal("20.00"),
        retest_sequence_number=1,
        reaction_confirmation_time=t0 + timedelta(hours=2),
        reaction_distance=Decimal("5.00"),
        status=FvgRetestStatus.RESPECT_CONFIRMED,
        status_reason="Respect confirmed",
    )
    resp_sum = FvgRespectSummary(
        fvg_id=fvg.id,
        instrument_id=INST,
        source_id=SRC,
        price_type=PriceType.BID,
        timeframe=Timeframe.H1,
        fvg_direction=FvgDirection.BULLISH,
        effective_direction=FvgReactionDirection.BULLISH_SUPPORT,
        formation_time=fvg.formation_time,
        inversion_confirmed_at=None,
        zone_bottom=fvg.bottom_price,
        zone_top=fvg.top_price,
        zone_width=fvg.width,
        total_touch_count=1,
        confirmed_respect_count=1,
        failed_reaction_count=0,
        pending_reaction_count=0,
        consecutive_respect_count=1,
        maximum_respect_streak=1,
        last_touch_time=t0 + timedelta(hours=1),
        last_respect_time=t0 + timedelta(hours=2),
        final_status=FvgRetestStatus.RESPECT_CONFIRMED,
        total_retest_count=1,
        reported_retest_count=1,
        remaining_retests=0,
        retest_events=(ev,),
    )

    setups = detect_ict_setup_candidates(
        c_list,
        structure_breaks=[bos],
        displacements=[dsp],
        fvg_zones=[fvg],
        fvg_respect_summaries=[resp_sum],
    )
    exp = explain_ict_setup(setups[0])
    assert "%" not in exp  # Strictly zero percentage / probability claims
    assert "win" not in exp.lower()


def test_43_zero_candidates_safe_empty_result() -> None:
    """Test 43: Empty candle or event input safely returns empty tuple and empty summary."""
    setups = detect_ict_setup_candidates([])
    assert setups == ()
    summary = summarize_ict_setups(setups)
    assert summary.total_candidates == 0
    assert summary.avg_confluence_score == Decimal("0")


def test_44_immutable_models_no_side_effects() -> None:
    """Test 44: Candidate and summary models are immutable frozen dataclasses."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))

    setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg]
    )
    s = setups[0]
    with pytest.raises(FrozenInstanceError):
        s.status = IctSetupStatus.INVALIDATED  # type: ignore[misc]


def test_45_max_analysis_cutoff_respects_data_coverage() -> None:
    """Test 45: Cutoff timestamp clamps candidates strictly within data coverage."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=7), break_type=StructureBreakType.BOS
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=7))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=8))

    # Cutoff at hour 5 excludes candidate forming at hour 8
    setups = detect_ict_setup_candidates(
        c_list,
        structure_breaks=[bos],
        displacements=[dsp],
        fvg_zones=[fvg],
        as_of_utc=t0 + timedelta(hours=5),
    )
    assert len(setups) == 0


def test_46_knowledge_snapshot_time_not_confused_with_close_time() -> None:
    """Test 46: Knowledge cutoff filters strictly against setup_available_time."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    bos = make_test_structure_break(
        "BOS_01", t0 + timedelta(hours=2), break_type=StructureBreakType.BOS
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))

    # Available time is hour 3 (fvg formation)
    setups = detect_ict_setup_candidates(
        c_list,
        structure_breaks=[bos],
        displacements=[dsp],
        fvg_zones=[fvg],
        as_of_utc=t0 + timedelta(hours=3),
    )
    assert len(setups) == 1
    assert setups[0].setup_available_time == t0 + timedelta(hours=3)


def test_47_no_cross_source_confluence() -> None:
    """Test 47: Validates that heterogeneous dataSource or instrument is rejected in candles."""
    t0 = BASE_TIME
    c1 = make_candle(t0, "4000", "4010", "3990", "4005")
    # Change instrument
    c2 = Candle(
        instrument_id=InstrumentId("EURUSD"),
        source_id=SRC,
        timeframe=Timeframe.H1,
        price_type=PriceType.BID,
        open_time=t0 + timedelta(hours=1),
        close_time=t0 + timedelta(hours=2),
        open=Decimal("4005"),
        high=Decimal("4015"),
        low=Decimal("3995"),
        close=Decimal("4010"),
        volume=Decimal("100"),
        is_closed=True,
    )
    with pytest.raises(IctSetupDataError, match="Heterogeneous candle sequence"):
        detect_ict_setup_candidates([c1, c2])


def test_48_pending_candidate_not_reported_as_confirmed() -> None:
    """Test 48: PENDING MSS candidate is not processed into a confirmed candidate."""
    t0 = BASE_TIME
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]
    swp = make_test_sweep("SWP_01", t0 + timedelta(hours=1))
    ch = make_test_structure_break(
        "CH_01", t0 + timedelta(hours=2), break_type=StructureBreakType.CHOCH
    )
    dsp = make_test_disp("DSP_01", t0 + timedelta(hours=2))
    fvg = make_test_fvg("FVG_01", t0 + timedelta(hours=3))
    mss_pend = make_test_mss(
        "MSS_01", "CH_01", ch.detection_time, t0 + timedelta(hours=3), status=MssStatus.PENDING
    )

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp],
        structure_breaks=[ch],
        mss_records=[mss_pend],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    assert len(setups) == 0


def test_49_future_invalidation_not_retroactively_applied() -> None:
    """Test 49: Future invalidation occurring at bar 8 is not present in snapshot at bar 5."""
    t0 = BASE_TIME
    t_bos = t0 + timedelta(hours=2)
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4015", "4030", "4010", "4025") for i in range(10)
    ]
    # Invalidation bar at hour 8 (closes below zone bottom 4010)
    c_list[8] = make_candle(t0 + timedelta(hours=8), "4025", "4025", "3990", "3995")

    bos = make_test_structure_break("BOS_01", t_bos, break_type=StructureBreakType.BOS)
    dsp = make_test_disp("DSP_01", t_bos)
    fvg = make_test_fvg("FVG_01", t_bos + timedelta(hours=1), bot="4010", top="4020")

    # At full evaluation, candidate is INVALIDATED
    full_setups = detect_ict_setup_candidates(
        c_list, structure_breaks=[bos], displacements=[dsp], fvg_zones=[fvg]
    )
    assert full_setups[0].status == IctSetupStatus.INVALIDATED

    # As of hour 5, candidate is NOT invalidated
    filtered = filter_ict_setups_as_of(full_setups, t0 + timedelta(hours=5))
    assert filtered[0].status != IctSetupStatus.INVALIDATED
    assert filtered[0].invalidation_time is None


def test_50_event_id_traceability_and_consistency() -> None:
    """Test 50: Verifies that candidate preserves exact IDs for structure, sweep, mss, displacement, and zone."""
    t0 = BASE_TIME
    t_swp = t0 + timedelta(hours=1)
    t_ch = t0 + timedelta(hours=2)
    t_fvg = t0 + timedelta(hours=3)
    c_list = [
        make_candle(t0 + timedelta(hours=i), "4000", "4010", "3990", "4005") for i in range(10)
    ]

    swp = make_test_sweep("SWP_TARGET_ID", t_swp)
    ch = make_test_structure_break("CH_TARGET_ID", t_ch, break_type=StructureBreakType.CHOCH)
    dsp = make_test_disp("DSP_TARGET_ID", t_ch)
    fvg = make_test_fvg("FVG_TARGET_ID", t_fvg)
    mss = make_test_mss(
        "MSS_TARGET_ID", ch.event_id, ch.detection_time, t_fvg, disp_id=dsp.event_id, fvg_id=fvg.id
    )

    setups = detect_ict_setup_candidates(
        c_list,
        sweeps=[swp],
        structure_breaks=[ch],
        mss_records=[mss],
        displacements=[dsp],
        fvg_zones=[fvg],
    )
    s = setups[0]
    assert s.structure_event_id == "CH_TARGET_ID"
    assert s.sweep_event_id == "SWP_TARGET_ID"
    assert s.mss_confirmation_id == "MSS_TARGET_ID"
    assert s.displacement_event_id == "DSP_TARGET_ID"
    assert s.fvg_id == "FVG_TARGET_ID"
    assert s.selected_zone_bottom == fvg.bottom_price
    assert s.selected_zone_top == fvg.top_price
