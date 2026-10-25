"""CSV reader and validator for Market-Data-Lab historical M1 dataset."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.infrastructure.providers.market_data_lab.errors import (
    InvalidCandleDataError,
    InvalidCsvSchemaError,
    NegativeSpreadError,
    TimestampAlignmentError,
)

EXPECTED_CSV_HEADERS: list[str] = ["timestamp", "open", "high", "low", "close"]
MILLISECONDS_PER_MINUTE: int = 60_000


@dataclass(frozen=True, slots=True)
class MarketDataLabBar:
    """A single one-minute OHLC price bar from Market-Data-Lab CSV."""

    timestamp_ms: int
    open_time: datetime  # UTC aware
    close_time: datetime  # UTC aware (open_time + 60s)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    price_type: PriceType  # Explicit BID or ASK
    is_flat: bool  # True when open == high == low == close (market closed / carry-forward)


@dataclass(frozen=True, slots=True)
class MergedBidAskBar:
    """Matched BID and ASK bars sharing identical timestamp."""

    timestamp_ms: int
    open_time: datetime
    close_time: datetime
    bid: MarketDataLabBar
    ask: MarketDataLabBar
    spread_close: Decimal  # ask.close - bid.close


@dataclass(frozen=True, slots=True)
class ValidationSummary:
    """Statistical summary of validated BID/ASK dataset."""

    total_bid_rows: int
    total_ask_rows: int
    matched_rows: int
    flat_bid_rows: int
    flat_ask_rows: int
    min_spread: Decimal
    max_spread: Decimal
    avg_spread: Decimal
    start_time: datetime
    end_time: datetime


def read_m1_csv(
    file_path: Path | str,
    price_type: PriceType,
    *,
    max_rows: int | None = None,
    validate_monotonicity: bool = True,
    validate_ohlc: bool = True,
) -> list[MarketDataLabBar]:
    """Read and validate an M1 CSV file from Market-Data-Lab in read-only mode.

    Args:
        file_path: Path to the CSV file.
        price_type: PriceType.BID or PriceType.ASK.
        max_rows: Optional row limit for fast pilot sampling.
        validate_monotonicity: Enforce strictly increasing timestamps with minute alignment.
        validate_ohlc: Enforce high >= low and bounds logic.

    Returns:
        List of validated MarketDataLabBar objects.

    Raises:
        InvalidCsvSchemaError: If CSV header does not match expected schema.
        TimestampAlignmentError: If timestamp is non-monotonic or not minute-aligned.
        InvalidCandleDataError: If price data violates OHLC constraints or is non-positive.
    """
    path = Path(file_path)
    bars: list[MarketDataLabBar] = []
    prev_ts: int | None = None

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise InvalidCsvSchemaError([], EXPECTED_CSV_HEADERS) from None

        # Normalize header tokens (lowercase, strip whitespace)
        clean_header = [col.strip().lower() for col in header]
        if clean_header != EXPECTED_CSV_HEADERS:
            raise InvalidCsvSchemaError(clean_header, EXPECTED_CSV_HEADERS)

        for line_num, row in enumerate(reader, start=2):
            if max_rows is not None and len(bars) >= max_rows:
                break

            if len(row) != 5:
                raise InvalidCsvSchemaError(row, EXPECTED_CSV_HEADERS)

            raw_ts, raw_open, raw_high, raw_low, raw_close = (col.strip() for col in row)

            # 1. Parse and validate timestamp
            try:
                ts_ms = int(raw_ts)
            except ValueError as exc:
                raise TimestampAlignmentError(
                    f"Invalid integer timestamp: '{raw_ts}'", line_num=line_num
                ) from exc

            if ts_ms < 0:
                raise TimestampAlignmentError(f"Negative timestamp: {ts_ms}", line_num=line_num)

            if ts_ms % MILLISECONDS_PER_MINUTE != 0:
                raise TimestampAlignmentError(
                    f"Timestamp {ts_ms} is not aligned to full minute boundary (:00.000)",
                    line_num=line_num,
                )

            if validate_monotonicity and prev_ts is not None and ts_ms <= prev_ts:
                raise TimestampAlignmentError(
                    f"Non-monotonic timestamp: current {ts_ms} <= previous {prev_ts}",
                    line_num=line_num,
                )

            prev_ts = ts_ms

            # 2. Parse and validate prices
            try:
                open_p = Decimal(raw_open)
                high_p = Decimal(raw_high)
                low_p = Decimal(raw_low)
                close_p = Decimal(raw_close)
            except InvalidOperation as exc:
                raise InvalidCandleDataError(
                    f"Non-decimal price value in row {row}", line_num=line_num
                ) from exc

            # Spot Gold / Forex cannot trade at negative or zero price
            if open_p <= 0 or high_p <= 0 or low_p <= 0 or close_p <= 0:
                raise InvalidCandleDataError(
                    f"Non-positive price found (O={open_p}, H={high_p}, L={low_p}, C={close_p})",
                    line_num=line_num,
                )

            # 3. OHLC mathematical consistency
            if validate_ohlc:
                if high_p < low_p:
                    raise InvalidCandleDataError(
                        f"High ({high_p}) is lower than Low ({low_p})",
                        line_num=line_num,
                    )
                if open_p > high_p or open_p < low_p:
                    raise InvalidCandleDataError(
                        f"Open ({open_p}) outside [Low={low_p}, High={high_p}] bounds",
                        line_num=line_num,
                    )
                if close_p > high_p or close_p < low_p:
                    raise InvalidCandleDataError(
                        f"Close ({close_p}) outside [Low={low_p}, High={high_p}] bounds",
                        line_num=line_num,
                    )

            # 4. Construct UTC timestamps
            open_dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)
            close_dt = open_dt + timedelta(minutes=1)
            is_flat = open_p == high_p == low_p == close_p

            bars.append(
                MarketDataLabBar(
                    timestamp_ms=ts_ms,
                    open_time=open_dt,
                    close_time=close_dt,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    price_type=price_type,
                    is_flat=is_flat,
                )
            )

    return bars


def merge_bid_ask_m1(
    bid_bars: Sequence[MarketDataLabBar],
    ask_bars: Sequence[MarketDataLabBar],
    *,
    allow_negative_spread: bool = False,
) -> list[MergedBidAskBar]:
    """Merge BID and ASK bars on matching timestamps and validate closing spread.

    Args:
        bid_bars: Sequence of BID bars.
        ask_bars: Sequence of ASK bars.
        allow_negative_spread: If False, raises NegativeSpreadError when ask.close < bid.close.

    Returns:
        List of synchronized MergedBidAskBar objects.

    Raises:
        NegativeSpreadError: When an inverted market (ask < bid) is encountered.
    """
    ask_by_ts = {bar.timestamp_ms: bar for bar in ask_bars}
    merged: list[MergedBidAskBar] = []

    for bid_bar in bid_bars:
        ask_bar = ask_by_ts.get(bid_bar.timestamp_ms)
        if ask_bar is None:
            continue

        spread = ask_bar.close - bid_bar.close
        if spread < 0 and not allow_negative_spread:
            raise NegativeSpreadError(
                timestamp=str(bid_bar.open_time),
                ask_close=str(ask_bar.close),
                bid_close=str(bid_bar.close),
            )

        merged.append(
            MergedBidAskBar(
                timestamp_ms=bid_bar.timestamp_ms,
                open_time=bid_bar.open_time,
                close_time=bid_bar.close_time,
                bid=bid_bar,
                ask=ask_bar,
                spread_close=spread,
            )
        )

    return merged


def bar_to_candle(
    bar: MarketDataLabBar,
    *,
    instrument_id: str = "XAUUSD",
    source_id: str = "market_data_lab_m1",
    spread: Decimal | None = None,
    is_synthetic: bool | None = None,
) -> Candle:
    """Convert an infrastructure MarketDataLabBar into a domain Candle.

    Applies conservative Point-in-Time semantics:
    - availability_confidence is set strictly to UNKNOWN (historical backfill).
    - published_at, received_at, and available_at are None (not fabricated).
    - is_synthetic is explicitly set if provided, else falls back to bar.is_flat.
    """
    synthetic_flag = is_synthetic if is_synthetic is not None else bar.is_flat
    return Candle(
        instrument_id=InstrumentId(instrument_id),
        source_id=DataSourceId(source_id),
        timeframe=Timeframe.M1,
        open_time=bar.open_time,
        close_time=bar.close_time,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=None,  # Not present in Dukascopy M1 CSV
        price_type=bar.price_type,
        published_at=None,
        received_at=None,
        available_at=None,
        ingested_at=None,
        availability_confidence=AvailabilityConfidence.UNKNOWN,
        spread=spread,
        tick_volume=None,
        is_closed=True,
        is_synthetic=synthetic_flag,
        revision=0,
        allow_negative_prices=False,
    )


def validate_month_data(
    bid_csv_path: Path | str,
    ask_csv_path: Path | str,
    *,
    max_rows: int | None = None,
) -> ValidationSummary:
    """Read, validate, and compute summary statistics for a month's BID and ASK files."""
    bid_bars = read_m1_csv(bid_csv_path, PriceType.BID, max_rows=max_rows)
    ask_bars = read_m1_csv(ask_csv_path, PriceType.ASK, max_rows=max_rows)

    merged = merge_bid_ask_m1(bid_bars, ask_bars)

    if not merged:
        raise InvalidCandleDataError("No overlapping BID and ASK bars found.")

    flat_bid = sum(1 for b in bid_bars if b.is_flat)
    flat_ask = sum(1 for b in ask_bars if b.is_flat)

    spreads = [m.spread_close for m in merged]
    min_spread = min(spreads)
    max_spread = max(spreads)
    avg_spread = sum(spreads) / Decimal(len(spreads))

    return ValidationSummary(
        total_bid_rows=len(bid_bars),
        total_ask_rows=len(ask_bars),
        matched_rows=len(merged),
        flat_bid_rows=flat_bid,
        flat_ask_rows=flat_ask,
        min_spread=min_spread,
        max_spread=max_spread,
        avg_spread=avg_spread.quantize(Decimal("0.0001")),
        start_time=merged[0].open_time,
        end_time=merged[-1].close_time,
    )
