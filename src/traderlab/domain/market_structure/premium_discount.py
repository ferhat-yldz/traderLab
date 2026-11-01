r"""Point-in-Time Safe Premium, Discount, and Dealing Range Engine.

Provides mathematically rigorous, deterministic, and Point-in-Time compliant
Dealing Range and Premium/Discount analysis for institutional ICT/SMC research.

Key Architectural & Financial Principles:
1. **Dealing Range Definition**:
   A structural price range defined between two confirmed market price extrema (Swing High and Swing Low).
   Requires strictly `range_low < range_high`. Inverted or zero-width ranges are invalid.
2. **Equilibrium (EQ)**:
   The geometric midpoint of the dealing range:
   $$EQ = \frac{range\_high + range\_low}{2}$$
   Evaluated with exact Decimal arithmetic.
3. **Zone Classifications**:
   - `PREMIUM`: Price strictly above Equilibrium ($EQ < price \le range\_high$).
   - `DISCOUNT`: Price strictly below Equilibrium ($range\_low \le price < EQ$).
   - `EQUILIBRIUM`: Price exactly at Equilibrium ($price == EQ$).
   - `OUTSIDE_RANGE`: Price beyond range boundaries ($price > range\_high$ or $price < range\_low$).
   - `UNDETERMINED`: Price cannot be determined due to missing, unconfirmed, or invalid range.
4. **Point-in-Time & Anti-Lookahead Guarantees**:
   - Both bounding swing pivots must be CONFIRMED at or prior to the observation time.
   - `range_available_at = max(first_swing.confirmation_time, second_swing.confirmation_time)`.
   - Never forms or updates a dealing range using unconfirmed or future swings.
5. **Deterministic Range Selection**:
   - Evaluates confirmed swing legs (Low -> High for BULLISH, High -> Low for BEARISH).
   - Resolves multiple candidates deterministically by availability time and stable IDs.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from traderlab.core.errors import TraderLabError
from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
    ensure_utc,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.market_structure import StructureBreakEvent
from traderlab.domain.market_structure.swing import (
    SwingConfirmationStatus,
    SwingPoint,
    SwingType,
)


# ── Exceptions ───────────────────────────────────────────────────────────────
class DealingRangeError(TraderLabError):
    """Base exception for Dealing Range and Premium/Discount domain errors."""

    def __init__(self, message: str, *, code: str = "DEALING_RANGE_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidDealingRangeParameterError(DealingRangeError):
    """Raised when Dealing Range configuration parameters are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_DEALING_RANGE_PARAMETER")


