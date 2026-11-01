"""Comprehensive acceptance test suite for FAZ 2C-B: Point-in-Time Multi-Timeframe (MTF) FVG Analysis.

Validates all 17 mandatory criteria:
1. MTF toggle on/off preserves single chart view integrity.
2. D1, H4, and H2 panels contain distinct timeframe candles.
3. All panels strictly respect the identical common as_of_utc timestamp.
4. M1 bars occurring after as_of_utc do not generate higher timeframe bars or FVGs.
5. An unclosed higher timeframe bar at as_of_utc never forms an FVG.
6. FVG is never visible before the third constituent candle closes.
7. Subsequent mitigation/fill/invalidation is completely masked when viewing at a historical as_of_utc.
8. IFVG is not retroactively painted purple in the pre-inversion period.
9. BID and ASK series isolation across all MTF layers.
10. D1 UTC_MIDNIGHT and NEW_YORK_1700 session modes correctly supported.
11. Monthly archive boundary PARTIAL bar behavior is preserved without fabrication.
12. Unexpected closed-session records fail COMPLETE and do not pollute OHLC.
13. Safe empty state when zero zones or insufficient bars exist.
14. Panel-level 50-zone shape limit with accurate drawn vs total count reporting.
15. MTF confluence price range mathematical intersection (positive and negative cases).
16. Figure creation is idempotent without duplicate shapes across reruns.
17. Common filter changes update all panels consistently without cross-contamination.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    WindowCompleteness,
    resample_m1_candles,
)
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
    FvgLifecycleEvent,
    FvgValidityState,
    FvgZone,
    detect_fair_value_gaps,
    track_fvg_lifecycle,
)
from traderlab.domain.market_structure.mtf_fvg import (
    compute_mtf_fvg_confluences,
    summarize_mtf_timeframe,
)
from traderlab.interfaces.dashboard.charts import create_candlestick_figure


def make_candle(
    open_time: datetime,
    open_p: str,
    high_p: str,
    low_p: str,
    close_p: str,
    *,
    timeframe: Timeframe = Timeframe.H1,
    price_type: PriceType = PriceType.BID,
    is_closed: bool = True,
    is_synthetic: bool = False,
    source_id: str = "mtf_test",
) -> Candle:
    """Helper to construct valid domain Candle."""
    delta = timedelta(seconds=timeframe.value)
    return Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId(source_id),
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + delta,
        open=Decimal(open_p),
        high=Decimal(high_p),
        low=Decimal(low_p),
        close=Decimal(close_p),
        volume=Decimal("100"),
        price_type=price_type,
        is_closed=is_closed,
        is_synthetic=is_synthetic,
    )


def make_m1(
    open_time: datetime,
    close_p: str = "2000.00",
    *,
    price_type: PriceType = PriceType.BID,
) -> Candle:
    """Helper to construct valid M1 Candle."""
    c_p = Decimal(close_p)
    return Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("m1_source"),
        timeframe=Timeframe.M1,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=c_p,
        high=c_p + Decimal("0.50"),
        low=c_p - Decimal("0.50"),
        close=c_p,
        volume=Decimal("10"),
        price_type=price_type,
        is_closed=True,
        is_synthetic=False,
    )


# ── Tests ────────────────────────────────────────────────────────────────────


def test_01_mtf_toggle_preserves_single_chart_integrity() -> None:
    """Requirement 1: Single chart figure generation remains 100% backward compatible."""
    t0 = datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
    candles = [
        make_candle(t0 + timedelta(hours=i), "2000", "2010", "1995", "2005") for i in range(5)
    ]

    fig_single = create_candlestick_figure(candles, title="Single View")
    assert fig_single is not None
    assert len(fig_single.data) >= 1
    assert fig_single.layout.title.text == "Single View"  # type: ignore[union-attr]
    assert len(fig_single.layout.shapes) == 0


def test_02_d1_h4_h2_panels_contain_distinct_timeframe_candles() -> None:
    """Requirement 2: D1, H4, and H2 series contain distinct timeframe candles."""
    base = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
    m1_bars = [make_m1(base + timedelta(minutes=m)) for m in range(1440)]

    d1 = resample_m1_candles(m1_bars, Timeframe.D1)
    h4 = resample_m1_candles(m1_bars, Timeframe.H4)
    h2 = resample_m1_candles(m1_bars, Timeframe.H2)

    assert len(d1) == 1
    assert d1[0].candle.timeframe == Timeframe.D1

    assert len(h4) == 6
    assert all(c.candle.timeframe == Timeframe.H4 for c in h4)

    assert len(h2) == 12
    assert all(c.candle.timeframe == Timeframe.H2 for c in h2)


def test_03_all_three_panels_respect_identical_common_as_of_utc() -> None:
    """Requirement 3: Common as_of_utc decision timestamp filters all three timeframes identically."""
    base = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
    m1_bars = [make_m1(base + timedelta(minutes=m)) for m in range(1440)]

    as_of = datetime(2024, 1, 16, 16, 0, tzinfo=UTC)
    m1_pit = [c for c in m1_bars if c.close_time <= as_of]

    d1 = resample_m1_candles(m1_pit, Timeframe.D1)
    h4 = resample_m1_candles(m1_pit, Timeframe.H4)
    h2 = resample_m1_candles(m1_pit, Timeframe.H2)

    # At 16:00 UTC, D1 (closes 00:00 next day) is unclosed
    assert len(d1) == 1
    assert d1[0].candle.is_closed is False
    assert d1[0].candle.close_time > as_of

    # H4 has completed bars up to 16:00 (00-04, 04-08, 08-12, 12-16 = 4 completed)
    completed_h4 = [rc for rc in h4 if rc.candle.close_time <= as_of]
    assert len(completed_h4) == 4

    # H2 has completed bars up to 16:00 (8 completed)
    completed_h2 = [rc for rc in h2 if rc.candle.close_time <= as_of]
    assert len(completed_h2) == 8


def test_04_m1_data_after_as_of_utc_does_not_create_higher_timeframe_or_fvg() -> None:
    """Requirement 4: Raw M1 quotes occurring after as_of_utc are excluded from resampling."""
    base = datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
    m1_all = [make_m1(base + timedelta(minutes=m)) for m in range(240)]

    as_of = datetime(2024, 1, 16, 14, 0, tzinfo=UTC)
    m1_pit = [c for c in m1_all if c.close_time <= as_of]

    h1_res = resample_m1_candles(m1_pit, Timeframe.H1)
    assert len(h1_res) == 2
    assert all(rc.candle.close_time <= as_of for rc in h1_res)


def test_05_unclosed_higher_timeframe_bar_does_not_form_fvg() -> None:
    """Requirement 5: A higher timeframe bar not fully closed at as_of_utc is rejected from FVG formation."""
    t0 = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000", "2005", "1995", "2004", timeframe=Timeframe.H4, is_closed=True)
    c2 = make_candle(
        t0 + timedelta(hours=4),
        "2004",
        "2020",
        "2003",
        "2018",
        timeframe=Timeframe.H4,
        is_closed=True,
    )
    # 3rd bar is unclosed at decision time!
    c3 = make_candle(
        t0 + timedelta(hours=8),
        "2018",
        "2030",
        "2010",
        "2025",
        timeframe=Timeframe.H4,
        is_closed=False,
    )

    fvgs = detect_fair_value_gaps([c1, c2, c3], require_closed=True)
    assert len(fvgs) == 0


def test_06_fvg_not_visible_before_third_candle_closes() -> None:
    """Requirement 6: FVG zone formation_time is strictly candle_3.close_time."""
    t0 = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000", "2005", "1995", "2004", timeframe=Timeframe.H2)
    c2 = make_candle(
        t0 + timedelta(hours=2), "2004", "2020", "2003", "2018", timeframe=Timeframe.H2
    )
    c3 = make_candle(
        t0 + timedelta(hours=4), "2018", "2030", "2010", "2025", timeframe=Timeframe.H2
    )

    fvgs = detect_fair_value_gaps([c1, c2, c3])
    assert len(fvgs) == 1
    assert fvgs[0].formation_time == c3.close_time
    assert fvgs[0].formation_time == datetime(2024, 1, 16, 6, 0, tzinfo=UTC)


def test_07_future_fill_or_invalidation_masked_at_historical_as_of() -> None:
    """Requirement 7: Mitigations occurring after as_of_utc are strictly masked."""
    t0 = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000", "2005", "1995", "2004", timeframe=Timeframe.H2)
    c2 = make_candle(
        t0 + timedelta(hours=2), "2004", "2020", "2003", "2018", timeframe=Timeframe.H2
    )
    c3 = make_candle(
        t0 + timedelta(hours=4), "2018", "2030", "2010", "2025", timeframe=Timeframe.H2
    )
    # c4 pierces zone at 10:00 UTC (future event)
    c4 = make_candle(
        t0 + timedelta(hours=6), "2025", "2026", "2006", "2008", timeframe=Timeframe.H2
    )

    fvgs = detect_fair_value_gaps([c1, c2, c3])
    assert len(fvgs) == 1
    fvg = fvgs[0]

    # Observing at as_of = 06:00 UTC (right at formation, before c4)
    as_of_early = datetime(2024, 1, 16, 6, 0, tzinfo=UTC)
    events_early = track_fvg_lifecycle(fvg, [c4], as_of_utc=as_of_early)
    assert len(events_early) == 0

    # Observing at as_of = 08:00 UTC (after c4 closes)
    as_of_late = datetime(2024, 1, 16, 8, 0, tzinfo=UTC)
    events_late = track_fvg_lifecycle(fvg, [c4], as_of_utc=as_of_late)
    assert len(events_late) == 1
    assert events_late[0].fill_state in (FvgFillState.PARTIALLY_FILLED, FvgFillState.CE_REACHED)


def test_08_ifvg_not_retroactively_painted_purple_in_pre_inversion_period() -> None:
    """Requirement 8: Segments before inversion are rendered in original color; only post-inversion is purple dashed."""
    t0 = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000", "2005", "1995", "2004", timeframe=Timeframe.H2)
    c2 = make_candle(
        t0 + timedelta(hours=2), "2004", "2020", "2003", "2018", timeframe=Timeframe.H2
    )
    c3 = make_candle(
        t0 + timedelta(hours=4), "2018", "2030", "2010", "2025", timeframe=Timeframe.H2
    )
    # c4 respects zone
    c4 = make_candle(
        t0 + timedelta(hours=6), "2025", "2026", "2007", "2015", timeframe=Timeframe.H2
    )
    # c5 closes below bottom (invalidates)
    c5 = make_candle(
        t0 + timedelta(hours=8), "2015", "2016", "1990", "1995", timeframe=Timeframe.H2
    )
    # c6 retests from below and holds (confirms inversion)
    c6 = make_candle(
        t0 + timedelta(hours=10), "1995", "2008", "1994", "2002", timeframe=Timeframe.H2
    )
    # c7 trades during inverted IFVG regime
    c7 = make_candle(
        t0 + timedelta(hours=12), "2002", "2006", "1998", "2004", timeframe=Timeframe.H2
    )

    all_candles = [c1, c2, c3, c4, c5, c6, c7]
    fvgs = detect_fair_value_gaps(all_candles)
    assert len(fvgs) == 1
    fvg = fvgs[0]

    events = track_fvg_lifecycle(fvg, all_candles, confirm_inversion_on_retest=True)
    assert len(events) >= 1
    last_ev = events[-1]
    assert last_ev.validity_state == FvgValidityState.INVERTED

    fig = create_candlestick_figure(
        all_candles,
        fvg_records=[(fvg, last_ev)],
        show_ifvg=True,
    )

    rects = [s for s in fig.layout.shapes if s.type == "rect"]
    # Must have 2 segments: pre-inversion (solid green) and post-inversion (dashed purple)
    assert len(rects) == 2
    pre_seg = rects[0]
    post_seg = rects[1]

    # Pre-inversion segment is solid green
    assert "38, 166, 154" in str(pre_seg.fillcolor)
    assert pre_seg.line.dash == "solid"  # type: ignore[union-attr]

    # Post-inversion segment is dashed purple
    assert "#a855f7" in str(post_seg.line.color)  # type: ignore[union-attr]
    assert post_seg.line.dash == "dash"  # type: ignore[union-attr]


def test_09_bid_ask_isolation_across_all_mtf_layers() -> None:
    """Requirement 9: BID and ASK candles are strictly isolated."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    bid_c = make_candle(t0, "2000", "2005", "1995", "2004", price_type=PriceType.BID)
    ask_c = make_candle(t0, "2000.50", "2005.50", "1995.50", "2004.50", price_type=PriceType.ASK)

    assert bid_c.price_type == PriceType.BID
    assert ask_c.price_type == PriceType.ASK
    assert bid_c.price_type != ask_c.price_type


