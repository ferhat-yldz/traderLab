"""Acceptance test suite for FAZ 2C-A: FVG & IFVG Chart Integration.

Validates the 12 mandatory criteria from Faz 2C-A specification:
1. Bullish FVG drawn at correct price boundaries.
2. Bearish FVG drawn at correct price boundaries.
3. Consequent Encroachment (CE) line positioned at exact 50% midpoint.
4. Inversion FVG (IFVG) rendered with distinct dashed styling and color.
5. FVG zone begins strictly upon candle 3 completion (never before formation).
6. Point-in-Time: events occurring after as_of_utc are excluded.
7. BID and ASK isolation.
8. Support across D1, H4, and H2 timeframes.
9. Legacy chart appearance preserved when FVG overlay is disabled.
10. Idempotent figure creation: repeated executions do not duplicate shapes.
11. Event table records exactly match overlaid chart records.
12. Data quality and unclosed bar warnings preserved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.fvg import (
    FvgFillState,
    FvgValidityState,
    detect_fair_value_gaps,
    track_fvg_lifecycle,
)
from traderlab.interfaces.dashboard.app import compute_fvg_records_for_view
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
) -> Candle:
    """Helper to construct valid domain Candle."""
    delta = timedelta(seconds=timeframe.value)
    return Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("chart_test"),
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


def test_01_bullish_fvg_drawn_at_correct_price_range() -> None:
    """Criterion 1: Bullish FVG drawn at exact [bottom_price, top_price] with mint green styling."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000.00", "2005.00", "1998.00", "2004.00")
    c2 = make_candle(t0 + timedelta(hours=1), "2004.00", "2020.00", "2003.00", "2018.00")
    c3 = make_candle(t0 + timedelta(hours=2), "2018.00", "2025.00", "2010.00", "2022.00")
    candles = [c1, c2, c3]

    fvgs = detect_fair_value_gaps(candles)
    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.is_bullish
    assert fvg.bottom_price == Decimal("2005.00")  # candle_1.high
    assert fvg.top_price == Decimal("2010.00")  # candle_3.low

    fig = create_candlestick_figure(
        candles,
        fvg_records=[(fvg, None)],
        show_bullish_fvg=True,
    )

    rect_shapes = [s for s in fig.layout.shapes if s.type == "rect"]
    assert len(rect_shapes) == 1
    rect = rect_shapes[0]
    assert rect.y0 == 2005.0
    assert rect.y1 == 2010.0
    assert "38, 166, 154" in str(rect.fillcolor)  # Mint green hue


def test_02_bearish_fvg_drawn_at_correct_price_range() -> None:
    """Criterion 2: Bearish FVG drawn at exact [bottom_price, top_price] with crimson red styling."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2030.00", "2032.00", "2025.00", "2026.00")
    c2 = make_candle(t0 + timedelta(hours=1), "2026.00", "2027.00", "2005.00", "2008.00")
    c3 = make_candle(t0 + timedelta(hours=2), "2008.00", "2015.00", "2006.00", "2012.00")
    candles = [c1, c2, c3]

    fvgs = detect_fair_value_gaps(candles)
    assert len(fvgs) == 1
    fvg = fvgs[0]
    assert fvg.is_bearish
    assert fvg.bottom_price == Decimal("2015.00")  # candle_3.high
    assert fvg.top_price == Decimal("2025.00")  # candle_1.low

    fig = create_candlestick_figure(
        candles,
        fvg_records=[(fvg, None)],
        show_bearish_fvg=True,
    )

    rect_shapes = [s for s in fig.layout.shapes if s.type == "rect"]
    assert len(rect_shapes) == 1
    rect = rect_shapes[0]
    assert rect.y0 == 2015.0
    assert rect.y1 == 2025.0
    assert "239, 83, 80" in str(rect.fillcolor)  # Crimson red hue


def test_03_ce_line_positioned_at_exact_midpoint() -> None:
    """Criterion 3: 50% Consequent Encroachment line shape positioned at exact midpoint."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000.00", "2010.00", "1998.00", "2008.00")
    c2 = make_candle(t0 + timedelta(hours=1), "2008.00", "2030.00", "2007.00", "2028.00")
    c3 = make_candle(t0 + timedelta(hours=2), "2028.00", "2035.00", "2020.00", "2032.00")
    candles = [c1, c2, c3]

    fvg = detect_fair_value_gaps(candles)[0]
    expected_ce = (Decimal("2010.00") + Decimal("2020.00")) / Decimal(2)  # 2015.00
    assert fvg.ce_price == expected_ce

    fig = create_candlestick_figure(
        candles,
        fvg_records=[(fvg, None)],
        show_ce_line=True,
    )

    line_shapes = [s for s in fig.layout.shapes if s.type == "line"]
    assert len(line_shapes) == 1
    assert line_shapes[0].y0 == 2015.0
    assert line_shapes[0].y1 == 2015.0


