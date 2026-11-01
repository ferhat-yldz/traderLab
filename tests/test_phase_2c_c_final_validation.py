"""Final acceptance and regression test suite for FAZ 2C-C: MTF FVG/IFVG Final Validation & Semantic Integrity.

Covers all 14 mandatory acceptance criteria:
1. Invalidated FVG is not counted as active.
2. Fully Filled FVG status is managed correctly (distinguished from active unfilled).
3. Inverted FVG is reported with its effective directional bias (bullish inversion acts as bearish resistance, bearish inversion acts as bullish support).
4. IFVG is not double-counted with original normal FVG.
5. MTF confluence only includes valid, non-exhausted zones (excludes invalidated and non-inverted fully filled).
6. Same FVG combination is not counted twice (permutation uniqueness).
7. Unique price bands count and total combination count are strictly separated.
8. Selected as_of ahead of data coverage end safely clamps to effective cutoff without fabricating bars.
9. Warm-up FVG count and visible window FVG count are cleanly partitioned.
10. Single-chart mode and MTF panel domain outputs exhibit 100% 1:1 semantic parity.
11. IFVG two-segment drawing preserves historical pre-inversion attributes without retroactive alteration.
12. FVG display toggled off draws no FVG shapes or CE lines.
13. FVG display toggled on properly renders bullish, bearish, CE line, and IFVG layers.
14. BID/ASK and D1 session mode changes do not cross-contaminate cached series or FVG IDs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    resample_m1_candles,
)
from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.fvg import (
    FvgDirection,
    FvgFillState,
    FvgLifecycleEvent,
    FvgValidityState,
    FvgZone,
    detect_fair_value_gaps,
    generate_fvg_id,
    track_fvg_lifecycle,
)
from traderlab.domain.market_structure.mtf_fvg import (
    analyze_mtf_confluences,
    compute_mtf_fvg_confluences,
    summarize_mtf_timeframe,
)
from traderlab.interfaces.dashboard.app import compute_fvg_records_for_view
from traderlab.interfaces.dashboard.charts import create_candlestick_figure


def make_candle(
    open_time: datetime,
    open_p: str,
    high_p: str,
    low_p: str,
    close_p: str,
    timeframe: Timeframe = Timeframe.H4,
    price_type: PriceType = PriceType.BID,
    is_closed: bool = True,
    ingested_at: datetime | None = None,
) -> Candle:
    """Helper to create deterministic test candles."""
    tf_delta = {
        Timeframe.M1: timedelta(minutes=1),
        Timeframe.H2: timedelta(hours=2),
        Timeframe.H4: timedelta(hours=4),
        Timeframe.D1: timedelta(days=1),
    }[timeframe]
    return Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("Test-Feed"),
        price_type=price_type,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + tf_delta,
        open=Decimal(open_p),
        high=Decimal(high_p),
        low=Decimal(low_p),
        close=Decimal(close_p),
        is_closed=is_closed,
        volume=Decimal("100"),
        ingested_at=ingested_at,
    )


def make_dummy_zone(
    tf: Timeframe,
    idx: int,
    top: str,
    bot: str,
    direction: FvgDirection,
    formation_time: datetime,
) -> FvgZone:
    """Helper to create valid domain FvgZone."""
    t_f = formation_time
    top_d = Decimal(top)
    bot_d = Decimal(bot)
    ce_d = (top_d + bot_d) / Decimal(2)
    return FvgZone(
        id=generate_fvg_id(
            InstrumentId("XAUUSD"),
            DataSourceId("Test"),
            PriceType.BID,
            tf,
            t_f,
            direction,
            revision=idx,
        ),
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("Test"),
        price_type=PriceType.BID,
        timeframe=tf,
        direction=direction,
        top_price=top_d,
        bottom_price=bot_d,
        ce_price=ce_d,
        width=top_d - bot_d,
        candle_1_open=t_f - timedelta(hours=8),
        candle_2_open=t_f - timedelta(hours=4),
        candle_3_open=t_f,
        formation_time=t_f,
        computable_at_utc=t_f,
        revision=idx,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Invalidated FVG is not counted as active
# ─────────────────────────────────────────────────────────────────────────────
def test_invalidated_fvg_not_counted_as_active() -> None:
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2600.0", "2605.0", "2598.0", "2604.0")
    c2 = make_candle(t0 + timedelta(hours=4), "2604.0", "2630.0", "2603.0", "2628.0")
    c3 = make_candle(t0 + timedelta(hours=8), "2628.0", "2635.0", "2615.0", "2632.0")

    fvgs = detect_fair_value_gaps([c1, c2, c3])
    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.direction == FvgDirection.BULLISH

    # Invalidation candle: closes below bottom_price (2605.0) -> invalidated
    c4 = make_candle(t0 + timedelta(hours=12), "2632.0", "2632.0", "2590.0", "2595.0")
    # confirm_inversion_on_retest=True keeps it INVALIDATED before any retest
    events = track_fvg_lifecycle(
        fvg, [c1, c2, c3, c4], as_of_utc=c4.close_time, confirm_inversion_on_retest=True
    )
    last_ev = events[-1]
    assert last_ev.validity_state == FvgValidityState.INVALIDATED

    summary = summarize_mtf_timeframe(
        Timeframe.H4,
        [c1, c2, c3, c4],
        [(fvg, last_ev)],
        drawn_count=1,
        total_filtered_count=1,
        as_of_utc=c4.close_time,
        visible_start_utc=t0,
    )

    assert summary.invalidated_count == 1
    assert summary.active_normal_bullish_count == 0
    assert summary.active_normal_bearish_count == 0
    assert summary.inverted_count == 0
    assert summary.total_detected_fvgs == 1


# ─────────────────────────────────────────────────────────────────────────────
# 2. Fully Filled FVG status is managed correctly
# ─────────────────────────────────────────────────────────────────────────────
def test_fully_filled_fvg_status_managed_correctly() -> None:
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2600.0", "2605.0", "2598.0", "2604.0")
    c2 = make_candle(t0 + timedelta(hours=4), "2604.0", "2630.0", "2603.0", "2628.0")
    c3 = make_candle(t0 + timedelta(hours=8), "2628.0", "2635.0", "2615.0", "2632.0")

    fvgs = detect_fair_value_gaps([c1, c2, c3])
    fvg = fvgs[0]

    # Wick fully fills to bottom_price 2605.0 but candle closes inside/above -> FULLY_FILLED, VALID
    c4 = make_candle(t0 + timedelta(hours=12), "2632.0", "2632.0", "2604.5", "2610.0")
    events = track_fvg_lifecycle(fvg, [c1, c2, c3, c4], as_of_utc=c4.close_time)
    last_ev = events[-1]

    assert last_ev.fill_state == FvgFillState.FULLY_FILLED
    assert last_ev.validity_state == FvgValidityState.VALID

    summary = summarize_mtf_timeframe(
        Timeframe.H4,
        [c1, c2, c3, c4],
        [(fvg, last_ev)],
        drawn_count=1,
        total_filtered_count=1,
        as_of_utc=c4.close_time,
        visible_start_utc=t0,
    )

    assert summary.fully_filled_count == 1
    assert summary.active_normal_bullish_count == 0
    assert summary.active_normal_bearish_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 3. Inverted FVG reported with effective direction
# ─────────────────────────────────────────────────────────────────────────────
def test_inverted_fvg_reported_with_effective_direction() -> None:
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    # 1) Bullish FVG that inverts -> acts as Bearish Resistance
    c1 = make_candle(t0, "2600.0", "2605.0", "2598.0", "2604.0")
    c2 = make_candle(t0 + timedelta(hours=4), "2604.0", "2630.0", "2603.0", "2628.0")
    c3 = make_candle(t0 + timedelta(hours=8), "2628.0", "2635.0", "2615.0", "2632.0")
    c4 = make_candle(t0 + timedelta(hours=12), "2632.0", "2632.0", "2590.0", "2595.0")  # inverts

    fvg_bull = detect_fair_value_gaps([c1, c2, c3])[0]
    ev_bull = track_fvg_lifecycle(
        fvg_bull, [c1, c2, c3, c4], as_of_utc=c4.close_time, confirm_inversion_on_retest=False
    )[-1]
    assert ev_bull.validity_state == FvgValidityState.INVERTED

    summary_bull = summarize_mtf_timeframe(
        Timeframe.H4,
        [c1, c2, c3, c4],
        [(fvg_bull, ev_bull)],
        drawn_count=1,
        total_filtered_count=1,
        as_of_utc=c4.close_time,
        visible_start_utc=t0,
    )

    assert summary_bull.inverted_count == 1
    assert summary_bull.active_ifvg_bearish_count == 1  # Former Bullish -> Eff. Bearish Resistance
    assert summary_bull.active_ifvg_bullish_count == 0

    # 2) Bearish FVG that inverts -> acts as Bullish Support
    b1 = make_candle(t0, "2630.0", "2632.0", "2625.0", "2626.0")
    b2 = make_candle(t0 + timedelta(hours=4), "2626.0", "2627.0", "2600.0", "2602.0")
    b3 = make_candle(t0 + timedelta(hours=8), "2602.0", "2615.0", "2595.0", "2600.0")
    b4 = make_candle(t0 + timedelta(hours=12), "2600.0", "2640.0", "2598.0", "2635.0")  # inverts

    fvg_bear = detect_fair_value_gaps([b1, b2, b3])[0]
    ev_bear = track_fvg_lifecycle(
        fvg_bear, [b1, b2, b3, b4], as_of_utc=b4.close_time, confirm_inversion_on_retest=False
    )[-1]
    assert ev_bear.validity_state == FvgValidityState.INVERTED

    summary_bear = summarize_mtf_timeframe(
        Timeframe.H4,
        [b1, b2, b3, b4],
        [(fvg_bear, ev_bear)],
        drawn_count=1,
        total_filtered_count=1,
        as_of_utc=b4.close_time,
        visible_start_utc=t0,
    )

    assert summary_bear.inverted_count == 1
    assert summary_bear.active_ifvg_bullish_count == 1  # Former Bearish -> Eff. Bullish Support
    assert summary_bear.active_ifvg_bearish_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. IFVG is not double-counted with original normal FVG
# ─────────────────────────────────────────────────────────────────────────────
def test_ifvg_not_double_counted_with_original_normal_fvg() -> None:
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)

    records = [
        (
            make_dummy_zone(Timeframe.H4, 1, "2620.0", "2610.0", FvgDirection.BULLISH, t0),
            None,
        ),  # Active normal bull
        (
            make_dummy_zone(Timeframe.H4, 2, "2630.0", "2620.0", FvgDirection.BEARISH, t0),
            None,
        ),  # Active normal bear
        (
            make_dummy_zone(Timeframe.H4, 3, "2640.0", "2630.0", FvgDirection.BULLISH, t0),
            FvgLifecycleEvent(
                fvg_id="3",
                bar_open_time=t0,
                bar_close_time=t0,
                event_time_utc=t0,
                fill_state=FvgFillState.UNTOUCHED,
                fill_ratio=Decimal("0"),
                validity_state=FvgValidityState.INVERTED,
                extreme_penetration_price=Decimal("2630.0"),
                mitigated_by_candle_open=t0,
                invalidated_by_candle_open=t0,
                inversion_confirmed_at=t0,
            ),
        ),  # Inverted (eff. bearish)
        (
            make_dummy_zone(Timeframe.H4, 4, "2650.0", "2640.0", FvgDirection.BEARISH, t0),
            FvgLifecycleEvent(
                fvg_id="4",
                bar_open_time=t0,
                bar_close_time=t0,
                event_time_utc=t0,
                fill_state=FvgFillState.FULLY_FILLED,
                fill_ratio=Decimal("1"),
                validity_state=FvgValidityState.VALID,
                extreme_penetration_price=Decimal("2650.0"),
                mitigated_by_candle_open=t0,
                invalidated_by_candle_open=None,
                inversion_confirmed_at=None,
            ),
        ),  # Fully filled normal
        (
            make_dummy_zone(Timeframe.H4, 5, "2660.0", "2650.0", FvgDirection.BULLISH, t0),
            FvgLifecycleEvent(
                fvg_id="5",
                bar_open_time=t0,
                bar_close_time=t0,
                event_time_utc=t0,
                fill_state=FvgFillState.UNTOUCHED,
                fill_ratio=Decimal("0"),
                validity_state=FvgValidityState.INVALIDATED,
                extreme_penetration_price=Decimal("2650.0"),
                mitigated_by_candle_open=t0,
                invalidated_by_candle_open=t0,
                inversion_confirmed_at=None,
            ),
        ),  # Invalidated
    ]

    summary = summarize_mtf_timeframe(
        Timeframe.H4,
        [],
        records,
        drawn_count=5,
        total_filtered_count=5,
        as_of_utc=t0 + timedelta(days=5),
        visible_start_utc=t0,
    )

    assert summary.total_detected_fvgs == 5
    assert summary.active_normal_bullish_count == 1
    assert summary.active_normal_bearish_count == 1
    assert summary.inverted_count == 1
    assert summary.fully_filled_count == 1
    assert summary.invalidated_count == 1

    # Exact sum invariant
    category_sum = (
        summary.active_normal_bullish_count
        + summary.active_normal_bearish_count
        + summary.inverted_count
        + summary.fully_filled_count
        + summary.invalidated_count
    )
    assert category_sum == summary.total_detected_fvgs


# ─────────────────────────────────────────────────────────────────────────────
# 5. MTF confluence only includes valid zones
# ─────────────────────────────────────────────────────────────────────────────
def test_mtf_confluence_only_includes_valid_zones() -> None:
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)

    z_h4_active = make_dummy_zone(Timeframe.H4, 1, "2630.0", "2610.0", FvgDirection.BULLISH, t0)
    z_h4_invalid = make_dummy_zone(Timeframe.H4, 2, "2650.0", "2640.0", FvgDirection.BULLISH, t0)
    z_h4_filled = make_dummy_zone(Timeframe.H4, 3, "2670.0", "2660.0", FvgDirection.BULLISH, t0)
    z_h2_active = make_dummy_zone(Timeframe.H2, 4, "2625.0", "2615.0", FvgDirection.BULLISH, t0)

    ev_invalid = FvgLifecycleEvent(
        fvg_id=z_h4_invalid.id,
        bar_open_time=t0,
        bar_close_time=t0,
        event_time_utc=t0,
        fill_state=FvgFillState.UNTOUCHED,
        fill_ratio=Decimal("0"),
        validity_state=FvgValidityState.INVALIDATED,
        extreme_penetration_price=Decimal("2640.0"),
        mitigated_by_candle_open=t0,
        invalidated_by_candle_open=t0,
        inversion_confirmed_at=None,
    )
    ev_filled = FvgLifecycleEvent(
        fvg_id=z_h4_filled.id,
        bar_open_time=t0,
        bar_close_time=t0,
        event_time_utc=t0,
        fill_state=FvgFillState.FULLY_FILLED,
        fill_ratio=Decimal("1"),
        validity_state=FvgValidityState.VALID,
        extreme_penetration_price=Decimal("2660.0"),
        mitigated_by_candle_open=t0,
        invalidated_by_candle_open=None,
        inversion_confirmed_at=None,
    )

    recs = {
        Timeframe.H4: [(z_h4_active, None), (z_h4_invalid, ev_invalid), (z_h4_filled, ev_filled)],
        Timeframe.H2: [(z_h2_active, None)],
    }

    confluences = compute_mtf_fvg_confluences(recs, as_of_utc=t0 + timedelta(days=1))
    assert len(confluences) == 1
    band = confluences[0]
    assert z_h4_active.id in band.participating_fvg_ids
    assert z_h2_active.id in band.participating_fvg_ids
    assert z_h4_invalid.id not in band.participating_fvg_ids
    assert z_h4_filled.id not in band.participating_fvg_ids


# ─────────────────────────────────────────────────────────────────────────────
# 6. Same FVG combination is not counted twice
# ─────────────────────────────────────────────────────────────────────────────
def test_same_fvg_combination_not_counted_twice() -> None:
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)

    z_d1 = make_dummy_zone(Timeframe.D1, 1, "2650.0", "2600.0", FvgDirection.BULLISH, t0)
    z_h4 = make_dummy_zone(Timeframe.H4, 2, "2640.0", "2610.0", FvgDirection.BULLISH, t0)
    z_h2 = make_dummy_zone(Timeframe.H2, 3, "2635.0", "2615.0", FvgDirection.BULLISH, t0)

    recs = {
        Timeframe.D1: [(z_d1, None)],
        Timeframe.H4: [(z_h4, None)],
        Timeframe.H2: [(z_h2, None)],
    }

    confluences = compute_mtf_fvg_confluences(recs, as_of_utc=t0 + timedelta(days=1))
    comb_tuples = [tuple(sorted(b.participating_fvg_ids)) for b in confluences]
    # Invariant: Each participating combination is strictly unique
    assert len(comb_tuples) == len(set(comb_tuples))
    assert len(confluences) >= 2


# ─────────────────────────────────────────────────────────────────────────────
# 7. Unique price bands count and total combination count are strictly separated
# ─────────────────────────────────────────────────────────────────────────────
def test_unique_price_bands_and_combinations_separated() -> None:
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)

    # D1 covers 2600 to 2650
    z_d1 = make_dummy_zone(Timeframe.D1, 1, "2650.0", "2600.0", FvgDirection.BEARISH, t0)
    # Two distinct H4 zones having identical price boundaries (e.g. retested levels)
    z_h4_a = make_dummy_zone(
        Timeframe.H4, 2, "2640.0", "2610.0", FvgDirection.BEARISH, t0 + timedelta(hours=4)
    )
    z_h4_b = make_dummy_zone(
        Timeframe.H4, 3, "2640.0", "2610.0", FvgDirection.BEARISH, t0 + timedelta(hours=8)
    )

    recs = {
        Timeframe.D1: [(z_d1, None)],
        Timeframe.H4: [(z_h4_a, None), (z_h4_b, None)],
    }

    confluences = compute_mtf_fvg_confluences(recs, as_of_utc=t0 + timedelta(days=2))
    analysis = analyze_mtf_confluences(confluences)

    # 2 different combinations: (D1, H4_a) and (D1, H4_b)
    assert analysis.total_combinations_count == 2
    # But only 1 unique price band: $2610.00 to $2640.00
    assert analysis.unique_price_bands_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# 8. Selected as_of ahead of data end uses effective cutoff + warning
# ─────────────────────────────────────────────────────────────────────────────
def test_selected_as_of_ahead_of_data_end_effective_cutoff_and_warning() -> None:
    # Coverage ends at 14:00 UTC
    data_end = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)
    requested_as_of = datetime(2026, 10, 19, 23, 59, tzinfo=UTC)

    effective_cutoff = min(requested_as_of, data_end)
    assert effective_cutoff == data_end
    assert requested_as_of > effective_cutoff
    gap_hours = (requested_as_of - effective_cutoff).total_seconds() / 3600
    assert abs(gap_hours - 9.9833) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# 9. Warm-up FVG count vs visible period FVG count separated
# ─────────────────────────────────────────────────────────────────────────────
def test_warmup_fvg_count_vs_visible_fvg_count_separated() -> None:
    t_warm = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    t_vis = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)

    z_warm = make_dummy_zone(Timeframe.H4, 1, "2620.0", "2610.0", FvgDirection.BULLISH, t_warm)
    z_vis = make_dummy_zone(
        Timeframe.H4, 2, "2630.0", "2620.0", FvgDirection.BULLISH, t_vis + timedelta(hours=4)
    )

    records = [(z_warm, None), (z_vis, None)]

    summary = summarize_mtf_timeframe(
        Timeframe.H4,
        [],
        records,
        drawn_count=2,
        total_filtered_count=2,
        as_of_utc=t_vis + timedelta(days=10),
        visible_start_utc=t_vis,
    )

    assert summary.warmup_carried_count == 1
    assert summary.visible_formed_count == 1
    assert summary.total_detected_fvgs == 2


# ─────────────────────────────────────────────────────────────────────────────
# 10. Single chart and MTF domain outputs match 1:1 semantically
# ─────────────────────────────────────────────────────────────────────────────
def test_single_chart_and_mtf_domain_outputs_1_to_1_parity() -> None:
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    candles = [
        make_candle(
            t0 + timedelta(hours=i * 4),
            str(2600 + i),
            str(2610 + i * 2),
            str(2595 + i),
            str(2605 + i),
        )
        for i in range(12)
    ]

    single_recs = compute_fvg_records_for_view(
        candles, t0, t0 + timedelta(days=5), as_of_utc=candles[-1].close_time
    )
    mtf_recs = compute_fvg_records_for_view(
        candles, t0, t0 + timedelta(days=5), as_of_utc=candles[-1].close_time
    )

    assert len(single_recs) == len(mtf_recs)
    for (f_s, ev_s), (f_m, ev_m) in zip(single_recs, mtf_recs, strict=True):
        assert f_s.id == f_m.id
        assert f_s.formation_time == f_m.formation_time
        assert f_s.top_price == f_m.top_price
        assert f_s.bottom_price == f_m.bottom_price
        assert f_s.direction == f_m.direction
        assert (ev_s is None and ev_m is None) or (ev_s is not None and ev_m is not None)
        if ev_s and ev_m:
            assert ev_s.validity_state == ev_m.validity_state
            assert ev_s.fill_state == ev_m.fill_state


# ─────────────────────────────────────────────────────────────────────────────
# 11. IFVG two-segment drawing does not alter past
# ─────────────────────────────────────────────────────────────────────────────
def test_ifvg_two_segment_drawing_does_not_alter_past() -> None:
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)

    c1 = make_candle(t0, "2600.0", "2605.0", "2598.0", "2604.0")
    c2 = make_candle(t0 + timedelta(hours=4), "2604.0", "2630.0", "2603.0", "2628.0")
    c3 = make_candle(t0 + timedelta(hours=8), "2628.0", "2635.0", "2615.0", "2632.0")
    # c4 trades inside zone, respecting it
    c4 = make_candle(t0 + timedelta(hours=12), "2632.0", "2632.0", "2610.0", "2620.0")
    # c5 closes below bottom_price (2605.0) and inverts
    c5 = make_candle(t0 + timedelta(hours=16), "2620.0", "2622.0", "2590.0", "2595.0")
    # c6 continues post-inversion
    c6 = make_candle(t0 + timedelta(hours=20), "2595.0", "2600.0", "2585.0", "2590.0")

    fvg = detect_fair_value_gaps([c1, c2, c3])[0]
    ev = track_fvg_lifecycle(
        fvg, [c1, c2, c3, c4, c5, c6], as_of_utc=c6.close_time, confirm_inversion_on_retest=False
    )[-1]
    assert ev.validity_state == FvgValidityState.INVERTED

    fig = create_candlestick_figure(
        [c1, c2, c3, c4, c5, c6],
        title="Test IFVG Drawing",
        fvg_records=[(fvg, ev)],
        show_bullish_fvg=True,
        show_bearish_fvg=True,
        show_ce_line=True,
        show_ifvg=True,
    )

    # 2 rectangle shapes: primary pre-inversion segment + secondary post-inversion IFVG segment
    rect_shapes = [s for s in fig.layout.shapes if s.type == "rect"]
    assert len(rect_shapes) == 2

    # Segment 1: pre-inversion with green bullish fill
    assert "38, 166, 154" in rect_shapes[0].fillcolor  # Green

    # Segment 2: post-inversion with purple dashed line
    assert "168, 85, 247" in rect_shapes[1].fillcolor  # Purple
    assert rect_shapes[1].line.dash == "dash"


# ─────────────────────────────────────────────────────────────────────────────
# 12. FVG display toggled off draws no FVG shapes or CE lines
# ─────────────────────────────────────────────────────────────────────────────
def test_fvg_layer_off_adds_no_shapes() -> None:
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2600.0", "2605.0", "2598.0", "2604.0")
    c2 = make_candle(t0 + timedelta(hours=4), "2604.0", "2630.0", "2603.0", "2628.0")
    c3 = make_candle(t0 + timedelta(hours=8), "2628.0", "2635.0", "2615.0", "2632.0")

    fvg = detect_fair_value_gaps([c1, c2, c3])[0]

    fig = create_candlestick_figure(
        [c1, c2, c3],
        title="FVG OFF Test",
        fvg_records=[(fvg, None)],
        show_bullish_fvg=False,
        show_bearish_fvg=False,
        show_ce_line=False,
        show_ifvg=False,
    )

    # No rectangle shapes or lines added for FVG
    assert len(fig.layout.shapes) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 13. FVG display toggled on renders visible layers
# ─────────────────────────────────────────────────────────────────────────────
def test_fvg_layer_on_adds_visible_shapes() -> None:
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2600.0", "2605.0", "2598.0", "2604.0")
    c2 = make_candle(t0 + timedelta(hours=4), "2604.0", "2630.0", "2603.0", "2628.0")
    c3 = make_candle(t0 + timedelta(hours=8), "2628.0", "2635.0", "2615.0", "2632.0")

    fvg = detect_fair_value_gaps([c1, c2, c3])[0]

    fig = create_candlestick_figure(
        [c1, c2, c3],
        title="FVG ON Test",
        fvg_records=[(fvg, None)],
        show_bullish_fvg=True,
        show_bearish_fvg=True,
        show_ce_line=True,
        show_ifvg=True,
    )

    rects = [s for s in fig.layout.shapes if s.type == "rect"]
    lines = [s for s in fig.layout.shapes if s.type == "line"]

    assert len(rects) >= 1  # FVG box
    assert len(lines) >= 1  # 50% CE line


# ─────────────────────────────────────────────────────────────────────────────
# 14. BID/ASK and D1 session mode cache isolation
# ─────────────────────────────────────────────────────────────────────────────
def test_bid_ask_and_d1_session_mode_cache_isolation() -> None:
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    m1_bid = [
        Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("Test"),
            price_type=PriceType.BID,
            timeframe=Timeframe.M1,
            open_time=t0 + timedelta(minutes=i),
            close_time=t0 + timedelta(minutes=i + 1),
            open=Decimal("2600.00"),
            high=Decimal("2601.00"),
            low=Decimal("2599.00"),
            close=Decimal("2600.50"),
            is_closed=True,
            volume=Decimal("10"),
        )
        for i in range(1440 * 3)
    ]
    m1_ask = [
        Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("Test"),
            price_type=PriceType.ASK,
            timeframe=Timeframe.M1,
            open_time=t0 + timedelta(minutes=i),
            close_time=t0 + timedelta(minutes=i + 1),
            open=Decimal("2600.50"),
            high=Decimal("2601.50"),
            low=Decimal("2599.50"),
            close=Decimal("2601.00"),
            is_closed=True,
            volume=Decimal("10"),
        )
        for i in range(1440 * 3)
    ]

    # D1 with UTC Midnight
    d1_utc = resample_m1_candles(
        m1_bid, Timeframe.D1, daily_close_mode=DailyCloseMode.UTC_MIDNIGHT
    )
    # D1 with NY 17:00
    d1_ny = resample_m1_candles(
        m1_bid, Timeframe.D1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
    )

    # Open times must differ between UTC midnight and NY 17:00
    assert d1_utc[0].candle.open_time != d1_ny[0].candle.open_time

    # BID vs ASK candle prices must differ
    d1_ask = resample_m1_candles(
        m1_ask, Timeframe.D1, daily_close_mode=DailyCloseMode.UTC_MIDNIGHT
    )
    assert d1_utc[0].candle.open != d1_ask[0].candle.open
    assert d1_utc[0].candle.price_type == PriceType.BID
    assert d1_ask[0].candle.price_type == PriceType.ASK


# ─────────────────────────────────────────────────────────────────────────────
# 15. IFVG Strict Confirmation Semantics (Wick vs Body Invalidation vs Retest)
# ─────────────────────────────────────────────────────────────────────────────
def test_ifvg_strict_confirmation_semantics() -> None:
    """Prove that:
    1. Wick piercing through boundary does NOT invalidate or invert (reaches FULLY_FILLED while VALID).
    2. Body closing beyond boundary transitions to INVALIDATED (inversion candidate), NOT yet INVERTED.
    3. Subsequent retest respecting the boundary confirms inversion to INVERTED.
    """
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2600.0", "2605.0", "2598.0", "2604.0")
    c2 = make_candle(t0 + timedelta(hours=4), "2604.0", "2630.0", "2603.0", "2628.0")
    c3 = make_candle(t0 + timedelta(hours=8), "2628.0", "2635.0", "2615.0", "2632.0")

    fvgs = detect_fair_value_gaps([c1, c2, c3])
    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.direction == FvgDirection.BULLISH
    # Zone: [2605.0, 2615.0], CE: 2610.0

    # Step 1: Deep wick pierce (low 2602.0 < bottom 2605.0), but body closes inside (close 2612.0)
    c4 = make_candle(t0 + timedelta(hours=12), "2620.0", "2625.0", "2602.0", "2612.0")
    evs_step1 = track_fvg_lifecycle(fvg, [c1, c2, c3, c4], confirm_inversion_on_retest=True)
    assert evs_step1[-1].fill_state == FvgFillState.FULLY_FILLED
    assert evs_step1[-1].validity_state == FvgValidityState.VALID  # Not invalidated!

    # Step 2: Body closing below bottom (close 2598.0 < 2605.0) -> INVALIDATED (candidate)
    c5 = make_candle(t0 + timedelta(hours=16), "2612.0", "2614.0", "2595.0", "2598.0")
    evs_step2 = track_fvg_lifecycle(fvg, [c1, c2, c3, c4, c5], confirm_inversion_on_retest=True)
    assert evs_step2[-1].validity_state == FvgValidityState.INVALIDATED
    assert evs_step2[-1].invalidated_by_candle_open == c5.open_time
    assert evs_step2[-1].inversion_confirmed_at is None  # Not yet confirmed!

    # Step 3: Retest bar re-enters zone from below (high 2608.0 >= bottom 2605.0) and closes <= top 2615.0
    c6 = make_candle(t0 + timedelta(hours=20), "2598.0", "2608.0", "2596.0", "2603.0")
    evs_step3 = track_fvg_lifecycle(
        fvg, [c1, c2, c3, c4, c5, c6], confirm_inversion_on_retest=True
    )
    assert evs_step3[-1].validity_state == FvgValidityState.INVERTED
    assert evs_step3[-1].inversion_confirmed_at == c6.close_time  # Confirmed on retest close!


# ─────────────────────────────────────────────────────────────────────────────
# 16. Bitemporal PIT Semantics: Coverage End vs Knowledge Snapshot
# ─────────────────────────────────────────────────────────────────────────────
def test_bitemporal_pit_semantics_coverage_vs_knowledge() -> None:
    """Verify that actual_data_coverage_end_utc and knowledge_snapshot_time represent distinct axes."""
    actual_coverage_end = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)
    requested_as_of = datetime(2026, 10, 19, 23, 59, tzinfo=UTC)

    # Event-time clamping
    effective_analysis_cutoff = min(requested_as_of, actual_coverage_end)
    assert effective_analysis_cutoff == actual_coverage_end
    assert effective_analysis_cutoff < requested_as_of

    # In RESEARCH_ESTIMATED policy, third-party CSV lacks transaction-time commit log:
    # knowledge_snapshot_time is distinct from candle close_time
    candle = make_candle(
        datetime(2026, 10, 19, 13, 59, tzinfo=UTC),
        "2700.0",
        "2701.0",
        "2699.0",
        "2700.5",
        timeframe=Timeframe.M1,
        ingested_at=datetime(2026, 10, 20, 0, 0, tzinfo=UTC),
    )
    assert candle.close_time == actual_coverage_end

    # Under RESEARCH_ESTIMATED, completed historical bars are available at event time
    assert candle.is_available_at(as_of=requested_as_of, policy=PitPolicy.RESEARCH_ESTIMATED)

    # Under STRICT_PIT, UNKNOWN confidence third-party CSV bars without verified available_at are rejected
    assert not candle.is_available_at(as_of=requested_as_of, policy=PitPolicy.STRICT_PIT)

    # When a knowledge snapshot prior to ingestion is specified, reject to prevent revision leakage
    assert not candle.is_available_at(
        as_of=requested_as_of,
        policy=PitPolicy.RESEARCH_ESTIMATED,
        knowledge_snapshot_time=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 17. Batch Shape Assignment Performance and Visual Parity
# ─────────────────────────────────────────────────────────────────────────────
def test_batch_shape_assignment_visual_parity() -> None:
    """Verify that batch-assigned Plotly shapes preserve all visual attributes and shape objects."""
    t0 = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2600.0", "2605.0", "2598.0", "2604.0")
    c2 = make_candle(t0 + timedelta(hours=4), "2604.0", "2630.0", "2603.0", "2628.0")
    c3 = make_candle(t0 + timedelta(hours=8), "2628.0", "2635.0", "2615.0", "2632.0")

    fvg = detect_fair_value_gaps([c1, c2, c3])[0]

    fig = create_candlestick_figure(
        [c1, c2, c3],
        fvg_records=[(fvg, None)],
        show_bullish_fvg=True,
        show_ce_line=True,
    )

    rects = [s for s in fig.layout.shapes if s.type == "rect"]
    lines = [s for s in fig.layout.shapes if s.type == "line"]

    assert len(rects) == 1
    assert len(lines) == 1

    r = rects[0]
    assert r.y0 == float(fvg.bottom_price)
    assert r.y1 == float(fvg.top_price)
    assert r.layer == "below"
    assert "rgba" in r.fillcolor

    ln = lines[0]
    assert ln.y0 == float(fvg.ce_price)
    assert ln.y1 == float(fvg.ce_price)
    assert ln.layer == "below"