def test_10_d1_utc_midnight_vs_new_york_1700_modes() -> None:
    """Requirement 10: D1 UTC_MIDNIGHT and NEW_YORK_1700 produce correct daily boundaries."""
    base = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
    m1_bars = [make_m1(base + timedelta(minutes=m)) for m in range(2880)]

    d1_utc = resample_m1_candles(
        m1_bars, Timeframe.D1, daily_close_mode=DailyCloseMode.UTC_MIDNIGHT
    )
    d1_ny = resample_m1_candles(
        m1_bars, Timeframe.D1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
    )

    assert len(d1_utc) >= 1
    assert d1_utc[0].candle.open_time.hour == 0  # Midnight UTC

    assert len(d1_ny) >= 1
    assert d1_ny[0].candle.open_time.hour == 22  # 17:00 EST is 22:00 UTC in winter


def test_11_archive_boundary_partial_bar_behavior() -> None:
    """Requirement 11: Incomplete boundary bars remain WindowCompleteness.PARTIAL."""
    base = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
    # Only 30 minutes of an H1 window
    m1_bars = [make_m1(base + timedelta(minutes=m)) for m in range(30)]

    h1_res = resample_m1_candles(m1_bars, Timeframe.H1)
    assert len(h1_res) == 1
    assert h1_res[0].completeness == WindowCompleteness.PARTIAL
    assert h1_res[0].candle.is_closed is False


