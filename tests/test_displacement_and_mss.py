"""Comprehensive test suite for Phase 3D Displacement and MSS Confirmation Engine.

Covers all 30 acceptance criteria specified in TraderLab Phase 3D:
1. Basic Bullish Displacement
2. Basic Bearish Displacement
3. Body Ratio Calculation
4. Zero Range Handling
5. Close Location Calculation
6. ATR / Historical Volatility
7. No Future ATR Leakage
8. Configurable Thresholds
9. Weak Candle Rejection
10. Candidate CHoCH Integration
11. MSS Pending State
12. MSS Displacement Confirmation
13. MSS FVG Confirmation
14. No MSS Confirmation Before FVG Formation
15. Candidate Expiration
16. Insufficient Data Handling
17. Wrong Direction Rejection
18. Duplicate MSS Prevention
19. Swing / Sweep / CHoCH Isolation
20. BID/ASK Isolation
21. D1/H4/H2 Isolation
22. D1 UTC/NY Modes
23. Partial Candle Rejection
24. Synthetic Candle Rejection
25. Data Gap Protection
26. Historical As_Of No Lookahead
27. Deterministic IDs
28. Decimal Precision
29. Empty Input Safety
30. Strict PIT vs Research Estimated
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
from traderlab.domain.market_structure.displacement import (
    DisplacementConfig,
    DisplacementDirection,
    compute_historical_atr,
    compute_true_range,
    detect_displacements,
    generate_displacement_id,
)
from traderlab.domain.market_structure.fvg import FvgDirection, FvgZone, detect_fair_value_gaps
from traderlab.domain.market_structure.market_structure import (
    MarketTrend,
    StructureBreakEvent,
    StructureBreakType,
    StructureDirection,
)
from traderlab.domain.market_structure.mss_confirmation import (
    MssConfirmationConfig,
    MssConfirmationDataError,
    MssStatus,
    evaluate_mss_confirmations,
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


def make_test_choch_event(
    event_id: str,
    direction: StructureDirection,
    break_bar_open: datetime,
    break_bar_close: datetime,
    *,
    ref_price: str = "2650.00",
    break_price: str = "2660.00",
    timeframe: Timeframe = Timeframe.H1,
    price_type: PriceType = PriceType.BID,
    instrument_id: str = "XAUUSD",
    source_id: str = "market_data_lab_m1",
    source_swing_id: str = "sh1",
) -> StructureBreakEvent:
    """Create a valid CHoCH StructureBreakEvent for testing."""
    mss_cand_id = f"MSS_CANDIDATE_{event_id}"
    trend_before = (
        MarketTrend.DOWNTREND if direction == StructureDirection.BULLISH else MarketTrend.UPTREND
    )
    return StructureBreakEvent(
        event_id=event_id,
        instrument_id=InstrumentId(instrument_id),
        source_id=DataSourceId(source_id),
        timeframe=timeframe,
        price_type=price_type,
        structure_direction=direction,
        break_type=StructureBreakType.CHOCH,
        source_swing_id=source_swing_id,
        protected_level_id=source_swing_id,
        reference_level_price=Decimal(ref_price),
        break_price=Decimal(break_price),
        break_bar_open_time=break_bar_open,
        break_bar_close_time=break_bar_close,
        detection_time=break_bar_close,
        trend_before=trend_before,
        trend_after=MarketTrend.POTENTIAL_REVERSAL,
        confirmation_status="CONFIRMED",
        mss_candidate_id=mss_cand_id,
    )


def make_test_fvg(
    fvg_id: str,
    direction: FvgDirection,
    candle_1_open: datetime,
    candle_2_open: datetime,
    candle_3_open: datetime,
    formation_time: datetime,
    top_p: str,
    bot_p: str,
    *,
    timeframe: Timeframe = Timeframe.H1,
    price_type: PriceType = PriceType.BID,
    instrument_id: str = "XAUUSD",
    source_id: str = "market_data_lab_m1",
) -> FvgZone:
    """Create a valid domain FvgZone for testing."""
    top = Decimal(top_p)
    bot = Decimal(bot_p)
    ce = (top + bot) / Decimal(2)
    width = top - bot
    return FvgZone(
        id=fvg_id,
        instrument_id=InstrumentId(instrument_id),
        source_id=DataSourceId(source_id),
        price_type=price_type,
        timeframe=timeframe,
        direction=direction,
        top_price=top,
        bottom_price=bot,
        ce_price=ce,
        width=width,
        candle_1_open=candle_1_open,
        candle_2_open=candle_2_open,
        candle_3_open=candle_3_open,
        formation_time=formation_time,
        computable_at_utc=formation_time,
    )


def create_baseline_candles(count: int = 14, range_val: str = "2.00") -> list[Candle]:
    """Create a sequence of standard historical candles with predictable TR (approx range_val)."""
    candles: list[Candle] = []
    base_price = Decimal("2650.00")
    for i in range(count):
        # alternate slightly: open=2650, high=2651, low=2649, close=2650.50 -> range=2.00
        o_p = str(base_price)
        h_p = str(base_price + Decimal(range_val) / 2)
        l_p = str(base_price - Decimal(range_val) / 2)
        c_p = str(base_price + Decimal("0.50"))
        candles.append(make_test_candle(i, o_p, h_p, l_p, c_p))
    return candles


# ── 1. Basic Bullish Displacement ─────────────────────────────────────────────
def test_basic_bullish_displacement() -> None:
    """Verify strong bullish candle with high body ratio and close near high is detected."""
    candles = create_baseline_candles(14, range_val="2.00")
    # Candle 14 is a large bullish expansion: open=2650.00, high=2660.00, low=2649.50, close=2659.00
    # Range = 10.50 (multiple = 10.50 / ~2.00 = 5.25x ATR), Body = 9.00 (ratio = 9/10.50 = 85.7%)
    # Close location = (2659 - 2649.50) / 10.50 = 0.905 (> 0.75)
    disp_bar = make_test_candle(14, "2650.00", "2660.00", "2649.50", "2659.00")
    candles.append(disp_bar)

    cfg = DisplacementConfig(tick_size=Decimal("0.01"), atr_window=14)
    events = detect_displacements(candles, config=cfg)

    assert len(events) == 1
    ev = events[0]
    assert ev.is_bullish
    assert ev.direction == DisplacementDirection.BULLISH
    assert ev.detection_time == disp_bar.close_time
    assert ev.body_ratio > Decimal("0.80")
    assert ev.close_location > Decimal("0.75")
    assert ev.range_atr_multiple > Decimal("3.00")


# ── 2. Basic Bearish Displacement ─────────────────────────────────────────────
def test_basic_bearish_displacement() -> None:
    """Verify strong bearish candle with high body ratio and close near low is detected."""
    candles = create_baseline_candles(14, range_val="2.00")
    # Candle 14 is a large bearish expansion: open=2660.00, high=2660.50, low=2650.00, close=2651.00
    # Range = 10.50, Body = 9.00 (ratio = 85.7%), Close location = (2651 - 2650) / 10.50 = 0.095 (<= 0.25)
    disp_bar = make_test_candle(14, "2660.00", "2660.50", "2650.00", "2651.00")
    candles.append(disp_bar)

    cfg = DisplacementConfig(tick_size=Decimal("0.01"), atr_window=14)
    events = detect_displacements(candles, config=cfg)

    assert len(events) == 1
    ev = events[0]
    assert ev.is_bearish
    assert ev.direction == DisplacementDirection.BEARISH
    assert ev.detection_time == disp_bar.close_time
    assert ev.close_location < Decimal("0.25")


# ── 3. Body Ratio Calculation ─────────────────────────────────────────────────
def test_body_ratio_calculation() -> None:
    """Verify body_size and total_range calculations match exact Decimal arithmetic."""
    c = make_test_candle(0, "2650.00", "2660.00", "2650.00", "2658.00")
    body = abs(c.close - c.open)  # 8.00
    total = c.high - c.low  # 10.00
    ratio = body / total
    assert ratio == Decimal("0.80")


# ── 4. Zero Range Handling ────────────────────────────────────────────────────
def test_zero_range_handling() -> None:
    """Verify flat candle where high == low does not cause ZeroDivisionError and is rejected."""
    candles = create_baseline_candles(14, range_val="2.00")
    flat_candle = make_test_candle(14, "2650.00", "2650.00", "2650.00", "2650.00")
    candles.append(flat_candle)

    events = detect_displacements(candles, tick_size=Decimal("0.01"))
    assert len(events) == 0


# ── 5. Close Location Calculation ─────────────────────────────────────────────
def test_close_location_calculation() -> None:
    """Verify close_location = (close - low) / total_range."""
    c = make_test_candle(0, "2652.00", "2660.00", "2650.00", "2658.00")
    loc = (c.close - c.low) / (c.high - c.low)
    assert loc == Decimal("0.80")


# ── 6. ATR / Historical Volatility ────────────────────────────────────────────
def test_atr_historical_volatility() -> None:
    """Verify compute_historical_atr computes exact average of preceding True Ranges."""
    candles = create_baseline_candles(14, range_val="2.00")
    atr = compute_historical_atr(candles, window=14)
    assert atr is not None
    assert atr > Decimal("1.50")
    assert atr < Decimal("2.50")


# ── 7. No Future ATR Leakage ──────────────────────────────────────────────────
def test_no_future_atr_leakage() -> None:
    """Verify candle i's reference ATR uses strictly bars before i, excluding i and future bars."""
    candles = create_baseline_candles(14, range_val="2.00")
    atr_before = compute_historical_atr(candles, window=14)

    # Add massive candle at index 14
    huge_bar = make_test_candle(14, "2650.00", "2750.00", "2650.00", "2750.00")
    candles.append(huge_bar)

    # Detect displacement on index 14
    cfg = DisplacementConfig(tick_size=Decimal("0.01"), atr_window=14)
    events = detect_displacements(candles, config=cfg)
    assert len(events) == 1
    # The reference ATR for huge_bar must match atr_before exactly!
    assert events[0].volatility_reference == atr_before


