"""Local dataset inventory scanner for Market-Data-Lab historical CSV files."""

from __future__ import annotations

import calendar
import contextlib
import csv
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from traderlab.core.types import PriceType
from traderlab.infrastructure.calendars.forex_session import XAUUSDTradingCalendar
from traderlab.infrastructure.providers.market_data_lab.config import (
    TRADERLAB_PROJECT_ROOT,
    get_market_data_lab_settings,
)

# Regex to extract instrument, side, year, month from filenames like xauusd_bid_m1_2024_01.csv
FILENAME_PATTERN = re.compile(
    r"^(?P<instrument>[a-zA-Z0-9]+)_(?P<side>bid|ask)_m1_(?P<year>\d{4})_(?P<month>\d{2})\.csv$"
)


@dataclass(frozen=True, slots=True)
class FileInventoryRecord:
    """Detailed audit record for a single M1 CSV file."""

    file_path: str
    file_name: str
    price_type: str
    year: int
    month: int
    file_size_bytes: int
    sha256_hash: str
    first_timestamp_ms: int
    first_datetime_utc: str
    last_timestamp_ms: int
    last_datetime_utc: str
    total_rows: int
    expected_rows: int
    is_full_month: bool
    duplicate_timestamps_count: int
    timestamp_step_errors_count: int
    quality_summary: str
    off_session_gaps_count: int = 0
    in_session_gaps_count: int = 0


@dataclass(frozen=True, slots=True)
class LocalInventorySummary:
    """Summary of the local Market-Data-Lab repository inventory."""

    total_files: int
    bid_files: list[FileInventoryRecord]
    ask_files: list[FileInventoryRecord]
    available_months: list[str]
    missing_months: list[str]
    partial_months: list[str]
    earliest_date_utc: str | None
    latest_date_utc: str | None
    common_overlap_start: str | None
    common_overlap_end: str | None

    def to_dict(self) -> dict[str, Any]:
        """Convert inventory summary to JSON-serializable dictionary."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """Convert inventory summary to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