def test_04_ifvg_rendered_with_distinct_dashed_styling() -> None:
    """Criterion 4: Inversion FVG (IFVG) rendered with dashed border and purple styling."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000.00", "2005.00", "1998.00", "2004.00")
    c2 = make_candle(t0 + timedelta(hours=1), "2004.00", "2020.00", "2003.00", "2018.00")
    c3 = make_candle(t0 + timedelta(hours=2), "2018.00", "2025.00", "2010.00", "2022.00")
    fvg = detect_fair_value_gaps([c1, c2, c3])[0]

    # Subsequent candles: invalidation below 2005, followed by retest (IFVG confirmation)
    c4 = make_candle(t0 + timedelta(hours=3), "2022.00", "2023.00", "2001.00", "2002.00")
    c5 = make_candle(t0 + timedelta(hours=4), "2002.00", "2008.00", "2001.00", "2004.00")
    candles = [c1, c2, c3, c4, c5]

    events = track_fvg_lifecycle(fvg, candles)
    last_event = events[-1]
    assert last_event.validity_state == FvgValidityState.INVERTED

    fig = create_candlestick_figure(
        candles,
        fvg_records=[(fvg, last_event)],
        show_ifvg=True,
    )

    rect_shapes = [s for s in fig.layout.shapes if s.type == "rect"]
    assert len(rect_shapes) == 1
    rect = rect_shapes[0]
    assert rect.line.dash == "dash"
    assert rect.line.color == "#a855f7"  # Distinct purple IFVG marker


def test_05_fvg_not_drawn_before_third_candle_closes() -> None:
    """Criterion 5: FVG zone begins strictly upon candle 3 completion (x0 == candle_3.close_time)."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000.00", "2005.00", "1998.00", "2004.00")
    c2 = make_candle(t0 + timedelta(hours=1), "2004.00", "2020.00", "2003.00", "2018.00")
    c3 = make_candle(t0 + timedelta(hours=2), "2018.00", "2025.00", "2010.00", "2022.00")
    c4 = make_candle(t0 + timedelta(hours=3), "2022.00", "2024.00", "2019.00", "2023.00")
    candles = [c1, c2, c3, c4]

    fvg = detect_fair_value_gaps(candles)[0]
    expected_formation = c3.close_time  # 13:00:00 UTC
    assert fvg.formation_time == expected_formation

    fig = create_candlestick_figure(
        candles,
        fvg_records=[(fvg, None)],
    )

    rect = next(s for s in fig.layout.shapes if s.type == "rect")
    # x0 must match formation_time (13:00), NEVER 10:00, 11:00, or 12:00
    assert rect.x0 == expected_formation
    assert rect.x0 != c1.open_time
    assert rect.x0 != c2.open_time
    assert rect.x0 != c3.open_time


def test_06_events_after_end_time_not_displayed() -> None:
    """Criterion 6: Point-in-Time cutoff: events occurring after as_of_utc are not reflected."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000.00", "2005.00", "1998.00", "2004.00")
    c2 = make_candle(t0 + timedelta(hours=1), "2004.00", "2020.00", "2003.00", "2018.00")
    c3 = make_candle(t0 + timedelta(hours=2), "2018.00", "2025.00", "2010.00", "2022.00")
    # Later candle piercing the zone at 14:00
    c4 = make_candle(t0 + timedelta(hours=3), "2022.00", "2023.00", "2006.00", "2015.00")
    candles = [c1, c2, c3, c4]

    fvg = detect_fair_value_gaps(candles)[0]

    # Evaluate lifecycle at as_of = 13:00 (right after formation, before c4)
    as_of = c3.close_time
    events_at_13 = track_fvg_lifecycle(fvg, candles, as_of_utc=as_of)
    assert len(events_at_13) == 0  # No events prior to or at 13:00

    # Evaluate lifecycle at as_of = 14:00 (includes c4)
    events_at_14 = track_fvg_lifecycle(fvg, candles, as_of_utc=c4.close_time)
    assert len(events_at_14) == 1
    assert events_at_14[0].fill_state == FvgFillState.CE_REACHED


def test_07_bid_ask_isolation() -> None:
    """Criterion 7: BID and ASK candles are resampled and analyzed in strict isolation."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    bid_candles = [
        make_candle(t0, "2000.00", "2005.00", "1998.00", "2004.00", price_type=PriceType.BID),
        make_candle(
            t0 + timedelta(hours=1),
            "2004.00",
            "2020.00",
            "2003.00",
            "2018.00",
            price_type=PriceType.BID,
        ),
        make_candle(
            t0 + timedelta(hours=2),
            "2018.00",
            "2025.00",
            "2010.00",
            "2022.00",
            price_type=PriceType.BID,
        ),
    ]

    ask_candles = [
        make_candle(t0, "2000.50", "2005.50", "1998.50", "2004.50", price_type=PriceType.ASK),
        make_candle(
            t0 + timedelta(hours=1),
            "2004.50",
            "2020.50",
            "2003.50",
            "2018.50",
            price_type=PriceType.ASK,
        ),
        make_candle(
            t0 + timedelta(hours=2),
            "2018.50",
            "2025.50",
            "2010.50",
            "2022.50",
            price_type=PriceType.ASK,
        ),
    ]

    bid_fvgs = detect_fair_value_gaps(bid_candles)
    ask_fvgs = detect_fair_value_gaps(ask_candles)

    assert len(bid_fvgs) == 1
    assert len(ask_fvgs) == 1
    assert bid_fvgs[0].price_type == PriceType.BID
    assert ask_fvgs[0].price_type == PriceType.ASK
    assert bid_fvgs[0].bottom_price == Decimal("2005.00")
    assert ask_fvgs[0].bottom_price == Decimal("2005.50")