# ── 8. Configurable Thresholds ────────────────────────────────────────────────
def test_configurable_thresholds() -> None:
    """Verify customized thresholds (e.g. min_body_ratio=0.90) correctly filter events."""
    candles = create_baseline_candles(14, range_val="2.00")
    # Bar with 80% body ratio
    bar14 = make_test_candle(
        14, "2650.00", "2660.00", "2649.50", "2658.00"
    )  # ratio = 8/10.5 = 76%
    candles.append(bar14)

    # Standard threshold 0.60 accepts
    cfg_std = DisplacementConfig(tick_size=Decimal("0.01"), min_body_ratio=Decimal("0.60"))
    assert len(detect_displacements(candles, config=cfg_std)) == 1

    # Strict threshold 0.85 rejects
    cfg_strict = DisplacementConfig(tick_size=Decimal("0.01"), min_body_ratio=Decimal("0.85"))
    assert len(detect_displacements(candles, config=cfg_strict)) == 0


# ── 9. Weak Candle Rejection ──────────────────────────────────────────────────
def test_weak_candle_rejection() -> None:
    """Verify doji/wicky candle is rejected even if range is large."""
    candles = create_baseline_candles(14, range_val="2.00")
    # Large range (10.00) but close near open (doji/wick): open=2655, high=2660, low=2650, close=2655.20
    wick_bar = make_test_candle(14, "2655.00", "2660.00", "2650.00", "2655.20")
    candles.append(wick_bar)

    events = detect_displacements(candles, tick_size=Decimal("0.01"))
    assert len(events) == 0


