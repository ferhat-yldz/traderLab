"""Comprehensive unit and integration test suite for the deterministic FVG calculation engine.

Tests all 14 Phase 2A acceptance criteria, edge cases, session gaps, and PIT safeguards.
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
from traderlab.domain.market_structure.fvg import (
    FvgDirection,
    FvgFillState,
    FvgValidityState,
    FvgZone,
    InvalidCandleSequenceError,
    detect_fair_value_gaps,
    generate_fvg_id,
    track_fvg_lifecycle,
)


# ── Helper Factory ───────────────────────────────────────────────────────────
def make_candle(
    open_time: datetime,
    open_p: str,
    high_p: str,
    low_p: str,
    close_p: str,
    *,
    instrument_id: str = "XAUUSD",
    source_id: str = "market_data_lab_m1",
    timeframe: Timeframe = Timeframe.H1,
    price_type: PriceType = PriceType.BID,
    is_closed: bool = True,
    is_synthetic: bool = False,
    revision: int = 0,
) -> Candle:
    """Create a valid domain Candle for deterministic FVG testing."""
    o_dt = open_time.replace(tzinfo=UTC) if open_time.tzinfo is None else open_time.astimezone(UTC)
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
        volume=Decimal("1000"),
        price_type=price_type,
        is_closed=is_closed,
        is_synthetic=is_synthetic,
        revision=revision,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1. Core Detection Tests (T01 - T05)
# ═════════════════════════════════════════════════════════════════════════════
class TestFvgDetectionCore:
    """Verify basic mathematical imbalance formulas, boundaries, and CE."""

    def test_t01_bullish_fvg_detection_basic(self) -> None:
        """T01: candle_3.low > candle_1.high produces BULLISH FVG [candle_1.high, candle_3.low]."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(base, "2600.00", "2610.00", "2598.00", "2608.00")
        c2 = make_candle(base + timedelta(hours=1), "2608.00", "2635.00", "2607.00", "2632.00")
        c3 = make_candle(base + timedelta(hours=2), "2632.00", "2645.00", "2620.00", "2642.00")

        # c3.low (2620.00) > c1.high (2610.00) -> Gap = [2610.00, 2620.00]
        fvgs = detect_fair_value_gaps([c1, c2, c3])
        assert len(fvgs) == 1
        fvg = fvgs[0]

        assert fvg.is_bullish is True
        assert fvg.is_bearish is False
        assert fvg.direction == FvgDirection.BULLISH
        assert fvg.bottom_price == Decimal("2610.00")
        assert fvg.top_price == Decimal("2620.00")
        assert fvg.width == Decimal("10.00")
        assert fvg.ce_price == Decimal("2615.00")
        assert fvg.formation_time == c3.close_time
        assert fvg.computable_at_utc == c3.close_time
        assert fvg.candle_1_open == c1.open_time
        assert fvg.candle_2_open == c2.open_time
        assert fvg.candle_3_open == c3.open_time

    def test_t02_bearish_fvg_detection_basic(self) -> None:
        """T02: candle_3.high < candle_1.low produces BEARISH FVG [candle_3.high, candle_1.low]."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(base, "2650.00", "2652.00", "2640.00", "2642.00")
        c2 = make_candle(base + timedelta(hours=1), "2642.00", "2643.00", "2615.00", "2618.00")
        c3 = make_candle(base + timedelta(hours=2), "2618.00", "2630.00", "2605.00", "2608.00")

        # c3.high (2630.00) < c1.low (2640.00) -> Gap = [2630.00, 2640.00]
        fvgs = detect_fair_value_gaps([c1, c2, c3])
        assert len(fvgs) == 1
        fvg = fvgs[0]

        assert fvg.is_bearish is True
        assert fvg.is_bullish is False
        assert fvg.direction == FvgDirection.BEARISH
        assert fvg.bottom_price == Decimal("2630.00")
        assert fvg.top_price == Decimal("2640.00")
        assert fvg.width == Decimal("10.00")
        assert fvg.ce_price == Decimal("2635.00")
        assert fvg.formation_time == c3.close_time

    def test_t03_overlapping_candles_no_fvg(self) -> None:
        """T03: Overlapping wicks produce 0 FVG imbalances."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(base, "2600.00", "2620.00", "2598.00", "2615.00")
        c2 = make_candle(base + timedelta(hours=1), "2615.00", "2630.00", "2610.00", "2625.00")
        c3 = make_candle(base + timedelta(hours=2), "2625.00", "2635.00", "2618.00", "2630.00")

        # c3.low (2618.00) < c1.high (2620.00) -> Overlap, no gap
        assert len(detect_fair_value_gaps([c1, c2, c3])) == 0

    def test_t04_unclosed_candle_rejection(self) -> None:
        """T04: An incomplete/forming bar (is_closed=False) cannot finalize an FVG."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(base, "2600.00", "2610.00", "2598.00", "2608.00")
        c2 = make_candle(base + timedelta(hours=1), "2608.00", "2635.00", "2607.00", "2632.00")
        c3_live = make_candle(
            base + timedelta(hours=2), "2632.00", "2645.00", "2620.00", "2642.00", is_closed=False
        )

        fvgs = detect_fair_value_gaps([c1, c2, c3_live], require_closed=True)
        assert len(fvgs) == 0

    def test_t05_ce_exact_decimal_precision(self) -> None:
        """T05: CE midpoint is evaluated with exact Decimal precision without float truncation."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        # 2618.125 and 2620.445 -> Sum = 5238.570 -> Mid = 2619.285
        c1 = make_candle(base, "2610.000", "2618.125", "2608.000", "2615.000")
        c2 = make_candle(base + timedelta(hours=1), "2615.000", "2635.000", "2614.000", "2630.000")
        c3 = make_candle(base + timedelta(hours=2), "2630.000", "2640.000", "2620.445", "2638.000")

        fvgs = detect_fair_value_gaps([c1, c2, c3])
        assert len(fvgs) == 1
        fvg = fvgs[0]
        assert fvg.bottom_price == Decimal("2618.125")
        assert fvg.top_price == Decimal("2620.445")
        assert fvg.width == Decimal("2.320")
        assert fvg.ce_price == Decimal("2619.285")