class LocalDataInventory:
    """Service to scan, audit, and index local historical CSV files."""

    def __init__(self, root_dir: Path | None = None) -> None:
        if root_dir is not None:
            self._root_dir = root_dir.resolve()
        else:
            settings = get_market_data_lab_settings()
            self._root_dir = settings.resolve_root_dir()
        self._calendar = XAUUSDTradingCalendar()

    @property
    def root_dir(self) -> Path:
        """Return the root directory being scanned."""
        return self._root_dir

    def audit_csv_file(self, file_path: Path, side: PriceType) -> FileInventoryRecord:
        """Inspect and compute cryptographic and temporal metrics for a single CSV file."""
        file_size = file_path.stat().st_size

        # Compute SHA-256 hash in streaming chunks
        hasher = hashlib.sha256()
        with file_path.open("rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        sha256_hash = hasher.hexdigest()

        # Extract year and month from filename
        match = FILENAME_PATTERN.match(file_path.name)
        if match:
            year = int(match.group("year"))
            month = int(match.group("month"))
        else:
            year, month = 0, 0

        days_in_month = calendar.monthrange(year, month)[1] if year > 0 and 1 <= month <= 12 else 0
        expected_rows = days_in_month * 1440

        # Read CSV row by row to verify actual first, last, duplicates, and steps
        first_ts: int | None = None
        last_ts: int | None = None
        total_rows = 0
        duplicate_count = 0
        step_errors_count = 0
        off_session_gaps_count = 0
        in_session_gaps_count = 0
        prev_ts: int | None = None

        with file_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            with contextlib.suppress(StopIteration):
                next(reader)

            for row in reader:
                if not row or len(row) < 5:
                    continue
                try:
                    ts = int(row[0])
                except ValueError:
                    continue

                if first_ts is None:
                    first_ts = ts

                if prev_ts is not None:
                    if ts == prev_ts:
                        duplicate_count += 1
                    elif ts != prev_ts + 60000:
                        step_errors_count += 1
                        gap_start = datetime.fromtimestamp(prev_ts / 1000.0, tz=UTC)
                        gap_end = datetime.fromtimestamp(ts / 1000.0, tz=UTC)
                        curr = gap_start + timedelta(minutes=1)
                        is_trading_gap = False
                        while curr < gap_end:
                            if self._calendar.is_market_open(curr):
                                is_trading_gap = True
                                break
                            curr += timedelta(minutes=1)

                        if is_trading_gap:
                            in_session_gaps_count += 1
                        else:
                            off_session_gaps_count += 1

                prev_ts = ts
                last_ts = ts
                total_rows += 1

        first_ts_val = first_ts if first_ts is not None else 0
        last_ts_val = last_ts if last_ts is not None else 0

        first_dt = (
            datetime.fromtimestamp(first_ts_val / 1000.0, tz=UTC).isoformat()
            if first_ts is not None
            else ""
        )
        last_dt = (
            datetime.fromtimestamp(last_ts_val / 1000.0, tz=UTC).isoformat()
            if last_ts is not None
            else ""
        )

        is_full_month = total_rows == expected_rows and expected_rows > 0

        if is_full_month and duplicate_count == 0 and step_errors_count == 0:
            quality = f"Complete full calendar month ({total_rows:,} rows, strictly monotonic 60s step, 0 gaps)"
        elif in_session_gaps_count == 0 and duplicate_count == 0:
            quality = (
                f"Partial month ({total_rows:,}/{expected_rows:,} rows, "
                f"0 duplicates, {off_session_gaps_count} off-session gaps [0 trading loss])"
            )
        else:
            quality = (
                f"Partial/Irregular ({total_rows:,}/{expected_rows:,} rows, "
                f"{duplicate_count} duplicates, {in_session_gaps_count} trading gaps, "
                f"{off_session_gaps_count} off-session gaps)"
            )

        return FileInventoryRecord(
            file_path=str(file_path),
            file_name=file_path.name,
            price_type=side.value,
            year=year,
            month=month,
            file_size_bytes=file_size,
            sha256_hash=sha256_hash,
            first_timestamp_ms=first_ts_val,
            first_datetime_utc=first_dt,
            last_timestamp_ms=last_ts_val,
            last_datetime_utc=last_dt,
            total_rows=total_rows,
            expected_rows=expected_rows,
            is_full_month=is_full_month,
            duplicate_timestamps_count=duplicate_count,
            timestamp_step_errors_count=step_errors_count,
            quality_summary=quality,
            off_session_gaps_count=off_session_gaps_count,
            in_session_gaps_count=in_session_gaps_count,
        )

    def scan_inventory(self) -> LocalInventorySummary:
        """Scan all local bid and ask folders and construct inventory summary."""
        bid_dir = self._root_dir / "xauusd" / "bid" / "m1"
        ask_dir = self._root_dir / "xauusd" / "ask" / "m1"

        bid_files: list[FileInventoryRecord] = []
        if bid_dir.exists() and bid_dir.is_dir():
            for p in sorted(bid_dir.glob("xauusd_bid_m1_*.csv")):
                bid_files.append(self.audit_csv_file(p, PriceType.BID))

        ask_files: list[FileInventoryRecord] = []
        if ask_dir.exists() and ask_dir.is_dir():
            for p in sorted(ask_dir.glob("xauusd_ask_m1_*.csv")):
                ask_files.append(self.audit_csv_file(p, PriceType.ASK))

        total_files = len(bid_files) + len(ask_files)

        # Available calendar months
        bid_months = {f"{f.year:04d}-{f.month:02d}" for f in bid_files}
        ask_months = {f"{f.year:04d}-{f.month:02d}" for f in ask_files}
        available_months = sorted(bid_months | ask_months)

        # Full historical reference scope (January 2010 to October 2026 = 202 months)
        all_possible_months: list[str] = []
        for y in range(2010, 2027):
            max_m = 10 if y == 2026 else 12
            for m in range(1, max_m + 1):
                all_possible_months.append(f"{y:04d}-{m:02d}")

        missing_months = [m for m in all_possible_months if m not in available_months]

        # Partial months
        partial_months = sorted(
            {f"{f.year:04d}-{f.month:02d}" for f in (bid_files + ask_files) if not f.is_full_month}
        )

        # Earliest and latest dates across all files
        all_first_dts = [
            f.first_datetime_utc for f in (bid_files + ask_files) if f.first_datetime_utc
        ]
        all_last_dts = [
            f.last_datetime_utc for f in (bid_files + ask_files) if f.last_datetime_utc
        ]

        earliest_date_utc = min(all_first_dts) if all_first_dts else None
        latest_date_utc = max(all_last_dts) if all_last_dts else None

        # Common overlap between BID and ASK
        if bid_files and ask_files:
            bid_start = min(f.first_datetime_utc for f in bid_files if f.first_datetime_utc)
            ask_start = min(f.first_datetime_utc for f in ask_files if f.first_datetime_utc)
            bid_end = max(f.last_datetime_utc for f in bid_files if f.last_datetime_utc)
            ask_end = max(f.last_datetime_utc for f in ask_files if f.last_datetime_utc)
            common_overlap_start = max(bid_start, ask_start)
            common_overlap_end = min(bid_end, ask_end)
        else:
            common_overlap_start = None
            common_overlap_end = None

        return LocalInventorySummary(
            total_files=total_files,
            bid_files=bid_files,
            ask_files=ask_files,
            available_months=available_months,
            missing_months=missing_months,
            partial_months=partial_months,
            earliest_date_utc=earliest_date_utc,
            latest_date_utc=latest_date_utc,
            common_overlap_start=common_overlap_start,
            common_overlap_end=common_overlap_end,
        )

    def write_json_report(self, output_path: Path | None = None) -> Path:
        """Scan inventory and write report to JSON file in docs/reports."""
        summary = self.scan_inventory()
        out = (
            output_path
            if output_path is not None
            else TRADERLAB_PROJECT_ROOT / "docs" / "reports" / "data-inventory-local.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(summary.to_json(), encoding="utf-8")
        return out