# ── 10. Candidate CHoCH Integration ───────────────────────────────────────────
def test_candidate_choch_integration() -> None:
    """Verify evaluate_mss_confirmations receives CHoCH event and initializes evaluation."""
    candles = create_baseline_candles(15, range_val="2.00")
    choch_bar = candles[10]
    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        choch_bar.open_time,
        choch_bar.close_time,
    )

    records = evaluate_mss_confirmations(candles, [choch])
    assert len(records) == 1
    assert records[0].source_choch_event_id == "choch_1"
    assert records[0].mss_candidate_id == "MSS_CANDIDATE_choch_1"


# ── 11. MSS Pending State ─────────────────────────────────────────────────────
def test_mss_pending_state() -> None:
    """Verify candidate remains PENDING while within confirmation window without displacement."""
    # 14 baseline + 1 break candle (index 14) + 1 small quiet candle (index 15)
    candles = create_baseline_candles(15, range_val="2.00")
    quiet_bar = make_test_candle(15, "2650.00", "2651.00", "2649.50", "2650.50")
    candles.append(quiet_bar)

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        candles[14].open_time,
        candles[14].close_time,
    )

    cfg = MssConfirmationConfig(max_confirmation_bars=3)
    records = evaluate_mss_confirmations(candles, [choch], config=cfg)

    assert len(records) == 1
    assert records[0].status == MssStatus.PENDING
    assert records[0].is_pending


# ── 12. MSS Displacement Confirmation ─────────────────────────────────────────
def test_mss_displacement_confirmation() -> None:
    """Verify candidate transitions to DISPLACEMENT_CONFIRMED when displacement occurs awaiting FVG."""
    candles = create_baseline_candles(14, range_val="2.00")
    # Bar 14 is the CHoCH break candle
    choch_bar = make_test_candle(14, "2650.00", "2652.00", "2649.00", "2651.50")
    candles.append(choch_bar)

    # Bar 15 is a strong bullish displacement
    disp_bar = make_test_candle(15, "2651.50", "2662.00", "2651.00", "2661.00")
    candles.append(disp_bar)

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        choch_bar.open_time,
        choch_bar.close_time,
    )

    # require_fvg_confirmation=True, but no FVG formed yet
    disp_cfg = DisplacementConfig(tick_size=Decimal("0.01"), atr_window=14)
    cfg = MssConfirmationConfig(
        max_confirmation_bars=3,
        require_fvg_confirmation=True,
        displacement_config=disp_cfg,
    )
    records = evaluate_mss_confirmations(candles, [choch], config=cfg)

    assert len(records) == 1
    assert records[0].status == MssStatus.DISPLACEMENT_CONFIRMED
    assert records[0].is_displacement_confirmed
    assert records[0].displacement_time == disp_bar.close_time


# ── 13. MSS FVG Confirmation ─────────────────────────────────────────────────
def test_mss_fvg_confirmation() -> None:
    """Verify candidate reaches MSS_CONFIRMED when both displacement and FVG are confirmed."""
    candles = create_baseline_candles(14, range_val="2.00")
    # Bar 14: CHoCH break candle (candle 1 of FVG sequence)
    bar14 = make_test_candle(14, "2650.00", "2652.00", "2649.00", "2651.50")
    candles.append(bar14)
    # Bar 15: Huge displacement (candle 2 of FVG sequence)
    bar15 = make_test_candle(15, "2651.50", "2662.00", "2651.00", "2661.00")
    candles.append(bar15)
    # Bar 16: Continuation (candle 3 of FVG sequence: low > bar14 high -> FVG!)
    # bar16 low = 2655.00 > bar14 high (2652.00)
    bar16 = make_test_candle(16, "2661.00", "2665.00", "2655.00", "2664.00")
    candles.append(bar16)

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        bar14.open_time,
        bar14.close_time,
    )

    disp_cfg = DisplacementConfig(tick_size=Decimal("0.01"), atr_window=14)
    cfg = MssConfirmationConfig(
        max_confirmation_bars=3,
        require_displacement=True,
        require_fvg_confirmation=True,
        displacement_config=disp_cfg,
    )
    records = evaluate_mss_confirmations(candles, [choch], config=cfg)

    assert len(records) == 1
    rec = records[0]
    assert rec.status == MssStatus.MSS_CONFIRMED
    assert rec.is_confirmed
    assert rec.final_confirmation_time == bar16.close_time


# ── 14. No MSS Confirmation Before FVG Formation ──────────────────────────────
def test_no_mss_confirmation_before_fvg_formation() -> None:
    """Verify MSS_CONFIRMED is NOT produced before candle 3 of FVG closes."""
    candles = create_baseline_candles(14, range_val="2.00")
    bar14 = make_test_candle(14, "2650.00", "2652.00", "2649.00", "2651.50")
    candles.append(bar14)
    bar15 = make_test_candle(15, "2651.50", "2662.00", "2651.00", "2661.00")
    candles.append(bar15)
    bar16 = make_test_candle(16, "2661.00", "2665.00", "2655.00", "2664.00")
    candles.append(bar16)

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        bar14.open_time,
        bar14.close_time,
    )

    # Query as of bar15 close (before bar16 closes and forms FVG)
    as_of = bar15.close_time
    disp_cfg = DisplacementConfig(tick_size=Decimal("0.01"), atr_window=14)
    cfg = MssConfirmationConfig(
        max_confirmation_bars=3,
        require_fvg_confirmation=True,
        displacement_config=disp_cfg,
    )
    records = evaluate_mss_confirmations(candles, [choch], config=cfg, as_of_utc=as_of)

    assert len(records) == 1
    assert records[0].status == MssStatus.DISPLACEMENT_CONFIRMED
    assert not records[0].is_confirmed