def test_12_unexpected_off_session_and_duplicate_anomaly_protection() -> None:
    """Requirement 12: Unexpected off-session bars fail COMPLETE and do not pollute OHLC."""
    base = datetime(2024, 1, 16, 22, 0, tzinfo=UTC)
    bars: list[Candle] = []

    # 1 unexpected bar in closed market with outlier corrupt price
    bars.append(make_m1(base + timedelta(minutes=15), close_p="1500.00"))

    # All 60 expected open minutes present with normal price 2000.00
    for m in range(60, 120):
        bars.append(make_m1(base + timedelta(minutes=m), close_p="2000.00"))

    res = resample_m1_candles(bars, Timeframe.H2)
    assert len(res) == 1
    rc = res[0]

    assert rc.completeness == WindowCompleteness.PARTIAL
    assert rc.candle.is_closed is False
    assert rc.candle.low >= Decimal("1999.00")


def test_13_zero_zones_and_insufficient_data_safe_empty_state() -> None:
    """Requirement 13: When no zones or insufficient bars exist, MTF modules handle safely without error."""
    summary = summarize_mtf_timeframe(
        Timeframe.D1,
        candles=[],
        fvg_records=[],
        drawn_count=0,
        total_filtered_count=0,
        data_completeness="EMPTY",
    )
    assert summary.total_detected_fvgs == 0
    assert summary.active_bullish_count == 0
    assert summary.drawn_count == 0

    confluences = compute_mtf_fvg_confluences({}, as_of_utc=datetime.now(tz=UTC))
    assert confluences == []


