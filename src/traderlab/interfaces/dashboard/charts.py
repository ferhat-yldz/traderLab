"""Plotly candlestick chart builder for TRADERLAB financial dashboard."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import plotly.graph_objects as go

from traderlab.core.types import Timeframe
from traderlab.domain.instruments.candle import Candle


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
        hovermode="x unified",
    )

    return fig
