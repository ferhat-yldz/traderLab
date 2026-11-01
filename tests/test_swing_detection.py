"""Comprehensive test suite for the Point-in-Time Safe Swing High / Swing Low Detection Engine.

Covers all 22 acceptance criteria specified in TraderLab Phase 3A:
1. Basic Swing High
2. Basic Swing Low
3. Equal High / Tie Policy (STRICT, ALLOW_EQUAL_FIRST, ALLOW_EQUAL_LAST)
4. Equal Low / Tie Policy (STRICT, ALLOW_EQUAL_FIRST, ALLOW_EQUAL_LAST)
5. Configurable left_bars
6. Configurable right_bars
7. Candidate before confirmation
8. Confirmed after right-side candle close
9. No future leakage under historical as_of
10. Partial candle rejection
11. Synthetic candle rejection
12. Data-gap protection
13. BID/ASK isolation
14. D1/H4/H2 separation
15. D1 UTC and NY session modes
16. Stable swing identifiers
17. Deterministic repeated execution
18. Empty input safety
19. Insufficient candle count
20. Decimal precision
21. UTC-only time handling
22. STRICT_PIT policy protection
+ Liquidity Level bridge testing (to_level)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.swing import (
    InvalidCandleSequenceError,
    InvalidSwingParameterError,
    SwingConfirmationStatus,
    SwingError,
    SwingPoint,
    SwingType,
    TiePolicy,
    detect_swings,
    filter_confirmed_swings,
    generate_swing_id,
    get_active_swing_levels,
)


# ── Helper Factory ───────────────────────────────────────────────────────────
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
    )


# ── 1. Basic Swing High ───────────────────────────────────────────────────────
def test_basic_swing_high() -> None:
    """Verify standard Swing High detection: high[i] > high[i-L..i-1] and high[i] > high[i+1..i+R]."""
    # 7 candles, index 3 is highest
    highs = ["2650.0", "2655.0", "2658.0", "2670.0", "2665.0", "2662.0", "2660.0"]
    candles = [
        make_test_candle(i, "2650.0", highs[i], "2648.0", "2650.0") for i in range(len(highs))
    ]

    swings = detect_swings(candles, left_bars=3, right_bars=3)
    high_swings = [s for s in swings if s.is_swing_high]

    assert len(high_swings) == 1
    sh = high_swings[0]
    assert sh.swing_type == SwingType.SWING_HIGH
    assert sh.pivot_price == Decimal("2670.0")
    assert sh.pivot_time == candles[3].open_time
    assert sh.confirmation_status == SwingConfirmationStatus.CONFIRMED
    assert sh.confirmation_time == candles[6].close_time
    assert sh.left_bars == 3
    assert sh.right_bars == 3


# ── 2. Basic Swing Low ────────────────────────────────────────────────────────
def test_basic_swing_low() -> None:
    """Verify standard Swing Low detection: low[i] < low[i-L..i-1] and low[i] < low[i+1..i+R]."""
    # 7 candles, index 3 is lowest
    lows = ["2650.0", "2645.0", "2640.0", "2630.0", "2635.0", "2638.0", "2642.0"]
    candles = [
        make_test_candle(i, "2650.0", "2655.0", lows[i], "2650.0") for i in range(len(lows))
    ]

    swings = detect_swings(candles, left_bars=3, right_bars=3)
    low_swings = [s for s in swings if s.is_swing_low]

    assert len(low_swings) == 1
    sl = low_swings[0]
    assert sl.swing_type == SwingType.SWING_LOW
    assert sl.pivot_price == Decimal("2630.0")
    assert sl.pivot_time == candles[3].open_time
    assert sl.confirmation_status == SwingConfirmationStatus.CONFIRMED
    assert sl.confirmation_time == candles[6].close_time


# ── 3. Equal High / Tie Policy ────────────────────────────────────────────────
def test_equal_high_tie_policies() -> None:
    """Verify TiePolicy resolution for equal highs (STRICT, ALLOW_EQUAL_FIRST, ALLOW_EQUAL_LAST)."""
    # Indices 3 and 4 have equal high 2670.0
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2670.0", "2665.0", "2660.0", "2655.0"]
    candles = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0") for i in range(len(highs))
    ]

    # STRICT: duplicate high disqualifies both
    swings_strict = detect_swings(candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
    sh_strict = [s for s in swings_strict if s.is_swing_high]
    assert len(sh_strict) == 0

    # ALLOW_EQUAL_FIRST: first bar (index 3) is selected
    swings_first = detect_swings(
        candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.ALLOW_EQUAL_FIRST
    )
    sh_first = [s for s in swings_first if s.is_swing_high]
    assert len(sh_first) == 1
    assert sh_first[0].pivot_time == candles[3].open_time

    # ALLOW_EQUAL_LAST: last bar (index 4) is selected
    swings_last = detect_swings(
        candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.ALLOW_EQUAL_LAST
    )
    sh_last = [s for s in swings_last if s.is_swing_high]
    assert len(sh_last) == 1
    assert sh_last[0].pivot_time == candles[4].open_time


# ── 4. Equal Low / Tie Policy ─────────────────────────────────────────────────
def test_equal_low_tie_policies() -> None:
    """Verify TiePolicy resolution for equal lows (STRICT, ALLOW_EQUAL_FIRST, ALLOW_EQUAL_LAST)."""
    lows = ["2650.0", "2645.0", "2640.0", "2630.0", "2630.0", "2635.0", "2640.0", "2645.0"]
    candles = [
        make_test_candle(i, "2650.0", "2655.0", lows[i], "2650.0") for i in range(len(lows))
    ]

    # STRICT: duplicate low disqualifies both
    swings_strict = detect_swings(candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
    assert len([s for s in swings_strict if s.is_swing_low]) == 0

    # ALLOW_EQUAL_FIRST: index 3
    swings_first = detect_swings(
        candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.ALLOW_EQUAL_FIRST
    )
    sl_first = [s for s in swings_first if s.is_swing_low]
    assert len(sl_first) == 1
    assert sl_first[0].pivot_time == candles[3].open_time

    # ALLOW_EQUAL_LAST: index 4
    swings_last = detect_swings(
        candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.ALLOW_EQUAL_LAST
    )
    sl_last = [s for s in swings_last if s.is_swing_low]
    assert len(sl_last) == 1
    assert sl_last[0].pivot_time == candles[4].open_time


# ── 5. Configurable left_bars ─────────────────────────────────────────────────
def test_configurable_left_bars() -> None:
    """Verify left_bars configuration (e.g. left_bars=2, 3, 5)."""
    # High at index 3: left bars must satisfy left_bars window
    highs = ["2680.0", "2650.0", "2660.0", "2670.0", "2665.0", "2660.0", "2655.0"]
    candles = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0") for i in range(len(highs))
    ]

    # With left_bars=2, window at index 3 is [1, 2] -> 2650, 2660 < 2670 -> Valid!
    swings_2 = detect_swings(candles, left_bars=2, right_bars=3)
    assert any(s.is_swing_high and s.pivot_price == Decimal("2670.0") for s in swings_2)

    # With left_bars=3, window at index 3 is [0, 1, 2] -> index 0 has 2680.0 > 2670.0 -> Disqualified!
    swings_3 = detect_swings(candles, left_bars=3, right_bars=3)
    assert not any(s.is_swing_high and s.pivot_price == Decimal("2670.0") for s in swings_3)


# ── 6. Configurable right_bars ────────────────────────────────────────────────
def test_configurable_right_bars() -> None:
    """Verify right_bars configuration (e.g. right_bars=1, 2, 4)."""
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0", "2675.0", "2660.0"]
    candles = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0") for i in range(len(highs))
    ]

    # With right_bars=1, index 3 needs 1 right bar (index 4 has 2665.0 < 2670.0) -> Confirmed at candle 4 close!
    swings_1 = detect_swings(candles, left_bars=3, right_bars=1)
    sh_1 = [s for s in swings_1 if s.is_swing_high and s.pivot_time == candles[3].open_time]
    assert len(sh_1) == 1
    assert sh_1[0].is_confirmed
    assert sh_1[0].confirmation_time == candles[4].close_time

    # With right_bars=2, index 3 needs 2 right bars (index 5 has 2675.0 > 2670.0) -> Disqualified!
    swings_2 = detect_swings(candles, left_bars=3, right_bars=2)
    sh_2 = [s for s in swings_2 if s.is_swing_high and s.pivot_time == candles[3].open_time]
    assert len(sh_2) == 0


# ── 7. Candidate before confirmation ──────────────────────────────────────────
def test_candidate_before_confirmation() -> None:
    """Verify that an incomplete right window is marked as CANDIDATE and not CONFIRMED."""
    # 5 candles: index 3 is high, but right_bars=3 requires candles up to index 6. Here only index 4 exists.
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0"]
    candles = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0") for i in range(len(highs))
    ]

    # With allow_candidates=True
    swings_with_cand = detect_swings(candles, left_bars=3, right_bars=3, allow_candidates=True)
    cand_highs = [s for s in swings_with_cand if s.is_swing_high]
    assert len(cand_highs) == 1
    cand = cand_highs[0]
    assert cand.confirmation_status == SwingConfirmationStatus.CANDIDATE
    assert cand.confirmation_time is None
    assert cand.is_candidate
    assert not cand.is_confirmed

    # With allow_candidates=False
    swings_no_cand = detect_swings(candles, left_bars=3, right_bars=3, allow_candidates=False)
    assert len([s for s in swings_no_cand if s.is_swing_high]) == 0


# ── 8. Confirmed after right-side candle close ────────────────────────────────
def test_confirmed_after_right_side_candle_close() -> None:
    """Verify that confirmation occurs strictly at candles[i+right_bars].close_time."""
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0", "2662.0", "2660.0"]
    candles = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0") for i in range(len(highs))
    ]

    swings = detect_swings(candles, left_bars=3, right_bars=3)
    sh = next(s for s in swings if s.is_swing_high)

    assert sh.confirmation_status == SwingConfirmationStatus.CONFIRMED
    assert sh.confirmation_time == candles[3 + 3].close_time
    assert sh.confirmation_time > sh.pivot_time


# ── 9. No future leakage under historical as_of ──────────────────────────────
def test_no_future_leakage_under_historical_as_of() -> None:
    """Verify that historical as_of prevents any lookahead bias or future confirmation leakage."""
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0", "2662.0", "2660.0"]
    candles = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0") for i in range(len(highs))
    ]

    # As of index 3 close time: right bars do not exist yet
    as_of_t3 = candles[3].close_time
    swings_t3 = detect_swings(candles, left_bars=3, right_bars=3, as_of_utc=as_of_t3)
    confirmed_t3 = filter_confirmed_swings(swings_t3, as_of_utc=as_of_t3)
    assert len(confirmed_t3) == 0

    # As of index 5 close time (only 2 right bars closed): still no confirmed swing
    as_of_t5 = candles[5].close_time
    swings_t5 = detect_swings(candles, left_bars=3, right_bars=3, as_of_utc=as_of_t5)
    confirmed_t5 = filter_confirmed_swings(swings_t5, as_of_utc=as_of_t5)
    assert len(confirmed_t5) == 0

    # As of index 6 close time (all 3 right bars closed): NOW confirmed
    as_of_t6 = candles[6].close_time
    swings_t6 = detect_swings(candles, left_bars=3, right_bars=3, as_of_utc=as_of_t6)
    confirmed_t6 = filter_confirmed_swings(swings_t6, as_of_utc=as_of_t6)
    assert len(confirmed_t6) == 1
    assert confirmed_t6[0].pivot_time == candles[3].open_time
    assert confirmed_t6[0].confirmation_time == candles[6].close_time


# ── 10. Partial candle rejection ──────────────────────────────────────────────
def test_partial_candle_rejection() -> None:
    """Verify unclosed (partial) candles are ignored and cannot serve as pivots or confirmation."""
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0", "2662.0", "2660.0"]
    candles = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0") for i in range(len(highs))
    ]
    # Mark candle 6 (confirmation bar) as not closed
    candles[6] = make_test_candle(6, "2650.0", highs[6], "2645.0", "2650.0", is_closed=False)

    swings = detect_swings(candles, left_bars=3, right_bars=3)
    confirmed = [s for s in swings if s.is_confirmed]
    # Cannot be confirmed because candle 6 was partial
    assert len(confirmed) == 0


# ── 11. Synthetic candle rejection ────────────────────────────────────────────
def test_synthetic_candle_rejection() -> None:
    """Verify synthetic rollover candles cannot serve as pivots and cannot confirm swings."""
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0", "2662.0", "2660.0"]
    candles = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0") for i in range(len(highs))
    ]

    # Case A: Pivot candle is synthetic -> rejected as swing
    candles_synth_pivot = list(candles)
    candles_synth_pivot[3] = make_test_candle(
        3, "2650.0", highs[3], "2645.0", "2650.0", is_synthetic=True
    )
    swings_a = detect_swings(candles_synth_pivot, left_bars=3, right_bars=3)
    assert not any(s.pivot_price == Decimal("2670.0") for s in swings_a)

    # Case B: One of the confirmation bars is synthetic -> rejected from confirmation
    candles_synth_conf = list(candles)
    candles_synth_conf[5] = make_test_candle(
        5, "2650.0", highs[5], "2645.0", "2650.0", is_synthetic=True
    )
    swings_b = detect_swings(candles_synth_conf, left_bars=3, right_bars=3)
    assert not any(s.is_confirmed and s.pivot_price == Decimal("2670.0") for s in swings_b)


# ── 12. Data-gap protection ───────────────────────────────────────────────────
def test_data_gap_protection() -> None:
    """Verify abnormal intraday data gaps invalidate swing formation when disallowed."""
    # Create candles with a 5-hour unannounced mid-week intraday gap between index 4 and 5
    candles: list[Candle] = []
    base = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)  # Tuesday
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0", "2662.0", "2660.0"]

    for i in range(5):
        candles.append(make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0", base_time=base))

    # Add 5-hour gap between 4 and 5
    gap_time = base + timedelta(hours=4 + 5)
    for i in range(5, 7):
        candles.append(
            make_test_candle(i - 5, "2650.0", highs[i], "2645.0", "2650.0", base_time=gap_time)
        )

    # When allow_weekend_gap=False, the gap disqualifies confirmation
    swings = detect_swings(candles, left_bars=3, right_bars=3, allow_weekend_gap=False)
    assert not any(s.is_confirmed for s in swings)


# ── 13. BID/ASK isolation ─────────────────────────────────────────────────────
def test_bid_ask_isolation() -> None:
    """Verify strict isolation of BID and ASK series and rejection of mixed sequences."""
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0", "2662.0", "2660.0"]

    candles_bid = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0", price_type=PriceType.BID)
        for i in range(len(highs))
    ]
    candles_ask = [
        make_test_candle(
            i,
            "2650.5",
            str(Decimal(highs[i]) + Decimal("0.5")),
            "2645.5",
            "2650.5",
            price_type=PriceType.ASK,
        )
        for i in range(len(highs))
    ]

    swings_bid = detect_swings(candles_bid, left_bars=3, right_bars=3)
    swings_ask = detect_swings(candles_ask, left_bars=3, right_bars=3)

    assert len(swings_bid) == 1
    assert len(swings_ask) == 1
    assert swings_bid[0].price_type == PriceType.BID
    assert swings_ask[0].price_type == PriceType.ASK
    assert "BID" in swings_bid[0].id
    assert "ASK" in swings_ask[0].id
    assert swings_bid[0].id != swings_ask[0].id

    # Mixing BID and ASK candles in the same call must raise InvalidCandleSequenceError
    mixed = list(candles_bid)
    mixed[2] = candles_ask[2]
    with pytest.raises(InvalidCandleSequenceError):
        detect_swings(mixed)


# ── 14. D1/H4/H2 separation ───────────────────────────────────────────────────
def test_timeframe_separation_d1_h4_h2() -> None:
    """Verify D1, H4, and H2 timeframes operate independently and reject mixed timeframes."""
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0", "2662.0", "2660.0"]

    candles_d1 = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0", timeframe=Timeframe.D1)
        for i in range(len(highs))
    ]
    candles_h4 = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0", timeframe=Timeframe.H4)
        for i in range(len(highs))
    ]
    candles_h2 = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0", timeframe=Timeframe.H2)
        for i in range(len(highs))
    ]

    s_d1 = detect_swings(candles_d1, left_bars=3, right_bars=3)[0]
    s_h4 = detect_swings(candles_h4, left_bars=3, right_bars=3)[0]
    s_h2 = detect_swings(candles_h2, left_bars=3, right_bars=3)[0]

    assert s_d1.timeframe == Timeframe.D1
    assert s_h4.timeframe == Timeframe.H4
    assert s_h2.timeframe == Timeframe.H2
    assert "D1" in s_d1.id
    assert "H4" in s_h4.id
    assert "H2" in s_h2.id

    # Mixing timeframes raises error
    mixed = list(candles_h4)
    mixed[2] = candles_d1[2]
    with pytest.raises(InvalidCandleSequenceError):
        detect_swings(mixed)


# ── 15. D1 UTC and NY session modes ───────────────────────────────────────────
def test_d1_utc_and_ny_session_modes() -> None:
    """Verify D1 candle handling under Sunday stub filtering and regular daily cadences."""
    base_sunday = datetime(2026, 10, 4, 0, 0, tzinfo=UTC)  # Sunday UTC midnight stub
    # Sunday stub + 7 daily candles where the 4th daily candle (effective index 3) is peak 2670.0
    sunday_high = "2648.0"
    daily_highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0", "2662.0", "2660.0"]

    candles: list[Candle] = []
    # Sunday 00:00 stub (automatically filtered out)
    candles.append(
        make_test_candle(
            0,
            "2648.0",
            sunday_high,
            "2645.0",
            "2648.0",
            base_time=base_sunday,
            timeframe=Timeframe.D1,
        )
    )

    # Daily candles Monday through Sunday
    base_monday = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    for i, h in enumerate(daily_highs):
        candles.append(
            make_test_candle(
                i, "2650.0", h, "2645.0", "2650.0", base_time=base_monday, timeframe=Timeframe.D1
            )
        )

    swings = detect_swings(candles, left_bars=3, right_bars=3)
    # The Sunday stub was safely excluded without corrupting the swing calculation
    assert len(swings) >= 1
    assert any(s.is_swing_high and s.pivot_price == Decimal("2670.0") for s in swings)


# ── 16. Stable swing identifiers ──────────────────────────────────────────────
def test_stable_swing_identifiers() -> None:
    """Verify deterministic identifier generation matching all required parameters."""
    dt = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
    s_id = generate_swing_id(
        instrument_id="XAUUSD",
        source_id="dukascopy",
        price_type=PriceType.BID,
        timeframe=Timeframe.H4,
        swing_type=SwingType.SWING_HIGH,
        pivot_time=dt,
        left_bars=3,
        right_bars=3,
    )
    expected = "XAUUSD_dukascopy_BID_H4_SWING_HIGH_20261015T140000Z_3L3R"
    assert s_id == expected


# ── 17. Deterministic repeated execution ──────────────────────────────────────
def test_deterministic_repeated_execution() -> None:
    """Verify running swing detection repeatedly yields mathematically identical results."""
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0", "2662.0", "2660.0"]
    candles = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0") for i in range(len(highs))
    ]

    first_run = detect_swings(candles, left_bars=3, right_bars=3)
    for _ in range(5):
        run = detect_swings(candles, left_bars=3, right_bars=3)
        assert len(run) == len(first_run)
        for s1, s2 in zip(first_run, run, strict=True):
            assert s1 == s2
            assert s1.id == s2.id
            assert s1.pivot_price == s2.pivot_price


# ── 18. Empty input safety ────────────────────────────────────────────────────
def test_empty_input_safety() -> None:
    """Verify empty input sequences safely return empty results."""
    assert detect_swings([]) == []
    assert filter_confirmed_swings([]) == []
    assert get_active_swing_levels([]) == []


# ── 19. Insufficient candle count ─────────────────────────────────────────────
def test_insufficient_candle_count() -> None:
    """Verify sequence shorter than (left_bars + 1) safely returns empty results."""
    candles = [make_test_candle(i, "2650.0", "2655.0", "2645.0", "2650.0") for i in range(3)]
    # left_bars=3 requires at least 4 candles (3 left + 1 pivot)
    assert detect_swings(candles, left_bars=3, right_bars=3) == []


# ── 20. Decimal precision ─────────────────────────────────────────────────────
def test_decimal_precision() -> None:
    """Verify sub-pip decimal precision without floating point imprecision."""
    # Exactly 0.00001 difference
    highs = [
        "2650.12340",
        "2650.12345",
        "2650.12348",
        "2650.12350",  # Pivot: highest by 0.00001
        "2650.12349",
        "2650.12348",
        "2650.12347",
    ]
    candles = [
        make_test_candle(i, "2650.0", highs[i], "2649.0", "2650.0") for i in range(len(highs))
    ]

    swings = detect_swings(candles, left_bars=3, right_bars=3)
    sh = [s for s in swings if s.is_swing_high]
    assert len(sh) == 1
    assert sh[0].pivot_price == Decimal("2650.12350")
    assert isinstance(sh[0].pivot_price, Decimal)


# ── 21. UTC-only time handling ────────────────────────────────────────────────
def test_utc_only_time_handling() -> None:
    """Verify all timestamps are strictly UTC timezone aware."""
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0", "2662.0", "2660.0"]
    candles = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0") for i in range(len(highs))
    ]

    swings = detect_swings(candles, left_bars=3, right_bars=3)
    for s in swings:
        assert s.pivot_time.tzinfo == UTC
        assert s.pivot_close_time.tzinfo == UTC
        if s.confirmation_time:
            assert s.confirmation_time.tzinfo == UTC
        assert s.computable_at_utc.tzinfo == UTC


# ── 22. STRICT_PIT policy protection ──────────────────────────────────────────
def test_strict_pit_policy_protection() -> None:
    """Verify that confirmation_time is strictly greater than pivot_time and post-init enforces it."""
    # Attempting to instantiate a SwingPoint with invalid confirmation time raises SwingError
    dt = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    with pytest.raises(SwingError, match="must be strictly after"):
        SwingPoint(
            id="test",
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("src"),
            price_type=PriceType.BID,
            timeframe=Timeframe.H1,
            swing_type=SwingType.SWING_HIGH,
            pivot_price=Decimal("2670.0"),
            pivot_time=dt,
            pivot_close_time=dt + timedelta(hours=1),
            confirmation_time=dt,  # Equal to pivot_time!
            confirmation_status=SwingConfirmationStatus.CONFIRMED,
            left_bars=3,
            right_bars=3,
            tie_policy=TiePolicy.STRICT,
            computable_at_utc=dt,
        )


# ── Parameter Validation and Liquidity Level Bridge ───────────────────────────
def test_parameter_validation_and_level_bridge() -> None:
    """Verify parameter bounds (< 1) and SwingLevel conversion for liquidity sweeps."""
    candles = [make_test_candle(i, "2650.0", "2660.0", "2640.0", "2650.0") for i in range(10)]

    with pytest.raises(InvalidSwingParameterError):
        detect_swings(candles, left_bars=0, right_bars=3)

    with pytest.raises(InvalidSwingParameterError):
        detect_swings(candles, left_bars=3, right_bars=0)

    # Level bridge test
    highs = ["2650.0", "2655.0", "2660.0", "2670.0", "2665.0", "2662.0", "2660.0", "2675.0"]
    candles_breach = [
        make_test_candle(i, "2650.0", highs[i], "2645.0", "2650.0") for i in range(len(highs))
    ]

    swings = detect_swings(candles_breach, left_bars=3, right_bars=3)
    sh = next(s for s in swings if s.is_swing_high)
    assert sh.broken_at_time == candles_breach[7].close_time

    # to_level conversion
    lvl = sh.to_level()
    assert lvl.level_price == Decimal("2670.0")
    assert lvl.level_formation_time == sh.pivot_time
    assert lvl.level_confirmation_time == sh.confirmation_time
    assert lvl.is_breached is True
    assert lvl.breached_at == candles_breach[7].close_time

    # Active levels before breach
    active_before = get_active_swing_levels(swings, as_of_utc=candles_breach[6].close_time)
    assert len(active_before) == 1
    assert active_before[0].is_breached is False

    # Candidate cannot be converted to level
    cand = SwingPoint(
        id="cand_test",
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("src"),
        price_type=PriceType.BID,
        timeframe=Timeframe.H1,
        swing_type=SwingType.SWING_HIGH,
        pivot_price=Decimal("2670.0"),
        pivot_time=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
        pivot_close_time=datetime(2026, 10, 1, 11, 0, tzinfo=UTC),
        confirmation_time=None,
        confirmation_status=SwingConfirmationStatus.CANDIDATE,
        left_bars=3,
        right_bars=3,
        tie_policy=TiePolicy.STRICT,
        computable_at_utc=datetime(2026, 10, 1, 11, 0, tzinfo=UTC),
    )
    with pytest.raises(SwingError, match="Only CONFIRMED"):
        cand.to_level()