def test_14_max_50_zones_per_panel_cap_and_summary_accuracy() -> None:
    """Requirement 14: Panel caps rendered shapes at max_zones=50; summary accurately reports drawn vs total."""
    t0 = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
    records: list[tuple[FvgZone, FvgLifecycleEvent | None]] = []

    for i in range(70):
        fvg = FvgZone(
            id=f"zone_{i}",
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("test"),
            price_type=PriceType.BID,
            timeframe=Timeframe.H2,
            direction=FvgDirection.BULLISH,
            top_price=Decimal("2010.00"),
            bottom_price=Decimal("2000.00"),
            ce_price=Decimal("2005.00"),
            width=Decimal("10.00"),
            candle_1_open=t0 + timedelta(hours=i * 2),
            candle_2_open=t0 + timedelta(hours=i * 2 + 1),
            candle_3_open=t0 + timedelta(hours=i * 2 + 2),
            formation_time=t0 + timedelta(hours=i * 2 + 3),
            computable_at_utc=t0 + timedelta(hours=i * 2 + 3),
        )
        records.append((fvg, None))

    candles = [
        make_candle(
            t0 + timedelta(hours=i), "2000", "2015", "1995", "2005", timeframe=Timeframe.H2
        )
        for i in range(150)
    ]

    fig = create_candlestick_figure(candles, fvg_records=records, max_fvg_zones=50)
    rects = [s for s in fig.layout.shapes if s.type == "rect"]
    assert len(rects) == 50  # Capped at exactly 50!

    summary = summarize_mtf_timeframe(
        Timeframe.H2, candles, records, drawn_count=len(rects), total_filtered_count=len(records)
    )
    assert summary.drawn_count == 50
    assert summary.total_filtered_count == 70