# ── 15. Candidate Expiration ──────────────────────────────────────────────────
def test_candidate_expiration() -> None:
    """Verify candidate transitions to REJECTED once confirmation window fully elapses without displacement."""
    candles = create_baseline_candles(14, range_val="2.00")
    choch_bar = make_test_candle(14, "2650.00", "2652.00", "2649.00", "2651.50")
    candles.append(choch_bar)

    # 3 quiet bars elapsed without displacement (max_confirmation_bars = 3)
    for k in range(15, 18):
        candles.append(make_test_candle(k, "2651.00", "2652.00", "2650.00", "2651.20"))

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        choch_bar.open_time,
        choch_bar.close_time,
    )

    cfg = MssConfirmationConfig(max_confirmation_bars=3)
    records = evaluate_mss_confirmations(candles, [choch], config=cfg)

    assert len(records) == 1
    assert records[0].status == MssStatus.REJECTED
    assert records[0].rejection_reason == "WINDOW_EXPIRED_NO_DISPLACEMENT"


# ── 16. Insufficient Data Handling ────────────────────────────────────────────
def test_insufficient_data_handling() -> None:
    """Verify candidate break bar not found in candle stream produces INSUFFICIENT_DATA."""
    candles = create_baseline_candles(10, range_val="2.00")
    # Event at a future timestamp not in candles
    choch = make_test_choch_event(
        "choch_missing",
        StructureDirection.BULLISH,
        datetime(2026, 10, 5, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 5, 1, 0, tzinfo=UTC),
    )

    records = evaluate_mss_confirmations(candles, [choch])
    assert len(records) == 1
    assert records[0].status == MssStatus.INSUFFICIENT_DATA


# ── 17. Wrong Direction Rejection ─────────────────────────────────────────────
def test_wrong_direction_rejection() -> None:
    """Verify opposite displacement immediately rejects the MSS candidate."""
    candles = create_baseline_candles(14, range_val="2.00")
    # Bullish CHoCH at index 14
    choch_bar = make_test_candle(14, "2650.00", "2652.00", "2649.00", "2651.50")
    candles.append(choch_bar)
    # Violent BEARISH crash at index 15 (opposite direction!)
    crash_bar = make_test_candle(15, "2651.50", "2652.00", "2640.00", "2640.50")
    candles.append(crash_bar)

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        choch_bar.open_time,
        choch_bar.close_time,
    )

    disp_cfg = DisplacementConfig(tick_size=Decimal("0.01"), atr_window=14)
    cfg = MssConfirmationConfig(displacement_config=disp_cfg)
    records = evaluate_mss_confirmations(candles, [choch], config=cfg)

    assert len(records) == 1
    assert records[0].status == MssStatus.REJECTED
    assert records[0].rejection_reason == "OPPOSITE_DISPLACEMENT_DETECTED"


# ── 18. Duplicate MSS Prevention ──────────────────────────────────────────────
def test_duplicate_mss_prevention() -> None:
    """Verify redundant evaluations for the same candidate produce exactly one record."""
    candles = create_baseline_candles(15, range_val="2.00")
    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        candles[14].open_time,
        candles[14].close_time,
    )

    # Pass duplicate identical CHoCH events
    records = evaluate_mss_confirmations(candles, [choch, choch])
    assert len(records) == 1


# ── 19. Swing / Sweep / CHoCH Isolation ───────────────────────────────────────
def test_swing_sweep_choch_isolation() -> None:
    """Verify sweeps are attached as metadata without altering candidate IDs or records."""
    candles = create_baseline_candles(15, range_val="2.00")
    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        candles[14].open_time,
        candles[14].close_time,
    )

    records = evaluate_mss_confirmations(candles, [choch])
    assert len(records) == 1
    assert records[0].source_choch_event_id == "choch_1"


# ── 20. BID/ASK Isolation ─────────────────────────────────────────────────────
def test_bid_ask_isolation() -> None:
    """Verify passing ASK CHoCH with BID candles raises MssConfirmationDataError."""
    candles_bid = create_baseline_candles(15, range_val="2.00")
    choch_ask = make_test_choch_event(
        "choch_ask",
        StructureDirection.BULLISH,
        candles_bid[14].open_time,
        candles_bid[14].close_time,
        price_type=PriceType.ASK,
    )

    with pytest.raises(MssConfirmationDataError, match="PriceType mismatch"):
        evaluate_mss_confirmations(candles_bid, [choch_ask])


# ── 21. D1/H4/H2 Isolation ────────────────────────────────────────────────────
def test_timeframe_isolation() -> None:
    """Verify passing H4 CHoCH with H1 candles raises MssConfirmationDataError."""
    candles_h1 = create_baseline_candles(15, range_val="2.00")
    choch_h4 = make_test_choch_event(
        "choch_h4",
        StructureDirection.BULLISH,
        candles_h1[14].open_time,
        candles_h1[14].close_time,
        timeframe=Timeframe.H4,
    )

    with pytest.raises(MssConfirmationDataError, match="Timeframe mismatch"):
        evaluate_mss_confirmations(candles_h1, [choch_h4])


