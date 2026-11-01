"""Plotly candlestick chart builder for TRADERLAB financial dashboard."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import plotly.graph_objects as go

from traderlab.core.types import Timeframe
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.fvg import (
    FvgDirection,
    FvgFillState,
    FvgLifecycleEvent,
    FvgValidityState,
    FvgZone,
)
from traderlab.interfaces.dashboard.fvg_view_filter import (
    FvgVisibilityMode,
    calculate_effective_direction,
    filter_fvg_records_for_view,
)


def create_candlestick_figure(
    candles: Sequence[Candle],
    title: str = "XAUUSD Candlestick Chart",
    *,
    chart_type: str = "Candlestick",
    height: int = 580,
    show_synthetic_markers: bool = True,
    empty_message: str = "Seçilen tarih aralığı için yerel veri bulunamadı.<br>Yerel depoda 2024 (Tüm Aylar), 2025 (Tüm Aylar) ve 2026-10 mevcuttur.",
    initial_range: tuple[datetime, datetime] | None = None,
    now_utc: datetime | None = None,
    timeframe: Timeframe | None = None,
    range_preset: str | None = None,
    show_rangeselector: bool = False,
    fvg_records: Sequence[tuple[FvgZone, FvgLifecycleEvent | None]] | None = None,
    show_bullish_fvg: bool = True,
    show_bearish_fvg: bool = True,
    show_ce_line: bool = True,
    show_ifvg: bool = True,
    active_fvg_only: bool = True,
    max_fvg_zones: int = 16,
    fvg_visibility_mode: FvgVisibilityMode | str = FvgVisibilityMode.PROFESSIONAL,
    selected_fvg_id: str | None = None,
    as_of_utc: datetime | None = None,
) -> go.Figure:
    """Construct an interactive TradingView-styled Plotly financial chart.

    Aesthetics & Behavior:
    - Dark mode background (#131722 / #171b26)
    - Supports both Candlestick and Line Chart (Close Price) modes
    - Mint green bullish candles (#26a69a), crimson red bearish candles (#ef5350)
    - Modern gradient area fill for line chart mode (#2962ff)
    - Real timezone-aware datetime x-axis (type="date")
    - Adaptive tick formatting (%H:%M for intraday, %b %d for multi-day, %b %Y for multi-month)
    - Crosshair spike lines enabled across x and y axes
    - Synthetic / market-closed bars subtly annotated (gold diamond/circle)
    - Distinguishes historical incomplete candles from in-flight live forming candles
    - Strict safeguard: Synthetic bars are NEVER falsely marked as missing historical data
    - Smooth zoom, pan, and responsive height configuration
    """
    if not candles:
        fig = go.Figure()
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="#131722",
            plot_bgcolor="#1e222d",
            title=dict(text=f"{title} (No Data Available)", font=dict(color="#d1d4dc", size=16)),
            xaxis=dict(showgrid=False, zeroline=False),
            yaxis=dict(showgrid=False, zeroline=False),
            annotations=[
                dict(
                    text=empty_message,
                    xref="paper",
                    yref="paper",
                    x=0.5,
                    y=0.5,
                    showarrow=False,
                    font=dict(size=15, color="#f59e0b"),
                )
            ],
        )
        return fig

    # Extract OHLC arrays using native datetime objects
    dates = [c.open_time for c in candles]
    opens = [float(c.open) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    closes = [float(c.close) for c in candles]

    fig = go.Figure()

    # Primary series: Candlestick or Line Chart (Close Price)
    if chart_type == "Çizgi Grafik (Close Price)":
        is_up = closes[-1] >= closes[0]
        line_color = "#26a69a" if is_up else "#ef5350"
        fill_color = "rgba(38, 166, 154, 0.12)" if is_up else "rgba(239, 83, 80, 0.12)"

        # Dynamic price baseline to keep area fill without forcing Y axis to 0
        min_p = min(closes)
        max_p = max(closes)
        pad = (max_p - min_p) * 0.08 if max_p > min_p else float(min_p) * 0.005
        baseline_y = min_p - pad
        is_daily_or_higher = (
            timeframe in (Timeframe.D1, Timeframe.W1)
            or range_preset in ("1Y", "5Y", "TÜMÜ", "YTD")
            or (candles and candles[0].timeframe in (Timeframe.D1, Timeframe.W1))
        )
        hover_fmt = "%d %b %Y" if is_daily_or_higher else "%Y-%m-%d %H:%M UTC"

        # Invisible baseline trace for area shading
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=[baseline_y] * len(dates),
                mode="lines",
                line=dict(width=0),
                showlegend=False,
                hoverinfo="skip",
            )
        )
        # Main line trace with fill to baseline
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=closes,
                mode="lines",
                name="Kapanış Fiyatı (Close)",
                line=dict(color=line_color, width=2.5),
                fill="tonexty",
                fillcolor=fill_color,
                xhoverformat=hover_fmt,
                hoverlabel=dict(
                    bgcolor="#1e222d",
                    font_size=12,
                    font_family="Roboto, Inter, sans-serif",
                ),
            )
        )
    else:
        is_daily_or_higher = (
            timeframe in (Timeframe.D1, Timeframe.W1)
            or range_preset in ("1Y", "5Y", "TÜMÜ", "YTD")
            or (candles and candles[0].timeframe in (Timeframe.D1, Timeframe.W1))
        )
        hover_fmt = "%d %b %Y" if is_daily_or_higher else "%Y-%m-%d %H:%M UTC"

        # Standard OHLC Candlestick trace with datetime x-axis
        fig.add_trace(
            go.Candlestick(
                x=dates,
                open=opens,
                high=highs,
                low=lows,
                close=closes,
                name="XAUUSD",
                increasing=dict(
                    line=dict(color="#26a69a", width=1),
                    fillcolor="#26a69a",
                ),
                decreasing=dict(
                    line=dict(color="#ef5350", width=1),
                    fillcolor="#ef5350",
                ),
                xhoverformat=hover_fmt,
                hoverlabel=dict(
                    bgcolor="#1e222d",
                    font_size=12,
                    font_family="Roboto, Inter, sans-serif",
                ),
            )
        )

    # Highlight synthetic / carry-forward closed-market bars
    if show_synthetic_markers:
        synthetic_indices = [i for i, c in enumerate(candles) if c.is_synthetic]
        if synthetic_indices:
            synth_dates = [dates[i] for i in synthetic_indices]
            synth_prices = [highs[i] for i in synthetic_indices]
            fig.add_trace(
                go.Scatter(
                    x=synth_dates,
                    y=synth_prices,
                    mode="markers",
                    name="Piyasa Kapalı / Sentetik Bar",
                    marker=dict(
                        symbol="circle-open",
                        size=6,
                        color="#f59e0b",
                        line=dict(width=1.5, color="#f59e0b"),
                    ),
                    hoverinfo="text",
                    hovertext=[
                        f"Piyasa Kapalı (Carry-Forward): {dates[i].strftime('%Y-%m-%d %H:%M UTC')}"
                        for i in synthetic_indices
                    ],
                )
            )

    # Highlight genuine unclosed / partial candles with historical vs live distinction
    # Strict safeguard: Synthetic carry-forward closed bars are NEVER marked as missing historical data!
    current_time = now_utc if now_utc is not None else datetime.now(tz=UTC)
    unclosed_indices = [i for i, c in enumerate(candles) if not c.is_closed and not c.is_synthetic]
    if unclosed_indices:
        hist_dates, hist_prices, hist_texts = [], [], []
        live_dates, live_prices, live_texts = [], [], []

        for i in unclosed_indices:
            c = candles[i]
            is_historical = current_time >= c.close_time
            if is_historical:
                hist_dates.append(c.open_time)
                hist_prices.append(float(c.high))
                hist_texts.append(
                    f"Kaynak Verisi Eksik Tarihsel Mum ({c.open_time.strftime('%H:%M')} - "
                    f"{c.close_time.strftime('%H:%M')} UTC)<br>Takvim penceresi kapandı, veri eksik"
                )
            else:
                live_dates.append(c.open_time)
                live_prices.append(float(c.high))
                live_texts.append(
                    f"Oluşmakta Olan Canlı Mum ({c.open_time.strftime('%H:%M')} - "
                    f"{c.close_time.strftime('%H:%M')} UTC)<br>İşlem seansı devam ediyor"
                )

        if hist_dates:
            fig.add_trace(
                go.Scatter(
                    x=hist_dates,
                    y=hist_prices,
                    mode="markers+text" if len(hist_dates) <= 4 else "markers",
                    name="Kaynak Verisi Eksik Tarihsel Mum",
                    text=["Eksik" for _ in hist_dates] if len(hist_dates) <= 4 else None,
                    textposition="top center",
                    textfont=dict(color="#f97316", size=10),
                    marker=dict(
                        symbol="triangle-down",
                        size=9,
                        color="#f97316",
                        line=dict(width=1, color="#ffffff"),
                    ),
                    hoverinfo="text",
                    hovertext=hist_texts,
                )
            )

        if live_dates:
            fig.add_trace(
                go.Scatter(
                    x=live_dates,
                    y=live_prices,
                    mode="markers+text" if len(live_dates) <= 4 else "markers",
                    name="Oluşmakta Olan Canlı Mum",
                    text=["Canlı" for _ in live_dates] if len(live_dates) <= 4 else None,
                    textposition="top center",
                    textfont=dict(color="#3b82f6", size=10),
                    marker=dict(
                        symbol="triangle-down",
                        size=9,
                        color="#3b82f6",
                        line=dict(width=1, color="#ffffff"),
                    ),
                    hoverinfo="text",
                    hovertext=live_texts,
                )
            )

    # Configure X-Axis with real datetime formatting and adaptive tick spacing
    xaxis_layout: dict[str, Any] = dict(
        type="date",
        rangeslider=dict(visible=False),
        showgrid=True,
        gridcolor="#2a2e39",
        zeroline=False,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikedash="dot",
        spikecolor="#758696",
        spikethickness=1,
        tickfont=dict(color="#787b86", size=11),
    )

    # Adaptive TradingView tick formatting based on range preset and timeframe
    if range_preset in ("5Y", "TÜMÜ"):
        xaxis_layout["tickformat"] = "%Y"
        xaxis_layout["tickformatstops"] = [
            dict(dtickrange=[None, 1000 * 60 * 60 * 24 * 90], value="%b %Y"),
            dict(dtickrange=[1000 * 60 * 60 * 24 * 90, None], value="%Y"),
        ]
    elif range_preset in ("1Y", "YTD"):
        xaxis_layout["tickformat"] = "%b %Y"
        xaxis_layout["tickformatstops"] = [
            dict(dtickrange=[None, 1000 * 60 * 60 * 24 * 14], value="%d %b"),
            dict(dtickrange=[1000 * 60 * 60 * 24 * 14, None], value="%b %Y"),
        ]
    elif range_preset == "6A":
        xaxis_layout["tickformat"] = "%b %Y"
        xaxis_layout["tickformatstops"] = [
            dict(dtickrange=[None, 1000 * 60 * 60 * 24 * 7], value="%d %b"),
            dict(dtickrange=[1000 * 60 * 60 * 24 * 7, None], value="%b %Y"),
        ]
    elif range_preset in ("1A", "3A"):
        xaxis_layout["tickformat"] = "%b %d"
        xaxis_layout["tickformatstops"] = [
            dict(dtickrange=[None, 1000 * 60 * 60 * 24], value="%b %d"),
            dict(dtickrange=[1000 * 60 * 60 * 24, None], value="%b %d"),
        ]
    elif range_preset == "5G":
        xaxis_layout["tickformatstops"] = [
            dict(dtickrange=[None, 1000 * 60 * 60 * 12], value="%H:%M"),
            dict(dtickrange=[1000 * 60 * 60 * 12, None], value="%d %b %H:%M"),
        ]
    elif range_preset == "1G":
        xaxis_layout["tickformat"] = "%H:%M"
        xaxis_layout["tickformatstops"] = [
            dict(dtickrange=[None, None], value="%H:%M"),
        ]
    elif is_daily_or_higher:
        xaxis_layout["tickformatstops"] = [
            dict(dtickrange=[None, 1000 * 60 * 60 * 24 * 30], value="%d %b %Y"),
            dict(dtickrange=[1000 * 60 * 60 * 24 * 30, 1000 * 60 * 60 * 24 * 365], value="%b %Y"),
            dict(dtickrange=[1000 * 60 * 60 * 24 * 365, None], value="%Y"),
        ]
    else:
        xaxis_layout["tickformatstops"] = [
            dict(dtickrange=[None, 1000 * 60 * 60 * 24], value="%H:%M"),
            dict(dtickrange=[1000 * 60 * 60 * 24, 1000 * 60 * 60 * 24 * 7], value="%d %b %H:%M"),
            dict(dtickrange=[1000 * 60 * 60 * 24 * 7, 1000 * 60 * 60 * 24 * 35], value="%d %b"),
            dict(dtickrange=[1000 * 60 * 60 * 24 * 35, 1000 * 60 * 60 * 24 * 180], value="%b %Y"),
            dict(dtickrange=[1000 * 60 * 60 * 24 * 180, None], value="%Y"),
        ]

    # Set initial range for readable zoom
    if initial_range is not None:
        xaxis_layout["range"] = [initial_range[0], initial_range[1]]
    elif (
        (range_preset == "1G" or range_preset is None)
        and len(candles) >= 720
        and not is_daily_or_higher
    ):
        # High density 1-day M1 view (1,440 bars): zoom into active 4-hour window by default
        mid_idx = len(candles) // 2
        r_start = candles[max(0, mid_idx - 120)].open_time
        r_end = candles[min(len(candles) - 1, mid_idx + 120)].open_time
        xaxis_layout["range"] = [r_start, r_end]

    # Layout styling matching professional charting terminals
    if show_rangeselector:
        xaxis_layout["rangeselector"] = dict(
            buttons=[
                dict(count=1, label="1G", step="day", stepmode="backward"),
                dict(count=5, label="5G", step="day", stepmode="backward"),
                dict(count=1, label="1A", step="month", stepmode="backward"),
                dict(count=3, label="3A", step="month", stepmode="backward"),
                dict(count=6, label="6A", step="month", stepmode="backward"),
                dict(count=1, label="YTD", step="year", stepmode="todate"),
                dict(count=1, label="1Y", step="year", stepmode="backward"),
                dict(count=5, label="5Y", step="year", stepmode="backward"),
                dict(step="all", label="TÜMÜ"),
            ],
            bgcolor="#171b26",
            activecolor="#2962ff",
            font=dict(color="#d1d4dc", size=10),
            x=0.0,
            y=-0.16,
            xanchor="left",
            yanchor="top",
        )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#131722",
        plot_bgcolor="#171b26",
        height=height,
        uirevision="constant",
        title=dict(
            text=title,
            font=dict(color="#e0e3eb", size=16, family="Inter, Roboto, sans-serif"),
            x=0.02,
            y=0.96,
        ),
        margin=dict(l=50, r=50, t=50, b=60),
        xaxis=xaxis_layout,
        yaxis=dict(
            title=dict(text="Fiyat (USD)", font=dict(color="#787b86", size=12)),
            side="right",
            showgrid=True,
            gridcolor="#2a2e39",
            zeroline=False,
            showspikes=True,
            spikemode="across",
            spikesnap="cursor",
            spikedash="dot",
            spikecolor="#758696",
            spikethickness=1,
            tickfont=dict(color="#787b86", size=11),
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=0.98,
            font=dict(color="#b2b5be", size=11),
        ),
        hovermode="closest" if fvg_records else "x unified",
    )

    if fvg_records:
        current_price = Decimal(str(closes[-1])) if closes else None
        p_min = Decimal(str(min(lows))) if lows else None
        p_max = Decimal(str(max(highs))) if highs else None

        if isinstance(fvg_visibility_mode, str):
            try:
                vis_mode = FvgVisibilityMode(fvg_visibility_mode)
            except ValueError:
                vis_mode = FvgVisibilityMode.PROFESSIONAL
        else:
            vis_mode = fvg_visibility_mode

        if not active_fvg_only and vis_mode == FvgVisibilityMode.PROFESSIONAL:
            vis_mode = FvgVisibilityMode.ALL_HISTORICAL

        view_res = filter_fvg_records_for_view(
            fvg_records,
            current_price=current_price,
            mode=vis_mode,
            max_zones=max_fvg_zones,
            selected_fvg_id=selected_fvg_id,
            show_bullish=show_bullish_fvg,
            show_bearish=show_bearish_fvg,
            show_ifvg=show_ifvg,
            as_of_utc=as_of_utc,
            price_min=p_min,
            price_max=p_max,
        )

        add_fvg_overlays(
            fig,
            view_res.visible_records,
            candles,
            show_bullish=show_bullish_fvg,
            show_bearish=show_bearish_fvg,
            show_ce_line=show_ce_line,
            show_ifvg=show_ifvg,
            active_only=active_fvg_only,
            max_zones=max_fvg_zones,
            as_of_utc=as_of_utc,
        )

    return fig


def add_fvg_overlays(
    fig: go.Figure,
    fvg_records: Sequence[tuple[FvgZone, FvgLifecycleEvent | None]],
    candles: Sequence[Candle],
    *,
    show_bullish: bool = True,
    show_bearish: bool = True,
    show_ce_line: bool = True,
    show_ifvg: bool = True,
    active_only: bool = True,
    max_zones: int = 50,
    as_of_utc: datetime | None = None,
) -> go.Figure:
    """Add Fair Value Gap (FVG) and Inversion FVG (IFVG) overlays to a Plotly candlestick figure.

    Visual & Quantitative Architecture:
    - Bullish FVG: Soft mint translucent price box (rgba(38, 166, 154, 0.10))
    - Bearish FVG: Soft red translucent price box (rgba(239, 83, 80, 0.10))
    - Consequent Encroachment (CE): 50% midpoint horizontal line (width=1.0, subtle opacity)
    - Inversion FVG (IFVG): Distinct dashed border and purple tint (rgba(168, 85, 247, 0.09))
    - Point-in-Time Timing: Zone strictly begins at fvg.formation_time (candle_3.close_time),
      never rendered before the 3rd candle closes.
    - Interactive Tooltips: Clean, vertical metadata displaying zone bounds, CE, width, fill state,
      validity state, and inversion details without obstructing price action.
    """
    if not fvg_records or not candles:
        return fig

    last_candle_close = candles[-1].close_time

    # Filter records based on user preferences
    filtered_records: list[tuple[FvgZone, FvgLifecycleEvent | None]] = []
    for fvg, event in fvg_records:
        if fvg.is_bullish and not show_bullish:
            continue
        if fvg.is_bearish and not show_bearish:
            continue

        is_inverted = event is not None and event.validity_state == FvgValidityState.INVERTED
        if is_inverted and not show_ifvg:
            continue

        if (
            active_only
            and event is not None
            and event.validity_state == FvgValidityState.INVALIDATED
        ):
            continue

        filtered_records.append((fvg, event))

    if len(filtered_records) > max_zones:
        filtered_records = filtered_records[-max_zones:]

    # Data structures for category hover traces and batch shapes
    cat_data: dict[str, dict[str, list[Any]]] = {
        "Bullish FVG": {"x": [], "y": [], "hover": []},
        "Bearish FVG": {"x": [], "y": [], "hover": []},
        "IFVG (Inverted)": {"x": [], "y": [], "hover": []},
    }
    new_shapes: list[dict[str, Any]] = []

    for fvg, event in filtered_records:
        is_inverted = event is not None and event.validity_state == FvgValidityState.INVERTED
        is_invalidated = event is not None and event.validity_state == FvgValidityState.INVALIDATED
        is_fully_filled_normal = (
            event is not None and event.fill_state == FvgFillState.FULLY_FILLED and not is_inverted
        )

        # 1. Coordinate boundaries
        # Formation time is strictly candle_3.close_time (Point-in-Time constraint)
        x0 = fvg.formation_time
        if is_invalidated and event is not None and event.invalidated_by_candle_open is not None:
            x1 = event.invalidated_by_candle_open
        elif is_fully_filled_normal and event is not None and event.bar_close_time is not None:
            x1 = event.bar_close_time
        else:
            x1 = last_candle_close

        if as_of_utc is not None and x1 > as_of_utc:
            x1 = as_of_utc

        # Ensure non-zero width for rendering
        if x1 <= x0:
            bar_delta = (
                candles[-1].close_time - candles[-1].open_time
                if len(candles) >= 1
                else timedelta(minutes=1)
            )
            x1 = x0 + bar_delta

        y0 = float(fvg.bottom_price)
        y1 = float(fvg.top_price)
        ce = float(fvg.ce_price)

        # 2. Segments generation (Strict Point-in-Time styling for IFVG)
        # If inverted, the zone was respected in its original directional role from formation
        # until inversion. Only from inversion onwards does it take the purple dashed IFVG styling.
        t_inversion = (
            event.inversion_confirmed_at or event.invalidated_by_candle_open
            if (is_inverted and event is not None)
            else None
        )

        segments: list[dict[str, Any]] = []

        if is_inverted and t_inversion is not None and x0 < t_inversion < x1:
            # Segment A: Prior period (formation to inversion) in original Bullish/Bearish style
            orig_is_bull = fvg.is_bullish
            segments.append(
                {
                    "x0": x0,
                    "x1": t_inversion,
                    "fill": "rgba(38, 166, 154, 0.10)"
                    if orig_is_bull
                    else "rgba(239, 83, 80, 0.10)",
                    "border": "rgba(38, 166, 154, 0.65)"
                    if orig_is_bull
                    else "rgba(239, 83, 80, 0.65)",
                    "dash": "solid",
                    "ce_color": "rgba(38, 166, 154, 0.50)"
                    if orig_is_bull
                    else "rgba(239, 83, 80, 0.50)",
                    "badge": "🟢 BULLISH FVG" if orig_is_bull else "🔴 BEARISH FVG",
                    "status": "VALID (Pre-Inversion)",
                    "cat": "Bullish FVG" if orig_is_bull else "Bearish FVG",
                }
            )
            # Segment B: Post-inversion period in IFVG purple dashed style
            segments.append(
                {
                    "x0": t_inversion,
                    "x1": x1,
                    "fill": "rgba(168, 85, 247, 0.09)",
                    "border": "#a855f7",
                    "dash": "dash",
                    "ce_color": "rgba(168, 85, 247, 0.50)",
                    "badge": "🟣 INVERSION FVG (IFVG)",
                    "status": f"INVERTED (Teyit: {t_inversion.strftime('%H:%M UTC')})",
                    "cat": "IFVG (Inverted)",
                }
            )
        elif is_inverted:
            # Entire visible portion is inverted
            segments.append(
                {
                    "x0": x0,
                    "x1": x1,
                    "fill": "rgba(168, 85, 247, 0.09)",
                    "border": "#a855f7",
                    "dash": "dash",
                    "ce_color": "rgba(168, 85, 247, 0.50)",
                    "badge": "🟣 INVERSION FVG (IFVG)",
                    "status": "INVERTED",
                    "cat": "IFVG (Inverted)",
                }
            )
        elif is_invalidated:
            segments.append(
                {
                    "x0": x0,
                    "x1": x1,
                    "fill": "rgba(100, 116, 139, 0.08)",
                    "border": "rgba(100, 116, 139, 0.35)",
                    "dash": "dot",
                    "ce_color": "rgba(100, 116, 139, 0.40)",
                    "badge": "🟢 BULLISH FVG" if fvg.is_bullish else "🔴 BEARISH FVG",
                    "status": "INVALIDATED",
                    "cat": "Bullish FVG" if fvg.is_bullish else "Bearish FVG",
                }
            )
        else:
            orig_is_bull = fvg.is_bullish
            segments.append(
                {
                    "x0": x0,
                    "x1": x1,
                    "fill": "rgba(38, 166, 154, 0.10)"
                    if orig_is_bull
                    else "rgba(239, 83, 80, 0.10)",
                    "border": "rgba(38, 166, 154, 0.65)"
                    if orig_is_bull
                    else "rgba(239, 83, 80, 0.65)",
                    "dash": "solid",
                    "ce_color": "rgba(38, 166, 154, 0.50)"
                    if orig_is_bull
                    else "rgba(239, 83, 80, 0.50)",
                    "badge": "🟢 BULLISH FVG" if orig_is_bull else "🔴 BEARISH FVG",
                    "status": "VALID",
                    "cat": "Bullish FVG" if orig_is_bull else "Bearish FVG",
                }
            )

        fill_label = event.fill_state.value if event else "UNTOUCHED"
        fill_pct = float(event.fill_ratio * 100) if event else 0.0
        eff_dir = calculate_effective_direction(fvg, event)
        eff_text = "🟢 Bullish Destek" if eff_dir == FvgDirection.BULLISH else "🔴 Bearish Direnç"
        orig_text = "Bullish" if fvg.is_bullish else "Bearish"
        type_label = (
            "🟣 Inversion FVG (IFVG)"
            if is_inverted
            else ("🟢 Bullish FVG" if fvg.is_bullish else "🔴 Bearish FVG")
        )
        inv_line = (
            f"<br><b>Teyit Zamanı:</b> {t_inversion.strftime('%Y-%m-%d %H:%M UTC')}"
            if (is_inverted and t_inversion)
            else ""
        )

        for seg in segments:
            # 3. Add background rectangular imbalance box
            new_shapes.append(
                dict(
                    type="rect",
                    x0=seg["x0"],
                    x1=seg["x1"],
                    y0=y0,
                    y1=y1,
                    fillcolor=seg["fill"],
                    line=dict(color=seg["border"], width=1.0, dash=seg["dash"]),
                    layer="below",
                )
            )

            # 4. Add 50% Consequent Encroachment (CE) line shape
            if show_ce_line:
                new_shapes.append(
                    dict(
                        type="line",
                        x0=seg["x0"],
                        x1=seg["x1"],
                        y0=ce,
                        y1=ce,
                        line=dict(color=seg["ce_color"], width=1.0, dash="dashdot"),
                        layer="below",
                    )
                )

            # 5. Build clean, compact vertical hover metadata tooltip
            hover_text = (
                f"<b>{type_label}</b><br>"
                f"<b>Zaman Dilimi:</b> {fvg.timeframe.name}<br>"
                f"<b>Orijinal Yön:</b> {orig_text}<br>"
                f"<b>Etkin Yön:</b> {eff_text}<br>"
                f"<b>Oluşum:</b> {fvg.formation_time.strftime('%Y-%m-%d %H:%M UTC')}<br>"
                f"<b>Üst Sınır:</b> ${fvg.top_price:.3f}<br>"
                f"<b>Alt Sınır:</b> ${fvg.bottom_price:.3f}<br>"
                f"<b>CE (%50):</b> ${fvg.ce_price:.3f}<br>"
                f"<b>Dolum:</b> {fill_label} ({fill_pct:.1f}%)<br>"
                f"<b>Durum:</b> {seg['status']}"
                f"{inv_line}"
            )

            cat_data[seg["cat"]]["x"].extend([seg["x0"], seg["x1"], None])
            cat_data[seg["cat"]]["y"].extend([ce, ce, None])
            cat_data[seg["cat"]]["hover"].extend([hover_text, hover_text, None])

    # Assign all batched FVG and CE shapes in a single layout update
    if new_shapes:
        existing_shapes = list(fig.layout.shapes or ())
        fig.layout.shapes = existing_shapes + new_shapes

    # 6. Add unified category Scatter traces for interactive hover & legend control
    cat_styles = {
        "Bullish FVG": dict(color="#26a69a", dash="solid"),
        "Bearish FVG": dict(color="#ef5350", dash="solid"),
        "IFVG (Inverted)": dict(color="#a855f7", dash="dash"),
    }

    for cat_name, data in cat_data.items():
        if data["x"]:
            fig.add_trace(
                go.Scatter(
                    x=data["x"],
                    y=data["y"],
                    mode="lines",
                    name=cat_name,
                    line=dict(
                        color=cat_styles[cat_name]["color"],
                        width=1.0,
                        dash=cat_styles[cat_name]["dash"],
                    ),
                    hoverinfo="text",
                    hovertext=data["hover"],
                    hoverlabel=dict(
                        bgcolor="#131722",
                        font_size=11,
                        font_family="Roboto, Inter, sans-serif",
                    ),
                    showlegend=True,
                )
            )

    return fig
