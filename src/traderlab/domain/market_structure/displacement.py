"""Point-in-Time Safe Displacement Detection Engine.

Provides mathematically rigorous, deterministic, and Point-in-Time compliant
Displacement identification for ICT/SMC quantitative trading research.

Key architectural and financial principles:
1. **Multi-Factor Measurable Criteria**:
   Displacement requires simultaneous satisfaction of multiple objective metrics:
   - `body_size = abs(close - open)`
   - `total_range = high - low`
   - `body_ratio = body_size / total_range >= min_body_ratio` (default: 0.60)
   - `close_location = (close - low) / total_range`:
     - Bullish: `close > open` and `close_location >= 0.75`
     - Bearish: `close < open` and `close_location <= 0.25`
   - `range_atr_multiple = total_range / historical_atr >= min_range_atr_multiple` (default: 1.50)
2. **Point-in-Time Historical Volatility Reference**:
   - Historical True Range is calculated strictly using completed candles prior to the candle.
   - The current candle's True Range or future candles' True Range never leaks into the reference ATR.
   - If historical completed candles < atr_window, no false displacement is generated.
3. **Point-in-Time Detection Time**:
   - `detection_time = candle.close_time`.
   - Historical queries with `as_of_utc` hide all future displacement events.
4. **Data Quality and Microstructure Guards**:
   - Zero-range candles handled safely without division by zero.
   - Synthetic bars and unclosed bars are rejected.
   - BID/ASK isolation enforced with exact Decimal precision.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from traderlab.core.errors import TraderLabError
from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
    ensure_utc,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.swing import (
    _is_calendar_break,
    _validate_candle_sequence,
)

if TYPE_CHECKING:
    from traderlab.domain.instruments.data_source import DataSource
    from traderlab.domain.instruments.instrument import Instrument


# ── Exceptions ───────────────────────────────────────────────────────────────
class DisplacementError(TraderLabError):
    """Base exception for Displacement domain errors."""

    def __init__(self, message: str, *, code: str = "DISPLACEMENT_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidDisplacementParameterError(DisplacementError):
    """Raised when displacement configuration parameters are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_DISPLACEMENT_PARAMETER")


