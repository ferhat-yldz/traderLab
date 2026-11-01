r"""Point-in-Time Safe Optimal Trade Entry (OTE) Engine.

Computes analytical Optimal Trade Entry (0.62, 0.705 sweet spot, 0.79) zones
anchored strictly to valid Dealing Ranges. Evaluates geometric intersections with
Order Blocks without generating trade signals, orders, or execution recommendations.

Key Principles:
1. **Fibonacci Retracement Calibration**:
   - OTE Start: 0.62 (62.0% retracement)
   - OTE Center / Sweet Spot: 0.705 (70.5% retracement — institutional equilibrium inside OTE)
   - OTE End: 0.79 (79.0% retracement)
   - Configurable exact Decimals.
2. **Directional Geometry**:
   - **Bullish OTE**: Retraces downward from High into Discount:
     $$OTE_{62} = range\_high - R \times 0.62$$
     $$OTE_{705} = range\_high - R \times 0.705$$
     $$OTE_{79} = range\_high - R \times 0.79$$
     $$zone\_bottom = OTE_{79}, \quad zone\_top = OTE_{62}$$
     Must reside strictly within Discount ($zone\_top < EQ$).
   - **Bearish OTE**: Retraces upward from Low into Premium:
     $$OTE_{62} = range\_low + R \times 0.62$$
     $$OTE_{705} = range\_low + R \times 0.705$$
     $$OTE_{79} = range\_low + R \times 0.79$$
     $$zone\_bottom = OTE_{62}, \quad zone\_top = OTE_{79}$$
     Must reside strictly within Premium ($zone\_bottom > EQ$).
3. **Analytic Scope Constraint**:
   - OTE zones and the 0.705 sweet spot are purely descriptive research levels.
   - Never represent an automated trade signal, position, or limit order.
4. **Order Block Geometric Confluence**:
   - Bullish OTE $\cap$ Bullish OB; Bearish OTE $\cap$ Bearish OB.
   - Evaluated strictly within identical (instrument, source, price_type, timeframe).
   - Distinguishes positive width overlap from zero-width boundary touch.
"""

from __future__ import annotations

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
from traderlab.domain.market_structure.order_block import (
    OrderBlockDirection,
    OrderBlockRecord,
)
from traderlab.domain.market_structure.premium_discount import (
    DealingRangeDirection,
    DealingRangeRecord,
)