# ═════════════════════════════════════════════════════════════════════════════
# 2. Lifecycle Tracking & Point-in-Time Tests (T06 - T10)
# ═════════════════════════════════════════════════════════════════════════════
class TestFvgLifecycleTracking:
    """Verify mitigation, partial fill, CE hit, full fill, invalidation, and IFVG."""

    @pytest.fixture
    def sample_bullish_fvg(self) -> tuple[FvgZone, datetime]:
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(base, "2600.00", "2610.00", "2598.00", "2608.00")
        c2 = make_candle(base + timedelta(hours=1), "2608.00", "2635.00", "2607.00", "2632.00")
        c3 = make_candle(base + timedelta(hours=2), "2632.00", "2645.00", "2620.00", "2642.00")
        # Zone: [2610.00, 2620.00], CE = 2615.00
        fvg = detect_fair_value_gaps([c1, c2, c3])[0]
        return fvg, base + timedelta(hours=3)

    def test_t06_lifecycle_wick_touch_partial_fill(
        self, sample_bullish_fvg: tuple[FvgZone, datetime]
    ) -> None:
        """T06: Wick dips into upper half of zone -> PARTIALLY_FILLED, zone remains VALID."""
        fvg, next_open = sample_bullish_fvg
        # Candle dips to 2617.00 (penetration = 3.00 / 10.00 = 0.3000), closes above zone at 2625.00
        c4 = make_candle(next_open, "2642.00", "2645.00", "2617.00", "2625.00")

        events = track_fvg_lifecycle(fvg, [c4])
        assert len(events) == 1
        ev = events[0]

        assert ev.fill_state == FvgFillState.PARTIALLY_FILLED
        assert ev.validity_state == FvgValidityState.VALID
        assert ev.fill_ratio == Decimal("0.30")
        assert ev.extreme_penetration_price == Decimal("2617.00")
        assert ev.mitigated_by_candle_open == c4.open_time
        assert ev.invalidated_by_candle_open is None

    def test_t07_lifecycle_ce_reached(self, sample_bullish_fvg: tuple[FvgZone, datetime]) -> None:
        """T07: Wick reaches or crosses 50% CE level -> CE_REACHED."""
        fvg, next_open = sample_bullish_fvg
        # Candle dips to 2614.00 (below CE 2615.00, above bottom 2610.00)
        c4 = make_candle(next_open, "2642.00", "2645.00", "2614.00", "2622.00")

        events = track_fvg_lifecycle(fvg, [c4])
        assert len(events) == 1
        ev = events[0]

        assert ev.fill_state == FvgFillState.CE_REACHED
        assert ev.validity_state == FvgValidityState.VALID
        assert ev.fill_ratio == Decimal("0.60")

    def test_t08_lifecycle_wick_through_remains_valid(
        self, sample_bullish_fvg: tuple[FvgZone, datetime]
    ) -> None:
        """T08: Wick completely pierces through bottom boundary, but closes above bottom -> FULLY_FILLED & VALID."""
        fvg, next_open = sample_bullish_fvg
        # Wick dips to 2608.00 (below bottom 2610.00), but closes inside at 2612.00
        c4 = make_candle(next_open, "2642.00", "2645.00", "2608.00", "2612.00")

        events = track_fvg_lifecycle(fvg, [c4], invalidation_by_close=True)
        assert len(events) == 1
        ev = events[0]

        assert ev.fill_state == FvgFillState.FULLY_FILLED
        assert ev.validity_state == FvgValidityState.VALID
        assert ev.fill_ratio == Decimal("1.00")
        assert ev.is_valid is True
        assert ev.is_invalidated is False

    def test_t09_lifecycle_body_close_invalidation(
        self, sample_bullish_fvg: tuple[FvgZone, datetime]
    ) -> None:
        """T09: Bar close below bottom boundary -> INVALIDATED."""
        fvg, next_open = sample_bullish_fvg
        # Candle closes at 2605.00 (below bottom 2610.00)
        c4 = make_candle(next_open, "2642.00", "2643.00", "2603.00", "2605.00")

        events = track_fvg_lifecycle(fvg, [c4], invalidation_by_close=True)
        assert len(events) == 1
        ev = events[0]

        assert ev.validity_state == FvgValidityState.INVALIDATED
        assert ev.is_invalidated is True
        assert ev.invalidated_by_candle_open == c4.open_time

    def test_t10_lifecycle_ifvg_transition_on_retest(
        self, sample_bullish_fvg: tuple[FvgZone, datetime]
    ) -> None:
        """T10: Invalidation followed by retest from below -> Transitions to INVERTED (IFVG)."""
        fvg, next_open = sample_bullish_fvg
        # Bar 4 closes below bottom -> Invalidated
        c4 = make_candle(next_open, "2642.00", "2643.00", "2603.00", "2605.00")
        # Bar 5 retests into former support [2610, 2620] and closes below top (at 2614) -> Confirms IFVG
        c5 = make_candle(
            next_open + timedelta(hours=1), "2605.00", "2618.00", "2604.00", "2614.00"
        )

        events = track_fvg_lifecycle(fvg, [c4, c5], confirm_inversion_on_retest=True)
        assert len(events) == 2
        assert events[0].validity_state == FvgValidityState.INVALIDATED
        assert events[1].validity_state == FvgValidityState.INVERTED
        assert events[1].is_inverted is True
        assert events[1].inversion_confirmed_at == c5.close_time


