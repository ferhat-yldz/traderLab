"""Deterministic multi-timeframe candle resampling engine for financial time series."""

from __future__ import annotations

import enum
import zoneinfo
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from traderlab.core.errors import TraderLabError
from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.infrastructure.calendars.forex_session import XAUUSDTradingCalendar

NEW_YORK_TZ = zoneinfo.ZoneInfo("America/New_York")


class ResamplingError(TraderLabError):
    """Base error for candle resampling operations."""

    def __init__(self, message: str, *, code: str = "RESAMPLING_ERROR") -> None:
        super().__init__(message, code=code)


class MixedPriceTypeError(ResamplingError):
    """Raised when candles with different price types (e.g. BID and ASK) are mixed in resampling."""

    def __init__(self, price_types: set[PriceType]) -> None:
        super().__init__(
            f"Cannot resample mixed price types: {[pt.value for pt in price_types]}. "
            "BID and ASK must be resampled strictly separately.",
            code="MIXED_PRICE_TYPE_ERROR",
        )


class InvalidTimeframeResamplingError(ResamplingError):
    """Raised when attempting invalid downsampling or resampling unsupported timeframes."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_TIMEFRAME_RESAMPLING")


class DailyCloseMode(enum.Enum):
    """Convention for daily (D1) bar closing time."""

    UTC_MIDNIGHT = "UTC_MIDNIGHT"  # 00:00:00 UTC to 00:00:00 UTC next day (1440 minutes)
    NEW_YORK_1700 = "NEW_YORK_1700"  # 17:00:00 NY to 17:00:00 NY next day (Forex standard)


class WindowCompleteness(enum.Enum):
    """Quality and completeness state of an aggregated timeframe window."""

    COMPLETE = "COMPLETE"
    """100% of expected trading M1 bars during open session are present in the window."""

    PARTIAL = "PARTIAL"
    """Some M1 bars are present, but active trading minutes are missing during open session."""

    EMPTY = "EMPTY"
    """No M1 bars present in window."""

    ALL_SYNTHETIC_CLOSED = "ALL_SYNTHETIC_CLOSED"
    """All M1 bars in window are synthetic carry-forward bars during verified market closed periods."""


@dataclass(frozen=True, slots=True)
class ResampledCandle:
    """A resampled higher-timeframe candle paired with its underlying window metrics."""

    candle: Candle
    target_timeframe: Timeframe
    expected_m1_count: int
    present_m1_count: int
    synthetic_m1_count: int
    active_m1_count: int
    completeness: WindowCompleteness
    first_m1_open: datetime
    last_m1_close: datetime
    expected_trading_minutes: int = 0
    missing_trading_minutes: int = 0
    market_closed_minutes: int = 0
    unexpected_trading_minutes: int = 0
    duplicate_trading_minutes: int = 0

    @property
    def calendar_minutes(self) -> int:
        """Total calendar duration in minutes for the window."""
        return self.expected_m1_count

    @property
    def is_session_complete(self) -> bool:
        """Return True if all expected trading minutes during market open are present and zero anomalies exist."""
        return (
            self.missing_trading_minutes == 0
            and self.duplicate_trading_minutes == 0
            and self.unexpected_trading_minutes == 0
            and self.expected_trading_minutes > 0
        )


def get_expected_m1_count(
    target_timeframe: Timeframe,
    window_open: datetime,
    window_close: datetime,
) -> int:
    """Calculate the expected number of M1 bars for a given window."""
    total_seconds = int((window_close - window_open).total_seconds())
    return total_seconds // 60


def get_window_boundaries(
    dt_utc: datetime,
    target_timeframe: Timeframe,
    daily_close_mode: DailyCloseMode = DailyCloseMode.UTC_MIDNIGHT,
) -> tuple[datetime, datetime]:
    """Calculate the [window_open, window_close) boundaries for a given UTC timestamp."""
    dt_utc = dt_utc.replace(tzinfo=UTC) if dt_utc.tzinfo is None else dt_utc.astimezone(UTC)

    if target_timeframe == Timeframe.M1:
        w_open = dt_utc.replace(second=0, microsecond=0)
        return w_open, w_open + timedelta(minutes=1)

    if target_timeframe == Timeframe.M5:
        minute = (dt_utc.minute // 5) * 5
        w_open = dt_utc.replace(minute=minute, second=0, microsecond=0)
        return w_open, w_open + timedelta(minutes=5)

    if target_timeframe == Timeframe.M15:
        minute = (dt_utc.minute // 15) * 15
        w_open = dt_utc.replace(minute=minute, second=0, microsecond=0)
        return w_open, w_open + timedelta(minutes=15)

    if target_timeframe == Timeframe.M30:
        minute = (dt_utc.minute // 30) * 30
        w_open = dt_utc.replace(minute=minute, second=0, microsecond=0)
        return w_open, w_open + timedelta(minutes=30)

    if target_timeframe == Timeframe.H1:
        w_open = dt_utc.replace(minute=0, second=0, microsecond=0)
        return w_open, w_open + timedelta(hours=1)

    if target_timeframe == Timeframe.H2:
        hour = (dt_utc.hour // 2) * 2
        w_open = dt_utc.replace(hour=hour, minute=0, second=0, microsecond=0)
        return w_open, w_open + timedelta(hours=2)

    if target_timeframe == Timeframe.H4:
        hour = (dt_utc.hour // 4) * 4
        w_open = dt_utc.replace(hour=hour, minute=0, second=0, microsecond=0)
        return w_open, w_open + timedelta(hours=4)

    if target_timeframe == Timeframe.D1:
        if daily_close_mode == DailyCloseMode.UTC_MIDNIGHT:
            w_open = dt_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            return w_open, w_open + timedelta(days=1)

        if daily_close_mode == DailyCloseMode.NEW_YORK_1700:
            ny_dt = dt_utc.astimezone(NEW_YORK_TZ)
            start_date = ny_dt.date() - timedelta(days=1) if ny_dt.hour < 17 else ny_dt.date()

            start_ny = datetime(
                start_date.year,
                start_date.month,
                start_date.day,
                17,
                0,
                tzinfo=NEW_YORK_TZ,
            )
            end_ny = start_ny + timedelta(days=1)
            return start_ny.astimezone(UTC), end_ny.astimezone(UTC)

    if target_timeframe == Timeframe.W1:
        if daily_close_mode == DailyCloseMode.UTC_MIDNIGHT:
            # Monday 00:00:00 UTC to next Monday 00:00:00 UTC
            days_since_monday = dt_utc.weekday()
            w_open = (dt_utc - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            return w_open, w_open + timedelta(days=7)

        if daily_close_mode == DailyCloseMode.NEW_YORK_1700:
            ny_dt = dt_utc.astimezone(NEW_YORK_TZ)
            # Forex week starts Sunday 17:00 New York time
            # Sunday is weekday 6
            if ny_dt.weekday() == 6 and ny_dt.hour >= 17:
                days_back = 0
            elif ny_dt.weekday() == 6 and ny_dt.hour < 17:
                days_back = 7
            else:
                days_back = ny_dt.weekday() + 1
            start_date = ny_dt.date() - timedelta(days=days_back)
            start_ny = datetime(
                start_date.year,
                start_date.month,
                start_date.day,
                17,
                0,
                tzinfo=NEW_YORK_TZ,
            )
            end_ny = start_ny + timedelta(days=7)
            return start_ny.astimezone(UTC), end_ny.astimezone(UTC)

    raise InvalidTimeframeResamplingError(
        f"Unsupported target timeframe for resampling: {target_timeframe}"
    )


def resample_m1_candles(
    candles: Sequence[Candle],
    target_timeframe: Timeframe,
    *,
    daily_close_mode: DailyCloseMode = DailyCloseMode.UTC_MIDNIGHT,
    source_id_suffix: str | None = None,
    calendar: XAUUSDTradingCalendar | None = None,
    use_session_calendar: bool = True,
) -> list[ResampledCandle]:
    """Resample an M1 candle sequence into higher timeframe candles deterministically.

    Mathematical aggregation rules:
    - Open = First valid M1 bar's open price.
    - High = Maximum price across all M1 bars in window.
    - Low = Minimum price across all M1 bars in window.
    - Close = Last valid M1 bar's close price.
    - Volume = Sum of non-null volumes (None if all volumes are None).
    - Window intervals: [window_open, window_close) half-open.
    - Point-in-Time: availability_confidence=UNKNOWN, published_at=None, available_at=None.
    - Synthetic flagging: is_synthetic=True only if ALL M1 bars in window are synthetic.
    - Session-Aware Completeness:
      - Evaluates expected trading minutes from trading calendar.
      - Closed market hours (rollover, weekends, holidays) are not counted as data loss.
      - is_closed=True if all expected trading minutes during market open are present (missing_trading_minutes == 0).

    Args:
        candles: Sequence of input M1 Candle objects.
        target_timeframe: Target timeframe (M5, M15, M30, H1, H2, H4, D1, W1).
        daily_close_mode: Daily closing convention for D1 (UTC_MIDNIGHT or NEW_YORK_1700).
        source_id_suffix: Optional suffix for derived source_id (defaults to f"_derived_{tf.name.lower()}").
        calendar: Optional trading calendar instance (defaults to XAUUSDTradingCalendar when use_session_calendar=True).
        use_session_calendar: If True, evaluates session-aware trading minutes; if False, uses pure calendar minutes.

    Returns:
        List of ResampledCandle objects sorted chronologically by window_open.

    Raises:
        InvalidTimeframeResamplingError: If input candles are not M1 or target <= M1.
        MixedPriceTypeError: If candles of different PriceTypes are mixed.
    """
    if not candles:
        return []

    if target_timeframe <= Timeframe.M1:
        raise InvalidTimeframeResamplingError(
            f"Target timeframe {target_timeframe.name} must be strictly coarser than M1."
        )

    # Validate uniformity of PriceType and InstrumentId across candles
    price_types = {c.price_type for c in candles}
    if len(price_types) > 1:
        raise MixedPriceTypeError(price_types)

    instrument_ids = {c.instrument_id for c in candles}
    if len(instrument_ids) > 1:
        raise ResamplingError(f"Cannot resample candles with mixed instruments: {instrument_ids}")

    common_instrument_id = next(iter(instrument_ids))
    common_price_type = next(iter(price_types))

    # Group M1 candles into target windows [window_open, window_close)
    windows: dict[tuple[datetime, datetime], list[Candle]] = defaultdict(list)
    for c in candles:
        if c.timeframe != Timeframe.M1:
            raise InvalidTimeframeResamplingError(
                f"Expected input candles with timeframe M1, got {c.timeframe.name}"
            )
        w_open, w_close = get_window_boundaries(c.open_time, target_timeframe, daily_close_mode)
        windows[(w_open, w_close)].append(c)

    cal: XAUUSDTradingCalendar | None = None
    if use_session_calendar:
        cal = calendar if calendar is not None else XAUUSDTradingCalendar()

    resampled: list[ResampledCandle] = []
    suffix = source_id_suffix or f"_derived_{target_timeframe.name.lower()}"

    for (w_open, w_close), window_candles in sorted(windows.items(), key=lambda item: item[0][0]):
        # Sort candles within window strictly by open_time
        sorted_bars = sorted(window_candles, key=lambda bar: bar.open_time)

        calendar_minutes = get_expected_m1_count(target_timeframe, w_open, w_close)
        present_count = len(sorted_bars)
        synthetic_count = sum(1 for b in sorted_bars if b.is_synthetic)
        active_count = present_count - synthetic_count

        if cal is not None:
            expected_open_timestamps: set[datetime] = set()
            cur = w_open
            step = timedelta(minutes=1)
            while cur < w_close:
                if cal.is_market_open(cur):
                    expected_open_timestamps.add(cur)
                cur += step
            expected_trading_mins = len(expected_open_timestamps)
            market_closed_mins = max(0, calendar_minutes - expected_trading_mins)
        else:
            expected_open_timestamps = {
                w_open + i * timedelta(minutes=1) for i in range(calendar_minutes)
            }
            expected_trading_mins = calendar_minutes
            market_closed_mins = 0

        active_bars = [b for b in sorted_bars if not b.is_synthetic]
        active_timestamps = [b.open_time for b in active_bars]
        active_timestamps_set = set(active_timestamps)
        duplicate_trading_mins = len(active_bars) - len(active_timestamps_set)
        missing_open_timestamps = expected_open_timestamps - active_timestamps_set
        unexpected_closed_timestamps = active_timestamps_set - expected_open_timestamps
        missing_trading_mins = len(missing_open_timestamps)
        unexpected_trading_mins = len(unexpected_closed_timestamps)

        # Determine completeness state
        if present_count == 0:
            completeness = WindowCompleteness.EMPTY
        elif active_count == 0 and synthetic_count > 0:
            completeness = WindowCompleteness.ALL_SYNTHETIC_CLOSED
        elif (
            missing_trading_mins == 0
            and duplicate_trading_mins == 0
            and unexpected_trading_mins == 0
            and (expected_trading_mins > 0 or active_count > 0)
        ):
            completeness = WindowCompleteness.COMPLETE
        elif active_count == 0 and expected_trading_mins == 0 and synthetic_count == 0:
            completeness = WindowCompleteness.EMPTY
        else:
            completeness = WindowCompleteness.PARTIAL

        # Aggregate OHLC
        # For higher timeframe bars (W1, D1, H4, etc.), if active non-synthetic bars exist,
        # the candle's open MUST come from the first active trade quote of the session,
        # high/low from active quotes, and close from the last active quote.
        # This prevents flat synthetic carry-forward bars (which fill closed market hours
        # between 17:00 NY Forex open and 18:00 NY XAUUSD open) from contaminating the
        # new week's opening price with Friday's closing price.
        # Additionally, if session calendar is active, sanitize active bars so that rogue
        # unexpected closed-session bars do not distort open/high/low/close.
        if active_bars:
            if cal is not None and expected_open_timestamps:
                valid_open_bars = [
                    b for b in active_bars if b.open_time in expected_open_timestamps
                ]
                bars_for_ohlc = valid_open_bars if valid_open_bars else active_bars
            else:
                bars_for_ohlc = active_bars

            agg_open = bars_for_ohlc[0].open
            agg_high = max(b.high for b in bars_for_ohlc)
            agg_low = min(b.low for b in bars_for_ohlc)
            agg_close = bars_for_ohlc[-1].close
            volumes = [b.volume for b in bars_for_ohlc if b.volume is not None]
        else:
            agg_open = sorted_bars[0].open
            agg_high = max(b.high for b in sorted_bars)
            agg_low = min(b.low for b in sorted_bars)
            agg_close = sorted_bars[-1].close
            volumes = [b.volume for b in sorted_bars if b.volume is not None]

        # Aggregate volume
        agg_volume = sum(volumes, Decimal(0)) if volumes else None

        # Determine if aggregated candle is fully closed (no missing trading minutes during open market)
        is_closed_bar = completeness in (
            WindowCompleteness.COMPLETE,
            WindowCompleteness.ALL_SYNTHETIC_CLOSED,
        )

        # Derived source ID retains parent lineage
        source_id = DataSourceId(f"{sorted_bars[0].source_id}{suffix}")

        # Determine aggregate Point-in-Time availability metadata
        all_available_ats = [b.available_at for b in sorted_bars if b.available_at is not None]
        agg_available_at: datetime | None = (
            max(all_available_ats) if len(all_available_ats) == present_count else None
        )

        all_ingested_ats = [b.ingested_at for b in sorted_bars if b.ingested_at is not None]
        agg_ingested_at: datetime | None = max(all_ingested_ats) if all_ingested_ats else None

        all_published_ats = [b.published_at for b in sorted_bars if b.published_at is not None]
        agg_published_at: datetime | None = (
            max(all_published_ats) if len(all_published_ats) == present_count else None
        )
        all_received_ats = [b.received_at for b in sorted_bars if b.received_at is not None]
        agg_received_at: datetime | None = (
            max(all_received_ats) if len(all_received_ats) == present_count else None
        )

        confidences = {b.availability_confidence for b in sorted_bars}
        if confidences == {AvailabilityConfidence.VERIFIED} and agg_available_at is not None:
            agg_confidence = AvailabilityConfidence.VERIFIED
        elif AvailabilityConfidence.UNKNOWN in confidences or not confidences:
            agg_confidence = AvailabilityConfidence.UNKNOWN
        else:
            agg_confidence = AvailabilityConfidence.ESTIMATED

        # Construct domain Candle with strict Point-in-Time semantics
        candle = Candle(
            instrument_id=common_instrument_id,
            source_id=source_id,
            timeframe=target_timeframe,
            open_time=w_open,
            close_time=w_close,
            open=agg_open,
            high=agg_high,
            low=agg_low,
            close=agg_close,
            volume=agg_volume,
            price_type=common_price_type,
            published_at=agg_published_at,
            received_at=agg_received_at,
            available_at=agg_available_at,
            ingested_at=agg_ingested_at,
            availability_confidence=agg_confidence,
            spread=None,
            tick_volume=None,
            is_closed=is_closed_bar,
            is_synthetic=(synthetic_count == present_count),
            revision=0,
            allow_negative_prices=False,
        )

        resampled.append(
            ResampledCandle(
                candle=candle,
                target_timeframe=target_timeframe,
                expected_m1_count=calendar_minutes,
                present_m1_count=present_count,
                synthetic_m1_count=synthetic_count,
                active_m1_count=active_count,
                completeness=completeness,
                first_m1_open=sorted_bars[0].open_time,
                last_m1_close=sorted_bars[-1].close_time,
                expected_trading_minutes=expected_trading_mins,
                missing_trading_minutes=missing_trading_mins,
                market_closed_minutes=market_closed_mins,
                unexpected_trading_minutes=unexpected_trading_mins,
                duplicate_trading_minutes=duplicate_trading_mins,
            )
        )

    return resampled


def filter_candles_by_decision_time(
    candles: Sequence[Candle],
    as_of_utc: datetime,
) -> list[Candle]:
    """Filter candles to strictly those finalized on or before the simulated decision time.

    Eliminates lookahead bias:
    An H1 candle (10:00 to 11:00 UTC) cannot be known at 10:30 UTC because its close
    and extrema are only determined at 11:00:00 UTC.

    Args:
        candles: Sequence of Candle objects.
        as_of_utc: Point-in-Time decision timestamp (UTC).

    Returns:
        List of Candle objects where candle.close_time <= as_of_utc.
    """
    if as_of_utc.tzinfo is None:
        as_of_utc = as_of_utc.replace(tzinfo=UTC)
    else:
        as_of_utc = as_of_utc.astimezone(UTC)

    return [c for c in candles if c.close_time <= as_of_utc]


def filter_resampled_by_decision_time(
    resampled: Sequence[ResampledCandle],
    as_of_utc: datetime,
) -> list[ResampledCandle]:
    """Filter resampled candles to strictly those finalized on or before the decision time."""
    if as_of_utc.tzinfo is None:
        as_of_utc = as_of_utc.replace(tzinfo=UTC)
    else:
        as_of_utc = as_of_utc.astimezone(UTC)

    return [r for r in resampled if r.candle.close_time <= as_of_utc]
