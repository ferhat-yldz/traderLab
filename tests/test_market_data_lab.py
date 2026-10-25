"""Tests for Market-Data-Lab CSV reader, settings, validation, and domain conversion."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from traderlab.core.types import AvailabilityConfidence, PriceType, Timeframe
from traderlab.domain.instruments.candle import Candle
from traderlab.infrastructure.providers.market_data_lab import (
    EXPECTED_CSV_HEADERS,
    ExternalDirectoryNotFoundError,
    InvalidCandleDataError,
    InvalidCsvSchemaError,
    MarketDataLabBar,
    MarketDataLabSettings,
    MissingDatasetFileError,
    NegativeSpreadError,
    TimestampAlignmentError,
    bar_to_candle,
    get_market_data_lab_settings,
    merge_bid_ask_m1,
    read_m1_csv,
    validate_month_data,
)

# ═════════════════════════════════════════════════════════════════════════════
# 1. Configuration & Path Resolution Tests (Independent of external files)
# ═════════════════════════════════════════════════════════════════════════════


class TestMarketDataLabSettings:
    """Test configuration paths and resolution logic."""

    def test_default_settings(self) -> None:
        settings = MarketDataLabSettings()
        assert settings.external_xauusd_data_dir == Path("../Market-Data-Lab")

    def test_resolve_root_dir_success(self, tmp_path: Path) -> None:
        mock_repo = tmp_path / "Market-Data-Lab"
        mock_repo.mkdir()
        settings = MarketDataLabSettings(external_xauusd_data_dir=mock_repo)
        resolved = settings.resolve_root_dir()
        assert resolved == mock_repo.resolve()

    def test_resolve_root_dir_missing_raises_error(self, tmp_path: Path) -> None:
        missing_repo = tmp_path / "NonExistentDir"
        settings = MarketDataLabSettings(external_xauusd_data_dir=missing_repo)
        with pytest.raises(ExternalDirectoryNotFoundError) as exc_info:
            settings.resolve_root_dir()
        assert "NonExistentDir" in str(exc_info.value)
        assert exc_info.value.code == "EXTERNAL_DIR_NOT_FOUND"

    def test_path_construction(self, tmp_path: Path) -> None:
        mock_repo = tmp_path / "Market-Data-Lab"
        mock_repo.mkdir()
        settings = MarketDataLabSettings(external_xauusd_data_dir=mock_repo)

        bid_path = settings.get_bid_csv_path(2024, 1)
        ask_path = settings.get_ask_csv_path(2024, 1)

        expected_bid = mock_repo / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2024_01.csv"
        expected_ask = mock_repo / "xauusd" / "ask" / "m1" / "xauusd_ask_m1_2024_01.csv"

        assert bid_path == expected_bid.resolve()
        assert ask_path == expected_ask.resolve()

    def test_validate_dataset_files_missing_bid(self, tmp_path: Path) -> None:
        mock_repo = tmp_path / "Market-Data-Lab"
        mock_repo.mkdir()
        settings = MarketDataLabSettings(external_xauusd_data_dir=mock_repo)

        with pytest.raises(MissingDatasetFileError) as exc_info:
            settings.validate_dataset_files(2024, 1)
        assert "Missing required BID dataset file" in str(exc_info.value)

    def test_validate_dataset_files_missing_ask(self, tmp_path: Path) -> None:
        mock_repo = tmp_path / "Market-Data-Lab"
        bid_dir = mock_repo / "xauusd" / "bid" / "m1"
        bid_dir.mkdir(parents=True)
        (bid_dir / "xauusd_bid_m1_2024_01.csv").write_text("timestamp,open,high,low,close\n")

        settings = MarketDataLabSettings(external_xauusd_data_dir=mock_repo)
        with pytest.raises(MissingDatasetFileError) as exc_info:
            settings.validate_dataset_files(2024, 1)
        assert "Missing required ASK dataset file" in str(exc_info.value)

    def test_validate_dataset_files_success(self, tmp_path: Path) -> None:
        mock_repo = tmp_path / "Market-Data-Lab"
        bid_dir = mock_repo / "xauusd" / "bid" / "m1"
        ask_dir = mock_repo / "xauusd" / "ask" / "m1"
        bid_dir.mkdir(parents=True)
        ask_dir.mkdir(parents=True)

        bid_file = bid_dir / "xauusd_bid_m1_2024_01.csv"
        ask_file = ask_dir / "xauusd_ask_m1_2024_01.csv"
        bid_file.write_text("timestamp,open,high,low,close\n")
        ask_file.write_text("timestamp,open,high,low,close\n")

        settings = MarketDataLabSettings(external_xauusd_data_dir=mock_repo)
        resolved_bid, resolved_ask = settings.validate_dataset_files(2024, 1)
        assert resolved_bid == bid_file.resolve()
        assert resolved_ask == ask_file.resolve()


# ═════════════════════════════════════════════════════════════════════════════
# 2. Synthetic Unit Fixture Tests (CSV schema, parsing, validations)
# ═════════════════════════════════════════════════════════════════════════════


class TestCsvReaderSynthetic:
    """Unit tests using synthetic in-memory CSV files via tmp_path."""

    def test_valid_csv_reading(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "sample_bid.csv"
        csv_file.write_text(
            "timestamp,open,high,low,close\n"
            "1704067200000,2062.688,2063.100,2062.500,2062.900\n"
            "1704067260000,2062.900,2063.500,2062.800,2063.400\n"
        )

        bars = read_m1_csv(csv_file, PriceType.BID)
        assert len(bars) == 2

        bar0 = bars[0]
        assert bar0.timestamp_ms == 1704067200000
        assert bar0.open_time == datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        assert bar0.close_time == datetime(2024, 1, 1, 0, 1, 0, tzinfo=UTC)
        assert bar0.open == Decimal("2062.688")
        assert bar0.high == Decimal("2063.100")
        assert bar0.low == Decimal("2062.500")
        assert bar0.close == Decimal("2062.900")
        assert bar0.price_type == PriceType.BID
        assert not bar0.is_flat

    def test_flat_bar_detection(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "flat_bar.csv"
        csv_file.write_text(
            "timestamp,open,high,low,close\n1704067200000,2062.688,2062.688,2062.688,2062.688\n"
        )
        bars = read_m1_csv(csv_file, PriceType.BID)
        assert len(bars) == 1
        assert bars[0].is_flat is True

    def test_max_rows_limit(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "multi_row.csv"
        content = "timestamp,open,high,low,close\n"
        for i in range(10):
            ts = 1704067200000 + i * 60000
            content += f"{ts},2062.5,2063.0,2062.0,2062.8\n"
        csv_file.write_text(content)

        bars = read_m1_csv(csv_file, PriceType.BID, max_rows=3)
        assert len(bars) == 3
        assert bars[-1].timestamp_ms == 1704067200000 + 2 * 60000

    def test_invalid_header_raises_error(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "bad_header.csv"
        csv_file.write_text("time,open,high,low,close\n1704067200000,2062,2063,2061,2062\n")

        with pytest.raises(InvalidCsvSchemaError) as exc_info:
            read_m1_csv(csv_file, PriceType.BID)
        assert "Invalid CSV headers" in str(exc_info.value)

    def test_empty_file_raises_schema_error(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("")

        with pytest.raises(InvalidCsvSchemaError):
            read_m1_csv(csv_file, PriceType.BID)

    def test_non_integer_timestamp(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "bad_ts.csv"
        csv_file.write_text("timestamp,open,high,low,close\nINVALID,2062,2063,2061,2062\n")

        with pytest.raises(TimestampAlignmentError) as exc_info:
            read_m1_csv(csv_file, PriceType.BID)
        assert "Invalid integer timestamp" in str(exc_info.value)

    def test_unaligned_minute_timestamp(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "unaligned_ts.csv"
        # 1704067200000 + 12345 ms (not aligned to minute)
        csv_file.write_text("timestamp,open,high,low,close\n1704067212345,2062,2063,2061,2062\n")

        with pytest.raises(TimestampAlignmentError) as exc_info:
            read_m1_csv(csv_file, PriceType.BID)
        assert "not aligned to full minute boundary" in str(exc_info.value)

    def test_non_monotonic_timestamp(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "non_monotonic.csv"
        csv_file.write_text(
            "timestamp,open,high,low,close\n"
            "1704067260000,2062,2063,2061,2062\n"
            "1704067200000,2062,2063,2061,2062\n"
        )

        with pytest.raises(TimestampAlignmentError) as exc_info:
            read_m1_csv(csv_file, PriceType.BID)
        assert "Non-monotonic timestamp" in str(exc_info.value)

    def test_duplicate_timestamp(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "duplicate_ts.csv"
        csv_file.write_text(
            "timestamp,open,high,low,close\n"
            "1704067200000,2062,2063,2061,2062\n"
            "1704067200000,2062,2063,2061,2062\n"
        )

        with pytest.raises(TimestampAlignmentError) as exc_info:
            read_m1_csv(csv_file, PriceType.BID)
        assert "Non-monotonic timestamp" in str(exc_info.value)

    def test_invalid_ohlc_high_less_than_low(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "bad_ohlc.csv"
        csv_file.write_text("timestamp,open,high,low,close\n1704067200000,2062,2050,2065,2062\n")

        with pytest.raises(InvalidCandleDataError) as exc_info:
            read_m1_csv(csv_file, PriceType.BID)
        assert "High (2050) is lower than Low (2065)" in str(exc_info.value)

    def test_invalid_ohlc_open_outside_bounds(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "bad_open.csv"
        csv_file.write_text("timestamp,open,high,low,close\n1704067200000,2070,2065,2055,2060\n")

        with pytest.raises(InvalidCandleDataError) as exc_info:
            read_m1_csv(csv_file, PriceType.BID)
        assert "Open (2070) outside [Low=2055, High=2065] bounds" in str(exc_info.value)

    def test_invalid_ohlc_close_outside_bounds(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "bad_close.csv"
        csv_file.write_text("timestamp,open,high,low,close\n1704067200000,2060,2065,2055,2050\n")

        with pytest.raises(InvalidCandleDataError) as exc_info:
            read_m1_csv(csv_file, PriceType.BID)
        assert "Close (2050) outside [Low=2055, High=2065] bounds" in str(exc_info.value)

    def test_non_positive_price(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "zero_price.csv"
        csv_file.write_text("timestamp,open,high,low,close\n1704067200000,0,2065,0,2060\n")

        with pytest.raises(InvalidCandleDataError) as exc_info:
            read_m1_csv(csv_file, PriceType.BID)
        assert "Non-positive price found" in str(exc_info.value)

    def test_decimal_precision_retained(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "precise.csv"
        csv_file.write_text(
            "timestamp,open,high,low,close\n"
            "1704067200000,2062.123456,2063.987654,2061.111111,2062.999999\n"
        )

        bars = read_m1_csv(csv_file, PriceType.ASK)
        bar = bars[0]
        assert bar.open == Decimal("2062.123456")
        assert bar.high == Decimal("2063.987654")
        assert bar.low == Decimal("2061.111111")
        assert bar.close == Decimal("2062.999999")


# ═════════════════════════════════════════════════════════════════════════════
# 3. BID / ASK Synchronization & Spread Validation Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestBidAskMerge:
    """Test BID/ASK alignment and spread calculations."""

    def test_merge_success(self) -> None:
        dt = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        bid = MarketDataLabBar(
            timestamp_ms=1704067200000,
            open_time=dt,
            close_time=dt,
            open=Decimal("2000.00"),
            high=Decimal("2005.00"),
            low=Decimal("1995.00"),
            close=Decimal("2002.50"),
            price_type=PriceType.BID,
            is_flat=False,
        )
        ask = MarketDataLabBar(
            timestamp_ms=1704067200000,
            open_time=dt,
            close_time=dt,
            open=Decimal("2000.40"),
            high=Decimal("2005.40"),
            low=Decimal("1995.40"),
            close=Decimal("2002.85"),
            price_type=PriceType.ASK,
            is_flat=False,
        )

        merged = merge_bid_ask_m1([bid], [ask])
        assert len(merged) == 1
        assert merged[0].spread_close == Decimal("0.35")
        assert merged[0].bid == bid
        assert merged[0].ask == ask

    def test_negative_spread_raises_error(self) -> None:
        dt = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        bid = MarketDataLabBar(
            timestamp_ms=1704067200000,
            open_time=dt,
            close_time=dt,
            open=Decimal("2000.00"),
            high=Decimal("2005.00"),
            low=Decimal("1995.00"),
            close=Decimal("2003.00"),  # BID close is HIGHER than ASK close
            price_type=PriceType.BID,
            is_flat=False,
        )
        ask = MarketDataLabBar(
            timestamp_ms=1704067200000,
            open_time=dt,
            close_time=dt,
            open=Decimal("2000.00"),
            high=Decimal("2005.00"),
            low=Decimal("1995.00"),
            close=Decimal("2002.50"),  # ASK close is LOWER than BID close
            price_type=PriceType.ASK,
            is_flat=False,
        )

        with pytest.raises(NegativeSpreadError) as exc_info:
            merge_bid_ask_m1([bid], [ask])
        assert "Negative spread" in str(exc_info.value)
        assert exc_info.value.code == "NEGATIVE_SPREAD_ERROR"

    def test_allow_negative_spread_flag(self) -> None:
        dt = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        bid = MarketDataLabBar(
            timestamp_ms=1704067200000,
            open_time=dt,
            close_time=dt,
            open=Decimal("2000"),
            high=Decimal("2005"),
            low=Decimal("1995"),
            close=Decimal("2003"),
            price_type=PriceType.BID,
            is_flat=False,
        )
        ask = MarketDataLabBar(
            timestamp_ms=1704067200000,
            open_time=dt,
            close_time=dt,
            open=Decimal("2000"),
            high=Decimal("2005"),
            low=Decimal("1995"),
            close=Decimal("2002"),
            price_type=PriceType.ASK,
            is_flat=False,
        )

        merged = merge_bid_ask_m1([bid], [ask], allow_negative_spread=True)
        assert len(merged) == 1
        assert merged[0].spread_close == Decimal("-1")


# ═════════════════════════════════════════════════════════════════════════════
# 4. Domain Conversion & PIT Semantics Tests
# ═════════════════════════════════════════════════════════════════════════════


class TestDomainConversion:
    """Verify conversion from MarketDataLabBar to domain Candle object."""

    def test_bar_to_candle_semantics(self) -> None:
        open_dt = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        close_dt = datetime(2024, 1, 1, 0, 1, tzinfo=UTC)

        bar = MarketDataLabBar(
            timestamp_ms=1704067200000,
            open_time=open_dt,
            close_time=close_dt,
            open=Decimal("2062.500"),
            high=Decimal("2063.000"),
            low=Decimal("2062.000"),
            close=Decimal("2062.800"),
            price_type=PriceType.BID,
            is_flat=False,
        )

        candle = bar_to_candle(bar, spread=Decimal("0.35"))
        assert isinstance(candle, Candle)
        assert candle.instrument_id == "XAUUSD"
        assert candle.source_id == "market_data_lab_m1"
        assert candle.timeframe == Timeframe.M1
        assert candle.open_time == open_dt
        assert candle.close_time == close_dt
        assert candle.open == Decimal("2062.500")
        assert candle.high == Decimal("2063.000")
        assert candle.low == Decimal("2062.000")
        assert candle.close == Decimal("2062.800")
        assert candle.price_type == PriceType.BID
        assert candle.volume is None
        assert candle.spread == Decimal("0.35")
        assert candle.is_closed is True
        assert candle.is_synthetic is False

        # STRICT PIT Assertions: Backfilled historical data cannot fabricate arrival timestamps
        assert candle.published_at is None
        assert candle.received_at is None
        assert candle.available_at is None
        assert candle.ingested_at is None
        assert candle.availability_confidence == AvailabilityConfidence.UNKNOWN

    def test_flat_bar_marked_synthetic(self) -> None:
        open_dt = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        close_dt = datetime(2024, 1, 1, 0, 1, tzinfo=UTC)

        bar = MarketDataLabBar(
            timestamp_ms=1704067200000,
            open_time=open_dt,
            close_time=close_dt,
            open=Decimal("2062.500"),
            high=Decimal("2062.500"),
            low=Decimal("2062.500"),
            close=Decimal("2062.500"),
            price_type=PriceType.BID,
            is_flat=True,
        )

        candle = bar_to_candle(bar)
        assert candle.is_synthetic is True


# ═════════════════════════════════════════════════════════════════════════════
# 5. Real Pilot Tests on Checked-out GitHub CSV Sample
# ═════════════════════════════════════════════════════════════════════════════


class TestRealMarketDataLabPilot:
    """Tests executing directly against the real cloned Market-Data-Lab repository."""

    @pytest.fixture
    def settings(self) -> MarketDataLabSettings:
        return get_market_data_lab_settings()

    def test_real_dataset_paths_exist(self, settings: MarketDataLabSettings) -> None:
        """Verify that sibling repository and January 2024 files are present."""
        bid_path, ask_path = settings.validate_dataset_files(2024, 1)
        assert bid_path.is_file()
        assert ask_path.is_file()

    def test_real_csv_headers_and_row_count(self, settings: MarketDataLabSettings) -> None:
        """Verify real January 2024 files have exactly 44,640 rows (31 days * 1440 min)."""
        bid_path, ask_path = settings.validate_dataset_files(2024, 1)

        for path in (bid_path, ask_path):
            with path.open("r", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader)
                assert header == EXPECTED_CSV_HEADERS
                row_count = sum(1 for _ in reader)
                assert row_count == 44640

    def test_real_csv_sample_pilot_slice(self, settings: MarketDataLabSettings) -> None:
        """Verify first 60 minutes of real January 2024 dataset."""
        bid_path, ask_path = settings.validate_dataset_files(2024, 1)

        bid_bars = read_m1_csv(bid_path, PriceType.BID, max_rows=60)
        ask_bars = read_m1_csv(ask_path, PriceType.ASK, max_rows=60)

        assert len(bid_bars) == 60
        assert len(ask_bars) == 60

        merged = merge_bid_ask_m1(bid_bars, ask_bars)
        assert len(merged) == 60

        # On Jan 1 2024 00:00 UTC (New Year holiday), market is closed; Dukascopy carries forward price
        assert bid_bars[0].is_flat is True
        assert ask_bars[0].is_flat is True
        assert merged[0].spread_close > 0

    def test_real_validation_summary_january_pilot(self, settings: MarketDataLabSettings) -> None:
        """Run statistical summary on a 1440-bar (24-hour) sample of the real dataset."""
        bid_path, ask_path = settings.validate_dataset_files(2024, 1)

        summary = validate_month_data(bid_path, ask_path, max_rows=1440)
        assert summary.total_bid_rows == 1440
        assert summary.total_ask_rows == 1440
        assert summary.matched_rows == 1440
        assert summary.min_spread >= Decimal("0")
        assert summary.max_spread > Decimal("0")
        assert summary.start_time == datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