# ── Exceptions ───────────────────────────────────────────────────────────────
class OTEError(TraderLabError):
    """Base exception for Optimal Trade Entry domain errors."""

    def __init__(self, message: str, *, code: str = "OTE_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidOTEParameterError(OTEError):
    """Raised when OTE ratios or configuration parameters are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_OTE_PARAMETER")


class OTEDataError(OTEError):
    """Raised when Dealing Range data is geometrically inconsistent for OTE generation."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="OTE_DATA_ERROR")


# ── Deterministic Identifier Generator ───────────────────────────────────────
def generate_ote_id(dealing_range_id: str) -> str:
    """Generate a deterministic identifier for an OTE zone linked to its dealing range."""
    return f"OTE_{dealing_range_id}"


def generate_intersection_id(ote_id: str, order_block_id: str) -> str:
    """Generate a deterministic identifier for an OTE and Order Block geometric confluence."""
    return f"INT_{ote_id}_{order_block_id}"


# ── Domain Models ────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class OTEConfig:
    """Configuration defining OTE retracement ratios and directional boundary checks."""

    ote_start_ratio: Decimal = Decimal("0.62")
    ote_center_ratio: Decimal = Decimal("0.705")
    ote_end_ratio: Decimal = Decimal("0.79")
    require_discount_for_bullish: bool = True
    require_premium_for_bearish: bool = True

    def __post_init__(self) -> None:
        if not (
            Decimal("0.50")
            < self.ote_start_ratio
            < self.ote_center_ratio
            < self.ote_end_ratio
            < Decimal("1.00")
        ):
            raise InvalidOTEParameterError(
                f"OTE ratios must satisfy 0.50 < start ({self.ote_start_ratio}) < "
                f"center ({self.ote_center_ratio}) < end ({self.ote_end_ratio}) < 1.00"
            )


@dataclass(frozen=True, slots=True)
class OTEZoneRecord:
    """Immutable record of an Optimal Trade Entry (OTE) zone derived from a Dealing Range."""

    ote_id: str
    dealing_range_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    price_type: PriceType
    direction: DealingRangeDirection

    range_low: Decimal
    range_high: Decimal
    equilibrium: Decimal

    ote_62: Decimal
    ote_705: Decimal  # Purely analytical reference level; NOT an order execution price
    ote_79: Decimal

    zone_bottom: Decimal
    zone_top: Decimal

    range_available_at: datetime
    availability_confidence: AvailabilityConfidence = AvailabilityConfidence.ESTIMATED
    pit_policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED

    def __post_init__(self) -> None:
        if not (self.zone_bottom < self.ote_705 < self.zone_top):
            raise OTEDataError(
                f"OTE geometry violation: zone_bottom ({self.zone_bottom}) < "
                f"ote_705 ({self.ote_705}) < zone_top ({self.zone_top}) failed"
            )

        if self.direction == DealingRangeDirection.BULLISH:
            if self.zone_bottom != self.ote_79 or self.zone_top != self.ote_62:
                raise OTEDataError("Bullish OTE boundaries must match [ote_79, ote_62]")
            if self.zone_top >= self.equilibrium:
                raise OTEDataError(
                    f"Bullish OTE zone_top ({self.zone_top}) must be strictly within Discount (< {self.equilibrium})"
                )
            if self.zone_bottom < self.range_low:
                raise OTEDataError(
                    f"Bullish OTE zone_bottom ({self.zone_bottom}) cannot breach range_low ({self.range_low})"
                )
        elif self.direction == DealingRangeDirection.BEARISH:
            if self.zone_bottom != self.ote_62 or self.zone_top != self.ote_79:
                raise OTEDataError("Bearish OTE boundaries must match [ote_62, ote_79]")
            if self.zone_bottom <= self.equilibrium:
                raise OTEDataError(
                    f"Bearish OTE zone_bottom ({self.zone_bottom}) must be strictly within Premium (> {self.equilibrium})"
                )
            if self.zone_top > self.range_high:
                raise OTEDataError(
                    f"Bearish OTE zone_top ({self.zone_top}) cannot breach range_high ({self.range_high})"
                )

    @property
    def zone_width(self) -> Decimal:
        """Vertical thickness of the OTE zone."""
        return self.zone_top - self.zone_bottom


@dataclass(frozen=True, slots=True)
class OTEOrderBlockIntersection:
    """Immutable geometric intersection record between an OTE zone and an Order Block.

    Analytic confluence only — does NOT constitute an order signal or execution guarantee.
    """

    intersection_id: str
    ote_id: str
    order_block_id: str
    direction: DealingRangeDirection
    instrument_id: InstrumentId
    price_type: PriceType
    timeframe: Timeframe

    intersection_bottom: Decimal
    intersection_top: Decimal
    intersection_width: Decimal
    is_touch_only: bool

    ote_zone_width: Decimal
    ob_zone_width: Decimal
    overlap_ratio_of_ob: Decimal
    overlap_ratio_of_ote: Decimal
    intersection_available_at: datetime


# ── Core Domain Functions ───────────────────────────────────────────────────
def calculate_ote_zone(
    dealing_range: DealingRangeRecord,
    config: OTEConfig | None = None,
) -> OTEZoneRecord:
    """Calculate the exact OTE zone for a given verified Dealing Range."""
    cfg = config or OTEConfig()

    r = dealing_range.range_size
    if r <= Decimal("0"):
        raise OTEDataError(f"Dealing range size must be positive, got {r}")

    if dealing_range.direction == DealingRangeDirection.BULLISH:
        ote_62 = dealing_range.range_high - (r * cfg.ote_start_ratio)
        ote_705 = dealing_range.range_high - (r * cfg.ote_center_ratio)
        ote_79 = dealing_range.range_high - (r * cfg.ote_end_ratio)
        zone_bottom = ote_79
        zone_top = ote_62
    elif dealing_range.direction == DealingRangeDirection.BEARISH:
        ote_62 = dealing_range.range_low + (r * cfg.ote_start_ratio)
        ote_705 = dealing_range.range_low + (r * cfg.ote_center_ratio)
        ote_79 = dealing_range.range_low + (r * cfg.ote_end_ratio)
        zone_bottom = ote_62
        zone_top = ote_79
    else:
        raise OTEDataError(f"Unsupported dealing range direction: {dealing_range.direction}")

    ote_id = generate_ote_id(dealing_range.range_id)

    return OTEZoneRecord(
        ote_id=ote_id,
        dealing_range_id=dealing_range.range_id,
        instrument_id=dealing_range.instrument_id,
        source_id=dealing_range.source_id,
        timeframe=dealing_range.timeframe,
        price_type=dealing_range.price_type,
        direction=dealing_range.direction,
        range_low=dealing_range.range_low,
        range_high=dealing_range.range_high,
        equilibrium=dealing_range.equilibrium,
        ote_62=ote_62,
        ote_705=ote_705,
        ote_79=ote_79,
        zone_bottom=zone_bottom,
        zone_top=zone_top,
        range_available_at=dealing_range.range_available_at,
        availability_confidence=dealing_range.availability_confidence,
        pit_policy=dealing_range.pit_policy,
    )


def generate_ote_zones(
    dealing_ranges: Sequence[DealingRangeRecord],
    config: OTEConfig | None = None,
) -> list[OTEZoneRecord]:
    """Generate OTE zones for a sequence of Dealing Ranges."""
    cfg = config or OTEConfig()
    return [calculate_ote_zone(dr, cfg) for dr in dealing_ranges]


def compute_ote_order_block_intersection(
    ote: OTEZoneRecord,
    ob: OrderBlockRecord,
    as_of_utc: datetime | None = None,
) -> OTEOrderBlockIntersection | None:
    """Compute the analytical geometric confluence between an OTE zone and an Order Block.

    Conditions for evaluation:
    - Identical instrument_id, source_id, price_type, timeframe.
    - Directional harmony: Bullish OTE with Bullish OB, Bearish OTE with Bearish OB.
    - Overlap: intersection_bottom <= intersection_top.
    - Point-in-Time safety: intersection_available_at <= as_of_utc (if as_of_utc provided).
    """
    norm_as_of = ensure_utc(as_of_utc) if as_of_utc is not None else None
    avail_at = max(ote.range_available_at, ob.confirmation_time)

    if norm_as_of is not None and avail_at > norm_as_of:
        return None

    # Metadata isolation
    if (
        ote.instrument_id != ob.instrument_id
        or ote.source_id != ob.source_id
        or ote.price_type != ob.price_type
        or ote.timeframe != ob.timeframe
    ):
        return None

    # Directional harmony
    is_bull_match = (
        ote.direction == DealingRangeDirection.BULLISH
        and ob.direction == OrderBlockDirection.BULLISH
    )
    is_bear_match = (
        ote.direction == DealingRangeDirection.BEARISH
        and ob.direction == OrderBlockDirection.BEARISH
    )
    if not (is_bull_match or is_bear_match):
        return None

    # Geometric intersection
    b = max(ote.zone_bottom, ob.zone_bottom)
    t = min(ote.zone_top, ob.zone_top)

    if b > t:
        return None

    is_touch = b == t
    width = t - b
    ote_w = ote.zone_width
    ob_w = ob.zone_top - ob.zone_bottom

    overlap_ob = (width / ob_w) if ob_w > Decimal("0") else Decimal("0")
    overlap_ote = (width / ote_w) if ote_w > Decimal("0") else Decimal("0")
    inter_id = generate_intersection_id(ote.ote_id, ob.order_block_id)

    return OTEOrderBlockIntersection(
        intersection_id=inter_id,
        ote_id=ote.ote_id,
        order_block_id=ob.order_block_id,
        direction=ote.direction,
        instrument_id=ote.instrument_id,
        price_type=ote.price_type,
        timeframe=ote.timeframe,
        intersection_bottom=b,
        intersection_top=t,
        intersection_width=width,
        is_touch_only=is_touch,
        ote_zone_width=ote_w,
        ob_zone_width=ob_w,
        overlap_ratio_of_ob=overlap_ob,
        overlap_ratio_of_ote=overlap_ote,
        intersection_available_at=avail_at,
    )


def compute_all_ote_order_block_intersections(
    ote_zones: Sequence[OTEZoneRecord],
    order_blocks: Sequence[OrderBlockRecord],
    as_of_utc: datetime | None = None,
) -> list[OTEOrderBlockIntersection]:
    """Compute all valid pairwise geometric intersections between OTE zones and Order Blocks."""
    results: list[OTEOrderBlockIntersection] = []
    for ote in ote_zones:
        for ob in order_blocks:
            inter = compute_ote_order_block_intersection(ote, ob, as_of_utc=as_of_utc)
            if inter is not None:
                results.append(inter)
    return results


def filter_ote_ob_intersections_as_of(
    intersections: Sequence[OTEOrderBlockIntersection],
    as_of_utc: datetime,
) -> list[OTEOrderBlockIntersection]:
    """Point-in-time filter for OTE/OB intersections based on joint availability timestamp."""
    norm_as_of = ensure_utc(as_of_utc)
    return [i for i in intersections if i.intersection_available_at <= norm_as_of]


def filter_ote_zones_as_of(
    ote_zones: Sequence[OTEZoneRecord],
    as_of_utc: datetime,
) -> list[OTEZoneRecord]:
    """Point-in-time filter for OTE zones based on range confirmation availability timestamp."""
    norm_as_of = ensure_utc(as_of_utc)
    return [z for z in ote_zones if z.range_available_at <= norm_as_of]


def summarize_ote_zones(
    ote_zones: Sequence[OTEZoneRecord],
) -> dict[str, int | str | Decimal]:
    """Produce a deterministic diagnostic summary dictionary for OTE zones."""
    if not ote_zones:
        return {
            "total_ote_zones": 0,
            "bullish_ote_zones": 0,
            "bearish_ote_zones": 0,
            "min_ote_width": Decimal("0"),
            "max_ote_width": Decimal("0"),
        }

    bull_count = sum(1 for z in ote_zones if z.direction == DealingRangeDirection.BULLISH)
    bear_count = sum(1 for z in ote_zones if z.direction == DealingRangeDirection.BEARISH)
    widths = [z.zone_width for z in ote_zones]

    return {
        "total_ote_zones": len(ote_zones),
        "bullish_ote_zones": bull_count,
        "bearish_ote_zones": bear_count,
        "min_ote_width": min(widths),
        "max_ote_width": max(widths),
    }


def summarize_ote_ob_intersections(
    intersections: Sequence[OTEOrderBlockIntersection],
) -> dict[str, int | str | Decimal]:
    """Produce a diagnostic summary of OTE / Order Block intersections."""
    if not intersections:
        return {
            "total_intersections": 0,
            "positive_width_overlaps": 0,
            "touch_only_overlaps": 0,
            "average_overlap_width": Decimal("0"),
        }

    pos_count = sum(1 for i in intersections if not i.is_touch_only)
    touch_count = sum(1 for i in intersections if i.is_touch_only)
    total_w = sum((i.intersection_width for i in intersections), Decimal("0"))
    avg_w = total_w / Decimal(len(intersections))

    return {
        "total_intersections": len(intersections),
        "positive_width_overlaps": pos_count,
        "touch_only_overlaps": touch_count,
        "average_overlap_width": avg_w,
    }