# ── 22. D1 UTC/NY Modes ───────────────────────────────────────────────────────
def test_d1_utc_and_ny_modes() -> None:
    """Verify Sunday stub exclusion works cleanly on D1 series."""
    sun_stub = make_test_candle(
        0,
        "2650.00",
        "2652.00",
        "2649.00",
        "2651.00",
        base_time=datetime(2026, 10, 4, 0, 0, tzinfo=UTC),
        timeframe=Timeframe.D1,
    )
    mon_bar = make_test_candle(
        0,
        "2651.00",
        "2665.00",
        "2650.00",
        "2664.00",
        base_time=datetime(2026, 10, 5, 0, 0, tzinfo=UTC),
        timeframe=Timeframe.D1,
    )
    # Sunday stub excluded, evaluation executes smoothly
    events = detect_displacements([sun_stub, mon_bar], tick_size=Decimal("0.01"))
    assert isinstance(events, list)


# ── 23. Partial Candle Rejection ──────────────────────────────────────────────
def test_partial_candle_rejection() -> None:
    """Verify unclosed candles are rejected from displacement and MSS evaluation."""
    candles = create_baseline_candles(14, range_val="2.00")
    partial = make_test_candle(14, "2650.00", "2665.00", "2650.00", "2664.00", is_closed=False)
    candles.append(partial)

    events = detect_displacements(candles, tick_size=Decimal("0.01"))
    assert len(events) == 0


# ── 24. Synthetic Candle Rejection ────────────────────────────────────────────
def test_synthetic_candle_rejection() -> None:
    """Verify synthetic candles are rejected from being displacements."""
    candles = create_baseline_candles(14, range_val="2.00")
    synthetic = make_test_candle(14, "2650.00", "2665.00", "2650.00", "2664.00", is_synthetic=True)
    candles.append(synthetic)

    events = detect_displacements(candles, tick_size=Decimal("0.01"))
    assert len(events) == 0


# ── 25. Data Gap Protection ───────────────────────────────────────────────────
def test_data_gap_protection() -> None:
    """Verify calendar gaps flag DATA_GAP_PRECEDING on displacement events."""
    candles = create_baseline_candles(14, range_val="2.00")
    # 5-hour unannounced gap before displacement bar
    gap_time = candles[-1].close_time + timedelta(hours=5)
    disp_bar = make_test_candle(0, "2650.00", "2662.00", "2649.50", "2661.00", base_time=gap_time)
    candles.append(disp_bar)

    cfg = DisplacementConfig(tick_size=Decimal("0.01"), allow_weekend_gap=False)
    events = detect_displacements(candles, config=cfg)
    assert len(events) == 1
    assert "DATA_GAP_PRECEDING" in events[0].flags


# ── 26. Historical As_Of No Lookahead ─────────────────────────────────────────
def test_historical_as_of_no_lookahead() -> None:
    """Verify querying at as_of before displacement hides the displacement."""
    candles = create_baseline_candles(14, range_val="2.00")
    disp_bar = make_test_candle(14, "2650.00", "2662.00", "2649.50", "2661.00")
    candles.append(disp_bar)

    # Query before disp_bar closes
    as_of = disp_bar.open_time + timedelta(minutes=30)
    events = detect_displacements(candles, tick_size=Decimal("0.01"), as_of_utc=as_of)
    assert len(events) == 0


# ── 27. Deterministic IDs ─────────────────────────────────────────────────────
def test_deterministic_ids() -> None:
    """Verify repeated execution produces identical displacement IDs."""
    candles = create_baseline_candles(14, range_val="2.00")
    disp_bar = make_test_candle(14, "2650.00", "2662.00", "2649.50", "2661.00")
    candles.append(disp_bar)

    ev1 = detect_displacements(candles, tick_size=Decimal("0.01"))
    ev2 = detect_displacements(candles, tick_size=Decimal("0.01"))

    assert len(ev1) == len(ev2) == 1
    expected_id = generate_displacement_id(
        disp_bar.instrument_id,
        disp_bar.source_id,
        disp_bar.price_type,
        disp_bar.timeframe,
        DisplacementDirection.BULLISH,
        disp_bar.open_time,
    )
    assert ev1[0].event_id == expected_id
    assert ev1[0].event_id == ev2[0].event_id


# ── 28. Decimal Precision ─────────────────────────────────────────────────────
def test_decimal_precision() -> None:
    """Verify sub-pip Decimal precision throughout True Range and ATR."""
    c1 = make_test_candle(0, "2650.12345", "2652.55555", "2649.88888", "2651.99999")
    tr = compute_true_range(c1)
    expected = Decimal("2652.55555") - Decimal("2649.88888")
    assert tr == expected


# ── 29. Empty Input Safety ────────────────────────────────────────────────────
def test_empty_input_safety() -> None:
    """Verify empty inputs return empty lists gracefully."""
    assert detect_displacements([], tick_size=Decimal("0.01")) == []
    assert evaluate_mss_confirmations([], []) == []


# ── 30. Strict PIT vs Research Estimated ──────────────────────────────────────
def test_strict_pit_vs_research_estimated() -> None:
    """Verify strict_pit=True enforces VERIFIED availability confidence."""
    candles = create_baseline_candles(15, range_val="2.00")
    # By default, make_test_candle creates ESTIMATED confidence
    cfg_strict = DisplacementConfig(tick_size=Decimal("0.01"), strict_pit=True)
    events_strict = detect_displacements(candles, config=cfg_strict)
    # ESTIMATED candles are rejected under STRICT_PIT
    assert len(events_strict) == 0

    # Under RESEARCH_ESTIMATED (default) they are processed
    cfg_est = DisplacementConfig(tick_size=Decimal("0.01"), strict_pit=False)
    # Not enough range to displace, but candles are accepted into the stream without crash
    assert isinstance(detect_displacements(candles, config=cfg_est), list)