# ═════════════════════════════════════════════════════════════════════════════
# 3. Calendar, Session, and Data Quality Protections (T11 - T14)
# ═════════════════════════════════════════════════════════════════════════════
class TestFvgSessionAndQualityProtections:
    """Verify session gap suppression, BID/ASK isolation, synthetic filtering, and PIT."""

    def test_t11_weekend_gap_suppression(self) -> None:
        """T11: Intraday bars spanning Friday close to Sunday open must NOT form an FVG."""
        # Friday 20:00 UTC (winter XAUUSD closes 22:00 UTC)
        c1 = make_candle(
            datetime(2025, 1, 17, 20, 0, tzinfo=UTC), "2600.00", "2610.00", "2598.00", "2608.00"
        )
        c2 = make_candle(
            datetime(2025, 1, 17, 21, 0, tzinfo=UTC), "2608.00", "2635.00", "2607.00", "2632.00"
        )
        # Sunday 23:00 UTC (re-open) -> 49 hour gap!
        c3 = make_candle(
            datetime(2025, 1, 19, 23, 0, tzinfo=UTC), "2640.00", "2650.00", "2638.00", "2645.00"
        )

        # Under default allow_weekend_gap=False: Suppressed!
        fvgs = detect_fair_value_gaps([c1, c2, c3], allow_weekend_gap=False)
        assert len(fvgs) == 0

        # If explicitly allowed, it detects:
        fvgs_allowed = detect_fair_value_gaps([c1, c2, c3], allow_weekend_gap=True)
        assert len(fvgs_allowed) == 1

    def test_t12_synthetic_market_closed_bar_rejection(self) -> None:
        """T12: Synthetic flat bars inserted during market closure cannot form an FVG."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(base, "2600.00", "2610.00", "2598.00", "2608.00")
        c2_synthetic = make_candle(
            base + timedelta(hours=1),
            "2608.00",
            "2635.00",
            "2607.00",
            "2632.00",
            is_synthetic=True,
        )
        c3 = make_candle(base + timedelta(hours=2), "2632.00", "2645.00", "2620.00", "2642.00")

        fvgs = detect_fair_value_gaps([c1, c2_synthetic, c3])
        assert len(fvgs) == 0

    def test_t13_bid_ask_isolation(self) -> None:
        """T13: BID series produces BID FVG, ASK series produces ASK FVG; mixing raises error."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        bid1 = make_candle(
            base, "2600.00", "2610.00", "2598.00", "2608.00", price_type=PriceType.BID
        )
        bid2 = make_candle(
            base + timedelta(hours=1),
            "2608.00",
            "2635.00",
            "2607.00",
            "2632.00",
            price_type=PriceType.BID,
        )
        bid3 = make_candle(
            base + timedelta(hours=2),
            "2632.00",
            "2645.00",
            "2620.00",
            "2642.00",
            price_type=PriceType.BID,
        )

        ask1 = make_candle(
            base, "2600.50", "2610.50", "2598.50", "2608.50", price_type=PriceType.ASK
        )

        # Pure BID:
        bid_fvgs = detect_fair_value_gaps([bid1, bid2, bid3])
        assert len(bid_fvgs) == 1
        assert bid_fvgs[0].price_type == PriceType.BID

        # Mixed BID and ASK: strictly raises InvalidCandleSequenceError
        with pytest.raises(InvalidCandleSequenceError):
            detect_fair_value_gaps([bid1, ask1, bid3])

    def test_t14_pit_as_of_decision_time_cutoff(self) -> None:
        """T14: Future candles past as_of_utc are excluded from lifecycle tracking (zero lookahead)."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(base, "2600.00", "2610.00", "2598.00", "2608.00")
        c2 = make_candle(base + timedelta(hours=1), "2608.00", "2635.00", "2607.00", "2632.00")
        c3 = make_candle(base + timedelta(hours=2), "2632.00", "2645.00", "2620.00", "2642.00")
        fvg = detect_fair_value_gaps([c1, c2, c3])[0]

        # Candle 4 at 13:00-14:00 (fills FVG)
        c4 = make_candle(base + timedelta(hours=3), "2642.00", "2645.00", "2615.00", "2620.00")
        # Candle 5 at 14:00-15:00
        c5 = make_candle(base + timedelta(hours=4), "2620.00", "2625.00", "2618.00", "2622.00")

        # Decision time as_of is 13:30 (before c4 closes at 14:00)
        as_of_1330 = base + timedelta(hours=3, minutes=30)
        events_at_1330 = track_fvg_lifecycle(fvg, [c4, c5], as_of_utc=as_of_1330)
        assert len(events_at_1330) == 0  # c4 has not closed yet!

        # Decision time as_of is 14:00 (c4 finalized)
        as_of_1400 = base + timedelta(hours=4)
        events_at_1400 = track_fvg_lifecycle(fvg, [c4, c5], as_of_utc=as_of_1400)
        assert len(events_at_1400) == 1
        assert events_at_1400[0].bar_open_time == c4.open_time


# ═════════════════════════════════════════════════════════════════════════════
# 4. Extended Edge Cases & Robustness
# ═════════════════════════════════════════════════════════════════════════════
class TestFvgExtendedEdgeCases:
    """Verify boundary conditions, sequence violations, deterministic IDs, and daily sessions."""

    def test_equal_boundaries_zero_width_no_fvg(self) -> None:
        """Equal boundaries (candle_3.low == candle_1.high) produce no FVG."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(base, "2600.00", "2615.00", "2598.00", "2608.00")
        c2 = make_candle(base + timedelta(hours=1), "2608.00", "2635.00", "2607.00", "2632.00")
        c3 = make_candle(base + timedelta(hours=2), "2632.00", "2645.00", "2615.00", "2642.00")

        assert len(detect_fair_value_gaps([c1, c2, c3])) == 0

    def test_non_chronological_ordering_raises_error(self) -> None:
        """Out of order timestamps raise InvalidCandleSequenceError."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(base, "2600.00", "2610.00", "2598.00", "2608.00")
        c2 = make_candle(
            base - timedelta(hours=1), "2608.00", "2635.00", "2607.00", "2632.00"
        )  # backwards!
        c3 = make_candle(base + timedelta(hours=1), "2632.00", "2645.00", "2620.00", "2642.00")

        with pytest.raises(InvalidCandleSequenceError, match="Non-chronological"):
            detect_fair_value_gaps([c1, c2, c3])

    def test_duplicate_timestamps_raises_error(self) -> None:
        """Duplicate open_time raises InvalidCandleSequenceError."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(base, "2600.00", "2610.00", "2598.00", "2608.00")
        c2 = make_candle(base, "2608.00", "2635.00", "2607.00", "2632.00")  # same open!
        c3 = make_candle(base + timedelta(hours=1), "2632.00", "2645.00", "2620.00", "2642.00")

        with pytest.raises(InvalidCandleSequenceError, match="duplicate"):
            detect_fair_value_gaps([c1, c2, c3])

    def test_mixed_instruments_raises_error(self) -> None:
        """Mixed instruments in the sequence raise InvalidCandleSequenceError."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(base, "2600.00", "2610.00", "2598.00", "2608.00", instrument_id="XAUUSD")
        c2 = make_candle(
            base + timedelta(hours=1),
            "2608.00",
            "2635.00",
            "2607.00",
            "2632.00",
            instrument_id="EURUSD",
        )
        c3 = make_candle(
            base + timedelta(hours=2),
            "2632.00",
            "2645.00",
            "2620.00",
            "2642.00",
            instrument_id="XAUUSD",
        )

        with pytest.raises(InvalidCandleSequenceError, match="Mixed instruments"):
            detect_fair_value_gaps([c1, c2, c3])

    def test_mixed_timeframes_raises_error(self) -> None:
        """Mixed timeframes in the sequence raise InvalidCandleSequenceError."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(base, "2600.00", "2610.00", "2598.00", "2608.00", timeframe=Timeframe.H1)
        c2 = make_candle(
            base + timedelta(hours=1),
            "2608.00",
            "2635.00",
            "2607.00",
            "2632.00",
            timeframe=Timeframe.H4,
        )
        c3 = make_candle(
            base + timedelta(hours=5),
            "2632.00",
            "2645.00",
            "2620.00",
            "2642.00",
            timeframe=Timeframe.H1,
        )

        with pytest.raises(InvalidCandleSequenceError, match="Mixed timeframes"):
            detect_fair_value_gaps([c1, c2, c3])

    def test_unknown_price_type_raises_error(self) -> None:
        """Candles with PriceType.UNKNOWN raise InvalidCandleSequenceError."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        c1 = make_candle(
            base, "2600.00", "2610.00", "2598.00", "2608.00", price_type=PriceType.UNKNOWN
        )
        c2 = make_candle(
            base + timedelta(hours=1),
            "2608.00",
            "2635.00",
            "2607.00",
            "2632.00",
            price_type=PriceType.UNKNOWN,
        )
        c3 = make_candle(
            base + timedelta(hours=2),
            "2632.00",
            "2645.00",
            "2620.00",
            "2642.00",
            price_type=PriceType.UNKNOWN,
        )

        with pytest.raises(InvalidCandleSequenceError, match=r"PriceType\.UNKNOWN"):
            detect_fair_value_gaps([c1, c2, c3])

    def test_deterministic_fvg_id_generation(self) -> None:
        """Verify collision-resistant, deterministic FVG identity format."""
        form_time = datetime(2025, 1, 15, 13, 0, tzinfo=UTC)
        fvg_id = generate_fvg_id(
            instrument_id="XAUUSD",
            source_id="market_data_lab_m1",
            price_type=PriceType.BID,
            timeframe=Timeframe.H4,
            formation_time=form_time,
            direction=FvgDirection.BULLISH,
            revision=2,
        )
        assert fvg_id == "XAUUSD:market_data_lab_m1:BID:H4:20250115130000:BULLISH:r2"

    def test_min_width_filter(self) -> None:
        """FVGs with width strictly smaller than min_width are filtered out."""
        base = datetime(2025, 1, 15, 10, 0, tzinfo=UTC)
        # Width is 2612.00 - 2610.00 = 2.00
        c1 = make_candle(base, "2600.00", "2610.00", "2598.00", "2608.00")
        c2 = make_candle(base + timedelta(hours=1), "2608.00", "2635.00", "2607.00", "2632.00")
        c3 = make_candle(base + timedelta(hours=2), "2632.00", "2645.00", "2612.00", "2642.00")

        # Threshold 5.00 filters out this 2.00 width gap
        assert len(detect_fair_value_gaps([c1, c2, c3], min_width=Decimal("5.00"))) == 0
        # Threshold 1.50 accepts it
        assert len(detect_fair_value_gaps([c1, c2, c3], min_width=Decimal("1.50"))) == 1

    def test_daily_d1_weekend_transition_allowed(self) -> None:
        """For Daily (D1) bars, Friday close to Monday open is a valid market transition."""
        # Friday D1 (Jan 17, 2025)
        c1 = make_candle(
            datetime(2025, 1, 17, 0, 0, tzinfo=UTC),
            "2600.00",
            "2610.00",
            "2590.00",
            "2605.00",
            timeframe=Timeframe.D1,
        )
        # Monday D1 (Jan 20, 2025) -> Delta from Friday close (Jan 18 00:00) to Monday open (Jan 20 00:00) is 2 days <= 4 days
        c2 = make_candle(
            datetime(2025, 1, 20, 0, 0, tzinfo=UTC),
            "2612.00",
            "2650.00",
            "2611.00",
            "2645.00",
            timeframe=Timeframe.D1,
        )
        # Tuesday D1 (Jan 21, 2025)
        c3 = make_candle(
            datetime(2025, 1, 21, 0, 0, tzinfo=UTC),
            "2645.00",
            "2660.00",
            "2625.00",
            "2655.00",
            timeframe=Timeframe.D1,
        )

        # c3.low (2625) > c1.high (2610) -> Valid D1 Bullish FVG across weekend trading days!
        fvgs = detect_fair_value_gaps([c1, c2, c3], allow_weekend_gap=False)
        assert len(fvgs) == 1
        assert fvgs[0].timeframe == Timeframe.D1
        assert fvgs[0].bottom_price == Decimal("2610.00")
        assert fvgs[0].top_price == Decimal("2625.00")

    def test_pilot_with_real_xauusd_data_read_only(self) -> None:
        """Pilot read-only test using real Market-Data-Lab CSV data for D1/H4 resampled FVG detection."""
        from traderlab.application.analysis.resampling import resample_m1_candles
        from traderlab.infrastructure.providers.market_data_lab.config import (
            get_market_data_lab_settings,
        )
        from traderlab.infrastructure.providers.market_data_lab.csv_reader import (
            bar_to_candle,
            read_m1_csv,
        )

        settings = get_market_data_lab_settings()
        try:
            bid_path, _ = settings.validate_dataset_files(2024, 1)
        except Exception:
            pytest.skip(
                "Market-Data-Lab raw archive not present in standard location; skipping real pilot."
            )

        if not bid_path.exists():
            pytest.skip("Market-Data-Lab raw archive not present; skipping real pilot.")

        # Read first 10,000 M1 bars (~7 trading days) from real Jan 2024 archive
        raw_bars = read_m1_csv(bid_path, PriceType.BID)
        sample_bars = raw_bars[:10000]

        # Convert to domain candles
        sample_candles = [bar_to_candle(b) for b in sample_bars]

        # Resample to H4
        resampled_h4 = resample_m1_candles(sample_candles, Timeframe.H4)
        h4_candles = [r.candle for r in resampled_h4]

        # Detect FVGs on real resampled H4 series
        h4_fvgs = detect_fair_value_gaps(h4_candles)
        # Verify that any detected FVG adheres 100% to invariants
        for fvg in h4_fvgs:
            assert fvg.width > Decimal(0)
            assert fvg.top_price > fvg.bottom_price
            assert fvg.ce_price == (fvg.top_price + fvg.bottom_price) / Decimal(2)
            assert fvg.price_type == PriceType.BID
            assert fvg.timeframe == Timeframe.H4
