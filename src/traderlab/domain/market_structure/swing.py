"""Point-in-Time Safe Swing High and Swing Low Detection Engine.

Provides mathematically rigorous, deterministic, and Point-in-Time compliant
Swing High and Swing Low identification for market structure analysis.

Key architectural and financial principles:
1. **Explicit Separation of Pivot and Confirmation Time**:
   - `pivot_time`: The historical timestamp of the candle that formed the peak or trough.
   - `confirmation_time`: The exact timestamp (`close_time` of the `right_bars`-th bar)
     at which the swing became definitively known to a market participant.
   - A swing point CANNOT be known or confirmed prior to its `confirmation_time`.
2. **Deterministic Tie Policies**:
   - `STRICT`: Requires strict inequality; duplicate highs or lows in the window reject the swing.
   - `ALLOW_EQUAL_FIRST`: If equal maximums/minimums exist, the first chronological bar is selected.
   - `ALLOW_EQUAL_LAST`: If equal maximums/minimums exist, the last chronological bar is selected.
3. **Candidate vs Confirmed State Separation**:
   - `CANDIDATE`: Satisfies left-bars condition and currently unbreached, but right-bars window is incomplete.
   - `CONFIRMED`: Fully satisfied across both left-bars and right-bars upon right-bar close.
   - `INVALIDATED`: A candidate whose pivot price was breached before right-side confirmation was achieved.
4. **Data Quality & Microstructure Guards**:
   - Requires completed candles (`is_closed=True`).
   - Synthetic bars are rejected from being pivots and cannot confirm swings.
   - BID and ASK series are strictly isolated with independent stable identifiers.
   - Timeframe isolation across D1, H4, and H2.
   - UTC-only datetime validation and exact Decimal price precision.
5. **Liquidity Sweep Preparation**:
   - Offers `SwingLevel` conversion to seamlessly support future Liquidity Sweeps,
     BOS, MSS, and CHoCH structural analysis.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from traderlab.core.errors import TraderLabError
from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
    ensure_utc,
)
from traderlab.domain.instruments.candle import Candle


# ── Exceptions ───────────────────────────────────────────────────────────────
class SwingError(TraderLabError):
    """Base exception for Swing detection and market structure errors."""

    def __init__(self, message: str, *, code: str = "SWING_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidSwingParameterError(SwingError):
    """Raised when swing detection parameters (e.g. left_bars, right_bars) are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_SWING_PARAMETER")