class DisplacementDataError(DisplacementError):
    """Raised when candle sequences are mixed, non-chronological, unclosed, or corrupted."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="DISPLACEMENT_DATA_ERROR")


# ── Enumerations ────────────────────────────────────────────────────────────
class DisplacementDirection(enum.Enum):
    """Directional orientation of the displacement event."""

    BULLISH = "BULLISH"
    """Bullish energetic expansion (large green body closing at the top of range)."""

    BEARISH = "BEARISH"
    """Bearish energetic expansion (large red body closing at the bottom of range)."""


# ── Deterministic Identifier Generator ───────────────────────────────────────
def generate_displacement_id(
    instrument_id: InstrumentId | str,
    source_id: DataSourceId | str,
    price_type: PriceType | str,
    timeframe: Timeframe | str,
    direction: DisplacementDirection | str,
    candle_open_time: datetime,
) -> str:
    """Generate a deterministic, globally unique string identifier for a displacement event."""
    inst_str = str(instrument_id)
    src_str = str(source_id)
    pt_str = price_type.value if isinstance(price_type, PriceType) else str(price_type)
    tf_str = timeframe.name if isinstance(timeframe, Timeframe) else str(timeframe)
    dir_str = direction.value if isinstance(direction, DisplacementDirection) else str(direction)
    t_str = ensure_utc(candle_open_time).strftime("%Y%m%dT%H%M%SZ")
    return f"{inst_str}_{src_str}_{pt_str}_{tf_str}_DISPLACEMENT_{dir_str}_{t_str}"


# ── Domain Models ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class DisplacementEvent:
    """Immutable domain event representing a confirmed displacement candle.

    Guarantees:
    - **Point-in-Time Safety**: Detected strictly at candle close.
      `detection_time == candle_close_time`.
    - **Exact Decimal Precision**: All price, ratio, and volatility metrics are Decimals.
    """

    event_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    price_type: PriceType
    candle_open_time: datetime
    candle_close_time: datetime
    detection_time: datetime
    direction: DisplacementDirection
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    body_size: Decimal
    total_range: Decimal
    body_ratio: Decimal
    volatility_reference: Decimal
    range_atr_multiple: Decimal
    close_location: Decimal
    criteria_passed: tuple[str, ...]
    confirmation_status: str  # "CONFIRMED"
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.detection_time != self.candle_close_time:
            raise DisplacementError(
                f"detection_time ({self.detection_time}) must equal "
                f"candle_close_time ({self.candle_close_time})"
            )
        if self.candle_close_time <= self.candle_open_time:
            raise DisplacementError(
                f"candle_close_time ({self.candle_close_time}) must be strictly after "
                f"candle_open_time ({self.candle_open_time})"
            )
        if self.total_range < Decimal("0"):
            raise DisplacementError("total_range cannot be negative")
        if self.body_size < Decimal("0"):
            raise DisplacementError("body_size cannot be negative")

    @property
    def is_bullish(self) -> bool:
        """Return True if this is a Bullish Displacement."""
        return self.direction == DisplacementDirection.BULLISH

    @property
    def is_bearish(self) -> bool:
        """Return True if this is a Bearish Displacement."""
        return self.direction == DisplacementDirection.BEARISH


@dataclass(frozen=True, slots=True)
class DisplacementConfig:
    """Configuration settings for the Displacement detection engine."""

    tick_size: Decimal = Decimal("0.01")
    min_body_ratio: Decimal = Decimal("0.60")
    min_bullish_close_location: Decimal = Decimal("0.75")
    max_bearish_close_location: Decimal = Decimal("0.25")
    atr_window: int = 14
    min_range_atr_multiple: Decimal = Decimal("1.50")
    policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED
    strict_pit: bool = False
    allow_synthetic: bool = False
    allow_weekend_gap: bool = True
    allow_rollover_gap: bool = True

    def __post_init__(self) -> None:
        if self.tick_size <= Decimal("0"):
            raise InvalidDisplacementParameterError(
                f"tick_size must be strictly positive Decimal, got {self.tick_size}"
            )
        if not (Decimal("0") < self.min_body_ratio <= Decimal("1.0")):
            raise InvalidDisplacementParameterError(
                f"min_body_ratio must be in (0, 1.0], got {self.min_body_ratio}"
            )
        if not (Decimal("0.5") <= self.min_bullish_close_location <= Decimal("1.0")):
            raise InvalidDisplacementParameterError(
                f"min_bullish_close_location must be in [0.5, 1.0], got {self.min_bullish_close_location}"
            )
        if not (Decimal("0.0") <= self.max_bearish_close_location <= Decimal("0.5")):
            raise InvalidDisplacementParameterError(
                f"max_bearish_close_location must be in [0.0, 0.5], got {self.max_bearish_close_location}"
            )
        if self.atr_window < 1:
            raise InvalidDisplacementParameterError(
                f"atr_window must be >= 1, got {self.atr_window}"
            )
        if self.min_range_atr_multiple <= Decimal("0"):
            raise InvalidDisplacementParameterError(
                f"min_range_atr_multiple must be > 0, got {self.min_range_atr_multiple}"
            )

    @classmethod
    def from_instrument_or_source(
        cls,
        instrument: Instrument | None = None,
        data_source: DataSource | None = None,
        *,
        tick_size: Decimal | None = None,
        min_body_ratio: Decimal = Decimal("0.60"),
        min_bullish_close_location: Decimal = Decimal("0.75"),
        max_bearish_close_location: Decimal = Decimal("0.25"),
        atr_window: int = 14,
        min_range_atr_multiple: Decimal = Decimal("1.50"),
        policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED,
        strict_pit: bool = False,
        allow_synthetic: bool = False,
        allow_weekend_gap: bool = True,
        allow_rollover_gap: bool = True,
    ) -> DisplacementConfig:
        """Resolve tick size dynamically from Instrument or DataSource."""
        resolved_tick: Decimal | None = tick_size

        if resolved_tick is None and data_source is not None and instrument is not None:
            resolved_tick = data_source.effective_tick_size(instrument)
        elif resolved_tick is None and instrument is not None:
            resolved_tick = instrument.tick_size
        elif (
            resolved_tick is None
            and data_source is not None
            and data_source.tick_size_override is not None
        ):
            resolved_tick = data_source.tick_size_override

        if resolved_tick is None:
            resolved_tick = Decimal("0.01")

        return cls(
            tick_size=resolved_tick,
            min_body_ratio=min_body_ratio,
            min_bullish_close_location=min_bullish_close_location,
            max_bearish_close_location=max_bearish_close_location,
            atr_window=atr_window,
            min_range_atr_multiple=min_range_atr_multiple,
            policy=policy,
            strict_pit=strict_pit,
            allow_synthetic=allow_synthetic,
            allow_weekend_gap=allow_weekend_gap,
            allow_rollover_gap=allow_rollover_gap,
        )


# ── Mathematical Volatility Helpers ──────────────────────────────────────────
def compute_true_range(current: Candle, previous: Candle | None = None) -> Decimal:
    """Compute True Range (TR) for a candle with exact Decimal precision.

    TR = max(high - low, abs(high - previous_close), abs(low - previous_close))
    """
    raw_range = current.high - current.low
    if previous is None:
        return raw_range
    gap_high = abs(current.high - previous.close)
    gap_low = abs(current.low - previous.close)
    return max(raw_range, gap_high, gap_low)


def compute_historical_true_ranges(candles: Sequence[Candle]) -> list[Decimal]:
    """Compute True Range sequence for a chronologically sorted candle series."""
    if not candles:
        return []
    trs: list[Decimal] = [compute_true_range(candles[0], None)]
    for i in range(1, len(candles)):
        trs.append(compute_true_range(candles[i], candles[i - 1]))
    return trs


def compute_historical_atr(candles: Sequence[Candle], window: int = 14) -> Decimal | None:
    """Compute simple average True Range over exactly `window` completed historical candles.

    Returns None if fewer than `window` candles are provided.
    """
    if len(candles) < window:
        return None
    eval_candles = candles[-window:]
    trs = compute_historical_true_ranges(eval_candles)
    return sum(trs) / Decimal(window)


# ── Core Displacement Detection Algorithm ────────────────────────────────────
def detect_displacements(
    candles: Sequence[Candle],
    *,
    config: DisplacementConfig | None = None,
    tick_size: Decimal | None = None,
    min_body_ratio: Decimal = Decimal("0.60"),
    min_bullish_close_location: Decimal = Decimal("0.75"),
    max_bearish_close_location: Decimal = Decimal("0.25"),
    atr_window: int = 14,
    min_range_atr_multiple: Decimal = Decimal("1.50"),
    as_of_utc: datetime | None = None,
    policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED,
    strict_pit: bool = False,
    allow_synthetic: bool = False,
    allow_weekend_gap: bool = True,
    allow_rollover_gap: bool = True,
    instrument: Instrument | None = None,
    data_source: DataSource | None = None,
) -> list[DisplacementEvent]:
    """Detect Point-in-Time safe Displacement events from completed candles.

    Evaluates each candle against multi-factor displacement criteria:
    - Body-to-range ratio >= min_body_ratio
    - Directional close location (>= 0.75 for bullish, <= 0.25 for bearish)
    - Total range >= min_range_atr_multiple * historical_atr
    - Strict Point-in-Time safety: historical ATR computed strictly from preceding bars.

    Returns:
        Chronologically sorted list of `DisplacementEvent` objects.
    """
    if config is None:
        config = DisplacementConfig.from_instrument_or_source(
            instrument=instrument,
            data_source=data_source,
            tick_size=tick_size,
            min_body_ratio=min_body_ratio,
            min_bullish_close_location=min_bullish_close_location,
            max_bearish_close_location=max_bearish_close_location,
            atr_window=atr_window,
            min_range_atr_multiple=min_range_atr_multiple,
            policy=policy,
            strict_pit=strict_pit,
            allow_synthetic=allow_synthetic,
            allow_weekend_gap=allow_weekend_gap,
            allow_rollover_gap=allow_rollover_gap,
        )

    if not candles:
        return []

    # 1. Validate candle sequence
    _validate_candle_sequence(candles)

    # 2. Filter candles by PIT criteria, closure, and synthetic rules
    effective_candles: list[Candle] = []
    as_of_norm = ensure_utc(as_of_utc) if as_of_utc is not None else None
    effective_policy = PitPolicy.STRICT_PIT if config.strict_pit else config.policy

    for c in candles:
        if not c.is_closed:
            continue
        if not config.allow_synthetic and c.is_synthetic:
            continue
        # D1 Sunday stub exclusion
        if c.timeframe == Timeframe.D1 and c.open_time.weekday() == 6 and c.open_time.hour == 0:
            continue
        if as_of_norm is not None and c.close_time > as_of_norm:
            continue
        if not c.is_available_at(as_of_norm or c.close_time, policy=effective_policy):
            continue
        effective_candles.append(c)

    if not effective_candles:
        return []

    events: list[DisplacementEvent] = []

    # Precompute True Ranges across all effective candles
    trs = compute_historical_true_ranges(effective_candles)

    for i, bar in enumerate(effective_candles):
        # Gap check against previous bar
        gap_flag = False
        if i > 0:
            gap_flag = _is_calendar_break(
                effective_candles[i - 1],
                bar,
                bar.timeframe,
                allow_weekend_gap=config.allow_weekend_gap,
                allow_rollover_gap=config.allow_rollover_gap,
            )
        gap_flags = ("DATA_GAP_PRECEDING",) if gap_flag else ()

        total_range = bar.high - bar.low
        # 1. Zero Range Safe Handling
        if total_range <= Decimal("0"):
            continue

        body_size = abs(bar.close - bar.open)
        body_ratio = body_size / total_range

        # 2. Body Ratio Criterion
        if body_ratio < config.min_body_ratio:
            continue

        # 3. Close Location Criterion
        close_location = (bar.close - bar.low) / total_range

        # Determine direction
        if bar.close > bar.open:
            if close_location < config.min_bullish_close_location:
                continue
            direction = DisplacementDirection.BULLISH
        elif bar.close < bar.open:
            if close_location > config.max_bearish_close_location:
                continue
            direction = DisplacementDirection.BEARISH
        else:
            # Doji / zero body cannot displace
            continue

        # 4. Historical Volatility Normalization (strictly preceding bars)
        if i < config.atr_window:
            # Insufficient preceding bars to form historical ATR baseline
            continue

        # Historical window strictly excludes current candle i: trs[i - window : i]
        hist_window_trs = trs[i - config.atr_window : i]
        hist_atr = sum(hist_window_trs) / Decimal(config.atr_window)

        if hist_atr <= Decimal("0"):
            continue

        range_atr_multiple = total_range / hist_atr
        if range_atr_multiple < config.min_range_atr_multiple:
            continue

        # All criteria met: create DisplacementEvent
        ev_id = generate_displacement_id(
            bar.instrument_id,
            bar.source_id,
            bar.price_type,
            bar.timeframe,
            direction,
            bar.open_time,
        )

        ev = DisplacementEvent(
            event_id=ev_id,
            instrument_id=bar.instrument_id,
            source_id=bar.source_id,
            timeframe=bar.timeframe,
            price_type=bar.price_type,
            candle_open_time=bar.open_time,
            candle_close_time=bar.close_time,
            detection_time=bar.close_time,
            direction=direction,
            open_price=bar.open,
            high_price=bar.high,
            low_price=bar.low,
            close_price=bar.close,
            body_size=body_size,
            total_range=total_range,
            body_ratio=body_ratio,
            volatility_reference=hist_atr,
            range_atr_multiple=range_atr_multiple,
            close_location=close_location,
            criteria_passed=(
                "MIN_BODY_RATIO",
                "CLOSE_LOCATION",
                "MIN_RANGE_ATR_MULTIPLE",
            ),
            confirmation_status="CONFIRMED",
            flags=gap_flags,
        )
        events.append(ev)

    events.sort(key=lambda e: (e.detection_time, e.candle_open_time, e.event_id))
    return events


# ── Query & Filtering Helpers ────────────────────────────────────────────────
def filter_displacements_by_direction(
    events: Sequence[DisplacementEvent],
    direction: DisplacementDirection,
) -> list[DisplacementEvent]:
    """Filter displacement events by direction."""
    return [e for e in events if e.direction == direction]


def summarize_displacements(events: Sequence[DisplacementEvent]) -> dict[str, int]:
    """Compile aggregated count metrics for displacement events."""
    bullish = sum(1 for e in events if e.is_bullish)
    bearish = sum(1 for e in events if e.is_bearish)
    return {
        "total_displacements": len(events),
        "bullish_displacements": bullish,
        "bearish_displacements": bearish,
    }
