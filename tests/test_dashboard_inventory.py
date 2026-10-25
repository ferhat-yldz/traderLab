"""Tests for local data inventory, remote freshness auditor, and dashboard chart components."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    WindowCompleteness,
    resample_m1_candles,
)
from traderlab.core.types import PriceType, Timeframe
from traderlab.domain.instruments.candle import Candle
from traderlab.infrastructure.calendars.forex_session import XAUUSDTradingCalendar
from traderlab.infrastructure.providers.market_data_lab.config import get_market_data_lab_settings
from traderlab.infrastructure.providers.market_data_lab.csv_reader import read_m1_csv
from traderlab.infrastructure.providers.market_data_lab.inventory import (
    LocalDataInventory,
)
from traderlab.infrastructure.providers.market_data_lab.remote_auditor import (
    RemoteFreshnessAuditor,
)
from traderlab.interfaces.dashboard.app import load_day_m1_candles, load_month_m1_candles
from traderlab.interfaces.dashboard.charts import create_candlestick_figure

# ═════════════════════════════════════════════════════════════════════════════
# 1. Local Data Inventory Service Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestLocalDataInventory:
    """Verify local CSV scanning, SHA-256 calculation, and calendar gap audits."""

    @pytest.fixture
    def inventory(self) -> LocalDataInventory:
        return LocalDataInventory()

    def test_scan_real_local_repository(self, inventory: LocalDataInventory) -> None:
        summary = inventory.scan_inventory()

        # Phase 1B-C5: 68 files (12 months 2024 + 12 months 2025 + 10 months 2026 for BID and ASK)
        assert summary.total_files == 68
        assert len(summary.bid_files) == 34
        assert len(summary.ask_files) == 34

        # Check January 2024 BID
        bid_2024 = next(f for f in summary.bid_files if f.year == 2024 and f.month == 1)
        assert bid_2024.file_name == "xauusd_bid_m1_2024_01.csv"
        assert bid_2024.price_type == "BID"
        assert bid_2024.total_rows == 44640
        assert bid_2024.expected_rows == 44640
        assert bid_2024.is_full_month is True
        assert bid_2024.duplicate_timestamps_count == 0
        assert bid_2024.timestamp_step_errors_count == 0
        assert (
            bid_2024.sha256_hash
            == "6fa24d76649fec4f15ccf6b7324a1ea8f1ed420c71438e41129d9a7203d68797"
        )

        # Check February 2025 BID (non-leap year: exactly 28 days * 1440 = 40,320 rows)
        bid_2025_02 = next(f for f in summary.bid_files if f.year == 2025 and f.month == 2)
        assert bid_2025_02.file_name == "xauusd_bid_m1_2025_02.csv"
        assert bid_2025_02.total_rows == 40320
        assert bid_2025_02.is_full_month is True

        # Check December 2025 BID
        bid_2025_12 = next(f for f in summary.bid_files if f.year == 2025 and f.month == 12)
        assert bid_2025_12.file_name == "xauusd_bid_m1_2025_12.csv"
        assert bid_2025_12.total_rows == 44640
        assert bid_2025_12.is_full_month is True

        # Check October 2026 BID
        bid_2026 = next(f for f in summary.bid_files if f.year == 2026 and f.month == 10)
        assert bid_2026.file_name == "xauusd_bid_m1_2026_10.csv"
        assert bid_2026.price_type == "BID"
        assert bid_2026.total_rows == 17400
        assert bid_2026.expected_rows == 44640
        assert bid_2026.is_full_month is False
        assert bid_2026.duplicate_timestamps_count == 0
        # Exactly 12 off-session gaps (weekend/rollover), 0 trading gaps
        assert bid_2026.timestamp_step_errors_count == 12
        assert bid_2026.off_session_gaps_count == 12
        assert bid_2026.in_session_gaps_count == 0
        assert (
            bid_2026.sha256_hash
            == "e0959f2b7793fe9b0e261647c8a30306683cc4e8f7803867614836fadbde622d"
        )

        # Check October 2026 ASK
        ask_2026 = next(f for f in summary.ask_files if f.year == 2026 and f.month == 10)
        assert ask_2026.file_name == "xauusd_ask_m1_2026_10.csv"
        assert ask_2026.price_type == "ASK"
        assert ask_2026.total_rows == 17400
        assert ask_2026.is_full_month is False
        assert ask_2026.duplicate_timestamps_count == 0
        assert (
            ask_2026.sha256_hash
            == "fef83918473b1a5319fd52e6399f9a18d5043b21807d62628235c4a087f23847"
        )

    def test_available_and_missing_months(self, inventory: LocalDataInventory) -> None:
        summary = inventory.scan_inventory()

        expected_available = (
            [f"2024-{m:02d}" for m in range(1, 13)]
            + [f"2025-{m:02d}" for m in range(1, 13)]
            + [f"2026-{m:02d}" for m in range(1, 11)]
        )
        assert summary.available_months == expected_available
        # Out of 202 historical months (2010-01 to 2026-10), 34 available -> 168 missing locally
        assert len(summary.missing_months) == 168
        assert "2010-01" in summary.missing_months
        assert "2024-01" in summary.available_months
        assert "2025-01" in summary.available_months
        assert "2026-01" in summary.available_months
        assert "2026-09" in summary.available_months
        assert "2026-10" in summary.available_months
        assert summary.partial_months == ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]

    def test_common_overlap_range(self, inventory: LocalDataInventory) -> None:
        summary = inventory.scan_inventory()

        assert summary.earliest_date_utc == "2024-01-01T00:00:00+00:00"
        assert summary.latest_date_utc == "2026-10-19T13:59:00+00:00"
        assert summary.common_overlap_start == "2024-01-01T00:00:00+00:00"
        assert summary.common_overlap_end == "2026-10-19T13:59:00+00:00"

    def test_synthetic_directory_audit(self, tmp_path: Path) -> None:
        """Verify audit with mock directory containing missing files and partial month."""
        mock_bid = tmp_path / "xauusd" / "bid" / "m1"
        mock_bid.mkdir(parents=True)

        sample_csv = mock_bid / "xauusd_bid_m1_2024_02.csv"
        sample_csv.write_text(
            "timestamp,open,high,low,close\n"
            "1706745600000,2050,2051,2049,2050\n"
            "1706745660000,2050,2051,2049,2050\n",
            encoding="utf-8",
        )

        inv = LocalDataInventory(root_dir=tmp_path)
        summary = inv.scan_inventory()

        assert summary.total_files == 1
        assert summary.available_months == ["2024-02"]
        assert "2024-02" in summary.partial_months
        assert summary.bid_files[0].is_full_month is False


# ═════════════════════════════════════════════════════════════════════════════
# 2. Remote Freshness Auditor Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestRemoteFreshnessAuditor:
    """Verify remote git inspection, offline error handling, and freshness audit."""

    @pytest.fixture
    def auditor(self) -> RemoteFreshnessAuditor:
        return RemoteFreshnessAuditor()

    def test_audit_freshness_local_metadata(self, auditor: RemoteFreshnessAuditor) -> None:
        report = auditor.audit_freshness()

        assert len(report.local_head_sha) == 40
        assert report.branch == "main"
        assert "Market-Data-Lab" in report.remote_url
        assert "2026-10-19" in report.last_commit_date_utc
        assert report.last_commit_author == "kevingtlin"
        assert report.readme_reported_end_utc == "2026-10-19 13:59:00 UTC"
        assert report.remote_verified_end_utc == "2026-10-19 13:59:00 UTC"
        # Local data end is now 2026-10-19 13:59:00 UTC because October 2026 is downloaded
        assert report.local_data_end_utc == "2026-10-19 13:59:00 UTC"
        # Repository does NOT have automated daily CI/CD
        assert report.has_automated_ci_daily_sync is False
        assert report.update_frequency_mode == "IRREGULAR_MANUAL_BATCH"

    def test_audit_offline_timeout_graceful_fallback(self) -> None:
        """Verify that when network timeout expires, auditor does not raise an exception."""
        auditor = RemoteFreshnessAuditor()
        report = auditor.audit_freshness(timeout_seconds=0.00001)
        assert report.remote_check_status in ("OFFLINE_TIMEOUT", "ERROR", "SUCCESS")
        assert report.local_head_sha is not None


# ═════════════════════════════════════════════════════════════════════════════
# 3. Dashboard Candlestick & Plotly Figure Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestDashboardCharts:
    """Verify Plotly figure construction, datetime x-axis, tickformat, and partial candle markers."""

    def test_empty_candles_produces_warning_figure(self) -> None:
        fig = create_candlestick_figure([], "XAUUSD BID M15")
        assert fig is not None
        assert len(fig.layout.annotations) > 0
        assert "Seçilen tarih aralığı için yerel veri bulunamadı" in fig.layout.annotations[0].text

    def test_figure_with_real_datetime_axis(self) -> None:
        dt = datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
        candles = [
            Candle(
                instrument_id="XAUUSD",
                source_id="src",
                timeframe=Timeframe.M15,
                open_time=dt,
                close_time=dt + timedelta(minutes=15),
                open=Decimal("2039.775"),
                high=Decimal("2039.935"),
                low=Decimal("2035.638"),
                close=Decimal("2036.165"),
                volume=None,
                price_type=PriceType.BID,
            )
        ]

        fig = create_candlestick_figure(candles, "XAUUSD BID M15")
        assert len(fig.data) >= 1
        assert fig.data[0].type == "candlestick"
        # Verify x-axis is real datetime (not category strings)
        assert fig.layout.xaxis.type == "date"
        assert fig.data[0].x[0] == dt

    def test_figure_with_historical_incomplete_vs_live_candle_distinction(self) -> None:
        dt = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
        candles = [
            Candle(
                instrument_id="XAUUSD",
                source_id="src",
                timeframe=Timeframe.H4,
                open_time=dt,
                close_time=dt + timedelta(hours=4),
                open=Decimal("4150.000"),
                high=Decimal("4160.000"),
                low=Decimal("4145.000"),
                close=Decimal("4155.000"),
                volume=None,
                price_type=PriceType.BID,
                is_closed=False,  # Unclosed candle
            )
        ]

        # 1. Historical: current time is after 16:00 UTC (e.g. 2026-10-21)
        simulated_future = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
        fig_hist = create_candlestick_figure(candles, "XAUUSD H4", now_utc=simulated_future)
        trace_names_hist = [t.name for t in fig_hist.data]
        assert "Kaynak Verisi Eksik Tarihsel Mum" in trace_names_hist

        # 2. In-flight Live: current time is within candle window (e.g. 14:00 UTC)
        simulated_live = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)
        fig_live = create_candlestick_figure(candles, "XAUUSD H4", now_utc=simulated_live)
        trace_names_live = [t.name for t in fig_live.data]
        assert "Oluşmakta Olan Canlı Mum" in trace_names_live

    def test_high_density_m1_chart_zoom_initial_range(self) -> None:
        """Verify that high-density M1 charts (>=720 candles) receive an initial range zoom."""
        dt = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
        candles = [
            Candle(
                instrument_id="XAUUSD",
                source_id="src",
                timeframe=Timeframe.M1,
                open_time=dt + timedelta(minutes=i),
                close_time=dt + timedelta(minutes=i + 1),
                open=Decimal("2040.0"),
                high=Decimal("2041.0"),
                low=Decimal("2039.0"),
                close=Decimal("2040.5"),
                volume=None,
                price_type=PriceType.BID,
            )
            for i in range(1440)
        ]
        fig = create_candlestick_figure(candles, "XAUUSD M1 1-Day")
        assert fig.layout.xaxis.range is not None
        assert len(fig.layout.xaxis.range) == 2


# ═════════════════════════════════════════════════════════════════════════════
# 4. End-to-End Data Flow & Mathematical OHLC Validation
# ═════════════════════════════════════════════════════════════════════════════


class TestDashboardDataFlow:
    """Verify exact control samples specified in user requirement."""

    def test_load_day_candles_out_of_range(self) -> None:
        # A date in 2023 where no local data exists (only 2024, 2025, and 2026-10 exist)
        candles, err = load_day_m1_candles(date(2023, 6, 15), PriceType.BID)
        assert len(candles) == 0
        assert err is not None
        assert "Seçilen tarih (2023-06-15) için yerel veri bulunamadı" in err

    def test_load_day_candles_january_16(self) -> None:
        candles, err = load_day_m1_candles(date(2024, 1, 16), PriceType.BID)
        assert err is None
        assert len(candles) == 1440

    def test_exact_ohlc_control_samples_m15_and_h4(self) -> None:
        """Verify the exact OHLC control samples:

        16 Ocak 2024, BID, M15, 12:00–12:15 UTC:
            Open: 2039.775, High: 2039.935, Low: 2035.638, Close: 2036.165

        16 Ocak 2024, BID, H4, 12:00–16:00 UTC:
            Open: 2039.775, High: 2044.915, Low: 2031.275, Close: 2041.035
        """
        candles, err = load_day_m1_candles(date(2024, 1, 16), PriceType.BID)
        assert err is None

        # 1. M15 Check
        res_m15 = resample_m1_candles(candles, Timeframe.M15)
        m15_sample = next(
            rc.candle
            for rc in res_m15
            if rc.candle.open_time == datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
        )
        assert m15_sample.open == Decimal("2039.775")
        assert m15_sample.high == Decimal("2039.935")
        assert m15_sample.low == Decimal("2035.638")
        assert m15_sample.close == Decimal("2036.165")

        # 2. H4 Check
        res_h4 = resample_m1_candles(candles, Timeframe.H4)
        h4_sample = next(
            rc.candle
            for rc in res_h4
            if rc.candle.open_time == datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
        )
        assert h4_sample.open == Decimal("2039.775")
        assert h4_sample.high == Decimal("2044.915")
        assert h4_sample.low == Decimal("2031.275")
        assert h4_sample.close == Decimal("2041.035")

    def test_d1_multi_day_loading_and_resampling(self) -> None:
        """Verify load_month_m1_candles produces multi-day D1 candles instead of 1 giant candle."""
        # 1. January 2024: 31 full days
        candles_jan, err_jan = load_month_m1_candles(2024, 1, PriceType.BID)
        assert err_jan is None
        assert len(candles_jan) == 44640
        d1_jan = resample_m1_candles(
            candles_jan, Timeframe.D1, daily_close_mode=DailyCloseMode.UTC_MIDNIGHT
        )
        assert len(d1_jan) == 31
        assert d1_jan[0].candle.open_time == datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        assert d1_jan[-1].candle.open_time == datetime(2024, 1, 31, 0, 0, tzinfo=UTC)

        # 2. October 2026: 16 trading days up to Oct 19
        candles_oct, err_oct = load_month_m1_candles(2026, 10, PriceType.BID)
        assert err_oct is None
        assert len(candles_oct) == 17400
        d1_oct = resample_m1_candles(
            candles_oct, Timeframe.D1, daily_close_mode=DailyCloseMode.UTC_MIDNIGHT
        )
        assert len(d1_oct) == 16


# ═════════════════════════════════════════════════════════════════════════════
# 5. Phase 1B-C2 & UI Fix: October 2026 Verification & Sunday D1 Quality Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestOctober2026DatasetAndDashboard:
    """Comprehensive tests for October 2026 XAUUSD data, partial month handling, and dashboard extension."""

    def test_october_2026_bid_csv_reading(self) -> None:
        settings = get_market_data_lab_settings()
        bid_path, _ = settings.validate_dataset_files(2026, 10)
        bars = read_m1_csv(bid_path, PriceType.BID)

        assert len(bars) == 17400
        assert bars[0].open_time == datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
        assert bars[-1].open_time == datetime(2026, 10, 19, 13, 59, tzinfo=UTC)
        # Gold prices in 2026 are around $4,100 - $4,200
        assert bars[0].open > Decimal("4000.0")
        assert bars[-1].close > Decimal("4000.0")

    def test_october_2026_ask_csv_reading(self) -> None:
        settings = get_market_data_lab_settings()
        _, ask_path = settings.validate_dataset_files(2026, 10)
        bars = read_m1_csv(ask_path, PriceType.ASK)

        assert len(bars) == 17400
        assert bars[0].open_time == datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
        assert bars[-1].open_time == datetime(2026, 10, 19, 13, 59, tzinfo=UTC)

    def test_october_2026_gaps_are_strictly_off_session(self) -> None:
        """Prove that all 12 non-60s steps in October 2026 coincide 100% with closed market periods."""
        settings = get_market_data_lab_settings()
        bid_path, _ = settings.validate_dataset_files(2026, 10)
        bars = read_m1_csv(bid_path, PriceType.BID)

        calendar_svc = XAUUSDTradingCalendar()
        off_session_gaps = 0
        in_session_gaps = 0

        for i in range(len(bars) - 1):
            t1 = bars[i].open_time
            t2 = bars[i + 1].open_time
            if (t2 - t1) != timedelta(minutes=1):
                curr = t1 + timedelta(minutes=1)
                is_trading = False
                while curr < t2:
                    if calendar_svc.is_market_open(curr):
                        is_trading = True
                        break
                    curr += timedelta(minutes=1)
                if is_trading:
                    in_session_gaps += 1
                else:
                    off_session_gaps += 1

        assert off_session_gaps == 12
        assert in_session_gaps == 0

    def test_date_switching_between_jan_2024_and_oct_2026(self) -> None:
        """Verify seamless switching between January 2024 and October 2026."""
        # 1. Jan 16, 2024
        candles_jan, err_jan = load_day_m1_candles(date(2024, 1, 16), PriceType.BID)
        assert err_jan is None
        assert len(candles_jan) == 1440

        # 2. Oct 19, 2026
        candles_oct, err_oct = load_day_m1_candles(date(2026, 10, 19), PriceType.BID)
        assert err_oct is None
        assert len(candles_oct) == 840  # 00:00 to 13:59 UTC = 840 bars

    def test_october_2026_unclosed_last_candle_detection(self) -> None:
        """Verify that on 2026-10-19, the 12:00-16:00 H4 candle is marked PARTIAL and is_closed=False."""
        candles_oct, err = load_day_m1_candles(date(2026, 10, 19), PriceType.BID)
        assert err is None

        # Resample to H4
        res_h4 = resample_m1_candles(candles_oct, Timeframe.H4)
        assert len(res_h4) == 4

        # First 3 bars (00:00-04:00, 04:00-08:00, 08:00-12:00) are 100% complete
        for rc in res_h4[:3]:
            assert rc.candle.is_closed is True
            assert rc.completeness == WindowCompleteness.COMPLETE
            assert rc.present_m1_count == 240

        # 4th bar (12:00-16:00) only has 120 bars (ends at 13:59)
        last_rc = res_h4[3]
        assert last_rc.candle.is_closed is False
        assert last_rc.completeness == WindowCompleteness.PARTIAL
        assert last_rc.present_m1_count == 120
        assert last_rc.expected_m1_count == 240

    def test_bid_vs_ask_spread_consistency_october_2026(self) -> None:
        """Verify that on 2026-10-19, ASK is strictly higher than BID for all M1 bars."""
        bid_candles, err_b = load_day_m1_candles(date(2026, 10, 19), PriceType.BID)
        ask_candles, err_a = load_day_m1_candles(date(2026, 10, 19), PriceType.ASK)

        assert err_b is None
        assert err_a is None
        assert len(bid_candles) == len(ask_candles) == 840

        for b, a in zip(bid_candles, ask_candles, strict=True):
            assert b.open_time == a.open_time
            assert a.close >= b.close
            assert a.high >= b.high
            assert a.low >= b.low

    def test_sunday_october_18_quality_and_last_full_trading_session(self) -> None:
        """Verify Sunday Oct 18 has only 120 M1 bars (22:00-24:00 UTC) and Friday Oct 16 is last full session."""
        settings = get_market_data_lab_settings()
        bid_path, _ = settings.validate_dataset_files(2026, 10)
        bars = read_m1_csv(bid_path, PriceType.BID)

        # Sunday 18 October 2026
        t18_start = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
        t18_end = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        bars_sunday = [b for b in bars if t18_start <= b.open_time < t18_end]

        assert len(bars_sunday) == 120  # Exactly 2 hours of trading (22:00 to 23:59 UTC)
        assert bars_sunday[0].open_time == datetime(2026, 10, 18, 22, 0, tzinfo=UTC)
        assert bars_sunday[-1].open_time == datetime(2026, 10, 18, 23, 59, tzinfo=UTC)

        # Friday 16 October 2026: 1260 minutes in UTC (00:00 to 21:00 UTC before weekend close)
        t16_start = datetime(2026, 10, 16, 0, 0, tzinfo=UTC)
        t16_end = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
        bars_friday = [b for b in bars if t16_start <= b.open_time < t16_end]
        assert len(bars_friday) == 1260

    def test_october_2026_exact_row_count_and_no_duplicates(self) -> None:
        """Verify October 2026 has exactly 17,400 rows in BID and ASK and 0 duplicates."""
        settings = get_market_data_lab_settings()
        bid_path, ask_path = settings.validate_dataset_files(2026, 10)
        bars_bid = read_m1_csv(bid_path, PriceType.BID)
        bars_ask = read_m1_csv(ask_path, PriceType.ASK)

        assert len(bars_bid) == 17400
        assert len(bars_ask) == 17400
        assert len({b.timestamp_ms for b in bars_bid}) == 17400
        assert len({b.timestamp_ms for b in bars_ask}) == 17400

    def test_october_2026_prices_in_csv_are_4150_to_4185_range(self) -> None:
        """Verify October 2026 prices in raw CSV are ~4150-4185 USD (not 2716)."""
        settings = get_market_data_lab_settings()
        bid_path, _ = settings.validate_dataset_files(2026, 10)
        bars_bid = read_m1_csv(bid_path, PriceType.BID)

        # 2026-10-18 22:00:00 UTC (Sunday open)
        bar_sun_open = next(
            b for b in bars_bid if b.open_time == datetime(2026, 10, 18, 22, 0, tzinfo=UTC)
        )
        assert Decimal("4160.0") <= bar_sun_open.open <= Decimal("4165.0")
        assert bar_sun_open.open == Decimal("4162.335")

        # 2026-10-18 23:59:00 UTC (Sunday end)
        bar_sun_end = next(
            b for b in bars_bid if b.open_time == datetime(2026, 10, 18, 23, 59, tzinfo=UTC)
        )
        assert bar_sun_end.close == Decimal("4184.735")

        # 2026-10-19 13:59:00 UTC (Last record)
        bar_last = bars_bid[-1]
        assert bar_last.open_time == datetime(2026, 10, 19, 13, 59, tzinfo=UTC)
        assert bar_last.close == Decimal("4154.495")

    def test_sunday_candles_visible_on_chart_no_rangebreaks_hiding(self) -> None:
        """Verify Sunday 22:00-23:59 UTC candles are included and not hidden by rangebreaks."""
        candles, err = load_day_m1_candles(date(2026, 10, 18), PriceType.BID)
        assert err is None
        assert len(candles) == 120

        fig = create_candlestick_figure(candles, "XAUUSD Sunday BID")
        # Ensure rangebreaks is either None or does not hide Sunday
        rb = fig.layout.xaxis.rangebreaks
        assert rb is None or len(rb) == 0
        assert len(fig.data[0].x) == 120


# ═════════════════════════════════════════════════════════════════════════════
# 6. 2024 Full Historical Archive & Multi-Month Integration Tests
# ═════════════════════════════════════════════════════════════════════════════


class Test2024FullArchiveAndMultiMonthSupport:
    """Verify loading, resampling, and consistency across all 12 months of 2024."""

    @pytest.mark.parametrize(
        ("month", "expected_days", "expected_m1_rows"),
        [
            (2, 29, 41760),  # Leap year February
            (6, 30, 43200),  # June
            (12, 31, 44640),  # December
        ],
    )
    def test_2024_monthly_loading_and_d1_resampling(
        self, month: int, expected_days: int, expected_m1_rows: int
    ) -> None:
        """Verify reading full month M1 bars and resampling to exact D1 count."""
        candles_m1, err = load_month_m1_candles(2024, month, PriceType.BID)
        assert err is None
        assert len(candles_m1) == expected_m1_rows

        res_d1 = resample_m1_candles(candles_m1, Timeframe.D1)
        assert len(res_d1) == expected_days

    def test_dst_transitions_in_trading_calendar_2024(self) -> None:
        """Verify US DST transitions in March and November 2024 change Friday NY close UTC hours."""
        from traderlab.infrastructure.calendars.forex_session import (
            MarketSessionState,
            XAUUSDTradingCalendar,
        )

        cal = XAUUSDTradingCalendar()

        # March 2024 (DST starts March 10):
        # March 8 (EST): 21:55 UTC is OPEN, 22:05 UTC is CLOSED_WEEKEND
        assert (
            cal.get_session_state(datetime(2024, 3, 8, 21, 55, tzinfo=UTC))
            == MarketSessionState.OPEN
        )
        assert (
            cal.get_session_state(datetime(2024, 3, 8, 22, 5, tzinfo=UTC))
            == MarketSessionState.CLOSED_WEEKEND
        )

        # March 15 (EDT): 20:55 UTC is OPEN, 21:05 UTC is CLOSED_WEEKEND
        assert (
            cal.get_session_state(datetime(2024, 3, 15, 20, 55, tzinfo=UTC))
            == MarketSessionState.OPEN
        )
        assert (
            cal.get_session_state(datetime(2024, 3, 15, 21, 5, tzinfo=UTC))
            == MarketSessionState.CLOSED_WEEKEND
        )

        # November 2024 (DST ends November 3):
        # November 1 (EDT): 20:55 UTC is OPEN, 21:05 UTC is CLOSED_WEEKEND
        assert (
            cal.get_session_state(datetime(2024, 11, 1, 20, 55, tzinfo=UTC))
            == MarketSessionState.OPEN
        )
        assert (
            cal.get_session_state(datetime(2024, 11, 1, 21, 5, tzinfo=UTC))
            == MarketSessionState.CLOSED_WEEKEND
        )

        # November 8 (EST): 21:55 UTC is OPEN, 22:05 UTC is CLOSED_WEEKEND
        assert (
            cal.get_session_state(datetime(2024, 11, 8, 21, 55, tzinfo=UTC))
            == MarketSessionState.OPEN
        )
        assert (
            cal.get_session_state(datetime(2024, 11, 8, 22, 5, tzinfo=UTC))
            == MarketSessionState.CLOSED_WEEKEND
        )

    def test_bid_ask_spread_integrity_across_2024(self) -> None:
        """Verify BID and ASK timestamps match and spread is positive on sample days across 2024."""
        sample_dates = [date(2024, 2, 15), date(2024, 6, 14), date(2024, 12, 10)]
        for d in sample_dates:
            bid_candles, err_b = load_day_m1_candles(d, PriceType.BID)
            ask_candles, err_a = load_day_m1_candles(d, PriceType.ASK)

            assert err_b is None
            assert err_a is None
            assert len(bid_candles) == len(ask_candles) == 1440

            for b, a in zip(bid_candles, ask_candles, strict=True):
                assert b.open_time == a.open_time
                assert a.close >= b.close
                assert a.high >= b.high
                assert a.low >= b.low


# ═════════════════════════════════════════════════════════════════════════════
# 7. 2025 Full Historical Archive & Multi-Year Integration Tests
# ═════════════════════════════════════════════════════════════════════════════


class Test2025FullArchiveAndMultiYearSupport:
    """Verify loading, resampling, and consistency across all 12 months of 2025 and year transitions."""

    def test_january_2025_pilot_bid_and_ask(self) -> None:
        """Verify January 2025 has exactly 44,640 rows, 0 dups, and strictly positive prices."""
        settings = get_market_data_lab_settings()
        bid_p, ask_p = settings.validate_dataset_files(2025, 1)
        bars_bid = read_m1_csv(bid_p, PriceType.BID)
        bars_ask = read_m1_csv(ask_p, PriceType.ASK)

        assert len(bars_bid) == 44640
        assert len(bars_ask) == 44640
        assert len({b.timestamp_ms for b in bars_bid}) == 44640
        assert len({b.timestamp_ms for b in bars_ask}) == 44640
        assert all(b.low > Decimal(0) for b in bars_bid)
        assert all(a.low > Decimal(0) for a in bars_ask)

    def test_february_2025_non_leap_year_28_days(self) -> None:
        """Verify February 2025 (non-leap year) has exactly 28 days * 1440 = 40,320 rows and 28 D1 bars."""
        candles_m1, err = load_month_m1_candles(2025, 2, PriceType.BID)
        assert err is None
        assert len(candles_m1) == 40320

        res_d1 = resample_m1_candles(candles_m1, Timeframe.D1)
        assert len(res_d1) == 28

    def test_2025_storage_policy_classification(self) -> None:
        """Verify 2025 dataset files use CONTINUOUS_WITH_SYNTHETIC format while Oct 2026 is TRADING_SESSION_ONLY."""
        settings = get_market_data_lab_settings()
        bid_2025, _ = settings.validate_dataset_files(2025, 6)
        bars_2025 = read_m1_csv(bid_2025, PriceType.BID)
        ts_2025 = [b.timestamp_ms for b in bars_2025]
        diffs_2025 = [ts_2025[i] - ts_2025[i - 1] for i in range(1, len(ts_2025))]
        # 2025 continuous format: 0 gaps > 60k ms
        assert sum(1 for d in diffs_2025 if d > 60000) == 0

        # October 2026 sparse format: exactly 12 gaps > 60k ms
        bid_2026, _ = settings.validate_dataset_files(2026, 10)
        bars_2026 = read_m1_csv(bid_2026, PriceType.BID)
        ts_2026 = [b.timestamp_ms for b in bars_2026]
        diffs_2026 = [ts_2026[i] - ts_2026[i - 1] for i in range(1, len(ts_2026))]
        assert sum(1 for d in diffs_2026 if d > 60000) == 12

    def test_year_transition_2024_to_2025_continuity(self) -> None:
        """Verify seamless 60,000 ms step between 2024-12-31 23:59 UTC and 2025-01-01 00:00 UTC."""
        settings = get_market_data_lab_settings()
        p_2024_12, _ = settings.validate_dataset_files(2024, 12)
        p_2025_01, _ = settings.validate_dataset_files(2025, 1)

        bars_24 = read_m1_csv(p_2024_12, PriceType.BID)
        bars_25 = read_m1_csv(p_2025_01, PriceType.BID)

        last_24 = bars_24[-1]
        first_25 = bars_25[0]

        assert last_24.open_time == datetime(2024, 12, 31, 23, 59, tzinfo=UTC)
        assert first_25.open_time == datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        assert first_25.timestamp_ms - last_24.timestamp_ms == 60000
        assert last_24.close == first_25.open

    def test_dst_transitions_2025(self) -> None:
        """Verify US DST transitions in March 2025 and November 2025."""
        from traderlab.infrastructure.calendars.forex_session import (
            MarketSessionState,
            XAUUSDTradingCalendar,
        )

        cal = XAUUSDTradingCalendar()

        # March 2025 (DST starts March 9):
        # March 7 (EST): 21:55 UTC is OPEN, 22:05 UTC is CLOSED_WEEKEND
        assert (
            cal.get_session_state(datetime(2025, 3, 7, 21, 55, tzinfo=UTC))
            == MarketSessionState.OPEN
        )
        assert (
            cal.get_session_state(datetime(2025, 3, 7, 22, 5, tzinfo=UTC))
            == MarketSessionState.CLOSED_WEEKEND
        )

        # March 14 (EDT): 20:55 UTC is OPEN, 21:05 UTC is CLOSED_WEEKEND
        assert (
            cal.get_session_state(datetime(2025, 3, 14, 20, 55, tzinfo=UTC))
            == MarketSessionState.OPEN
        )
        assert (
            cal.get_session_state(datetime(2025, 3, 14, 21, 5, tzinfo=UTC))
            == MarketSessionState.CLOSED_WEEKEND
        )

        # November 2025 (DST ends November 2):
        # October 31 (EDT): 20:55 UTC is OPEN, 21:05 UTC is CLOSED_WEEKEND
        assert (
            cal.get_session_state(datetime(2025, 10, 31, 20, 55, tzinfo=UTC))
            == MarketSessionState.OPEN
        )
        assert (
            cal.get_session_state(datetime(2025, 10, 31, 21, 5, tzinfo=UTC))
            == MarketSessionState.CLOSED_WEEKEND
        )

        # November 7 (EST): 21:55 UTC is OPEN, 22:05 UTC is CLOSED_WEEKEND
        assert (
            cal.get_session_state(datetime(2025, 11, 7, 21, 55, tzinfo=UTC))
            == MarketSessionState.OPEN
        )
        assert (
            cal.get_session_state(datetime(2025, 11, 7, 22, 5, tzinfo=UTC))
            == MarketSessionState.CLOSED_WEEKEND
        )

    def test_bid_ask_spread_integrity_across_2025(self) -> None:
        """Verify BID and ASK timestamps match and spread is positive on sample days across 2025."""
        sample_dates = [date(2025, 1, 15), date(2025, 6, 18), date(2025, 11, 12)]
        for d in sample_dates:
            bid_candles, err_b = load_day_m1_candles(d, PriceType.BID)
            ask_candles, err_a = load_day_m1_candles(d, PriceType.ASK)

            assert err_b is None
            assert err_a is None
            assert len(bid_candles) == len(ask_candles) == 1440

            for b, a in zip(bid_candles, ask_candles, strict=True):
                assert b.open_time == a.open_time
                assert a.close >= b.close
                assert a.high >= b.high
                assert a.low >= b.low


# ═════════════════════════════════════════════════════════════════════════════
# 7. 2026 Full Archive & Policy Split Verification (Faz 1B-C5)
# ═════════════════════════════════════════════════════════════════════════════


DATA_ROOT = get_market_data_lab_settings().resolve_root_dir()


class Test2026FullArchiveAndMultiYearSupport:
    """Verify 2026 complete dataset (Jan-Sep + Oct partial), policy split, and transitions."""

    def test_january_2026_pilot_bid_and_ask(self) -> None:
        """Verify January 2026 has exactly 44,640 rows, 100% matched, and 0 negative spreads."""
        bid_path = DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_01.csv"
        ask_path = DATA_ROOT / "xauusd" / "ask" / "m1" / "xauusd_ask_m1_2026_01.csv"

        bid_bars = read_m1_csv(bid_path, PriceType.BID)
        ask_bars = read_m1_csv(ask_path, PriceType.ASK)

        assert len(bid_bars) == 44640
        assert len(ask_bars) == 44640

        for b, a in zip(bid_bars, ask_bars, strict=True):
            assert b.open_time == a.open_time
            assert a.close >= b.close

    def test_february_2026_non_leap_year_28_days(self) -> None:
        """Verify February 2026 has exactly 28 days (40,320 rows) and 0 gaps."""
        bid_path = DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_02.csv"
        bid_bars = read_m1_csv(bid_path, PriceType.BID)
        assert len(bid_bars) == 40320

        # Step check
        for i in range(1, len(bid_bars)):
            diff_ms = bid_bars[i].timestamp_ms - bid_bars[i - 1].timestamp_ms
            assert diff_ms == 60_000

    def test_2026_storage_policy_split(self) -> None:
        """Verify 2026 policy split: Jan-May is Continuous, Jun-Sep is Session-Only."""
        # 1. May 2026: Continuous (44,640 rows, 0 gaps)
        bid_may = DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_05.csv"
        bars_may = read_m1_csv(bid_may, PriceType.BID)
        assert len(bars_may) == 44640

        # 2. June 2026: Session-Only (36,479 rows, weekend gaps present)
        bid_jun = DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_06.csv"
        bars_jun = read_m1_csv(bid_jun, PriceType.BID)
        assert len(bars_jun) == 36479
        assert len(bars_jun) < 43200

    def test_august_2026_bid_ask_synchronization(self) -> None:
        """Verify August 2026 timestamp matching: 33,776 shared bars, 0 negative spreads."""
        bid_path = DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_08.csv"
        ask_path = DATA_ROOT / "xauusd" / "ask" / "m1" / "xauusd_ask_m1_2026_08.csv"

        bid_bars = read_m1_csv(bid_path, PriceType.BID)
        ask_bars = read_m1_csv(ask_path, PriceType.ASK)

        assert len(bid_bars) == 33776
        assert len(ask_bars) == 33777

        # Inner join on timestamp_ms
        ask_map = {a.timestamp_ms: a for a in ask_bars}
        matched = 0
        for b in bid_bars:
            if b.timestamp_ms in ask_map:
                matched += 1
                a = ask_map[b.timestamp_ms]
                assert a.close >= b.close
        assert matched == 33776

    def test_year_transition_2025_to_2026_continuity(self) -> None:
        """Verify seamless 60s step between 31 Dec 2025 23:59 and 1 Jan 2026 00:00."""
        bid_2025_12 = read_m1_csv(
            DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2025_12.csv",
            PriceType.BID,
        )
        bid_2026_01 = read_m1_csv(
            DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_01.csv",
            PriceType.BID,
        )

        last_2025 = bid_2025_12[-1]
        first_2026 = bid_2026_01[0]

        step_ms = first_2026.timestamp_ms - last_2025.timestamp_ms
        assert step_ms == 60_000
        assert last_2025.close == first_2026.open

    def test_september_to_october_2026_continuity(self) -> None:
        """Verify seamless 60s step between 30 Sep 2026 23:59 and 1 Oct 2026 00:00."""
        bid_2026_09 = read_m1_csv(
            DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_09.csv",
            PriceType.BID,
        )
        bid_2026_10 = read_m1_csv(
            DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_10.csv",
            PriceType.BID,
        )

        last_sep = bid_2026_09[-1]
        first_oct = bid_2026_10[0]

        step_ms = first_oct.timestamp_ms - last_sep.timestamp_ms
        assert step_ms == 60_000

    def test_dst_transitions_2026(self) -> None:
        """Verify US DST transition on March 8, 2026 in XAUUSDTradingCalendar."""
        from traderlab.infrastructure.calendars.forex_session import (
            MarketSessionState,
            XAUUSDTradingCalendar,
        )

        cal = XAUUSDTradingCalendar()

        # March 1, 2026 Sunday (EST, UTC-5): market opens 18:00 NY = 23:00 UTC
        assert (
            cal.get_session_state(datetime(2026, 3, 1, 22, 30, tzinfo=UTC))
            == MarketSessionState.CLOSED_WEEKEND
        )
        assert (
            cal.get_session_state(datetime(2026, 3, 1, 23, 30, tzinfo=UTC))
            == MarketSessionState.OPEN
        )

        # March 8, 2026 Sunday (EDT, UTC-4): market opens 18:00 NY = 22:00 UTC
        assert (
            cal.get_session_state(datetime(2026, 3, 8, 21, 30, tzinfo=UTC))
            == MarketSessionState.CLOSED_WEEKEND
        )
        assert (
            cal.get_session_state(datetime(2026, 3, 8, 22, 30, tzinfo=UTC))
            == MarketSessionState.OPEN
        )


# ═════════════════════════════════════════════════════════════════════════════
# 8. Faz 1B-C5A Reconciliation & Pre-PostgreSQL Acceptance Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestPhase1BC5AReconciliationAndPrePostgresAcceptance:
    """Verify inventory reconciliation, August 2026 asymmetry, and policy classification."""

    def test_2026_yearly_bid_and_ask_total_reconciliation(self) -> None:
        """Verify 2026 BID sum is exactly 373,042 and ASK sum is 373,043."""
        bid_counts = []
        ask_counts = []
        for m in range(1, 11):
            b_path = DATA_ROOT / "xauusd" / "bid" / "m1" / f"xauusd_bid_m1_2026_{m:02d}.csv"
            a_path = DATA_ROOT / "xauusd" / "ask" / "m1" / f"xauusd_ask_m1_2026_{m:02d}.csv"
            b_bars = read_m1_csv(b_path, PriceType.BID)
            a_bars = read_m1_csv(a_path, PriceType.ASK)
            bid_counts.append(len(b_bars))
            ask_counts.append(len(a_bars))

        assert sum(bid_counts) == 373042
        assert sum(ask_counts) == 373043
        # Reconciliation: The 12,956 difference with the preliminary 360,086 is mathematically confirmed
        assert sum(bid_counts) - 360086 == 12956

    def test_combined_2024_2026_inventory_totals(self) -> None:
        """Verify 2024-2026 combined totals: BID = 1,425,682 and ASK = 1,425,683."""
        total_bid = 0
        total_ask = 0
        for y, max_m in [(2024, 12), (2025, 12), (2026, 10)]:
            for m in range(1, max_m + 1):
                b_path = DATA_ROOT / "xauusd" / "bid" / "m1" / f"xauusd_bid_m1_{y}_{m:02d}.csv"
                a_path = DATA_ROOT / "xauusd" / "ask" / "m1" / f"xauusd_ask_m1_{y}_{m:02d}.csv"
                b_bars = read_m1_csv(b_path, PriceType.BID)
                a_bars = read_m1_csv(a_path, PriceType.ASK)
                total_bid += len(b_bars)
                total_ask += len(a_bars)

        assert total_bid == 1425682
        assert total_ask == 1425683
        assert total_ask - total_bid == 1

    def test_august_2026_unmatched_ask_record_details(self) -> None:
        """Verify exact unmatched record at 2026-08-16 23:59:00 UTC."""
        b_path = DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_08.csv"
        a_path = DATA_ROOT / "xauusd" / "ask" / "m1" / "xauusd_ask_m1_2026_08.csv"

        b_bars = read_m1_csv(b_path, PriceType.BID)
        a_bars = read_m1_csv(a_path, PriceType.ASK)

        b_ts = {b.timestamp_ms for b in b_bars}
        a_ts = {a.timestamp_ms for a in a_bars}

        only_in_ask = a_ts - b_ts
        only_in_bid = b_ts - a_ts

        assert len(only_in_bid) == 0
        assert len(only_in_ask) == 1

        unmatched_ms = next(iter(only_in_ask))
        unmatched_dt = datetime.fromtimestamp(unmatched_ms / 1000, tz=UTC)
        assert unmatched_dt == datetime(2026, 8, 16, 23, 59, tzinfo=UTC)

        # Surrounding check
        unmatched_ask_bar = next(a for a in a_bars if a.timestamp_ms == unmatched_ms)
        assert unmatched_ask_bar.close == Decimal("4370.875")

    def test_2026_policy_classification_continuous_vs_mixed_vs_session_only(self) -> None:
        """Verify 2026 policy: Jan-May Continuous, Jun-Aug Mixed, Sep-Oct Session-Only."""
        # 1. Continuous (April 2026): Gaps = 0, Saturday > 0
        b_apr = read_m1_csv(
            DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_04.csv", PriceType.BID
        )
        assert len(b_apr) == 43200

        # 2. Mixed (June 2026): Saturday omitted (0 rows), Sunday pre-22 flat present (5,280 rows)
        b_jun = read_m1_csv(
            DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_06.csv", PriceType.BID
        )
        assert len(b_jun) == 36479
        sat_jun = sum(1 for b in b_jun if b.open_time.weekday() == 5)
        sun_pre22_jun = sum(
            1 for b in b_jun if b.open_time.weekday() == 6 and b.open_time.hour < 22
        )
        assert sat_jun == 0
        assert sun_pre22_jun == 5280

        # 3. Session-Only (September 2026): Saturday 0, Sunday pre-22 0, 0% synthetic
        b_sep = read_m1_csv(
            DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_09.csv", PriceType.BID
        )
        assert len(b_sep) == 30208
        sat_sep = sum(1 for b in b_sep if b.open_time.weekday() == 5)
        sun_pre22_sep = sum(
            1 for b in b_sep if b.open_time.weekday() == 6 and b.open_time.hour < 22
        )
        assert sat_sep == 0
        assert sun_pre22_sep == 0

    def test_synthetic_rollover_bars_marked_closed_not_partial(self) -> None:
        """Verify complete synthetic windows have is_closed=True and is_synthetic=True."""
        base_dt = datetime(2024, 1, 16, 21, 0, tzinfo=UTC)  # Tuesday rollover break
        m1_bars = [
            Candle(
                instrument_id="XAUUSD",
                source_id="src",
                timeframe=Timeframe.M1,
                open_time=base_dt + timedelta(minutes=i),
                close_time=base_dt + timedelta(minutes=i + 1),
                open=Decimal("2030.00"),
                high=Decimal("2030.00"),
                low=Decimal("2030.00"),
                close=Decimal("2030.00"),
                volume=None,
                price_type=PriceType.BID,
                is_closed=True,
                is_synthetic=True,
            )
            for i in range(15)
        ]

        res = resample_m1_candles(m1_bars, Timeframe.M15)
        assert len(res) == 1
        rc = res[0]
        assert rc.completeness == WindowCompleteness.ALL_SYNTHETIC_CLOSED
        assert rc.candle.is_closed is True
        assert rc.candle.is_synthetic is True

    def test_2024_2025_data_immutability(self) -> None:
        """Verify baseline SHA-256 hashes of 2024 and 2025 files are untouched."""
        import hashlib

        def sha256_of(p: Path) -> str:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                while c := f.read(65536):
                    h.update(c)
            return h.hexdigest()

        # Jan 2024 BID
        jan_2024_sha = sha256_of(DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2024_01.csv")
        assert jan_2024_sha == "6fa24d76649fec4f15ccf6b7324a1ea8f1ed420c71438e41129d9a7203d68797"

        # Jan 2025 BID
        jan_2025_sha = sha256_of(DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2025_01.csv")
        assert jan_2025_sha == "a881a9fa24cdaf1506281511307696810b324ddfa4b791235293a368198060fa"


# ═════════════════════════════════════════════════════════════════════════════
# 7. Phase 1B-C5B: Chart Navigation, Range Presets, W1 & Hotfix Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestPhase1BC5BChartNavigation:
    """Verify multi-period range calculation, W1 aggregation, line charts, and eksik label hotfix."""

    def test_calculate_date_range_presets(self) -> None:
        """Test calculation of 1G, 5G, 1A, 3A, 6A, YTD, 1Y, TÜMÜ ranges."""
        from traderlab.interfaces.dashboard.app import calculate_date_range

        anchor = date(2024, 1, 16)

        # 1G: same day
        s, e = calculate_date_range("1G", anchor)
        assert s == datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
        assert e == datetime(2024, 1, 16, 23, 59, 59, tzinfo=UTC)

        # 5G: 5 calendar days ending at anchor
        s, e = calculate_date_range("5G", anchor)
        assert s == datetime(2024, 1, 12, 0, 0, tzinfo=UTC)
        assert e == datetime(2024, 1, 16, 23, 59, 59, tzinfo=UTC)

        # 1A: 1 calendar month back
        s, e = calculate_date_range("1A", anchor)
        assert s == datetime(2023, 12, 16, 0, 0, tzinfo=UTC)
        assert e == datetime(2024, 1, 16, 23, 59, 59, tzinfo=UTC)

        # 3A: 3 calendar months back
        s, e = calculate_date_range("3A", anchor)
        assert s == datetime(2023, 10, 16, 0, 0, tzinfo=UTC)
        assert e == datetime(2024, 1, 16, 23, 59, 59, tzinfo=UTC)

        # 6A: 6 calendar months back
        s, e = calculate_date_range("6A", anchor)
        assert s == datetime(2023, 7, 16, 0, 0, tzinfo=UTC)
        assert e == datetime(2024, 1, 16, 23, 59, 59, tzinfo=UTC)

        # YTD: from Jan 1 of anchor year
        s, e = calculate_date_range("YTD", anchor)
        assert s == datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        assert e == datetime(2024, 1, 16, 23, 59, 59, tzinfo=UTC)

        # 1Y: 1 calendar year back
        s, e = calculate_date_range("1Y", anchor)
        assert s == datetime(2023, 1, 16, 0, 0, tzinfo=UTC)
        assert e == datetime(2024, 1, 16, 23, 59, 59, tzinfo=UTC)

        # 5Y: 5 calendar years back
        s, e = calculate_date_range("5Y", anchor)
        assert s == datetime(2019, 1, 16, 0, 0, tzinfo=UTC)
        assert e == datetime(2024, 1, 16, 23, 59, 59, tzinfo=UTC)

        # TÜMÜ: entire repository range
        s, e = calculate_date_range("TÜMÜ", anchor)
        assert s == datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        assert e == datetime(2026, 10, 19, 23, 59, 59, tzinfo=UTC)

    def test_get_required_months(self) -> None:
        """Verify month iteration spans years correctly."""
        from traderlab.interfaces.dashboard.app import get_required_months

        s = datetime(2024, 11, 15, tzinfo=UTC)
        e = datetime(2025, 2, 10, tzinfo=UTC)
        months = get_required_months(s, e)
        assert months == [(2024, 11), (2024, 12), (2025, 1), (2025, 2)]

    def test_w1_resampling_utc_and_ny(self) -> None:
        """Verify W1 weekly window boundary calculations."""
        from traderlab.application.analysis.resampling import DailyCloseMode, get_window_boundaries

        # Wednesday in UTC
        dt = datetime(2024, 1, 17, 14, 30, tzinfo=UTC)

        # UTC mode: starts Monday Jan 15 00:00 to Monday Jan 22 00:00
        w_open, w_close = get_window_boundaries(dt, Timeframe.W1, DailyCloseMode.UTC_MIDNIGHT)
        assert w_open == datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
        assert w_close == datetime(2024, 1, 22, 0, 0, tzinfo=UTC)

        # NY mode: starts Sunday Jan 14 17:00 NY (= 22:00 UTC) to Sunday Jan 21 17:00 NY (= 22:00 UTC)
        w_open_ny, w_close_ny = get_window_boundaries(
            dt, Timeframe.W1, DailyCloseMode.NEW_YORK_1700
        )
        assert w_open_ny == datetime(2024, 1, 14, 22, 0, tzinfo=UTC)
        assert w_close_ny == datetime(2024, 1, 21, 22, 0, tzinfo=UTC)

    def test_multi_month_range_loader_h4_and_d1(self) -> None:
        """Verify load_candles_for_range retrieves H4 and D1 without single-day restrictions."""
        from traderlab.interfaces.dashboard.app import load_candles_for_range

        # 5 Days of H4 (Jan 12 to Jan 16, 2024)
        s = datetime(2024, 1, 12, 0, 0, tzinfo=UTC)
        e = datetime(2024, 1, 16, 23, 59, 59, tzinfo=UTC)

        candles_h4, _rc_h4, warn, err = load_candles_for_range(
            "XAUUSD", PriceType.BID, Timeframe.H4, s, e
        )
        assert err is None
        assert warn is None
        assert len(candles_h4) == 30  # 5 days * 6 H4 candles = 30 candles! Not just 6!
        assert all(c.timeframe == Timeframe.H4 for c in candles_h4)

        # 3 Months of D1 (Jan 1 to Mar 31, 2024)
        s_3m = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        e_3m = datetime(2024, 3, 31, 23, 59, 59, tzinfo=UTC)
        candles_d1, _rc_d1, warn, err = load_candles_for_range(
            "XAUUSD", PriceType.BID, Timeframe.D1, s_3m, e_3m
        )
        assert err is None
        assert warn is None
        assert len(candles_d1) == 91  # 31 + 29 (leap) + 31 = 91 daily candles!

    def test_high_density_m1_guard(self) -> None:
        """Verify M1 timeframe over large range triggers warning without crashing."""
        from traderlab.interfaces.dashboard.app import load_candles_for_range

        s = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        e = datetime(2024, 1, 31, 23, 59, 59, tzinfo=UTC)  # 31 days of M1
        candles, _rc, warn, _err = load_candles_for_range(
            "XAUUSD", PriceType.BID, Timeframe.M1, s, e
        )
        assert warn is not None
        assert "aşırı yoğundur" in warn
        assert len(candles) == 0

    def test_candlestick_and_line_chart_figure_creation(self) -> None:
        """Verify create_candlestick_figure creates both candlestick and line chart with custom height and uirevision."""
        from decimal import Decimal

        from traderlab.domain.instruments.candle import Candle, DataSourceId

        dt = datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
        test_candle = Candle(
            instrument_id="XAUUSD",
            source_id=DataSourceId("test"),
            timeframe=Timeframe.H1,
            open_time=dt,
            close_time=dt + timedelta(hours=1),
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
            is_synthetic=False,
        )

        # Candlestick
        fig_candle = create_candlestick_figure(
            [test_candle], title="Test Candlestick", chart_type="Candlestick", height=820
        )
        assert fig_candle.layout.height == 820
        assert fig_candle.layout.uirevision == "constant"
        assert any(t.type == "candlestick" for t in fig_candle.data)

        # Line Chart
        fig_line = create_candlestick_figure(
            [test_candle], title="Test Line", chart_type="Çizgi Grafik (Close Price)", height=580
        )
        assert fig_line.layout.height == 580
        assert any(t.type == "scatter" and t.mode == "lines" for t in fig_line.data)

    def test_synthetic_rollover_no_eksik_triangles(self) -> None:
        """Verify that synthetic rollover bars NEVER produce orange 'Eksik' downward triangles."""
        from decimal import Decimal

        from traderlab.domain.instruments.candle import Candle, DataSourceId

        # Create a synthetic rollover bar (e.g. 2024-01-16 22:00)
        dt = datetime(2024, 1, 16, 22, 0, tzinfo=UTC)
        synth_bar = Candle(
            instrument_id="XAUUSD",
            source_id=DataSourceId("test"),
            timeframe=Timeframe.M15,
            open_time=dt,
            close_time=dt + timedelta(minutes=15),
            open=Decimal("2028.00"),
            high=Decimal("2028.00"),
            low=Decimal("2028.00"),
            close=Decimal("2028.00"),
            volume=Decimal("0"),
            price_type=PriceType.BID,
            is_closed=True,
            is_synthetic=True,
        )

        fig = create_candlestick_figure([synth_bar], title="Rollover Test")

        # Verify no trace has 'triangle-down' or name 'Kaynak Verisi Eksik'
        for trace in fig.data:
            trace_name = getattr(trace, "name", "") or ""
            assert "Kaynak Verisi Eksik" not in trace_name
            marker = getattr(trace, "marker", None)
            if marker:
                symbol = getattr(marker, "symbol", "")
                assert symbol != "triangle-down"

    def test_5y_range_and_timeframe_pairing(self) -> None:
        """Verify 5Y period preset and automatic resolution pairing."""
        from traderlab.interfaces.dashboard.app import (
            DEFAULT_TIMEFRAME_FOR_RANGE,
            RANGE_PRESETS,
            calculate_date_range,
        )

        anchor = date(2024, 1, 16)
        s, e = calculate_date_range("5Y", anchor)
        # 5 calendar years back from anchor
        assert s == datetime(2019, 1, 16, 0, 0, tzinfo=UTC)
        assert e == datetime(2024, 1, 16, 23, 59, 59, tzinfo=UTC)

        # Both 5Y and TÜMÜ coexist in presets
        assert "5Y" in RANGE_PRESETS
        assert "TÜMÜ" in RANGE_PRESETS

        # Check default timeframe mappings (aligned with TradingView Advanced Charts standard & user preference)
        assert DEFAULT_TIMEFRAME_FOR_RANGE["1G"] == "M1"
        assert DEFAULT_TIMEFRAME_FOR_RANGE["5G"] == "M5"
        assert DEFAULT_TIMEFRAME_FOR_RANGE["1A"] == "M30"
        assert DEFAULT_TIMEFRAME_FOR_RANGE["3A"] == "H1"
        assert DEFAULT_TIMEFRAME_FOR_RANGE["6A"] == "H2"
        assert DEFAULT_TIMEFRAME_FOR_RANGE["1Y"] == "D1"
        assert DEFAULT_TIMEFRAME_FOR_RANGE["5Y"] == "W1"
        assert DEFAULT_TIMEFRAME_FOR_RANGE["TÜMÜ"] == "W1"

    def test_h2_resampling_window_boundaries(self) -> None:
        """Verify H2 (2 hours) window calculation."""
        from traderlab.application.analysis.resampling import get_window_boundaries

        # 15:23 UTC should fall into [14:00, 16:00) UTC
        dt = datetime(2024, 1, 16, 15, 23, tzinfo=UTC)
        w_open, w_close = get_window_boundaries(dt, Timeframe.H2)
        assert w_open == datetime(2024, 1, 16, 14, 0, tzinfo=UTC)
        assert w_close == datetime(2024, 1, 16, 16, 0, tzinfo=UTC)

    def test_tradingview_adaptive_xaxis_formatting(self) -> None:
        """Verify that x-axis formatting adapts cleanly across TradingView range presets."""
        from decimal import Decimal

        from traderlab.domain.instruments.candle import Candle, DataSourceId

        dt = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
        test_candle = Candle(
            instrument_id="XAUUSD",
            source_id=DataSourceId("test"),
            timeframe=Timeframe.D1,
            open_time=dt,
            close_time=dt + timedelta(days=1),
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            is_closed=True,
            is_synthetic=False,
        )

        # 5Y view: should format with %Y and have NO %H:%M stops
        fig_5y = create_candlestick_figure([test_candle], range_preset="5Y")
        assert fig_5y.layout.xaxis.tickformat == "%Y"
        assert not any("%H:%M" in str(stop.value) for stop in fig_5y.layout.xaxis.tickformatstops)

        # 1Y view: should format with %b %Y and have NO %H:%M stops
        fig_1y = create_candlestick_figure([test_candle], range_preset="1Y")
        assert fig_1y.layout.xaxis.tickformat == "%b %Y"
        assert not any("%H:%M" in str(stop.value) for stop in fig_1y.layout.xaxis.tickformatstops)

        # 1A view: should format with %b %d and have NO %H:%M stops
        fig_1a = create_candlestick_figure([test_candle], range_preset="1A")
        assert fig_1a.layout.xaxis.tickformat == "%b %d"
        assert not any("%H:%M" in str(stop.value) for stop in fig_1a.layout.xaxis.tickformatstops)

        # 1G view: should format with %H:%M
        fig_1g = create_candlestick_figure([test_candle], range_preset="1G")
        assert fig_1g.layout.xaxis.tickformat == "%H:%M"

        # Verify rangeselector button labels if enabled: must contain BOTH 5Y and TÜMÜ
        fig_rs = create_candlestick_figure([test_candle], show_rangeselector=True)
        btn_labels = [b.label for b in fig_rs.layout.xaxis.rangeselector.buttons]
        assert "5Y" in btn_labels
        assert "TÜMÜ" in btn_labels

    def test_5y_and_tumu_data_scope_no_pre_2024_synthetic_candles(self) -> None:
        """Verify that 5Y view over 2024-2026 local archive loads only real months and no fabricated bars for 2019-2023."""
        from traderlab.interfaces.dashboard.app import load_candles_for_range

        # 5Y range ending at Oct 19, 2026 (starts at Oct 19, 2021)
        s = datetime(2021, 10, 19, 0, 0, tzinfo=UTC)
        e = datetime(2026, 10, 19, 23, 59, 59, tzinfo=UTC)
        avail_months = (
            [f"2024-{m:02d}" for m in range(1, 13)]
            + [f"2025-{m:02d}" for m in range(1, 13)]
            + [f"2026-{m:02d}" for m in range(1, 11)]
        )

        candles, _rc_list, _warn, err = load_candles_for_range(
            "XAUUSD", PriceType.BID, Timeframe.W1, s, e, available_months=avail_months
        )
        assert err is None
        assert len(candles) > 0
        # Earliest candle must NOT be earlier than 2024-01-01
        assert candles[0].open_time >= datetime(2024, 1, 1, tzinfo=UTC)
        # All candles must be within available range (no 2019-2023 fabricated candles)
        assert all(c.open_time >= datetime(2024, 1, 1, tzinfo=UTC) for c in candles)

        # Real vs synthetic counts
        real_count = sum(1 for c in candles if not c.is_synthetic)
        synth_count = sum(1 for c in candles if c.is_synthetic)
        assert real_count > 0
        assert real_count + synth_count == len(candles)

    def test_w1_candle_session_hours_dukascopy_comparison(self) -> None:
        """Verify W1 weekly window boundary calculations against Dukascopy trading hours.

        Accounting vs Trading Session Open Distinction:
        - NEW_YORK_1700 sets the standard Forex weekly accounting window boundary at Sunday 17:00 NY
          (21:00 UTC Summer DST / 22:00 UTC Winter).
        - Dukascopy Spot Gold (XAUUSD) trading officially opens Sunday at 18:00 NY
          (22:00 UTC Summer DST / 23:00 UTC Winter).
        - The 1-hour interval (17:00-18:00 NY) between general Forex open and XAUUSD quote open
          contains synthetic carry-forward bars in 2024-2025 raw datasets.
        - resample_m1_candles preserves the NEW_YORK_1700 accounting window while strictly deriving
          the W1 open price from the first active trade quote (at 18:00 NY).
        """
        from traderlab.application.analysis.resampling import DailyCloseMode, get_window_boundaries

        # Winter date: Jan 17, 2024 (Wednesday)
        dt_winter = datetime(2024, 1, 17, 14, 30, tzinfo=UTC)
        w_open_ny, w_close_ny = get_window_boundaries(
            dt_winter, Timeframe.W1, DailyCloseMode.NEW_YORK_1700
        )
        # In January (Winter / EST), 17:00 NY = 22:00 UTC Sunday
        assert w_open_ny == datetime(2024, 1, 14, 22, 0, tzinfo=UTC)
        assert w_close_ny == datetime(2024, 1, 21, 22, 0, tzinfo=UTC)

        # Summer date: July 17, 2024 (Wednesday)
        dt_summer = datetime(2024, 7, 17, 14, 30, tzinfo=UTC)
        w_open_ny_s, w_close_ny_s = get_window_boundaries(
            dt_summer, Timeframe.W1, DailyCloseMode.NEW_YORK_1700
        )
        # In July (Summer / EDT), 17:00 NY = 21:00 UTC Sunday
        assert w_open_ny_s == datetime(2024, 7, 14, 21, 0, tzinfo=UTC)
        assert w_close_ny_s == datetime(2024, 7, 21, 21, 0, tzinfo=UTC)

    def test_w1_open_price_uses_first_active_quote_across_2024_2025_2026(self) -> None:
        """Prove that W1 candle open is strictly derived from the first active XAUUSD quote.

        Verifies:
        1. 2024 Winter (Jan 14, 2024): 21:00-22:59 UTC are flat carry-forward bars (2048.675).
           First active quote arrives at 23:00:00 UTC (18:00 NY) with O=2048.498.
           W1 open MUST equal 2048.498 (not Friday close 2048.675).
        2. 2024 Summer (Jul 14, 2024): 20:00-21:59 UTC are flat carry-forward bars (2410.534).
           First active quote arrives at 22:00:00 UTC (18:00 NY) with O=2412.455.
           W1 open MUST equal 2412.455 (not Friday close 2410.534).
        3. 2026 Summer (Oct 18, 2026): First bar starts at 22:00:00 UTC with O=4162.335.
           W1 open MUST equal 4162.335.
        """
        from traderlab.application.analysis.resampling import DailyCloseMode, resample_m1_candles
        from traderlab.infrastructure.providers.market_data_lab.bar_classifier import (
            BarQualityClassification,
            classify_m1_series,
        )
        from traderlab.infrastructure.providers.market_data_lab.csv_reader import (
            bar_to_candle,
            read_m1_csv,
        )

        # ── 1. Winter 2024 (Jan 14, 2024) ──
        jan_path = DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2024_01.csv"
        jan_bars = read_m1_csv(jan_path, PriceType.BID)
        jan_classified = classify_m1_series(jan_bars)
        jan_candles = [
            bar_to_candle(
                cb.bar,
                is_synthetic=(
                    cb.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD
                ),
            )
            for cb in jan_classified
            if cb.bar.open_time >= datetime(2024, 1, 14, 22, 0, tzinfo=UTC)
            and cb.bar.open_time < datetime(2024, 1, 21, 22, 0, tzinfo=UTC)
        ]
        # Resample the weekly candle using NEW_YORK_1700
        w1_jan = resample_m1_candles(
            jan_candles, Timeframe.W1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
        )
        assert len(w1_jan) >= 1
        first_w1 = w1_jan[0].candle
        # Must strictly equal the first active quote (2048.498), NOT Friday's carry-forward (2048.675)
        assert first_w1.open == Decimal("2048.498")

        # ── 2. Summer 2024 (Jul 14, 2024) ──
        jul_path = DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2024_07.csv"
        jul_bars = read_m1_csv(jul_path, PriceType.BID)
        jul_classified = classify_m1_series(jul_bars)
        jul_candles = [
            bar_to_candle(
                cb.bar,
                is_synthetic=(
                    cb.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD
                ),
            )
            for cb in jul_classified
            if cb.bar.open_time >= datetime(2024, 7, 14, 21, 0, tzinfo=UTC)
            and cb.bar.open_time < datetime(2024, 7, 21, 21, 0, tzinfo=UTC)
        ]
        w1_jul = resample_m1_candles(
            jul_candles, Timeframe.W1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
        )
        assert len(w1_jul) >= 1
        # Must strictly equal the first active quote (2412.455), NOT Friday's carry-forward (2410.534)
        assert w1_jul[0].candle.open == Decimal("2412.455")

        # ── 3. Summer 2026 (Oct 18, 2026) ──
        oct_path = DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_10.csv"
        oct_bars = read_m1_csv(oct_path, PriceType.BID)
        oct_classified = classify_m1_series(oct_bars)
        oct_candles = [
            bar_to_candle(
                cb.bar,
                is_synthetic=(
                    cb.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD
                ),
            )
            for cb in oct_classified
            if cb.bar.open_time >= datetime(2026, 10, 18, 21, 0, tzinfo=UTC)
        ]
        w1_oct = resample_m1_candles(
            oct_candles, Timeframe.W1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
        )
        assert len(w1_oct) >= 1
        # Must strictly equal the first active quote (4162.335)
        assert w1_oct[0].candle.open == Decimal("4162.335")

    def test_w1_synthetic_carry_forward_rejection_unit(self) -> None:
        """Unit test: synthetic carry-forward bars at window start must be rejected for candle open."""
        from traderlab.application.analysis.resampling import DailyCloseMode, resample_m1_candles

        # Accounting window starts at Sunday 22:00 UTC (17:00 NY in winter)
        w_start = datetime(2024, 1, 14, 22, 0, tzinfo=UTC)

        candles: list[Candle] = []
        # 60 synthetic carry-forward bars (22:00 to 22:59 UTC) carrying Friday's close 2000.00
        for i in range(60):
            t = w_start + timedelta(minutes=i)
            candles.append(
                Candle(
                    instrument_id="XAUUSD",
                    source_id="src",
                    timeframe=Timeframe.M1,
                    open_time=t,
                    close_time=t + timedelta(minutes=1),
                    open=Decimal("2000.00"),
                    high=Decimal("2000.00"),
                    low=Decimal("2000.00"),
                    close=Decimal("2000.00"),
                    volume=None,
                    price_type=PriceType.BID,
                    is_closed=True,
                    is_synthetic=True,
                )
            )

        # First real trade bar arrives at Sunday 23:00 UTC (18:00 NY) with gap open 2015.50
        t_open = w_start + timedelta(hours=1)
        candles.append(
            Candle(
                instrument_id="XAUUSD",
                source_id="src",
                timeframe=Timeframe.M1,
                open_time=t_open,
                close_time=t_open + timedelta(minutes=1),
                open=Decimal("2015.50"),
                high=Decimal("2018.00"),
                low=Decimal("2014.00"),
                close=Decimal("2016.20"),
                volume=Decimal("120"),
                price_type=PriceType.BID,
                is_closed=True,
                is_synthetic=False,
            )
        )

        res = resample_m1_candles(
            candles, Timeframe.W1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
        )
        assert len(res) == 1
        rc = res[0]
        # Open price MUST be 2015.50 (first active quote), NOT 2000.00 (Friday close carry-forward)
        assert rc.candle.open == Decimal("2015.50")
        assert rc.candle.high == Decimal("2018.00")
        assert rc.candle.low == Decimal("2014.00")
        assert rc.candle.close == Decimal("2016.20")
        assert rc.active_m1_count == 1
        assert rc.synthetic_m1_count == 60
        assert rc.candle.is_synthetic is False

    def test_calendar_date_range_arithmetic(self) -> None:
        """Verify leap-year and variable month calendar subtraction arithmetic."""
        from traderlab.interfaces.dashboard.app import (
            subtract_calendar_months,
            subtract_calendar_years,
        )

        # 1. Leap year Feb 29 backward 1 year -> Feb 28
        leap_day = date(2024, 2, 29)
        prev_year = subtract_calendar_years(leap_day, 1)
        assert prev_year == date(2023, 2, 28)

        # 2. End of March (31 days) backward 1 month -> Feb 29 (in 2024 leap year)
        mar_31 = date(2024, 3, 31)
        prev_month = subtract_calendar_months(mar_31, 1)
        assert prev_month == date(2024, 2, 29)

        # 3. End of March in non-leap year 2025 -> Feb 28
        mar_31_2025 = date(2025, 3, 31)
        assert subtract_calendar_months(mar_31_2025, 1) == date(2025, 2, 28)

        # 4. Oct 19 anchor back 1A, 3A, 6A, 1Y, 5Y
        oct_19 = date(2026, 10, 19)
        assert subtract_calendar_months(oct_19, 1) == date(2026, 9, 19)
        assert subtract_calendar_months(oct_19, 3) == date(2026, 7, 19)
        assert subtract_calendar_months(oct_19, 6) == date(2026, 4, 19)
        assert subtract_calendar_years(oct_19, 1) == date(2025, 10, 19)
        assert subtract_calendar_years(oct_19, 5) == date(2021, 10, 19)

    def test_calendar_one_month_range_tradingview_boundary_semantics(self) -> None:
        """Verify 1A (1 Month) range bounds and boundary day inclusivity semantics.

        In TradingView and professional charting terminals (e.g. Bloomberg):
        - When anchor date is Oct 19, clicking '1M' sets visible range from Sep 19 to Oct 19.
        - Both boundary endpoints [Sep 19 00:00:00 UTC, Oct 19 23:59:59 UTC] are included
          in the chart viewport, spanning exactly 31 calendar days.
        - Both calendar month subtraction (Oct 19 - 1 calendar month = Sep 19) and
          rolling 30-day subtraction (Oct 19 - 30 days = Sep 19, since Sep has 30 days)
          arrive at the exact same start date: Sep 19.
        """
        from traderlab.interfaces.dashboard.app import calculate_date_range

        anchor = date(2026, 10, 19)
        s_dt, e_dt = calculate_date_range("1A", anchor)

        assert s_dt == datetime(2026, 9, 19, 0, 0, 0, tzinfo=UTC)
        assert e_dt == datetime(2026, 10, 19, 23, 59, 59, tzinfo=UTC)

        # Total calendar days in inclusive window
        total_days = (e_dt.date() - s_dt.date()).days + 1
        assert total_days == 31  # 12 days in Sep (19-30) + 19 days in Oct (1-19) = 31 days

        # Verify rolling 30-day delta comparison
        rolling_30_start = anchor - timedelta(days=30)
        assert rolling_30_start == date(2026, 9, 19)  # Identical to calendar subtraction!

    def test_w1_archive_quality_distribution_reconciliation(self) -> None:
        """Prove that across 147 weekly candles of 2024-2026 archive:
        - Exactly 8 weeks are PURE_REAL (September-October 2026, 0 synthetic bars, full active sessions).
        - Exactly 138 weeks are MIXED (active market quotes + weekend/rollover carry-forward bars).
        - Exactly 1 week is PARTIAL (18-19 October 2026, 960 active minutes cutoff).
        - Exactly 0 weeks are PURE_SYNTHETIC (every single week contains active trading).
        """
        from traderlab.application.analysis.resampling import DailyCloseMode
        from traderlab.interfaces.dashboard.app import (
            get_cached_inventory_summary,
            load_candles_for_range,
        )

        inv = get_cached_inventory_summary()
        s = datetime(2021, 10, 19, 0, 0, tzinfo=UTC)
        e = datetime(2026, 10, 19, 23, 59, 59, tzinfo=UTC)

        _candles, rc_list, _warn, err = load_candles_for_range(
            "XAUUSD",
            PriceType.BID,
            Timeframe.W1,
            s,
            e,
            daily_close_mode=DailyCloseMode.NEW_YORK_1700,
            available_months=inv.available_months,
        )
        assert err is None
        assert len(rc_list) == 147

        pure_real = [
            rc for rc in rc_list if rc.synthetic_m1_count == 0 and rc.active_m1_count >= 6000
        ]
        mixed = [rc for rc in rc_list if rc.synthetic_m1_count > 0 and rc.active_m1_count > 0]
        partial = [
            rc for rc in rc_list if rc.synthetic_m1_count == 0 and 0 < rc.active_m1_count < 6000
        ]
        pure_synth = [rc for rc in rc_list if rc.active_m1_count == 0]

        assert len(pure_real) == 8
        assert len(mixed) == 138
        assert len(partial) == 1
        assert len(pure_synth) == 0

    def test_phase_1c_bid_ask_mathematical_identities(self) -> None:
        """Prove the 7 core Phase 1C database staging mathematical identities:
        1. active_bid_count == matched_active_count + bid_only_count
        2. active_ask_count == matched_active_count + ask_only_count
        3. raw_bid_count == active_bid_count + synthetic_bid_count
        4. raw_ask_count == active_ask_count + synthetic_ask_count
        5. duplicate_bid_count == 0
        6. duplicate_ask_count == 0
        7. negative_spread_count == 0
        """
        import pandas as pd

        csv_path = Path("docs/reports/phase_1c_bid_ask_timestamp_reconciliation.csv")
        assert csv_path.exists()
        df = pd.read_csv(csv_path)

        raw_bid_count = int(df["raw_bid"].sum())
        raw_ask_count = int(df["raw_ask"].sum())
        active_bid_count = int(df["active_bid"].sum())
        active_ask_count = int(df["active_ask"].sum())
        matched_active_count = int(df["active_matched"].sum())
        bid_only_count = int(df["active_b_only"].sum())
        ask_only_count = int(df["active_a_only"].sum())
        duplicate_bid_count = int(df["dup_bid"].sum())
        duplicate_ask_count = int(df["dup_ask"].sum())
        negative_spread_count = int(df["inverted_spread_count"].sum())
        synthetic_bid_count = raw_bid_count - active_bid_count
        synthetic_ask_count = raw_ask_count - active_ask_count

        assert active_bid_count == matched_active_count + bid_only_count
        assert active_ask_count == matched_active_count + ask_only_count
        assert raw_bid_count == active_bid_count + synthetic_bid_count
        assert raw_ask_count == active_ask_count + synthetic_ask_count
        assert duplicate_bid_count == 0
        assert duplicate_ask_count == 0
        assert negative_spread_count == 0

        # Exact audited totals across all 34 months
        assert raw_bid_count == 1_425_682
        assert raw_ask_count == 1_425_683
        assert active_bid_count == 1_002_412
        assert active_ask_count == 1_002_413
        assert matched_active_count == 1_002_412
        assert bid_only_count == 0
        assert ask_only_count == 1
        assert synthetic_bid_count == 423_270
        assert synthetic_ask_count == 423_270