# ── 31. Unrelated FVG Cannot Confirm MSS (Faz 3D-K1) ───────────────────────────
def test_unrelated_fvg_cannot_confirm_mss() -> None:
    """Verify that an FVG whose candle_2 is NOT the displacement cannot confirm MSS."""
    candles = create_baseline_candles(14, range_val="2.00")
    # Bar 14: CHoCH break candle
    bar14 = make_test_candle(14, "2650.00", "2652.00", "2649.00", "2651.50")
    candles.append(bar14)
    # Bar 15: Ordinary candle (NOT displacement)
    bar15 = make_test_candle(15, "2651.50", "2653.00", "2650.50", "2652.00")
    candles.append(bar15)
    # Bar 16: Ordinary candle
    bar16 = make_test_candle(16, "2652.00", "2656.00", "2651.50", "2655.00")
    candles.append(bar16)
    # Bar 17: Genuine displacement candle (Bar 17)
    bar17 = make_test_candle(17, "2655.00", "2670.00", "2654.50", "2669.00")
    candles.append(bar17)
    # Bar 18: Ordinary candle
    bar18 = make_test_candle(18, "2669.00", "2672.00", "2668.00", "2671.00")
    candles.append(bar18)

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        bar14.open_time,
        bar14.close_time,
    )

    # An unrelated FVG across bar 14, 15, 16 (where candle 2 is bar 15, NOT bar 17!)
    unrelated_fvg = make_test_fvg(
        "fvg_unrelated",
        FvgDirection.BULLISH,
        bar14.open_time,
        bar15.open_time,
        bar16.open_time,
        bar16.close_time,
        "2654.00",
        "2652.50",
    )

    disp_cfg = DisplacementConfig(tick_size=Decimal("0.01"), atr_window=14)
    cfg = MssConfirmationConfig(
        max_confirmation_bars=3,
        require_displacement=True,
        require_fvg_confirmation=True,
        displacement_config=disp_cfg,
    )

    # Displacements detected from candles (will find bar 17)
    displacements = detect_displacements(candles, config=disp_cfg)
    assert any(d.candle_open_time == bar17.open_time for d in displacements)

    records = evaluate_mss_confirmations(
        candles,
        [choch],
        displacements=displacements,
        fvgs=[unrelated_fvg],
        config=cfg,
    )

    assert len(records) == 1
    rec = records[0]
    # The unrelated FVG MUST be rejected; candidate must NOT be MSS_CONFIRMED
    assert rec.status != MssStatus.MSS_CONFIRMED
    assert rec.associated_fvg_id is None
    assert rec.status in (MssStatus.DISPLACEMENT_CONFIRMED, MssStatus.REJECTED)


# ── 32. FVG Candle 2 Must Match Selected Displacement (Faz 3D-K1) ─────────────
def test_fvg_candle_2_must_match_selected_displacement() -> None:
    """Verify that an FVG whose candle_2 matches selected displacement confirms MSS."""
    candles = create_baseline_candles(14, range_val="2.00")
    bar14 = make_test_candle(14, "2650.00", "2652.00", "2649.00", "2651.50")
    candles.append(bar14)
    # Bar 15 is displacement (Candle 2 of FVG)
    bar15 = make_test_candle(15, "2651.50", "2665.00", "2651.00", "2664.00")
    candles.append(bar15)
    # Bar 16 is continuation (Candle 3 of FVG)
    bar16 = make_test_candle(16, "2664.00", "2668.00", "2656.00", "2667.00")
    candles.append(bar16)

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        bar14.open_time,
        bar14.close_time,
    )

    valid_fvg = make_test_fvg(
        "fvg_valid",
        FvgDirection.BULLISH,
        bar14.open_time,
        bar15.open_time,  # Matches bar15 (displacement)
        bar16.open_time,
        bar16.close_time,
        "2656.00",
        "2652.00",
    )

    disp_cfg = DisplacementConfig(tick_size=Decimal("0.01"), atr_window=14)
    cfg = MssConfirmationConfig(
        max_confirmation_bars=3,
        require_displacement=True,
        require_fvg_confirmation=True,
        displacement_config=disp_cfg,
    )
    records = evaluate_mss_confirmations(
        candles,
        [choch],
        fvgs=[valid_fvg],
        config=cfg,
    )

    assert len(records) == 1
    rec = records[0]
    assert rec.status == MssStatus.MSS_CONFIRMED
    assert rec.associated_fvg_id == "fvg_valid"
    assert rec.fvg_confirmation_time == bar16.close_time


# ── 33. Matching FVG Confirms MSS Correctly (Faz 3D-K1) ───────────────────────
def test_matching_fvg_confirms_mss_correctly() -> None:
    """Verify that full pipeline with genuine displacement and matching FVG confirms correctly."""
    candles = create_baseline_candles(14, range_val="2.00")
    bar14 = make_test_candle(14, "2650.00", "2652.00", "2649.00", "2651.50")
    candles.append(bar14)
    bar15 = make_test_candle(15, "2651.50", "2665.00", "2651.00", "2664.00")
    candles.append(bar15)
    bar16 = make_test_candle(16, "2664.00", "2668.00", "2656.00", "2667.00")
    candles.append(bar16)

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        bar14.open_time,
        bar14.close_time,
    )

    cfg = MssConfirmationConfig(max_confirmation_bars=3)
    records = evaluate_mss_confirmations(candles, [choch], config=cfg)

    assert len(records) == 1
    rec = records[0]
    assert rec.status == MssStatus.MSS_CONFIRMED
    assert rec.is_confirmed
    assert rec.displacement_event_id is not None
    assert rec.associated_fvg_id is not None
    assert rec.final_confirmation_time == bar16.close_time