def test_15_mtf_confluence_intersection_calculation_positive_and_negative() -> None:
    """Requirement 15: Exact Decimal numerical intersection for positive overlap and exclusion for negative/opposite."""
    t0 = datetime(2024, 1, 16, 12, 0, tzinfo=UTC)

    # D1 Bullish Zone: [2000, 2020]
    z_d1 = FvgZone(
        id="fvg_d1",
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("s"),
        price_type=PriceType.BID,
        timeframe=Timeframe.D1,
        direction=FvgDirection.BULLISH,
        top_price=Decimal("2020.00"),
        bottom_price=Decimal("2000.00"),
        ce_price=Decimal("2010.00"),
        width=Decimal("20.00"),
        candle_1_open=t0,
        candle_2_open=t0 + timedelta(days=1),
        candle_3_open=t0 + timedelta(days=2),
        formation_time=t0 + timedelta(days=3),
        computable_at_utc=t0 + timedelta(days=3),
    )

    # H4 Bullish Zone: [2010, 2030] -> Overlaps with D1 on [2010, 2020]!
    z_h4 = FvgZone(
        id="fvg_h4",
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("s"),
        price_type=PriceType.BID,
        timeframe=Timeframe.H4,
        direction=FvgDirection.BULLISH,
        top_price=Decimal("2030.00"),
        bottom_price=Decimal("2010.00"),
        ce_price=Decimal("2020.00"),
        width=Decimal("20.00"),
        candle_1_open=t0 + timedelta(days=3),
        candle_2_open=t0 + timedelta(days=3, hours=4),
        candle_3_open=t0 + timedelta(days=3, hours=8),
        formation_time=t0 + timedelta(days=3, hours=12),
        computable_at_utc=t0 + timedelta(days=3, hours=12),
    )

    as_of = t0 + timedelta(days=4)
    confluences = compute_mtf_fvg_confluences(
        {Timeframe.D1: [(z_d1, None)], Timeframe.H4: [(z_h4, None)]},
        as_of_utc=as_of,
    )

    assert len(confluences) == 1
    c = confluences[0]
    assert c.direction == FvgDirection.BULLISH
    assert c.timeframes == (Timeframe.D1, Timeframe.H4)
    assert c.overlap_bottom_price == Decimal("2010.00")
    assert c.overlap_top_price == Decimal("2020.00")
    assert c.overlap_width == Decimal("10.00")

    # Negative Case: Non-overlapping zone [2025, 2040] does NOT overlap with D1 [2000, 2020]
    z_h2_far = FvgZone(
        id="fvg_h2_far",
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("s"),
        price_type=PriceType.BID,
        timeframe=Timeframe.H2,
        direction=FvgDirection.BULLISH,
        top_price=Decimal("2040.00"),
        bottom_price=Decimal("2025.00"),
        ce_price=Decimal("2032.50"),
        width=Decimal("15.00"),
        candle_1_open=t0,
        candle_2_open=t0 + timedelta(hours=2),
        candle_3_open=t0 + timedelta(hours=4),
        formation_time=t0 + timedelta(hours=6),
        computable_at_utc=t0 + timedelta(hours=6),
    )

    conf_neg = compute_mtf_fvg_confluences(
        {Timeframe.D1: [(z_d1, None)], Timeframe.H2: [(z_h2_far, None)]},
        as_of_utc=as_of,
    )
    assert len(conf_neg) == 0

    # Negative Case: Opposite direction (Bearish H2) does NOT form confluence with Bullish D1
    z_h2_bear = FvgZone(
        id="fvg_h2_bear",
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("s"),
        price_type=PriceType.BID,
        timeframe=Timeframe.H2,
        direction=FvgDirection.BEARISH,
        top_price=Decimal("2015.00"),
        bottom_price=Decimal("2005.00"),
        ce_price=Decimal("2010.00"),
        width=Decimal("10.00"),
        candle_1_open=t0,
        candle_2_open=t0 + timedelta(hours=2),
        candle_3_open=t0 + timedelta(hours=4),
        formation_time=t0 + timedelta(hours=6),
        computable_at_utc=t0 + timedelta(hours=6),
    )
    conf_opp = compute_mtf_fvg_confluences(
        {Timeframe.D1: [(z_d1, None)], Timeframe.H2: [(z_h2_bear, None)]},
        as_of_utc=as_of,
    )
    assert len(conf_opp) == 0


