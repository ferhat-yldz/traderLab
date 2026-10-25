"""Streamlit interactive financial dashboard for TRADERLAB.

Provides a TradingView-styled quantitative market research terminal with:
- Independent candle timeframes (M1, M5, M15, H1, H4, D1, W1).
- Multi-period visible ranges (1G, 5G, 1A, 3A, 6A, YTD, 1Y, TÜMÜ, Özel).
- Multi-month contiguous CSV loading and boundary-aware resampling.
- Both Candlestick and Line Chart (Close Price) modes with gradient area fill.
- Normal and Wide Screen workstation view modes.
- Strict Point-in-Time tracking, synthetic rollover distinction, and data quality guards.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import streamlit as st

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    ResampledCandle,
    resample_m1_candles,
)
from traderlab.core.types import PriceType, Timeframe
from traderlab.domain.instruments.candle import Candle
from traderlab.infrastructure.providers.market_data_lab.bar_classifier import (
    BarQualityClassification,
    classify_m1_series,
)
from traderlab.infrastructure.providers.market_data_lab.config import (
    get_market_data_lab_settings,
)
from traderlab.infrastructure.providers.market_data_lab.csv_reader import (
    bar_to_candle,
    read_m1_csv,
)
from traderlab.infrastructure.providers.market_data_lab.inventory import (
    LocalDataInventory,
    LocalInventorySummary,
)
from traderlab.infrastructure.providers.market_data_lab.remote_auditor import (
    RemoteFreshnessAuditor,
    RemoteFreshnessReport,
)
from traderlab.interfaces.dashboard.charts import create_candlestick_figure

# ── Streamlit Page Configuration ─────────────────────────────────────────────
st.set_page_config(
    page_title="TRADERLAB — XAUUSD Research Terminal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS for High-Contrast Dark Terminal Aesthetics ─────────────────────
st.markdown(
    """
    <style>
    /* Dark Terminal Background and Clean Typography */
    .stApp {
        background-color: #0e1117;
        color: #e0e3eb;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    .block-container {
        padding-top: 1.0rem !important;
        padding-bottom: 2rem !important;
    }
    /* Sidebar Styling & Crisp High Contrast */
    section[data-testid="stSidebar"] {
        background-color: #131722;
        border-right: 1px solid #2a2e39;
    }
    section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] p {
        color: #e0e3eb !important;
        font-weight: 500;
        font-size: 0.92rem;
    }
    /* Metric Card Styling */
    div[data-testid="stMetric"] {
        background-color: #171b26;
        border: 1px solid #2a2e39;
        border-radius: 6px;
        padding: 10px 14px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.25);
    }
    div[data-testid="stMetricLabel"] p {
        color: #94a3b8 !important;
        font-size: 0.82rem !important;
        font-weight: 600 !important;
    }
    div[data-testid="stMetricValue"] {
        color: #26a69a !important;
        font-weight: 700 !important;
        font-size: 1.20rem !important;
    }
    /* Period Toolbar Badges */
    .tv-toolbar {
        background-color: #171b26;
        border: 1px solid #2a2e39;
        border-radius: 6px;
        padding: 8px 14px;
        margin-bottom: 12px;
        display: flex;
        align-items: center;
        justify-content: space-between;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_required_months(start_dt: datetime, end_dt: datetime) -> list[tuple[int, int]]:
    """Return list of (year, month) tuples covering [start_dt, end_dt]."""
    months = []
    cur_y, cur_m = start_dt.year, start_dt.month
    end_y, end_m = end_dt.year, end_dt.month
    while (cur_y, cur_m) <= (end_y, end_m):
        months.append((cur_y, cur_m))
        if cur_m == 12:
            cur_y += 1
            cur_m = 1
        else:
            cur_m += 1
    return months


RANGE_PRESETS: list[str] = [
    "1G",
    "5G",
    "1A",
    "3A",
    "6A",
    "YTD",
    "1Y",
    "5Y",
    "TÜMÜ",
    "Özel",
]

DEFAULT_TIMEFRAME_FOR_RANGE: dict[str, str] = {
    "1G": "M1",
    "5G": "M5",
    "1A": "M30",
    "3A": "H1",
    "6A": "H2",
    "YTD": "D1",
    "1Y": "D1",
    "5Y": "W1",
    "TÜMÜ": "W1",
    "Özel": "D1",
}


def subtract_calendar_months(source_date: date, months: int) -> date:
    """Subtract an exact number of calendar months, clamping to valid month days."""
    year = source_date.year - (months // 12)
    month = source_date.month - (months % 12)
    if month <= 0:
        month += 12
        year -= 1
    max_days = calendar.monthrange(year, month)[1]
    day = min(source_date.day, max_days)
    return date(year, month, day)


def subtract_calendar_years(source_date: date, years: int) -> date:
    """Subtract an exact number of calendar years, handling leap year Feb 29 safely."""
    year = source_date.year - years
    day = source_date.day
    if source_date.month == 2 and source_date.day == 29 and not calendar.isleap(year):
        day = 28
    return date(year, source_date.month, day)


def calculate_date_range(
    range_mode: str,
    anchor_date: date,
    custom_start: date | None = None,
    custom_end: date | None = None,
) -> tuple[datetime, datetime]:
    """Calculate the [start_utc, end_utc] datetime range for a chosen range preset.

    Uses financial calendar arithmetic for multi-month and multi-year ranges (1A, 3A, 6A, 1Y, 5Y)
    so leap years and varying month lengths (28-31 days) are handled accurately relative to anchor_date.
    """
    if range_mode == "1G":
        s_dt = datetime(anchor_date.year, anchor_date.month, anchor_date.day, 0, 0, tzinfo=UTC)
        e_dt = datetime(
            anchor_date.year, anchor_date.month, anchor_date.day, 23, 59, 59, tzinfo=UTC
        )
    elif range_mode == "5G":
        sd = anchor_date - timedelta(days=4)
        s_dt = datetime(sd.year, sd.month, sd.day, 0, 0, tzinfo=UTC)
        e_dt = datetime(
            anchor_date.year, anchor_date.month, anchor_date.day, 23, 59, 59, tzinfo=UTC
        )
    elif range_mode == "1A":
        sd = subtract_calendar_months(anchor_date, 1)
        s_dt = datetime(sd.year, sd.month, sd.day, 0, 0, tzinfo=UTC)
        e_dt = datetime(
            anchor_date.year, anchor_date.month, anchor_date.day, 23, 59, 59, tzinfo=UTC
        )
    elif range_mode == "3A":
        sd = subtract_calendar_months(anchor_date, 3)
        s_dt = datetime(sd.year, sd.month, sd.day, 0, 0, tzinfo=UTC)
        e_dt = datetime(
            anchor_date.year, anchor_date.month, anchor_date.day, 23, 59, 59, tzinfo=UTC
        )
    elif range_mode == "6A":
        sd = subtract_calendar_months(anchor_date, 6)
        s_dt = datetime(sd.year, sd.month, sd.day, 0, 0, tzinfo=UTC)
        e_dt = datetime(
            anchor_date.year, anchor_date.month, anchor_date.day, 23, 59, 59, tzinfo=UTC
        )
    elif range_mode in ("YTD", "Bu Sene"):
        s_dt = datetime(anchor_date.year, 1, 1, 0, 0, tzinfo=UTC)
        e_dt = datetime(
            anchor_date.year, anchor_date.month, anchor_date.day, 23, 59, 59, tzinfo=UTC
        )
    elif range_mode == "1Y":
        sd = subtract_calendar_years(anchor_date, 1)
        s_dt = datetime(sd.year, sd.month, sd.day, 0, 0, tzinfo=UTC)
        e_dt = datetime(
            anchor_date.year, anchor_date.month, anchor_date.day, 23, 59, 59, tzinfo=UTC
        )
    elif range_mode in ("5Y", "5 Yıl"):
        sd = subtract_calendar_years(anchor_date, 5)
        s_dt = datetime(sd.year, sd.month, sd.day, 0, 0, tzinfo=UTC)
        e_dt = datetime(
            anchor_date.year, anchor_date.month, anchor_date.day, 23, 59, 59, tzinfo=UTC
        )
    elif range_mode == "TÜMÜ":
        s_dt = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        e_dt = datetime(2026, 10, 19, 23, 59, 59, tzinfo=UTC)
    elif range_mode == "Özel" and custom_start and custom_end:
        s_dt = datetime(custom_start.year, custom_start.month, custom_start.day, 0, 0, tzinfo=UTC)
        e_dt = datetime(custom_end.year, custom_end.month, custom_end.day, 23, 59, 59, tzinfo=UTC)
    else:
        s_dt = datetime(anchor_date.year, anchor_date.month, anchor_date.day, 0, 0, tzinfo=UTC)
        e_dt = datetime(
            anchor_date.year, anchor_date.month, anchor_date.day, 23, 59, 59, tzinfo=UTC
        )

    return s_dt, e_dt


@st.cache_data(show_spinner=False)
def load_day_m1_candles(
    selected_date: date,
    price_type: PriceType,
) -> tuple[list[Candle], str | None]:
    """Load, classify, and convert M1 bars for a single date from local CSV datasets."""
    settings = get_market_data_lab_settings()
    try:
        bid_path, ask_path = settings.validate_dataset_files(
            selected_date.year, selected_date.month
        )
    except Exception:
        try:
            inv = LocalDataInventory().scan_inventory()
            avail_str = ", ".join(inv.available_months)
        except Exception:
            avail_str = "2024-01 .. 2026-10"
        return (
            [],
            f"Seçilen tarih ({selected_date.strftime('%Y-%m-%d')}) için yerel veri bulunamadı. "
            f"Yerel depoda yalnızca {avail_str} mevcuttur.",
        )

    target_csv = bid_path if price_type == PriceType.BID else ask_path
    if not target_csv.exists():
        return (
            [],
            f"Seçilen tarih ({selected_date.strftime('%Y-%m-%d')}) için yerel veri bulunamadı.",
        )

    t_start = datetime(
        selected_date.year, selected_date.month, selected_date.day, 0, 0, tzinfo=UTC
    )
    t_end = t_start + timedelta(days=1)

    raw_bars = read_m1_csv(target_csv, price_type)
    day_bars = [b for b in raw_bars if t_start <= b.open_time < t_end]

    if not day_bars:
        return (
            [],
            f"Seçilen gün ({selected_date.strftime('%Y-%m-%d')}) için kayıtlı M1 mumu bulunamadı. "
            "Piyasa tatil/hafta sonu olabilir veya ay henüz bu güne ulaşmamıştır.",
        )

    classified = classify_m1_series(day_bars)
    candles = [
        bar_to_candle(
            cb.bar,
            is_synthetic=(cb.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD),
        )
        for cb in classified
    ]

    return candles, None


@st.cache_data(show_spinner=False)
def load_month_m1_candles(
    year: int,
    month: int,
    price_type: PriceType,
) -> tuple[list[Candle], str | None]:
    """Load, classify, and convert M1 bars for an entire month from local CSV datasets."""
    settings = get_market_data_lab_settings()
    try:
        bid_path, ask_path = settings.validate_dataset_files(year, month)
    except Exception:
        try:
            inv = LocalDataInventory().scan_inventory()
            avail_str = ", ".join(inv.available_months)
        except Exception:
            avail_str = "2024-01 .. 2026-10"
        return (
            [],
            f"Seçilen dönem ({year}-{month:02d}) için yerel veri bulunamadı. "
            f"Yerel depoda yalnızca {avail_str} mevcuttur.",
        )

    target_csv = bid_path if price_type == PriceType.BID else ask_path
    if not target_csv.exists():
        return (
            [],
            f"Seçilen dönem ({year}-{month:02d}) için kayıtlı M1 mumu bulunamadı.",
        )

    raw_bars = read_m1_csv(target_csv, price_type)
    if not raw_bars:
        return (
            [],
            f"Seçilen dönem ({year}-{month:02d}) için kayıtlı M1 mumu bulunamadı.",
        )

    classified = classify_m1_series(raw_bars)
    candles = [
        bar_to_candle(
            cb.bar,
            is_synthetic=(cb.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD),
        )
        for cb in classified
    ]

    return candles, None


@st.cache_data(show_spinner=False)
def load_month_m1_candles_cached(
    year: int,
    month: int,
    price_type: PriceType,
) -> list[Candle]:
    """Load, classify, and convert M1 bars for an entire month with Streamlit caching."""
    settings = get_market_data_lab_settings()
    try:
        bid_path, ask_path = settings.validate_dataset_files(year, month)
    except Exception:
        return []

    target_csv = bid_path if price_type == PriceType.BID else ask_path
    if not target_csv.exists():
        return []

    raw_bars = read_m1_csv(target_csv, price_type)
    if not raw_bars:
        return []

    classified = classify_m1_series(raw_bars)
    candles = [
        bar_to_candle(
            cb.bar,
            is_synthetic=(cb.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD),
        )
        for cb in classified
    ]
    return candles


def load_candles_for_range(
    instrument_id: str,
    price_type: PriceType,
    timeframe: Timeframe,
    start_utc: datetime,
    end_utc: datetime,
    daily_close_mode: DailyCloseMode = DailyCloseMode.UTC_MIDNIGHT,
    available_months: Sequence[str] = (),
) -> tuple[list[Candle], list[ResampledCandle], str | None, str | None]:
    """Load and resample candles for a given arbitrary date range across multiple months.

    Returns:
        (display_candles, resampled_candles, warning_msg, error_msg)
    """
    total_days = max(1, (end_utc - start_utc).days + 1)

    # ── Density safeguards to prevent browser freezing on millions of bars ──
    if timeframe == Timeframe.M1 and total_days > 5:
        return (
            [],
            [],
            f"M1 (1 Dakika) zaman dilimi seçilen {total_days} günlük dönem için aşırı yoğundur (~{total_days * 1440:,} bar). "
            "Tarayıcı performansını korumak için M1 aralığı en fazla 5 gün (1G veya 5G) ile sınırlandırılmıştır. "
            "Lütfen dönemi 1G / 5G'ye indiriniz ya da zaman dilimini M5, M15, M30, H1, H2, H4, D1, W1 olarak değiştiriniz.",
            None,
        )

    if timeframe == Timeframe.M5 and total_days > 35:
        return (
            [],
            [],
            f"M5 (5 Dakika) zaman dilimi seçilen {total_days} günlük dönem için aşırı yoğundur (~{total_days * 288:,} bar). "
            "Lütfen dönemi 1A / 5G'ye getiriniz ya da M15, M30, H1, H2, H4, D1, W1 zaman dilimlerine geçiniz.",
            None,
        )

    if timeframe == Timeframe.M15 and total_days > 95:
        return (
            [],
            [],
            f"M15 (15 Dakika) zaman dilimi seçilen {total_days} günlük dönem için aşırı yoğundur (~{total_days * 96:,} bar). "
            "Lütfen dönemi 1A / 3A olarak seçiniz ya da M30, H1, H2, H4, D1, W1 zaman dilimlerine geçiniz.",
            None,
        )

    if timeframe == Timeframe.M30 and total_days > 185:
        return (
            [],
            [],
            f"M30 (30 Dakika) zaman dilimi seçilen {total_days} günlük dönem için aşırı yoğundur (~{total_days * 48:,} bar). "
            "Lütfen dönemi 1A / 3A olarak seçiniz ya da H1, H2, H4, D1, W1 zaman dilimlerine geçiniz.",
            None,
        )

    if timeframe == Timeframe.H1 and total_days > 370:
        return (
            [],
            [],
            f"H1 (1 Saat) zaman dilimi seçilen {total_days} günlük dönem için aşırı yoğundur (~{total_days * 24:,} bar). "
            "Lütfen dönemi 3A / 6A olarak seçiniz ya da H2, H4, D1, W1 zaman dilimlerine geçiniz.",
            None,
        )

    if timeframe == Timeframe.H2 and total_days > 730:
        return (
            [],
            [],
            f"H2 (2 Saat) zaman dilimi seçilen {total_days} günlük dönem için aşırı yoğundur (~{total_days * 12:,} bar). "
            "Lütfen dönemi 6A / 1Y olarak seçiniz ya da H4, D1, W1 zaman dilimlerine geçiniz.",
            None,
        )

    required_months = get_required_months(start_utc, end_utc)
    if available_months:
        valid_months = [
            (y, m) for y, m in required_months if f"{y:04d}-{m:02d}" in available_months
        ]
    else:
        valid_months = required_months

    if not valid_months:
        avail_str = ", ".join(available_months) if available_months else "2024-01 .. 2026-10"
        return (
            [],
            [],
            None,
            f"Seçilen dönem ({start_utc.strftime('%Y-%m-%d')} — {end_utc.strftime('%Y-%m-%d')}) "
            f"için yerel veri bulunamadı. Yerel depoda yalnızca {avail_str} mevcuttur.",
        )

    # Accumulate M1 bars for the required months
    raw_m1: list[Candle] = []
    for yr, mo in valid_months:
        m_bars = load_month_m1_candles_cached(yr, mo, price_type)
        raw_m1.extend(m_bars)

    if not raw_m1:
        return (
            [],
            [],
            None,
            f"Seçilen aralıkta ({start_utc.strftime('%Y-%m-%d')} — {end_utc.strftime('%Y-%m-%d')}) "
            "okunabilen M1 mum verisi bulunamadı.",
        )

    if timeframe == Timeframe.M1:
        # Filter strictly within [start_utc, end_utc]
        filtered = [c for c in raw_m1 if start_utc <= c.open_time <= end_utc]
        return filtered, [], None, None

    # Resample gathered M1 bars to target timeframe
    resampled = resample_m1_candles(
        raw_m1,
        timeframe,
        daily_close_mode=daily_close_mode,
    )

    # Filter resampled candles within requested range
    tolerance = (
        timedelta(days=6)
        if timeframe == Timeframe.W1
        else (timedelta(hours=23) if timeframe == Timeframe.D1 else timedelta(minutes=0))
    )
    filtered_rc = [
        rc
        for rc in resampled
        if (start_utc - tolerance) <= rc.candle.open_time <= end_utc
        and rc.candle.open_time >= start_utc
    ]
    if not filtered_rc and resampled:
        filtered_rc = [
            rc
            for rc in resampled
            if rc.candle.open_time <= end_utc and rc.candle.close_time >= start_utc
        ]

    display_candles = [rc.candle for rc in filtered_rc]
    return display_candles, filtered_rc, None, None


def on_period_button_click(chosen_range: str, chosen_timeframe: str) -> None:
    """Callback triggered before widgets render when a TradingView period button is clicked."""
    st.session_state["range_preset"] = chosen_range
    st.session_state["timeframe_select"] = chosen_timeframe
    st.session_state["_prev_range"] = chosen_range


def on_radio_range_change() -> None:
    """Callback triggered before widgets render when the sidebar range radio is toggled."""
    new_range = st.session_state.get("range_preset", "1G")
    st.session_state["_prev_range"] = new_range
    st.session_state["timeframe_select"] = DEFAULT_TIMEFRAME_FOR_RANGE.get(new_range, "M1")


@st.cache_data(ttl=120, show_spinner=False)
def get_cached_inventory_summary() -> LocalInventorySummary:
    """Cache the local filesystem data inventory scan to avoid re-hashing 68 CSVs on every rerun."""
    return LocalDataInventory().scan_inventory()


@st.cache_data(ttl=300, show_spinner=False)
def get_cached_remote_report() -> RemoteFreshnessReport:
    """Cache the remote git freshness audit to avoid slow network round-trips on every rerun."""
    return RemoteFreshnessAuditor().audit_freshness(timeout_seconds=3.0)


def main() -> None:
    """Main Streamlit dashboard entrypoint."""
    st.title("TRADERLAB — XAUUSD Research Terminal")
    st.caption(
        "Profesyonel Kantitatif Piyasa Araştırma Platformu — "
        "TradingView Benzeri Gezinme, Çoklu Zaman Dilimi Resampling ve Veri Envanteri"
    )

    # ── Real-Time Data Freshness & PIT Status Bar ────────────────────────────
    local_summary = get_cached_inventory_summary()
    remote_report = get_cached_remote_report()

    local_end_str = (
        local_summary.latest_date_utc.replace("T", " ")[:19] + " UTC"
        if local_summary.latest_date_utc
        else "Yok"
    )
    remote_end_str = remote_report.remote_verified_end_utc or "Yok"

    # Compute difference in minutes between local and remote
    diff_desc = "Senkron (0 dk)"
    if local_summary.latest_date_utc and remote_report.remote_verified_end_utc:
        try:
            dt_loc = datetime.fromisoformat(local_summary.latest_date_utc)
            dt_rem = datetime.strptime(
                remote_report.remote_verified_end_utc.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=UTC)
            diff_sec = int((dt_rem - dt_loc).total_seconds())
            if diff_sec == 0:
                diff_desc = "Tam Senkron (0 dk)"
            elif diff_sec > 0:
                diff_desc = f"{diff_sec // 60:,} dk geride"
            else:
                diff_desc = f"{abs(diff_sec) // 60:,} dk ileride"
        except Exception:
            diff_desc = "Bilinmiyor"

    # Top Freshness Metrics Row
    c_f1, c_f2, c_f3, c_f4 = st.columns(4)
    c_f1.metric("Yerel Veri Son Tarih", local_end_str)
    c_f2.metric("GitHub Doğrulanan Son Tarih", remote_end_str)
    c_f3.metric("Yerel vs GitHub Durumu", diff_desc)
    c_f4.metric(
        "Uzak Veri Gecikmesi (Lag)",
        f"{remote_report.dataset_lag_hours:.1f} saat (~{remote_report.dataset_lag_hours / 24:.1f} gün)",
    )

    # ── Sidebar Controls ─────────────────────────────────────────────────────
    st.sidebar.header("Grafik ve Görünüm Kontrolleri")

    # Chart Type: Candlestick vs Çizgi Grafik (Close Price)
    chart_type = st.sidebar.radio(
        "Grafik Türü",
        ["Candlestick", "Çizgi Grafik (Close Price)"],
        index=0,
        key="chart_type_radio",
    )

    # View Mode: Normal vs Geniş Ekran
    view_mode = st.sidebar.radio(
        "Görünüm Modu",
        ["Standart (580px)", "Geniş Ekran (820px)"],
        index=0,
        key="view_mode_radio",
    )
    if "Geniş" in view_mode:
        st.markdown(
            """
            <style>
            .block-container {
                padding-left: 1.2rem !important;
                padding-right: 1.2rem !important;
                max-width: 100% !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

    st.sidebar.divider()
    st.sidebar.header("Enstrüman & Fiyat")
    st.sidebar.selectbox("Enstrüman", ["XAUUSD"], index=0, disabled=True)
    price_type_str = st.sidebar.radio("Fiyat Türü", ["BID", "ASK"], index=0, horizontal=True)
    selected_price_type = PriceType.BID if price_type_str == "BID" else PriceType.ASK

    st.sidebar.divider()
    st.sidebar.header("Zaman Dilimi & Dönem")

    # Timeframe selection: M1, M5, M15, M30, H1, H2, H4, D1, W1
    timeframe_map = {
        "M1": Timeframe.M1,
        "M5": Timeframe.M5,
        "M15": Timeframe.M15,
        "M30": Timeframe.M30,
        "H1": Timeframe.H1,
        "H2": Timeframe.H2,
        "H4": Timeframe.H4,
        "D1": Timeframe.D1,
        "W1": Timeframe.W1,
    }

    range_options = RANGE_PRESETS
    default_tf_map = DEFAULT_TIMEFRAME_FOR_RANGE

    tf_keys = list(timeframe_map.keys())
    if "timeframe_select" not in st.session_state:
        st.session_state["timeframe_select"] = "M1"
    if "range_preset" not in st.session_state:
        st.session_state["range_preset"] = "1G"

    timeframe_str = st.sidebar.selectbox(
        "Mum Zaman Dilimi",
        tf_keys,
        key="timeframe_select",
    )
    selected_timeframe = timeframe_map[timeframe_str]

    selected_range = st.sidebar.radio(
        "Görünür Dönem",
        range_options,
        horizontal=True,
        key="range_preset",
        on_change=on_radio_range_change,
    )

    # Reference Anchor End Date (defaults to latest available archive date)
    default_anchor = date(2026, 10, 19)
    anchor_date = st.sidebar.date_input(
        "Referans Bitiş Tarihi",
        value=default_anchor,
        min_value=date(2024, 1, 1),
        max_value=date(2026, 10, 19),
        key="anchor_date_picker",
    )

    custom_start: date | None = None
    custom_end: date | None = None
    if selected_range == "Özel":
        c_s1, c_s2 = st.sidebar.columns(2)
        custom_start = c_s1.date_input(
            "Başlangıç", value=date(2024, 1, 1), min_value=date(2024, 1, 1)
        )
        custom_end = c_s2.date_input(
            "Bitiş", value=date(2024, 1, 16), max_value=date(2026, 10, 19)
        )

    daily_mode_str = st.sidebar.selectbox(
        "D1 / W1 Kapanış Hizalaması",
        ["UTC_MIDNIGHT (00:00 UTC)", "NEW_YORK_1700 (17:00 NY)"],
        index=0,
    )
    selected_daily_mode = (
        DailyCloseMode.UTC_MIDNIGHT if "UTC" in daily_mode_str else DailyCloseMode.NEW_YORK_1700
    )

    show_synthetic = st.sidebar.checkbox(
        "Piyasa Kapalı / Sentetik Barları Vurgula",
        value=True,
    )

    st.sidebar.divider()
    st.sidebar.subheader("Veri Kökeni & PIT Durumu")
    st.sidebar.info(
        "**Kaynak:** Üçüncü Taraf GitHub Deposu (`Market-Data-Lab`)\n\n"
        "**Beyan Edilen Sağlayıcı:** Dukascopy (Doğrulanmamış)\n\n"
        "**PIT Politikası:** `RESEARCH_ESTIMATED`\n\n"
        "**Yasal Uyarı:** Fiyat verileri üçüncü taraf kaynaklıdır; ticari kullanım veya kesin lisansı yoktur."
    )

    # Calculate actual UTC start and end bounds
    start_utc, end_utc = calculate_date_range(
        selected_range,
        anchor_date,
        custom_start=custom_start,
        custom_end=custom_end,
    )

    # ── Primary View Tabs ────────────────────────────────────────────────────
    tab_chart, tab_inventory = st.tabs(
        ["📊 XAUUSD Mum Grafiği", "📋 Veri Envanteri & GitHub Güncellik Paneli"]
    )

    # ── Tab 1: Interactive Candlestick Chart ──────────────────────────────────
    with tab_chart:
        # Load multi-month candles for chosen range and timeframe
        display_candles, resampled_rc, warning_msg, error_msg = load_candles_for_range(
            "XAUUSD",
            selected_price_type,
            selected_timeframe,
            start_utc,
            end_utc,
            daily_close_mode=selected_daily_mode,
            available_months=local_summary.available_months,
        )

        # Range and Timeframe Banner Toolbar (TradingView-style)
        total_days = max(1, (end_utc - start_utc).days + 1)
        st.markdown(
            f"""
            <div class="tv-toolbar">
                <div>
                    <strong>📅 Görünür Aralık:</strong> <code>{start_utc.strftime("%Y-%m-%d")}</code> — <code>{end_utc.strftime("%Y-%m-%d")}</code> ({total_days} Takvim Günü)
                    &nbsp;|&nbsp;
                    <strong>⏱️ Zaman Dilimi:</strong> <code>{timeframe_str}</code>
                    &nbsp;|&nbsp;
                    <strong>🏷️ Grafik Türü:</strong> <code>{chart_type}</code>
                </div>
                <div>
                    <span style="color: #26a69a; font-weight: 600;">{len(display_candles)} Mum Yüklendi</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if selected_range in ("5Y", "TÜMÜ") or start_utc < datetime(2024, 1, 1, tzinfo=UTC):
            st.info(
                "ℹ️ **Mevcut Veri:** `01.01.2024 – 19.10.2026` (Yaklaşık 34 Ay, 68 CSV Dosyası). "
                "Yerel arşivimiz tam 5 yıllık (60 ay) veri içermediğinden yalnızca depodaki gerçek kayıtlar çizilmektedir. "
                "2024 öncesindeki olmayan yıllara suni/sentetik mum üretilmemiştir."
            )

        if warning_msg:
            st.warning(f"⚠️ {warning_msg}")

        if error_msg:
            st.error(f"❌ {error_msg}")
            avail_months_str = ", ".join(local_summary.available_months)
            empty_fig = create_candlestick_figure(
                [],
                f"XAUUSD {price_type_str} {timeframe_str}",
                empty_message=f"{error_msg}<br>Yerel depoda yalnızca {avail_months_str} mevcuttur.",
            )
            st.plotly_chart(empty_fig, width="stretch")
        elif not display_candles:
            if not warning_msg:
                st.info("ℹ️ Bu aralık için görüntülenecek veri bulunmamaktadır.")
        else:
            # Check for genuine historical incomplete candles (e.g. October 19, 2026 archive boundary)
            # Strict safeguard: Synthetic carry-forward closed bars are NEVER marked as missing!
            if (
                resampled_rc
                and not resampled_rc[-1].candle.is_closed
                and not resampled_rc[-1].candle.is_synthetic
            ):
                last_rc = resampled_rc[-1]
                now_utc = datetime.now(tz=UTC)
                is_historical = now_utc >= last_rc.candle.close_time

                if is_historical:
                    alert_title = "Kaynak Verisi Eksik Tarihsel Mum"
                    reason_desc = (
                        f"Bu mumun zaman penceresi (`{last_rc.candle.open_time.strftime('%H:%M')} - "
                        f"{last_rc.candle.close_time.strftime('%H:%M')} UTC`) geçmiş olmasına rağmen, "
                        f"harici veri akışı {last_rc.last_m1_close.strftime('%H:%M')} UTC anında sonlanmıştır "
                        f"({last_rc.present_m1_count}/{last_rc.expected_m1_count} M1 barı mevcuttur)."
                    )
                else:
                    alert_title = "Oluşmakta Olan Canlı Mum"
                    reason_desc = (
                        f"Bu mumun süresi henüz dolmamıştır (`{last_rc.candle.open_time.strftime('%H:%M')} - "
                        f"{last_rc.candle.close_time.strftime('%H:%M')} UTC`) ve piyasa akışı devam etmektedir "
                        f"({last_rc.present_m1_count}/{last_rc.expected_m1_count} M1 barı mevcuttur)."
                    )

                closed_bars = [rc for rc in resampled_rc if rc.candle.is_closed]
                last_closed_text = (
                    f"{closed_bars[-1].candle.open_time.strftime('%Y-%m-%d %H:%M')} - "
                    f"{closed_bars[-1].candle.close_time.strftime('%H:%M')} UTC"
                    if closed_bars
                    else "Yok"
                )
                st.warning(
                    f"⚠️ **{alert_title}:** {reason_desc} "
                    f"Son tamamlanmış {timeframe_str} mumu: `{last_closed_text}`."
                )

            # Chart Title
            chart_title = (
                f"XAUUSD — {price_type_str} | {timeframe_str} | "
                f"{start_utc.strftime('%d %b %Y')} – {end_utc.strftime('%d %b %Y')} | "
                f"Toplam Mum: {len(display_candles)}"
            )

            fig_height = 820 if "Geniş" in view_mode else 580
            fig = create_candlestick_figure(
                display_candles,
                title=chart_title,
                chart_type=chart_type,
                height=fig_height,
                show_synthetic_markers=show_synthetic,
                timeframe=selected_timeframe,
                range_preset=selected_range,
                show_rangeselector=False,
            )
            st.plotly_chart(fig, width="stretch")

            # TradingView-style interactive range preset buttons directly under the chart
            btn_cols = st.columns(len(range_options))
            for idx, r_opt in enumerate(range_options):
                is_active = r_opt == selected_range
                btn_type: Literal["primary", "secondary"] = "primary" if is_active else "secondary"
                btn_cols[idx].button(
                    r_opt,
                    key=f"tv_chart_btn_{r_opt}",
                    type=btn_type,
                    use_container_width=True,
                    on_click=on_period_button_click,
                    args=(r_opt, default_tf_map.get(r_opt, "D1")),
                )

            # Metadata metrics bar under chart (TradingView style change stats)
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            first_open = display_candles[0].open
            last_close = display_candles[-1].close
            price_change = last_close - first_open
            pct_change = (price_change / first_open) * 100 if first_open else 0

            c1.metric("İlk Açılış", f"${first_open:.3f}")
            c2.metric("En Yüksek (High)", f"${max(c.high for c in display_candles):.3f}")
            c3.metric("En Düşük (Low)", f"${min(c.low for c in display_candles):.3f}")
            c4.metric("Son Kapanış", f"${last_close:.3f}")

            chg_color: Literal["normal", "inverse", "off"] = (
                "normal" if price_change >= 0 else "inverse"
            )
            c5.metric(
                "Dönem Değişimi",
                f"${price_change:+.2f}",
                f"{pct_change:+.2f}%",
                delta_color=chg_color,
            )

            if resampled_rc:
                pure_real = sum(
                    1
                    for rc in resampled_rc
                    if rc.synthetic_m1_count == 0 and rc.active_m1_count >= 6000
                )
                mixed_real = sum(
                    1
                    for rc in resampled_rc
                    if rc.synthetic_m1_count > 0 and rc.active_m1_count > 0
                )
                pure_synth = sum(1 for rc in resampled_rc if rc.active_m1_count == 0)
                partial_count = sum(
                    1
                    for rc in resampled_rc
                    if (rc.synthetic_m1_count == 0 and 0 < rc.active_m1_count < 6000)
                    or (not rc.candle.is_closed and rc.present_m1_count == rc.synthetic_m1_count)
                )

                parts = []
                if pure_real > 0:
                    parts.append(f"{pure_real} Saf Gerçek")
                if mixed_real > 0:
                    parts.append(f"{mixed_real} Karma")
                if pure_synth > 0:
                    parts.append(f"{pure_synth} Sentetik")
                if partial_count > 0:
                    parts.append(f"{partial_count} Kısmi")
                delta_note = " | ".join(parts) if parts else f"{len(display_candles)} Bar"
            else:
                real_count = sum(1 for c in display_candles if not c.is_synthetic)
                synth_count = sum(1 for c in display_candles if c.is_synthetic)
                partial_count = sum(
                    1 for c in display_candles if not c.is_closed and not c.is_synthetic
                )
                delta_note = f"{real_count} Gerçek | {synth_count} Sentetik"
                if partial_count > 0:
                    delta_note += f" ({partial_count} Açık)"

            c6.metric(
                "Bar Bileşimi & Durumu",
                f"{len(display_candles)} Toplam Bar",
                delta_note,
            )

    # ── Tab 2: Veri Envanteri & GitHub Güncellik Paneli ──────────────────────
    with tab_inventory:
        st.subheader("Yerel Bilgisayar Veri Envanteri vs GitHub Depo Güncelliği")

        # Top Metric Cards
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Yerel En Eski Veri", local_summary.earliest_date_utc or "Yok")
        c2.metric("Yerel En Yeni Veri", local_summary.latest_date_utc or "Yok")
        c3.metric("GitHub Bildirilen Son Veri", remote_report.readme_reported_end_utc)
        c4.metric("GitHub Doğrulanan Son Veri", remote_report.remote_verified_end_utc)

        c5, c6, c7, c8 = st.columns(4)
        avail_months_summary = ", ".join(local_summary.available_months)
        c5.metric(
            "Yerel Mevcut Aylar",
            f"{len(local_summary.available_months)} Ay ({avail_months_summary})",
        )
        c6.metric("Eksik Ay Sayısı", f"{len(local_summary.missing_months)} ay (2010–2026)")
        c7.metric("Veri Gecikmesi (Lag)", f"{remote_report.dataset_lag_hours:.1f} saat")
        c8.metric("Git Commit SHA", remote_report.local_head_sha[:10])

        st.divider()

        # Detailed Breakdown Tables
        left_col, right_col = st.columns(2)

        with left_col:
            st.markdown("### 📁 Yerel CSV Dosya Denetimi")
            files_data = []
            for f in local_summary.bid_files + local_summary.ask_files:
                files_data.append(
                    {
                        "Dosya": f.file_name,
                        "Taraf": f.price_type,
                        "Boyut": f"{f.file_size_bytes:,} B",
                        "Satır": f"{f.total_rows:,}",
                        "İlk Tarih": f.first_datetime_utc[:16],
                        "Son Tarih": f.last_datetime_utc[:16],
                        "Hafta Sonu / Rollover Boşluğu": f"{f.off_session_gaps_count} adet",
                        "İşlem Seansı Boşluğu": f"{f.in_session_gaps_count} adet",
                        "SHA-256": f.sha256_hash[:16] + "...",
                    }
                )
            st.dataframe(files_data, width="stretch")

            st.info(
                f"**BID/ASK Ortak Veri Kapsamı:** {local_summary.common_overlap_start} "
                f"ile {local_summary.common_overlap_end} arası (%100 senkron)."
            )

        with right_col:
            st.markdown("### 🌐 GitHub Veri Deposu Güncellik Denetimi")
            st.write(f"**Uzak Depo:** `{remote_report.remote_url}`")
            st.write(f"**Uzak Dal:** `{remote_report.branch}`")
            st.write(f"**Son İlgili Commit:** `{remote_report.last_commit_date_utc}`")
            st.write(f"**Commit Mesajı:** `{remote_report.last_commit_message}`")
            st.write(
                f"**Otomatik Günlük CI/CD (GitHub Actions):** `{'VAR' if remote_report.has_automated_ci_daily_sync else 'YOK (Manuel/Düzensiz)'}`"
            )
            st.write(f"**Güncelleme Rejimi:** `{remote_report.update_frequency_mode}`")

            st.warning(
                "⚠️ **Önemli Gerçek:** GitHub'da veri bulunması, verilerin her gün otomatik "
                "güncellendiği anlamına gelmez. Depoda otomatik GitHub Actions iş akışı bulunmamakta; "
                "depo sahibi belirli aralıklarla toplu commit atmaktadır."
            )

        st.divider()
        st.markdown("### 🔄 Gelecekteki Güvenli ve Kullanıcı Onaylı Senkronizasyon Tasarımı")
        st.markdown(
            """
            İleride yeni tarihsel CSV verilerinin TRADERLAB'a güvenle indirilmesi için uygulanan protokol:
            1. **Git Blob Filtreli İnceleme:** Dosyaları indirmeden önce `git ls-remote` ve `git show` ile uzaktaki son tarih ve satır sayısı doğrulanır.
            2. **Kullanıcı Onayı:** Kullanıcı arayüzden veya CLI'dan açıkça onay vermedikçe otomatik indirme yapılmaz.
            3. **Aşamalı Sparse Checkout:** Yalnızca talep edilen yeni ay dosyaları `sparse-checkout` listesine eklenir; 850 MiB arşiv toplu indirilmez.
            4. **Kriptografik SHA-256 ve Bitemporal Doğrulama:** İndirilen dosyanın SHA-256 özeti hesaplanır, salt-okunur kontrol edilir.
            5. **Kısmi Son Mumun Reddi:** Ay tamamlanmamışsa son dakika mumu kesinleşene kadar kapalı bar olarak kabul edilmez.
            6. **Geri Dönüş (Failover) Garantisi:** Ağ veya veri doğrulama hatası oluşursa mevcut yerel doğrulanmış veriler korunur.
            """
        )


if __name__ == "__main__":
    main()