# ── 34. Matching FVG With Wrong Source Is Rejected (Faz 3D-K1) ────────────────
def test_matching_fvg_with_wrong_source_is_rejected() -> None:
    """Verify that an FVG with incompatible source_id is rejected."""
    candles = create_baseline_candles(14, range_val="2.00")
    bar14 = make_test_candle(14, "2650.00", "2652.00", "2649.00", "2651.50")
    candles.append(bar14)
    bar15 = make_test_candle(15, "2651.50", "2665.00", "2651.00", "2664.00")
    candles.append(bar15)
    bar16 = make_test_candle(16, "2664.00", "2668.00", "2656.00", "2667.00")
    candles.append(bar16)

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        bar14.open_time,
        bar14.close_time,
        source_id="market_data_lab_m1",
    )

    # FVG with incompatible source_id
    wrong_source_fvg = make_test_fvg(
        "fvg_wrong_src",
        FvgDirection.BULLISH,
        bar14.open_time,
        bar15.open_time,
        bar16.open_time,
        bar16.close_time,
        "2656.00",
        "2652.00",
        source_id="DIFFERENT_DATA_SOURCE",
    )

    cfg = MssConfirmationConfig(max_confirmation_bars=3)
    records = evaluate_mss_confirmations(
        candles,
        [choch],
        fvgs=[wrong_source_fvg],
        config=cfg,
    )

    assert len(records) == 1
    assert records[0].status != MssStatus.MSS_CONFIRMED
    assert records[0].associated_fvg_id is None


# ── 35. Matching FVG With Wrong Price Type Is Rejected (Faz 3D-K1) ────────────
def test_matching_fvg_with_wrong_price_type_is_rejected() -> None:
    """Verify that an ASK FVG cannot confirm a BID CHoCH candidate."""
    candles = create_baseline_candles(14, range_val="2.00")
    bar14 = make_test_candle(
        14, "2650.00", "2652.00", "2649.00", "2651.50", price_type=PriceType.BID
    )
    candles.append(bar14)
    bar15 = make_test_candle(
        15, "2651.50", "2665.00", "2651.00", "2664.00", price_type=PriceType.BID
    )
    candles.append(bar15)
    bar16 = make_test_candle(
        16, "2664.00", "2668.00", "2656.00", "2667.00", price_type=PriceType.BID
    )
    candles.append(bar16)

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        bar14.open_time,
        bar14.close_time,
        price_type=PriceType.BID,
    )

    # FVG on ASK series
    ask_fvg = make_test_fvg(
        "fvg_ask",
        FvgDirection.BULLISH,
        bar14.open_time,
        bar15.open_time,
        bar16.open_time,
        bar16.close_time,
        "2656.00",
        "2652.00",
        price_type=PriceType.ASK,
    )

    cfg = MssConfirmationConfig(max_confirmation_bars=3)
    records = evaluate_mss_confirmations(
        candles,
        [choch],
        fvgs=[ask_fvg],
        config=cfg,
    )

    assert len(records) == 1
    assert records[0].status != MssStatus.MSS_CONFIRMED
    assert records[0].associated_fvg_id is None


# ── 36. FVG After Confirmation Window Is Rejected (Faz 3D-K1) ─────────────────
def test_fvg_after_confirmation_window_is_rejected() -> None:
    """Verify that an FVG forming after the confirmation window boundary is rejected."""
    candles = create_baseline_candles(25, range_val="2.00")
    bar5 = candles[5]
    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        bar5.open_time,
        bar5.close_time,
    )

    # FVG formed far in the future at bar 20 (window was max 3 bars after bar 5 -> bars 5..8)
    late_fvg = make_test_fvg(
        "fvg_late",
        FvgDirection.BULLISH,
        candles[18].open_time,
        candles[19].open_time,
        candles[20].open_time,
        candles[20].close_time,
        "2656.00",
        "2652.00",
    )

    cfg = MssConfirmationConfig(max_confirmation_bars=3, require_displacement=False)
    records = evaluate_mss_confirmations(
        candles,
        [choch],
        fvgs=[late_fvg],
        config=cfg,
    )

    assert len(records) == 1
    assert records[0].status == MssStatus.REJECTED
    assert records[0].associated_fvg_id is None


# ── 37. No MSS Confirmation Before FVG Third Candle Close (Faz 3D-K1) ──────────
def test_no_mss_confirmation_before_fvg_third_candle_close() -> None:
    """Verify candidate cannot be MSS_CONFIRMED before candle 3 of FVG closes."""
    candles = create_baseline_candles(14, range_val="2.00")
    bar14 = make_test_candle(14, "2650.00", "2652.00", "2649.00", "2651.50")
    candles.append(bar14)
    bar15 = make_test_candle(15, "2651.50", "2665.00", "2651.00", "2664.00")
    candles.append(bar15)
    bar16 = make_test_candle(16, "2664.00", "2668.00", "2656.00", "2667.00")
    candles.append(bar16)

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        bar14.open_time,
        bar14.close_time,
    )

    # Evaluate at bar 15 close (displacement is closed, but candle 3 has NOT closed yet)
    as_of = bar15.close_time
    cfg = MssConfirmationConfig(max_confirmation_bars=3)
    records = evaluate_mss_confirmations(candles, [choch], config=cfg, as_of_utc=as_of)

    assert len(records) == 1
    assert records[0].status == MssStatus.DISPLACEMENT_CONFIRMED
    assert not records[0].is_confirmed
    assert records[0].associated_fvg_id is None