def test_08_d1_h4_h2_support() -> None:
    """Criterion 8: FVG overlays supported across D1, H4, and H2 timeframes."""
    t0 = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)

    for tf in (Timeframe.H2, Timeframe.H4, Timeframe.D1):
        step = timedelta(seconds=tf.value)
        c1 = make_candle(t0, "2000.00", "2005.00", "1998.00", "2004.00", timeframe=tf)
        c2 = make_candle(t0 + step, "2004.00", "2020.00", "2003.00", "2018.00", timeframe=tf)
        c3 = make_candle(t0 + step * 2, "2018.00", "2025.00", "2010.00", "2022.00", timeframe=tf)
        candles = [c1, c2, c3]

        fvgs = detect_fair_value_gaps(candles)
        assert len(fvgs) == 1
        assert fvgs[0].timeframe == tf

        fig = create_candlestick_figure(
            candles,
            timeframe=tf,
            fvg_records=[(fvgs[0], None)],
        )
        assert len([s for s in fig.layout.shapes if s.type == "rect"]) == 1


def test_09_legacy_chart_look_preserved_when_fvg_disabled() -> None:
    """Criterion 9: When FVG overlay is disabled (None), chart appearance is 100% identical to legacy."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000.00", "2005.00", "1998.00", "2004.00")
    c2 = make_candle(t0 + timedelta(hours=1), "2004.00", "2020.00", "2003.00", "2018.00")
    c3 = make_candle(t0 + timedelta(hours=2), "2018.00", "2025.00", "2010.00", "2022.00")
    candles = [c1, c2, c3]

    # Legacy figure without fvg_records parameter
    fig_legacy = create_candlestick_figure(candles)
    # Figure with fvg_records=None explicitly
    fig_fvg_none = create_candlestick_figure(candles, fvg_records=None)

    assert len(fig_legacy.layout.shapes) == 0
    assert len(fig_fvg_none.layout.shapes) == 0
    assert len(fig_legacy.data) == len(fig_fvg_none.data)


def test_10_idempotent_figure_creation_no_duplicate_shapes() -> None:
    """Criterion 10: Repeated figure creation or function calls do not multiply or duplicate shapes."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000.00", "2005.00", "1998.00", "2004.00")
    c2 = make_candle(t0 + timedelta(hours=1), "2004.00", "2020.00", "2003.00", "2018.00")
    c3 = make_candle(t0 + timedelta(hours=2), "2018.00", "2025.00", "2010.00", "2022.00")
    candles = [c1, c2, c3]
    fvg = detect_fair_value_gaps(candles)[0]

    fig1 = create_candlestick_figure(candles, fvg_records=[(fvg, None)])
    fig2 = create_candlestick_figure(candles, fvg_records=[(fvg, None)])

    assert len(fig1.layout.shapes) == len(fig2.layout.shapes)
    assert len(fig1.data) == len(fig2.data)


def test_11_event_table_records_match_chart_items() -> None:
    """Criterion 11: Records returned by compute_fvg_records_for_view exactly match those drawn."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000.00", "2005.00", "1998.00", "2004.00")
    c2 = make_candle(t0 + timedelta(hours=1), "2004.00", "2020.00", "2003.00", "2018.00")
    c3 = make_candle(t0 + timedelta(hours=2), "2018.00", "2025.00", "2010.00", "2022.00")
    candles = [c1, c2, c3]

    view_records = compute_fvg_records_for_view(
        candles,
        visible_start_utc=t0,
        visible_end_utc=t0 + timedelta(hours=3),
        as_of_utc=t0 + timedelta(hours=3),
    )
    assert len(view_records) == 1
    fvg, _ev = view_records[0]

    fig = create_candlestick_figure(candles, fvg_records=view_records)
    rects = [s for s in fig.layout.shapes if s.type == "rect"]
    assert len(rects) == 1
    assert rects[0].y0 == float(fvg.bottom_price)
    assert rects[0].y1 == float(fvg.top_price)


def test_12_data_quality_unclosed_bar_rejection_preserved() -> None:
    """Criterion 12: Unclosed or partial candles cannot form FVGs."""
    t0 = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    c1 = make_candle(t0, "2000.00", "2005.00", "1998.00", "2004.00")
    c2 = make_candle(t0 + timedelta(hours=1), "2004.00", "2020.00", "2003.00", "2018.00")
    # c3 is unclosed (forming / live)
    c3 = make_candle(
        t0 + timedelta(hours=2), "2018.00", "2025.00", "2010.00", "2022.00", is_closed=False
    )

    fvgs = detect_fair_value_gaps([c1, c2, c3], require_closed=True)
    assert len(fvgs) == 0  # Strict rejection: no FVG formed by incomplete bar