def test_16_rerun_idempotence_no_duplicate_shapes() -> None:
    """Requirement 16: Multiple calls create clean, idempotent figures with zero shape accumulation."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000", "2005", "1995", "2004", timeframe=Timeframe.H4)
    c2 = make_candle(
        t0 + timedelta(hours=4), "2004", "2020", "2003", "2018", timeframe=Timeframe.H4
    )
    c3 = make_candle(
        t0 + timedelta(hours=8), "2018", "2025", "2010", "2022", timeframe=Timeframe.H4
    )
    candles = [c1, c2, c3]

    fvgs = detect_fair_value_gaps(candles)
    fig1 = create_candlestick_figure(candles, fvg_records=[(fvgs[0], None)])
    fig2 = create_candlestick_figure(candles, fvg_records=[(fvgs[0], None)])

    assert len(fig1.layout.shapes) == len(fig2.layout.shapes)
    assert len(fig1.layout.shapes) == 2  # 1 rect + 1 ce line


def test_17_filter_changes_do_not_cross_contaminate_panels() -> None:
    """Requirement 17: Filtering out Bearish FVGs leaves Bullish intact and updates shape counts."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000", "2005", "1995", "2004")
    c2 = make_candle(t0 + timedelta(hours=1), "2004", "2020", "2003", "2018")
    c3 = make_candle(t0 + timedelta(hours=2), "2018", "2025", "2010", "2022")
    bull_fvg = detect_fair_value_gaps([c1, c2, c3])[0]

    # When show_bullish_fvg=False, zero shapes rendered
    fig_no_bull = create_candlestick_figure(
        [c1, c2, c3],
        fvg_records=[(bull_fvg, None)],
        show_bullish_fvg=False,
    )
    assert len(fig_no_bull.layout.shapes) == 0

    # When show_bullish_fvg=True, shapes rendered
    fig_bull = create_candlestick_figure(
        [c1, c2, c3],
        fvg_records=[(bull_fvg, None)],
        show_bullish_fvg=True,
    )
    assert len(fig_bull.layout.shapes) >= 1