class InvalidCandleSequenceError(SwingError):
    """Raised when an invalid, non-chronological, unclosed, or mixed candle sequence is provided."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_CANDLE_SEQUENCE")


# ── Enumerations ────────────────────────────────────────────────────────────
class SwingType(enum.Enum):
    """Structural type of the market swing point."""

    SWING_HIGH = "SWING_HIGH"
    """Swing High (Peak / Resistance / Liquidity Pool Above Market)."""

    SWING_LOW = "SWING_LOW"
    """Swing Low (Trough / Support / Liquidity Pool Below Market)."""


class SwingConfirmationStatus(enum.Enum):
    """Point-in-time confirmation status of the swing point."""

    CANDIDATE = "CANDIDATE"
    """Left bars satisfied and unbreached, but awaiting right bars closure for full confirmation."""

    CONFIRMED = "CONFIRMED"
    """All right bars have closed below (for high) or above (for low) the pivot price."""

    INVALIDATED = "INVALIDATED"
    """Candidate pivot was breached by a subsequent bar before right-side confirmation."""


class TiePolicy(enum.Enum):
    """Deterministic resolution policy when equal high or low prices occur within the window."""

    STRICT = "STRICT"
    """Strict inequality required: high[i] > high[j] or low[i] < low[j]. Equal extremes reject the swing."""

    ALLOW_EQUAL_FIRST = "ALLOW_EQUAL_FIRST"
    """First chronological bar among identical extremes is chosen as the pivot."""

    ALLOW_EQUAL_LAST = "ALLOW_EQUAL_LAST"
    """Last chronological bar among identical extremes is chosen as the pivot."""


# ── Deterministic Identifier Generator ───────────────────────────────────────
def generate_swing_id(
    instrument_id: InstrumentId | str,
    source_id: DataSourceId | str,
    price_type: PriceType | str,
    timeframe: Timeframe | str,
    swing_type: SwingType | str,
    pivot_time: datetime,
    left_bars: int,
    right_bars: int,
) -> str:
    """Generate a deterministic, globally unique string identifier for a swing point.

    Distinguishes:
    - Instrument and data source.
    - Price series (BID vs ASK).
    - Timeframe (e.g. D1 vs H4 vs H2).
    - Structural type (SWING_HIGH vs SWING_LOW).
    - Pivot candle timestamp.
    - Parameter configuration (e.g. 3L3R).
    """
    inst_str = str(instrument_id)
    src_str = str(source_id)
    pt_str = price_type.value if isinstance(price_type, PriceType) else str(price_type)
    tf_str = timeframe.name if isinstance(timeframe, Timeframe) else str(timeframe)
    st_str = swing_type.value if isinstance(swing_type, SwingType) else str(swing_type)
    t_str = pivot_time.strftime("%Y%m%dT%H%M%SZ")
    return f"{inst_str}_{src_str}_{pt_str}_{tf_str}_{st_str}_{t_str}_{left_bars}L{right_bars}R"


# ── Domain Models ────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class SwingLevel:
    """Structural liquidity level derived from a confirmed SwingPoint.

    Directly bridges the Swing Detection Engine to future Liquidity Sweep,
    BOS, MSS, and CHoCH strategy analysis components.
    """

    swing_id: str
    swing_type: SwingType
    level_price: Decimal
    level_formation_time: datetime  # pivot_time
    level_confirmation_time: datetime  # confirmation_time
    timeframe: Timeframe
    price_type: PriceType

    # Liquidity state tracking
    is_breached: bool = False
    breached_at: datetime | None = None
    tested_count: int = 0
    last_test_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class SwingPoint:
    """Immutable domain representation of a market structure Swing Point.

    Guarantees:
    - **Point-in-Time Safety**: Explicitly decouples `pivot_time` (when the price extreme occurred)
      from `confirmation_time` (when right-side bars finalized and confirmed the pivot).
    - **Precision**: `pivot_price` is an exact `Decimal`.
    - **Immutability**: Once formed, structural properties cannot be altered.
    - **Stable Identification**: Deterministic ID distinguishes instrument, timeframe, and price type.
    """

    id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    price_type: PriceType
    timeframe: Timeframe
    swing_type: SwingType

    pivot_price: Decimal
    pivot_time: datetime  # Open time of the pivot candle (UTC)
    pivot_close_time: datetime  # Close time of the pivot candle (UTC)
    confirmation_time: datetime | None  # Close time of candle i + right_bars, or None if CANDIDATE

    confirmation_status: SwingConfirmationStatus
    left_bars: int
    right_bars: int
    tie_policy: TiePolicy

    # Point-in-time evaluation attributes
    computable_at_utc: datetime  # Earliest PIT timestamp computable (= confirmation_time if CONFIRMED, else pivot_close_time)
    broken_at_time: datetime | None = (
        None  # Timestamp when a subsequent candle breached the pivot level
    )
    broken_by_candle_open: datetime | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        if self.left_bars < 1 or self.right_bars < 1:
            raise InvalidSwingParameterError(
                f"left_bars ({self.left_bars}) and right_bars ({self.right_bars}) must be >= 1"
            )
        if self.confirmation_status == SwingConfirmationStatus.CONFIRMED:
            if self.confirmation_time is None:
                raise SwingError("confirmation_time cannot be None for a CONFIRMED swing point")
            if self.confirmation_time <= self.pivot_time:
                raise SwingError(
                    f"confirmation_time ({self.confirmation_time}) must be strictly after "
                    f"pivot_time ({self.pivot_time})"
                )

    @property
    def is_swing_high(self) -> bool:
        """Return True if this is a Swing High."""
        return self.swing_type == SwingType.SWING_HIGH

    @property
    def is_swing_low(self) -> bool:
        """Return True if this is a Swing Low."""
        return self.swing_type == SwingType.SWING_LOW

    @property
    def is_confirmed(self) -> bool:
        """Return True if this swing point has achieved full right-side confirmation."""
        return self.confirmation_status == SwingConfirmationStatus.CONFIRMED

    @property
    def is_candidate(self) -> bool:
        """Return True if this swing point is currently an unconfirmed candidate."""
        return self.confirmation_status == SwingConfirmationStatus.CANDIDATE

    @property
    def is_invalidated(self) -> bool:
        """Return True if this candidate was invalidated before confirmation."""
        return self.confirmation_status == SwingConfirmationStatus.INVALIDATED

    def to_level(self) -> SwingLevel:
        """Convert a confirmed SwingPoint into an active structural SwingLevel."""
        if self.confirmation_status != SwingConfirmationStatus.CONFIRMED:
            raise SwingError(
                f"Cannot create a SwingLevel from a swing with status {self.confirmation_status.value}. "
                "Only CONFIRMED swing points can form structural liquidity levels."
            )
        assert self.confirmation_time is not None
        return SwingLevel(
            swing_id=self.id,
            swing_type=self.swing_type,
            level_price=self.pivot_price,
            level_formation_time=self.pivot_time,
            level_confirmation_time=self.confirmation_time,
            timeframe=self.timeframe,
            price_type=self.price_type,
            is_breached=self.broken_at_time is not None,
            breached_at=self.broken_at_time,
        )


# ── Candle Sequence Validation ───────────────────────────────────────────────
def _validate_candle_sequence(candles: Sequence[Candle]) -> None:
    """Validate homogeneity, price type validity, and strict chronological ordering."""
    if not candles:
        return

    first = candles[0]
    if first.price_type == PriceType.UNKNOWN:
        raise InvalidCandleSequenceError(
            "Candles with PriceType.UNKNOWN cannot be used for Swing detection"
        )

    expected_instrument = first.instrument_id
    expected_source = first.source_id
    expected_timeframe = first.timeframe
    expected_price_type = first.price_type

    prev_open = first.open_time
    for i in range(1, len(candles)):
        c = candles[i]
        if c.instrument_id != expected_instrument:
            raise InvalidCandleSequenceError(
                f"Mixed instruments detected at index {i}: '{c.instrument_id}' != '{expected_instrument}'"
            )
        if c.source_id != expected_source:
            raise InvalidCandleSequenceError(
                f"Mixed data sources detected at index {i}: '{c.source_id}' != '{expected_source}'"
            )
        if c.timeframe != expected_timeframe:
            raise InvalidCandleSequenceError(
                f"Mixed timeframes detected at index {i}: {c.timeframe.name} != {expected_timeframe.name}"
            )
        if c.price_type != expected_price_type:
            raise InvalidCandleSequenceError(
                f"Mixed price types detected at index {i}: {c.price_type.value} != {expected_price_type.value}"
            )
        if c.open_time <= prev_open:
            raise InvalidCandleSequenceError(
                f"Non-chronological or duplicate open_time at index {i}: {c.open_time} <= {prev_open}"
            )
        prev_open = c.open_time


def _is_calendar_break(
    c_prev: Candle,
    c_curr: Candle,
    timeframe: Timeframe,
    *,
    allow_weekend_gap: bool,
    allow_rollover_gap: bool,
) -> bool:
    """Check if the transition between two bars spans a session break (weekend, rollover, holiday)."""
    if c_curr.open_time == c_prev.close_time:
        return False

    delta = c_curr.open_time - c_prev.close_time

    # Daily or coarser timeframes (D1, W1): trading day skips over weekend are normal
    if timeframe >= Timeframe.D1:
        if delta <= timedelta(days=4):
            return False
        return not allow_weekend_gap

    # Intraday timeframes:
    # Gap > 4 hours indicates weekend or multi-session closure
    if delta >= timedelta(hours=4):
        return not allow_weekend_gap

    # Gap between 30 minutes and 4 hours indicates daily rollover or holiday early close
    if delta >= timedelta(minutes=30):
        return not allow_rollover_gap

    return False


# ── Swing Detection Core Engine ──────────────────────────────────────────────
def detect_swings(
    candles: Sequence[Candle],
    *,
    left_bars: int = 3,
    right_bars: int = 3,
    tie_policy: TiePolicy = TiePolicy.STRICT,
    as_of_utc: datetime | None = None,
    allow_candidates: bool = True,
    strict_pit: bool = True,
    allow_weekend_gap: bool = True,
    allow_rollover_gap: bool = True,
) -> list[SwingPoint]:
    """Detect all deterministic, Point-in-Time safe Swing High and Swing Low points.

    Algorithm Definition:
        For index `i`:
        - A candle is a candidate Swing High if its `high` price is the maximum in the window
          `[i - left_bars ... i + right_bars]` according to the specified `tie_policy`.
        - A candle is a candidate Swing Low if its `low` price is the minimum in the window
          `[i - left_bars ... i + right_bars]` according to the specified `tie_policy`.

    Point-in-Time Guarantees:
        - If `as_of_utc` is specified, only bars finalized (`close_time <= as_of_utc`) are used.
        - `confirmation_time` is strictly set to `candles[i + right_bars].close_time`.
        - Before `confirmation_time`, the swing is strictly classified as `CANDIDATE`
          (if `allow_candidates=True`) and never marked as `CONFIRMED`.
        - If price breaches the pivot before `right_bars` are completed, the candidate is discarded.
        - Synthetic market-closed bars cannot be pivots and cannot confirm swings.

    Args:
        candles: Chronologically sorted sequence of uniform Candle objects.
        left_bars: Number of required predecessor bars (default: 3).
        right_bars: Number of required successor confirmation bars (default: 3).
        tie_policy: Policy for resolving identical price extremes (default: STRICT).
        as_of_utc: Point-in-time evaluation cutoff timestamp.
        allow_candidates: If True (default), includes in-flight unconfirmed candidate swings.
        strict_pit: If True, enforces strict chronological and confirmation ceilings.
        allow_weekend_gap: If True, permits weekend boundaries across the swing window.
        allow_rollover_gap: If True, permits daily rollover gaps across the swing window.

    Returns:
        List of detected `SwingPoint` objects in chronological order of pivot time.
    """
    if left_bars < 1 or right_bars < 1:
        raise InvalidSwingParameterError(
            f"left_bars ({left_bars}) and right_bars ({right_bars}) must be >= 1"
        )

    if not candles:
        return []

    _validate_candle_sequence(candles)

    # Filter out Sunday single-hour stub candles on D1 UTC_MIDNIGHT convention
    effective_candles: list[Candle] = []
    for c in candles:
        if not c.is_closed:
            continue
        if c.timeframe == Timeframe.D1 and c.open_time.weekday() == 6 and c.open_time.hour == 0:
            continue
        if as_of_utc is not None and c.close_time > as_of_utc:
            continue
        effective_candles.append(c)

    n_bars = len(effective_candles)
    if n_bars < (left_bars + 1):
        return []

    detected_swings: list[SwingPoint] = []

    for i in range(left_bars, n_bars):
        p_candle = effective_candles[i]

        # Synthetic bars cannot form genuine market structural pivots
        if p_candle.is_synthetic:
            continue

        available_right = n_bars - 1 - i

        # Check calendar/session discontinuities in left window
        left_window_has_unpermitted_gap = False
        for idx in range(i - left_bars, i):
            if _is_calendar_break(
                effective_candles[idx],
                effective_candles[idx + 1],
                p_candle.timeframe,
                allow_weekend_gap=allow_weekend_gap,
                allow_rollover_gap=allow_rollover_gap,
            ):
                left_window_has_unpermitted_gap = True
                break

        if left_window_has_unpermitted_gap:
            continue

        # ── 1. Evaluate Swing High ────────────────────────────────────────────
        is_high_candidate = True
        p_high = p_candle.high

        # Left comparison
        for j in range(i - left_bars, i):
            c_left = effective_candles[j]
            if c_left.is_synthetic:
                is_high_candidate = False
                break
            if tie_policy in (TiePolicy.STRICT, TiePolicy.ALLOW_EQUAL_FIRST):
                if not (p_high > c_left.high):
                    is_high_candidate = False
                    break
            elif tie_policy == TiePolicy.ALLOW_EQUAL_LAST and not (p_high >= c_left.high):
                is_high_candidate = False
                break

        if is_high_candidate:
            # Right comparison
            right_count = min(right_bars, available_right)
            right_satisfied = True
            breached = False

            for k_step in range(1, right_count + 1):
                c_right = effective_candles[i + k_step]
                if _is_calendar_break(
                    effective_candles[i + k_step - 1],
                    c_right,
                    p_candle.timeframe,
                    allow_weekend_gap=allow_weekend_gap,
                    allow_rollover_gap=allow_rollover_gap,
                ):
                    right_satisfied = False
                    break
                if c_right.is_synthetic:
                    right_satisfied = False
                    break

                if tie_policy in (TiePolicy.STRICT, TiePolicy.ALLOW_EQUAL_LAST):
                    if not (p_high > c_right.high):
                        right_satisfied = False
                        breached = True
                        break
                elif tie_policy == TiePolicy.ALLOW_EQUAL_FIRST and not (p_high >= c_right.high):
                    right_satisfied = False
                    breached = True
                    break

            if not breached and right_satisfied:
                if right_count == right_bars:
                    # Fully confirmed Swing High
                    conf_time = effective_candles[i + right_bars].close_time
                    s_id = generate_swing_id(
                        p_candle.instrument_id,
                        p_candle.source_id,
                        p_candle.price_type,
                        p_candle.timeframe,
                        SwingType.SWING_HIGH,
                        p_candle.open_time,
                        left_bars,
                        right_bars,
                    )

                    # Look ahead for breach after confirmation
                    broken_time = None
                    broken_candle_open = None
                    for m in range(i + right_bars + 1, n_bars):
                        c_fwd = effective_candles[m]
                        if c_fwd.high > p_high:
                            broken_time = c_fwd.close_time
                            broken_candle_open = c_fwd.open_time
                            break

                    detected_swings.append(
                        SwingPoint(
                            id=s_id,
                            instrument_id=p_candle.instrument_id,
                            source_id=p_candle.source_id,
                            price_type=p_candle.price_type,
                            timeframe=p_candle.timeframe,
                            swing_type=SwingType.SWING_HIGH,
                            pivot_price=p_high,
                            pivot_time=ensure_utc(p_candle.open_time),
                            pivot_close_time=ensure_utc(p_candle.close_time),
                            confirmation_time=ensure_utc(conf_time),
                            confirmation_status=SwingConfirmationStatus.CONFIRMED,
                            left_bars=left_bars,
                            right_bars=right_bars,
                            tie_policy=tie_policy,
                            computable_at_utc=ensure_utc(conf_time),
                            broken_at_time=ensure_utc(broken_time) if broken_time else None,
                            broken_by_candle_open=ensure_utc(broken_candle_open)
                            if broken_candle_open
                            else None,
                        )
                    )
                elif allow_candidates and right_count < right_bars:
                    # Unconfirmed candidate Swing High
                    s_id = generate_swing_id(
                        p_candle.instrument_id,
                        p_candle.source_id,
                        p_candle.price_type,
                        p_candle.timeframe,
                        SwingType.SWING_HIGH,
                        p_candle.open_time,
                        left_bars,
                        right_bars,
                    )
                    detected_swings.append(
                        SwingPoint(
                            id=s_id,
                            instrument_id=p_candle.instrument_id,
                            source_id=p_candle.source_id,
                            price_type=p_candle.price_type,
                            timeframe=p_candle.timeframe,
                            swing_type=SwingType.SWING_HIGH,
                            pivot_price=p_high,
                            pivot_time=ensure_utc(p_candle.open_time),
                            pivot_close_time=ensure_utc(p_candle.close_time),
                            confirmation_time=None,
                            confirmation_status=SwingConfirmationStatus.CANDIDATE,
                            left_bars=left_bars,
                            right_bars=right_bars,
                            tie_policy=tie_policy,
                            computable_at_utc=ensure_utc(p_candle.close_time),
                        )
                    )

        # ── 2. Evaluate Swing Low ─────────────────────────────────────────────
        is_low_candidate = True
        p_low = p_candle.low

        # Left comparison
        for j in range(i - left_bars, i):
            c_left = effective_candles[j]
            if c_left.is_synthetic:
                is_low_candidate = False
                break
            if tie_policy in (TiePolicy.STRICT, TiePolicy.ALLOW_EQUAL_FIRST):
                if not (p_low < c_left.low):
                    is_low_candidate = False
                    break
            elif tie_policy == TiePolicy.ALLOW_EQUAL_LAST and not (p_low <= c_left.low):
                is_low_candidate = False
                break

        if is_low_candidate:
            # Right comparison
            right_count = min(right_bars, available_right)
            right_satisfied = True
            breached = False

            for k_step in range(1, right_count + 1):
                c_right = effective_candles[i + k_step]
                if _is_calendar_break(
                    effective_candles[i + k_step - 1],
                    c_right,
                    p_candle.timeframe,
                    allow_weekend_gap=allow_weekend_gap,
                    allow_rollover_gap=allow_rollover_gap,
                ):
                    right_satisfied = False
                    break
                if c_right.is_synthetic:
                    right_satisfied = False
                    break

                if tie_policy in (TiePolicy.STRICT, TiePolicy.ALLOW_EQUAL_LAST):
                    if not (p_low < c_right.low):
                        right_satisfied = False
                        breached = True
                        break
                elif tie_policy == TiePolicy.ALLOW_EQUAL_FIRST and not (p_low <= c_right.low):
                    right_satisfied = False
                    breached = True
                    break

            if not breached and right_satisfied:
                if right_count == right_bars:
                    # Fully confirmed Swing Low
                    conf_time = effective_candles[i + right_bars].close_time
                    s_id = generate_swing_id(
                        p_candle.instrument_id,
                        p_candle.source_id,
                        p_candle.price_type,
                        p_candle.timeframe,
                        SwingType.SWING_LOW,
                        p_candle.open_time,
                        left_bars,
                        right_bars,
                    )

                    # Look ahead for breach after confirmation
                    broken_time = None
                    broken_candle_open = None
                    for m in range(i + right_bars + 1, n_bars):
                        c_fwd = effective_candles[m]
                        if c_fwd.low < p_low:
                            broken_time = c_fwd.close_time
                            broken_candle_open = c_fwd.open_time
                            break

                    detected_swings.append(
                        SwingPoint(
                            id=s_id,
                            instrument_id=p_candle.instrument_id,
                            source_id=p_candle.source_id,
                            price_type=p_candle.price_type,
                            timeframe=p_candle.timeframe,
                            swing_type=SwingType.SWING_LOW,
                            pivot_price=p_low,
                            pivot_time=ensure_utc(p_candle.open_time),
                            pivot_close_time=ensure_utc(p_candle.close_time),
                            confirmation_time=ensure_utc(conf_time),
                            confirmation_status=SwingConfirmationStatus.CONFIRMED,
                            left_bars=left_bars,
                            right_bars=right_bars,
                            tie_policy=tie_policy,
                            computable_at_utc=ensure_utc(conf_time),
                            broken_at_time=ensure_utc(broken_time) if broken_time else None,
                            broken_by_candle_open=ensure_utc(broken_candle_open)
                            if broken_candle_open
                            else None,
                        )
                    )
                elif allow_candidates and right_count < right_bars:
                    # Unconfirmed candidate Swing Low
                    s_id = generate_swing_id(
                        p_candle.instrument_id,
                        p_candle.source_id,
                        p_candle.price_type,
                        p_candle.timeframe,
                        SwingType.SWING_LOW,
                        p_candle.open_time,
                        left_bars,
                        right_bars,
                    )
                    detected_swings.append(
                        SwingPoint(
                            id=s_id,
                            instrument_id=p_candle.instrument_id,
                            source_id=p_candle.source_id,
                            price_type=p_candle.price_type,
                            timeframe=p_candle.timeframe,
                            swing_type=SwingType.SWING_LOW,
                            pivot_price=p_low,
                            pivot_time=ensure_utc(p_candle.open_time),
                            pivot_close_time=ensure_utc(p_candle.close_time),
                            confirmation_time=None,
                            confirmation_status=SwingConfirmationStatus.CANDIDATE,
                            left_bars=left_bars,
                            right_bars=right_bars,
                            tie_policy=tie_policy,
                            computable_at_utc=ensure_utc(p_candle.close_time),
                        )
                    )

    # Sort deterministically by pivot_time ASC, swing_type ASC, id ASC
    detected_swings.sort(key=lambda s: (s.pivot_time, s.swing_type.value, s.id))
    return detected_swings


# ── Query & Filtering Helpers ────────────────────────────────────────────────
def filter_confirmed_swings(
    swings: Sequence[SwingPoint],
    as_of_utc: datetime | None = None,
) -> list[SwingPoint]:
    """Filter confirmed swing points known as of a specified Point-in-Time timestamp.

    Guarantees:
    - Any swing whose `confirmation_time` is after `as_of_utc` is excluded.
    - Candidates (`confirmation_status == CANDIDATE`) are excluded.
    """
    confirmed: list[SwingPoint] = []
    for s in swings:
        if s.confirmation_status != SwingConfirmationStatus.CONFIRMED:
            continue
        if s.confirmation_time is None:
            continue
        if as_of_utc is not None and s.confirmation_time > as_of_utc:
            continue
        confirmed.append(s)
    return confirmed


def get_active_swing_levels(
    swings: Sequence[SwingPoint],
    as_of_utc: datetime | None = None,
) -> list[SwingLevel]:
    """Retrieve all active, unbreached structural SwingLevels confirmed as of `as_of_utc`."""
    confirmed = filter_confirmed_swings(swings, as_of_utc=as_of_utc)
    levels: list[SwingLevel] = []
    for s in confirmed:
        lvl = s.to_level()
        # If level was breached after as_of_utc, as of that moment it was still active!
        if as_of_utc is not None and s.broken_at_time is not None and s.broken_at_time > as_of_utc:
            lvl = SwingLevel(
                swing_id=lvl.swing_id,
                swing_type=lvl.swing_type,
                level_price=lvl.level_price,
                level_formation_time=lvl.level_formation_time,
                level_confirmation_time=lvl.level_confirmation_time,
                timeframe=lvl.timeframe,
                price_type=lvl.price_type,
                is_breached=False,
                breached_at=None,
            )
        levels.append(lvl)
    return levels