class DealingRangeDataError(DealingRangeError):
    """Raised when input candles or swings are invalid, unconfirmed, or geometrically contradictory."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="DEALING_RANGE_DATA_ERROR")


# ── Enumerations ────────────────────────────────────────────────────────────
class PremiumDiscountZone(enum.Enum):
    """Classification of price location relative to a Dealing Range."""

    PREMIUM = "PREMIUM"
    """Above Equilibrium; favorable zone for short entries or distribution."""

    DISCOUNT = "DISCOUNT"
    """Below Equilibrium; favorable zone for long entries or accumulation."""

    EQUILIBRIUM = "EQUILIBRIUM"
    """Exactly at the 50% midpoint between range low and high."""

    OUTSIDE_RANGE = "OUTSIDE_RANGE"
    """Price is beyond the dealing range boundaries (above high or below low)."""

    UNDETERMINED = "UNDETERMINED"
    """Range is missing, unconfirmed, or geometrically invalid."""


class DealingRangeDirection(enum.Enum):
    """Structural direction of the price expansion establishing the Dealing Range."""

    BULLISH = "BULLISH"
    """Established by an upward structural expansion: Swing Low -> Swing High."""

    BEARISH = "BEARISH"
    """Established by a downward structural expansion: Swing High -> Swing Low."""


class DealingRangeSelectionPolicy(enum.Enum):
    """Policy used to extract and select active Dealing Ranges from market structure."""

    CONFIRMED_SWING_LEG = "CONFIRMED_SWING_LEG"
    """Standard SMC policy: Dealing range formed by adjacent confirmed opposing swing points."""

    STRUCTURE_BREAK_ANCHORED = "STRUCTURE_BREAK_ANCHORED"
    """Dealing range explicitly anchored to the swing extremes that generated a BOS or MSS."""


# ── Deterministic Identifier Generator ───────────────────────────────────────
def generate_dealing_range_id(
    instrument_id: InstrumentId | str,
    source_id: DataSourceId | str,
    price_type: PriceType | str,
    timeframe: Timeframe | str,
    direction: DealingRangeDirection | str,
    start_pivot_time: datetime,
    end_pivot_time: datetime,
    start_pivot_id: str,
    end_pivot_id: str,
) -> str:
    """Generate a deterministic, globally unique string identifier for a Dealing Range."""
    inst_str = str(instrument_id)
    src_str = str(source_id)
    pt_str = price_type.value if isinstance(price_type, PriceType) else str(price_type)
    tf_str = timeframe.name if isinstance(timeframe, Timeframe) else str(timeframe)
    dir_str = direction.value if isinstance(direction, DealingRangeDirection) else str(direction)
    t_start = start_pivot_time.strftime("%Y%m%dT%H%M%SZ")
    t_end = end_pivot_time.strftime("%Y%m%dT%H%M%SZ")
    return f"{inst_str}_{src_str}_{pt_str}_{tf_str}_{dir_str}_DR_{t_start}_{t_end}"


# ── Domain Models ────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class DealingRangeRecord:
    """Immutable record of a Point-in-Time verified Dealing Range."""

    range_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    price_type: PriceType
    direction: DealingRangeDirection

    range_low: Decimal
    range_high: Decimal
    equilibrium: Decimal

    range_start_pivot_id: str
    range_end_pivot_id: str
    start_pivot_time: datetime
    end_pivot_time: datetime
    range_available_at: datetime

    selection_policy: DealingRangeSelectionPolicy = DealingRangeSelectionPolicy.CONFIRMED_SWING_LEG
    availability_confidence: AvailabilityConfidence = AvailabilityConfidence.ESTIMATED
    pit_policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED
    structure_reference_id: str | None = None

    def __post_init__(self) -> None:
        if self.range_low >= self.range_high:
            raise DealingRangeDataError(
                f"range_low ({self.range_low}) must be strictly less than range_high ({self.range_high})"
            )
        expected_eq = (self.range_low + self.range_high) / Decimal("2")
        if self.equilibrium != expected_eq:
            raise DealingRangeDataError(
                f"equilibrium ({self.equilibrium}) does not match exact midpoint ({expected_eq})"
            )
        if self.start_pivot_time >= self.end_pivot_time:
            raise DealingRangeDataError(
                f"start_pivot_time ({self.start_pivot_time}) must precede end_pivot_time ({self.end_pivot_time})"
            )
        if self.range_available_at < self.end_pivot_time:
            raise DealingRangeDataError(
                f"range_available_at ({self.range_available_at}) cannot precede end_pivot_time ({self.end_pivot_time})"
            )

    @property
    def range_size(self) -> Decimal:
        """Total vertical price span of the dealing range."""
        return self.range_high - self.range_low


@dataclass(frozen=True, slots=True)
class PremiumDiscountAssessment:
    """Immutable evaluation of a specific price level within a Dealing Range."""

    assessment_id: str
    range_id: str
    price: Decimal
    zone: PremiumDiscountZone
    equilibrium: Decimal
    distance_from_eq: Decimal
    percentage_in_range: Decimal
    evaluated_at_utc: datetime


@dataclass(frozen=True, slots=True)
class PremiumDiscountConfig:
    """Configuration for Dealing Range identification and Premium/Discount evaluation."""

    selection_policy: DealingRangeSelectionPolicy = DealingRangeSelectionPolicy.CONFIRMED_SWING_LEG
    require_confirmed_swings: bool = True
    tick_size: Decimal = Decimal("0.01")
    require_bos_alignment: bool = False

    def __post_init__(self) -> None:
        if self.tick_size <= Decimal("0"):
            raise InvalidDealingRangeParameterError("tick_size must be positive")


# ── Core Domain Functions ───────────────────────────────────────────────────
def classify_premium_discount(
    price: Decimal,
    range_low: Decimal,
    range_high: Decimal,
    equilibrium: Decimal | None = None,
) -> PremiumDiscountZone:
    """Pure mathematical classification of a price relative to range low and high boundaries.

    Rules:
    - If range_low >= range_high: Returns UNDETERMINED.
    - If price < range_low or price > range_high: Returns OUTSIDE_RANGE.
    - If price == equilibrium: Returns EQUILIBRIUM.
    - If price > equilibrium: Returns PREMIUM.
    - If price < equilibrium: Returns DISCOUNT.
    """
    if range_low >= range_high:
        return PremiumDiscountZone.UNDETERMINED

    eq = equilibrium if equilibrium is not None else (range_low + range_high) / Decimal("2")

    if price < range_low or price > range_high:
        return PremiumDiscountZone.OUTSIDE_RANGE
    if price == eq:
        return PremiumDiscountZone.EQUILIBRIUM
    if price > eq:
        return PremiumDiscountZone.PREMIUM
    return PremiumDiscountZone.DISCOUNT


def assess_price_in_range(
    price: Decimal,
    dealing_range: DealingRangeRecord | None,
    evaluated_at_utc: datetime,
) -> PremiumDiscountAssessment:
    """Point-in-time safe assessment of a price inside a Dealing Range."""
    norm_eval_time = ensure_utc(evaluated_at_utc)

    if dealing_range is None:
        return PremiumDiscountAssessment(
            assessment_id=f"ASSESS_UNDETERMINED_{norm_eval_time.strftime('%Y%m%dT%H%M%SZ')}",
            range_id="UNDETERMINED",
            price=price,
            zone=PremiumDiscountZone.UNDETERMINED,
            equilibrium=Decimal("0"),
            distance_from_eq=Decimal("0"),
            percentage_in_range=Decimal("0"),
            evaluated_at_utc=norm_eval_time,
        )

    zone = classify_premium_discount(
        price=price,
        range_low=dealing_range.range_low,
        range_high=dealing_range.range_high,
        equilibrium=dealing_range.equilibrium,
    )
    dist = price - dealing_range.equilibrium
    pct = ((price - dealing_range.range_low) / dealing_range.range_size) * Decimal("100")
    t_str = norm_eval_time.strftime("%Y%m%dT%H%M%SZ")

    return PremiumDiscountAssessment(
        assessment_id=f"ASSESS_{dealing_range.range_id}_{t_str}",
        range_id=dealing_range.range_id,
        price=price,
        zone=zone,
        equilibrium=dealing_range.equilibrium,
        distance_from_eq=dist,
        percentage_in_range=pct,
        evaluated_at_utc=norm_eval_time,
    )


def validate_candles_for_range(candles: Sequence[Candle]) -> None:
    """Validate that input candles contain no unclosed, synthetic, or contradictory bars."""
    for c in candles:
        if not c.is_closed:
            raise DealingRangeDataError(
                f"Unclosed candle at {c.open_time} rejected for dealing range computation"
            )
        if c.is_synthetic:
            raise DealingRangeDataError(
                f"Synthetic candle at {c.open_time} rejected for dealing range computation"
            )


def identify_dealing_ranges(
    swings: Sequence[SwingPoint],
    config: PremiumDiscountConfig | None = None,
    as_of_utc: datetime | None = None,
    structure_breaks: Sequence[StructureBreakEvent] | None = None,
) -> list[DealingRangeRecord]:
    """Identify valid, Point-in-Time compliant Dealing Ranges from confirmed swing points.

    Guarantees:
    - Never uses unconfirmed swing points (`confirmation_status == CONFIRMED` required).
    - Respects `as_of_utc` filter: `range_available_at <= as_of_utc`.
    - Enforces strictly `range_low < range_high`.
    - Deterministic ordering by `(range_available_at, end_pivot_time, range_id)`.
    """
    if not swings:
        return []

    cfg = config or PremiumDiscountConfig()
    norm_as_of = ensure_utc(as_of_utc) if as_of_utc is not None else None

    # Filter strictly confirmed swings
    valid_swings: list[SwingPoint] = []
    for s in swings:
        if (
            cfg.require_confirmed_swings
            and s.confirmation_status != SwingConfirmationStatus.CONFIRMED
        ):
            continue
        if s.confirmation_time is None:
            continue
        if norm_as_of is not None and s.confirmation_time > norm_as_of:
            continue
        valid_swings.append(s)

    if len(valid_swings) < 2:
        return []

    # Chronological ordering by pivot_time
    sorted_swings = sorted(valid_swings, key=lambda s: (s.pivot_time, s.id))

    records: list[DealingRangeRecord] = []

    # For each swing, find its most recent preceding opposing swing to form a structural leg
    for s_end in sorted_swings:
        preceding_opposing = [
            s
            for s in sorted_swings
            if s.pivot_time < s_end.pivot_time and s.swing_type != s_end.swing_type
        ]
        if not preceding_opposing:
            continue

        # Most recent preceding opposing swing is the leg anchor
        s_start = max(preceding_opposing, key=lambda s: (s.pivot_time, s.id))

        # Confirmation timestamps
        assert s_start.confirmation_time is not None
        assert s_end.confirmation_time is not None
        avail_at = max(s_start.confirmation_time, s_end.confirmation_time)

        if norm_as_of is not None and avail_at > norm_as_of:
            continue

        if s_start.swing_type == SwingType.SWING_LOW and s_end.swing_type == SwingType.SWING_HIGH:
            direction = DealingRangeDirection.BULLISH
            r_low = s_start.pivot_price
            r_high = s_end.pivot_price
        elif (
            s_start.swing_type == SwingType.SWING_HIGH and s_end.swing_type == SwingType.SWING_LOW
        ):
            direction = DealingRangeDirection.BEARISH
            r_high = s_start.pivot_price
            r_low = s_end.pivot_price
        else:
            continue

        # Invariant: range_low < range_high
        if r_low >= r_high:
            continue

        eq = (r_low + r_high) / Decimal("2")
        range_id = generate_dealing_range_id(
            instrument_id=s_start.instrument_id,
            source_id=s_start.source_id,
            price_type=s_start.price_type,
            timeframe=s_start.timeframe,
            direction=direction,
            start_pivot_time=s_start.pivot_time,
            end_pivot_time=s_end.pivot_time,
            start_pivot_id=s_start.id,
            end_pivot_id=s_end.id,
        )

        record = DealingRangeRecord(
            range_id=range_id,
            instrument_id=s_start.instrument_id,
            source_id=s_start.source_id,
            timeframe=s_start.timeframe,
            price_type=s_start.price_type,
            direction=direction,
            range_low=r_low,
            range_high=r_high,
            equilibrium=eq,
            range_start_pivot_id=s_start.id,
            range_end_pivot_id=s_end.id,
            start_pivot_time=s_start.pivot_time,
            end_pivot_time=s_end.pivot_time,
            range_available_at=avail_at,
            selection_policy=cfg.selection_policy,
            availability_confidence=AvailabilityConfidence.ESTIMATED,
            pit_policy=PitPolicy.RESEARCH_ESTIMATED,
        )
        records.append(record)

    # Sort deterministically
    return sorted(
        records,
        key=lambda r: (r.range_available_at, r.end_pivot_time, r.range_id),
    )


def get_active_dealing_range(
    dealing_ranges: Sequence[DealingRangeRecord],
    as_of_utc: datetime | None = None,
) -> DealingRangeRecord | None:
    """Return the single most recent completed Dealing Range active as of `as_of_utc`."""
    if not dealing_ranges:
        return None

    norm_as_of = ensure_utc(as_of_utc) if as_of_utc is not None else None
    candidates = [
        r for r in dealing_ranges if norm_as_of is None or r.range_available_at <= norm_as_of
    ]
    if not candidates:
        return None

    # Deterministic selection: highest range_available_at, then latest end_pivot_time, then range_id
    return max(
        candidates,
        key=lambda r: (r.range_available_at, r.end_pivot_time, r.range_id),
    )


def filter_dealing_ranges_as_of(
    dealing_ranges: Sequence[DealingRangeRecord],
    as_of_utc: datetime,
) -> list[DealingRangeRecord]:
    """Filter dealing ranges to only those whose confirmation/availability <= `as_of_utc`."""
    norm_as_of = ensure_utc(as_of_utc)
    return [r for r in dealing_ranges if r.range_available_at <= norm_as_of]


def summarize_dealing_ranges(
    dealing_ranges: Sequence[DealingRangeRecord],
) -> dict[str, int | str | Decimal]:
    """Produce a deterministic diagnostic summary dictionary for Dealing Ranges."""
    if not dealing_ranges:
        return {
            "total_ranges": 0,
            "bullish_ranges": 0,
            "bearish_ranges": 0,
            "min_range_size": Decimal("0"),
            "max_range_size": Decimal("0"),
            "latest_available_at": "NONE",
        }

    bullish_count = sum(1 for r in dealing_ranges if r.direction == DealingRangeDirection.BULLISH)
    bearish_count = sum(1 for r in dealing_ranges if r.direction == DealingRangeDirection.BEARISH)
    sizes = [r.range_size for r in dealing_ranges]
    latest = max(r.range_available_at for r in dealing_ranges)

    return {
        "total_ranges": len(dealing_ranges),
        "bullish_ranges": bullish_count,
        "bearish_ranges": bearish_count,
        "min_range_size": min(sizes),
        "max_range_size": max(sizes),
        "latest_available_at": latest.isoformat(),
    }