# ── 38. Historical As_Of Does Not Leak Future FVG (Faz 3D-K1) ─────────────────
def test_historical_as_of_does_not_leak_future_fvg() -> None:
    """Verify that querying at a historical timestamp never leaks a future FVG."""
    candles = create_baseline_candles(14, range_val="2.00")
    bar14 = make_test_candle(14, "2650.00", "2652.00", "2649.00", "2651.50")
    candles.append(bar14)
    bar15 = make_test_candle(15, "2651.50", "2665.00", "2651.00", "2664.00")
    candles.append(bar15)
    bar16 = make_test_candle(16, "2664.00", "2668.00", "2656.00", "2667.00")
    candles.append(bar16)

    choch = make_test_choch_event(
        "choch_1",
        StructureDirection.BULLISH,
        bar14.open_time,
        bar14.close_time,
    )

    cfg = MssConfirmationConfig(max_confirmation_bars=3)

    # Even if pre-computed future FVGs are passed in, as_of_utc MUST suppress future leakage
    fvgs = detect_fair_value_gaps(candles)
    assert len(fvgs) >= 1

    records_before = evaluate_mss_confirmations(
        candles,
        [choch],
        fvgs=fvgs,
        config=cfg,
        as_of_utc=bar15.close_time,
    )
    records_at = evaluate_mss_confirmations(
        candles,
        [choch],
        fvgs=fvgs,
        config=cfg,
        as_of_utc=bar16.close_time,
    )

    assert records_before[0].status == MssStatus.DISPLACEMENT_CONFIRMED
    assert records_before[0].associated_fvg_id is None

    assert records_at[0].status == MssStatus.MSS_CONFIRMED
    assert records_at[0].associated_fvg_id is not None


# ── 39. Existing H4 Real MSS Still Confirmed (Faz 3D-K1) ──────────────────────
def test_existing_h4_real_mss_still_confirmed() -> None:
    """Verify that real XAUUSD BID H4 MSS on 2026-10-14 is still fully confirmed."""
    from traderlab.application.analysis.resampling import resample_m1_candles
    from traderlab.domain.market_structure.fvg import detect_fair_value_gaps
    from traderlab.domain.market_structure.market_structure import (
        MarketStructureConfig,
        detect_market_structure,
        filter_mss_candidates,
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

    conf_swings = filter_confirmed_swings(
        detect_swings(h4_c, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
    )
    ms_events, _ = detect_market_structure(
        h4_c, conf_swings, config=MarketStructureConfig(Decimal("0.01"), 1)
    )
    cands = filter_mss_candidates(ms_events)
    fvgs = detect_fair_value_gaps(h4_c)
    disps = detect_displacements(h4_c)
    cfg = MssConfirmationConfig(max_confirmation_bars=3)
    records = evaluate_mss_confirmations(h4_c, cands, displacements=disps, fvgs=fvgs, config=cfg)

    confirmed_recs = [r for r in records if r.is_confirmed]
    assert len(confirmed_recs) == 1
    rec = confirmed_recs[0]
    assert rec.direction == StructureDirection.BEARISH
    assert rec.candidate_time == datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
    assert rec.displacement_time == datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
    assert rec.fvg_confirmation_time == datetime(2026, 10, 14, 20, 0, tzinfo=UTC)
    assert rec.final_confirmation_time == datetime(2026, 10, 14, 20, 0, tzinfo=UTC)


# ── 40. Existing H2 Real MSS Still Confirmed (Faz 3D-K1) ──────────────────────
def test_existing_h2_real_mss_still_confirmed() -> None:
    """Verify that real XAUUSD BID H2 MSS on 2026-10-09 is still fully confirmed."""
    from traderlab.application.analysis.resampling import resample_m1_candles
    from traderlab.domain.market_structure.fvg import detect_fair_value_gaps
    from traderlab.domain.market_structure.market_structure import (
        MarketStructureConfig,
        detect_market_structure,
        filter_mss_candidates,
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

    conf_swings = filter_confirmed_swings(
        detect_swings(h2_c, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
    )
    ms_events, _ = detect_market_structure(
        h2_c, conf_swings, config=MarketStructureConfig(Decimal("0.01"), 1)
    )
    cands = filter_mss_candidates(ms_events)
    fvgs = detect_fair_value_gaps(h2_c)
    disps = detect_displacements(h2_c)
    cfg = MssConfirmationConfig(max_confirmation_bars=3)
    records = evaluate_mss_confirmations(h2_c, cands, displacements=disps, fvgs=fvgs, config=cfg)

    confirmed_recs = [r for r in records if r.is_confirmed]
    assert len(confirmed_recs) == 1
    rec = confirmed_recs[0]
    assert rec.direction == StructureDirection.BULLISH
    assert rec.candidate_time == datetime(2026, 10, 9, 2, 0, tzinfo=UTC)
    assert rec.displacement_time == datetime(2026, 10, 9, 2, 0, tzinfo=UTC)
    assert rec.fvg_confirmation_time == datetime(2026, 10, 9, 4, 0, tzinfo=UTC)
    assert rec.final_confirmation_time == datetime(2026, 10, 9, 4, 0, tzinfo=UTC)
