"""Point-in-Time Safe ICT Strategy Rules & Trade Plan Generation Engine.

Transforms confirmed ICT Setup Candidates (Reversals & Continuations) and structural
confluences into deterministic, explainable, and audit-ready analytical Trade Plans.

Guarantees:
    1. **Strict Pure Domain Logic** — Zero dependencies on infrastructure (PostgreSQL,
       filesystem), visualization (Streamlit, Plotly), or external APIs.
    2. **Point-in-Time Integrity** — Strictly enforces 4 distinct timestamps:
       - `setup_available_time`: when the structural setup was verified.
       - `entry_zone_available_time`: when the chosen entry zone was computable.
       - `plan_decision_time`: the exact evaluation / snapshot moment (`as_of_utc`).
       - `entry_condition_confirmation_time`: when touch / reaction was confirmed on a closed candle.
    3. **Historical vs Current Entry Eligibility** — Historical setup validity is preserved
       even if an Order Block is subsequently invalidated; however, if at `plan_decision_time`
       the zone is `INVALIDATED` or `FULLY_MITIGATED`, no active entry plan can be formed.
    4. **Analytical Only** — No broker execution, order placement, fictitious fills,
       position sizing, or PnL claims.
"""

from __future__ import annotations

import enum
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
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
from traderlab.domain.market_structure.fvg import FvgZone
from traderlab.domain.market_structure.fvg_respect import FvgRespectSummary
from traderlab.domain.market_structure.ict_setup import (
    IctSetupCandidate,
    IctSetupDirection,
    IctSetupStatus,
    IctSetupType,
)
from traderlab.domain.market_structure.order_block import (
    OrderBlockRecord,
    OrderBlockStatus,
    filter_order_blocks_as_of,
)
from traderlab.domain.market_structure.ote import OTEZoneRecord as OteZone


# ── Exceptions ───────────────────────────────────────────────────────────────
class TradePlanError(TraderLabError):
    """Base exception for trade plan domain errors."""

    def __init__(self, message: str, *, code: str = "TRADE_PLAN_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidTradePlanConfigError(TradePlanError):
    """Raised when trade plan configuration parameters are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_TRADE_PLAN_CONFIG")


# ── Enumerations ────────────────────────────────────────────────────────────
class TradePlanDirection(enum.Enum):
    """Direction of the analytical trade plan."""

    LONG = "LONG"
    SHORT = "SHORT"


class TradePlanEntryMode(enum.Enum):
    """Approach used to derive the analytical entry reference price."""

    ZONE_LIMIT_REFERENCE = "ZONE_LIMIT_REFERENCE"
    """Entry at the proximal boundary of the zone (top for Bullish, bottom for Bearish)."""

    ZONE_MIDPOINT_REFERENCE = "ZONE_MIDPOINT_REFERENCE"
    """Entry at the exact 50% midpoint of the zone boundaries."""

    CONFIRMED_REACTION_REFERENCE = "CONFIRMED_REACTION_REFERENCE"
    """Entry verified only after an eligible closed candle confirms a reaction/departure."""


class TradePlanStatus(enum.Enum):
    """Lifecycle state machine for an ICT Trade Plan."""

    DRAFT = "DRAFT"
    """Candidate plan generated with verified setup and zone, pending market action."""

    WAITING_FOR_RETRACEMENT = "WAITING_FOR_RETRACEMENT"
    """Active plan waiting for price to enter / test the selected entry zone."""

    TOUCH_OBSERVED = "TOUCH_OBSERVED"
    """A closed candle has touched / penetrated the entry zone boundaries."""

    REACTION_CONFIRMED = "REACTION_CONFIRMED"
    """Price touch and subsequent directional departure / respect verified on closed candles."""

    INVALIDATED = "INVALIDATED"
    """Zone violated by an opposing candle close or underlying structure invalidated."""

    EXPIRED = "EXPIRED"
    """Maximum allowed waiting duration / bars elapsed without retracement."""

    BLOCKED = "BLOCKED"
    """Plan rejected due to data quality anomalies, PIT violation, or policy exclusion."""


class EntryZoneType(enum.Enum):
    """Type of price imbalance or structural block selected as entry zone."""

    NORMAL_FVG = "NORMAL_FVG"
    INVERTED_FVG = "INVERTED_FVG"
    ORDER_BLOCK = "ORDER_BLOCK"
    OTE_OVERLAP = "OTE_OVERLAP"
    CONFLUENCE_OVERLAP = "CONFLUENCE_OVERLAP"


class EntryZoneSelectionPolicy(enum.Enum):
    """Deterministic policy for choosing primary entry zone among candidate structures."""

    FVG_FIRST = "FVG_FIRST"
    """Select eligible FVG/IFVG; fallback to Order Block if FVG absent."""

    ORDER_BLOCK_FIRST = "ORDER_BLOCK_FIRST"
    """Select eligible Order Block; fallback to FVG if Order Block absent."""

    NARROWEST_VALID_OVERLAP = "NARROWEST_VALID_OVERLAP"
    """Select the zone with the narrowest price width among eligible zones."""

    EXPLICIT_ZONE_ID = "EXPLICIT_ZONE_ID"
    """Target a specific zone by ID; reject if not found or invalid."""


class TradePlanRejectionReason(enum.Enum):
    """Detailed explanation for why an ICT setup cannot generate an active trade plan."""

    SETUP_NOT_CONFIRMED = "SETUP_NOT_CONFIRMED"
    SETUP_INVALIDATED = "SETUP_INVALIDATED"
    SETUP_EXPIRED = "SETUP_EXPIRED"
    ENTRY_ZONE_MISSING = "ENTRY_ZONE_MISSING"
    ENTRY_ZONE_INVALIDATED = "ENTRY_ZONE_INVALIDATED"
    ENTRY_ZONE_FULLY_MITIGATED = "ENTRY_ZONE_FULLY_MITIGATED"
    DIRECTION_MISMATCH = "DIRECTION_MISMATCH"
    METADATA_MISMATCH = "METADATA_MISMATCH"
    PRICE_TYPE_MISMATCH = "PRICE_TYPE_MISMATCH"
    TIMEFRAME_MISMATCH = "TIMEFRAME_MISMATCH"
    FUTURE_INFORMATION = "FUTURE_INFORMATION"
    UNAVAILABLE_DATA = "UNAVAILABLE_DATA"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    AMBIGUOUS_INTRABAR_ORDER = "AMBIGUOUS_INTRABAR_ORDER"
    MAX_WAITING_BARS_EXCEEDED = "MAX_WAITING_BARS_EXCEEDED"
    QUALITY_ANOMALY_PARTIAL_BAR = "QUALITY_ANOMALY_PARTIAL_BAR"
    QUALITY_ANOMALY_SYNTHETIC = "QUALITY_ANOMALY_SYNTHETIC"
    QUALITY_ANOMALY_OFF_SESSION = "QUALITY_ANOMALY_OFF_SESSION"
    RESPECT_CONDITION_NOT_MET = "RESPECT_CONDITION_NOT_MET"


# ── Deterministic Identifier Generator ───────────────────────────────────────
def generate_trade_plan_id(
    instrument_id: InstrumentId | str,
    source_id: DataSourceId | str,
    price_type: PriceType | str,
    timeframe: Timeframe | str,
    direction: TradePlanDirection,
    setup_id: str,
    entry_zone_id: str,
    entry_mode: TradePlanEntryMode,
    config_fingerprint: str = "",
) -> str:
    """Generate a collision-resistant, deterministic identifier for an ICT Trade Plan.

    Format:
        {instrument}:{source}:{price_type}:{timeframe}:{direction}:{setup_hash}:{zone_hash}:{entry_mode}[:{cfg_hash}]
    """
    inst_str = str(instrument_id)
    src_str = str(source_id)
    pt_str = price_type.value if isinstance(price_type, PriceType) else str(price_type)
    tf_str = timeframe.name if isinstance(timeframe, Timeframe) else str(timeframe)
    dir_str = direction.value

    s_hash = hashlib.sha256(setup_id.encode("utf-8")).hexdigest()[:12]
    z_hash = hashlib.sha256(entry_zone_id.encode("utf-8")).hexdigest()[:12]
    base = f"{inst_str}:{src_str}:{pt_str}:{tf_str}:{dir_str}:{s_hash}:{z_hash}:{entry_mode.value}"
    if config_fingerprint:
        c_hash = hashlib.sha256(config_fingerprint.encode("utf-8")).hexdigest()[:8]
        return f"{base}:{c_hash}"
    return base


# ── Domain Models ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class TradePlanEvidence:
    """Immutable audit trail for a structural or contextual piece of confluence."""

    evidence_type: str
    source_event_id: str
    source_timeframe: Timeframe
    direction: TradePlanDirection | None
    event_time: datetime
    available_time: datetime
    price_bottom: Decimal | None = None
    price_top: Decimal | None = None
    is_satisfied: bool = True
    explanation: str = ""


@dataclass(frozen=True, slots=True)
class TradePlanLifecycleEvent:
    """A point-in-time state transition record for a trade plan."""

    event_id: str
    plan_id: str
    bar_open_time: datetime
    bar_close_time: datetime
    status_before: TradePlanStatus
    status_after: TradePlanStatus
    trigger_price: Decimal | None
    reason: str
    event_time_utc: datetime


@dataclass(frozen=True, slots=True)
class TradePlanConfig:
    """Configuration parameters for deterministic Trade Plan generation and evaluation."""

    entry_zone_policy: EntryZoneSelectionPolicy = EntryZoneSelectionPolicy.FVG_FIRST
    """Policy for selecting primary entry zone when multiple structures exist."""

    entry_mode: TradePlanEntryMode = TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE
    """Mathematical approach used to derive the reference entry price."""

    max_waiting_bars: int = 30
    """Maximum number of closed bars allowed to wait for zone retracement before expiry."""

    require_fvg_respect: bool = False
    """If True, requires >= 1 confirmed FVG respect reaction before plan activation."""

    min_respect_count: int = 1
    """Minimum number of confirmed respect events required when respect is enforced."""

    explicit_entry_zone_id: str | None = None
    """Specific zone ID targeted when policy is EXPLICIT_ZONE_ID."""

    tick_size: Decimal = Decimal("0.01")
    """Instrument tick size in exact Decimal units."""

    strict_pit: bool = False
    """If True, strictly rejects historical records marked as ESTIMATED."""

    policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED
    """Point-in-Time evaluation policy."""

    def __post_init__(self) -> None:
        if self.max_waiting_bars < 1:
            raise InvalidTradePlanConfigError("max_waiting_bars must be >= 1")
        if self.min_respect_count < 1:
            raise InvalidTradePlanConfigError("min_respect_count must be >= 1")
        if self.tick_size <= Decimal("0"):
            raise InvalidTradePlanConfigError("tick_size must be strictly positive Decimal")


@dataclass(frozen=True, slots=True)
class IctTradePlan:
    """Immutable domain representation of an analytical ICT Trade Plan.

    Separates four core timestamps:
    - `setup_available_time`: when the structural ICT setup was confirmed.
    - `entry_zone_available_time`: when the chosen entry zone was computable.
    - `plan_decision_time`: the exact evaluation / snapshot moment (`as_of_utc`).
    - `entry_condition_confirmation_time`: when touch / reaction was confirmed on a closed candle.
    """

    deterministic_trade_plan_id: str
    source_setup_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    price_type: PriceType
    setup_type: IctSetupType
    direction: TradePlanDirection
    entry_mode: TradePlanEntryMode

    entry_zone_id: str
    entry_zone_type: EntryZoneType
    entry_zone_bottom: Decimal
    entry_zone_top: Decimal
    entry_reference_price: Decimal

    entry_conditions: tuple[str, ...]
    plan_status: TradePlanStatus

    setup_available_time: datetime
    entry_zone_available_time: datetime
    plan_available_time: datetime
    plan_decision_time: datetime
    entry_condition_confirmation_time: datetime | None = None

    as_of_utc: datetime | None = None
    last_evaluation_time: datetime | None = None
    d1_session_mode: str | None = None
    availability_confidence: AvailabilityConfidence = AvailabilityConfidence.ESTIMATED

    related_event_ids: tuple[str, ...] = field(default_factory=tuple)
    rejection_reasons: tuple[TradePlanRejectionReason, ...] = field(default_factory=tuple)
    lifecycle_events: tuple[TradePlanLifecycleEvent, ...] = field(default_factory=tuple)

    touch_bar_open_time: datetime | None = None
    touch_detection_time: datetime | None = None
    touch_price: Decimal | None = None
    is_entry_price_reached: bool = False
    entry_price_reached_time: datetime | None = None
    entry_price_reached_bar_open_time: datetime | None = None
    invalidation_time: datetime | None = None

    def __post_init__(self) -> None:
        if self.entry_zone_bottom >= self.entry_zone_top:
            raise TradePlanError(
                f"Invalid entry zone boundaries: bottom ({self.entry_zone_bottom}) must be < top ({self.entry_zone_top})"
            )
        if self.plan_available_time < self.setup_available_time:
            raise TradePlanError(
                f"plan_available_time ({self.plan_available_time}) cannot precede setup_available_time ({self.setup_available_time})"
            )
        if self.plan_available_time < self.entry_zone_available_time:
            raise TradePlanError(
                f"plan_available_time ({self.plan_available_time}) cannot precede entry_zone_available_time ({self.entry_zone_available_time})"
            )


@dataclass(frozen=True, slots=True)
class TradePlanSummary:
    """Statistical summary of generated trade plans across a dataset or snapshot."""

    total_candidates: int
    active_plans: int
    waiting_for_retracement_count: int
    touch_observed_count: int
    reaction_confirmed_count: int
    invalidated_count: int
    expired_count: int
    blocked_count: int
    long_plans_count: int
    short_plans_count: int
    fvg_zone_count: int
    ob_zone_count: int
    ote_zone_count: int
    unique_plan_ids: int
    entry_price_reached_count: int = 0
    confluence_overlap_zone_count: int = 0


# ── Internal Calculation Helpers ─────────────────────────────────────────────
def _calculate_entry_reference_price(
    mode: TradePlanEntryMode,
    direction: TradePlanDirection,
    zone_bottom: Decimal,
    zone_top: Decimal,
    confirmed_price: Decimal | None = None,
) -> Decimal:
    """Calculate the exact Decimal entry reference price according to the entry mode."""
    if mode == TradePlanEntryMode.ZONE_LIMIT_REFERENCE:
        # Bullish/LONG: entering a pullback from above -> proximal boundary is top
        # Bearish/SHORT: entering a pullback from below -> proximal boundary is bottom
        return zone_top if direction == TradePlanDirection.LONG else zone_bottom
    if mode == TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE:
        return (zone_bottom + zone_top) / Decimal("2")
    if mode == TradePlanEntryMode.CONFIRMED_REACTION_REFERENCE:
        if confirmed_price is not None:
            return confirmed_price
        # Default to proximal boundary until confirmed reaction price is observed
        return zone_top if direction == TradePlanDirection.LONG else zone_bottom
    return (zone_bottom + zone_top) / Decimal("2")


def _select_entry_zone(
    setup: IctSetupCandidate,
    policy: EntryZoneSelectionPolicy,
    fvg_dict: dict[str, FvgZone],
    ob_dict: dict[str, OrderBlockRecord],
    ote_dict: dict[str, OteZone],
    decision_time: datetime,
    explicit_id: str | None = None,
) -> tuple[
    str | None,
    EntryZoneType | None,
    Decimal | None,
    Decimal | None,
    datetime | None,
    TradePlanRejectionReason | None,
]:
    """Select the primary entry zone according to the configured selection policy.

    Enforces that the zone is known at `decision_time` and is active (not invalidated or fully mitigated).
    """
    # 1. Gather potential zone candidates from the setup confluences / IDs
    candidate_fvg: FvgZone | None = None
    if setup.fvg_id and setup.fvg_id in fvg_dict:
        f = fvg_dict[setup.fvg_id]
        # Check computable time
        if f.computable_at_utc <= decision_time:
            candidate_fvg = f

    candidate_ob: OrderBlockRecord | None = None
    unconfirmed_ob_time: datetime | None = None
    if setup.order_block_id and setup.order_block_id in ob_dict:
        raw_ob = ob_dict[setup.order_block_id]
        if raw_ob.confirmation_time <= decision_time:
            reconstructed = filter_order_blocks_as_of([raw_ob], decision_time)
            if reconstructed:
                candidate_ob = reconstructed[0]
        else:
            unconfirmed_ob_time = raw_ob.confirmation_time

    candidate_ote: OteZone | None = None
    if setup.ote_id and setup.ote_id in ote_dict:
        candidate_ote = ote_dict[setup.ote_id]

    # Handle EXPLICIT_ZONE_ID policy
    if policy == EntryZoneSelectionPolicy.EXPLICIT_ZONE_ID:
        if not explicit_id:
            return None, None, None, None, None, TradePlanRejectionReason.ENTRY_ZONE_MISSING
        if explicit_id in ob_dict:
            raw_ob = ob_dict[explicit_id]
            reconstructed = filter_order_blocks_as_of([raw_ob], decision_time)
            if reconstructed and reconstructed[0].is_active:
                return (
                    raw_ob.order_block_id,
                    EntryZoneType.ORDER_BLOCK,
                    reconstructed[0].zone_bottom,
                    reconstructed[0].zone_top,
                    raw_ob.confirmation_time,
                    None,
                )
            reason = (
                TradePlanRejectionReason.ENTRY_ZONE_FULLY_MITIGATED
                if reconstructed and reconstructed[0].status == OrderBlockStatus.FULLY_MITIGATED
                else TradePlanRejectionReason.ENTRY_ZONE_INVALIDATED
            )
            return None, None, None, None, None, reason
        if explicit_id in fvg_dict:
            f = fvg_dict[explicit_id]
            return (
                f.id,
                EntryZoneType.NORMAL_FVG,
                f.bottom_price,
                f.top_price,
                f.computable_at_utc,
                None,
            )
        return None, None, None, None, None, TradePlanRejectionReason.ENTRY_ZONE_MISSING

    # Handle ORDER_BLOCK_FIRST policy
    if policy == EntryZoneSelectionPolicy.ORDER_BLOCK_FIRST:
        if candidate_ob:
            if candidate_ob.is_active:
                return (
                    candidate_ob.order_block_id,
                    EntryZoneType.ORDER_BLOCK,
                    candidate_ob.zone_bottom,
                    candidate_ob.zone_top,
                    candidate_ob.confirmation_time,
                    None,
                )
            # OB exists but is inactive at decision time: try FVG fallback
            if candidate_fvg:
                return (
                    candidate_fvg.id,
                    EntryZoneType.NORMAL_FVG,
                    candidate_fvg.bottom_price,
                    candidate_fvg.top_price,
                    candidate_fvg.computable_at_utc,
                    None,
                )
            reason = (
                TradePlanRejectionReason.ENTRY_ZONE_FULLY_MITIGATED
                if candidate_ob.status == OrderBlockStatus.FULLY_MITIGATED
                else TradePlanRejectionReason.ENTRY_ZONE_INVALIDATED
            )
            return None, None, None, None, None, reason
        if (
            unconfirmed_ob_time is not None
            and setup.order_block_id
            and setup.order_block_id in ob_dict
        ):
            target_ob = ob_dict[setup.order_block_id]
            return (
                target_ob.order_block_id,
                EntryZoneType.ORDER_BLOCK,
                target_ob.zone_bottom,
                target_ob.zone_top,
                unconfirmed_ob_time,
                None,
            )
        # Fallback to FVG if no OB
        if candidate_fvg:
            return (
                candidate_fvg.id,
                EntryZoneType.NORMAL_FVG,
                candidate_fvg.bottom_price,
                candidate_fvg.top_price,
                candidate_fvg.computable_at_utc,
                None,
            )
        # Fallback to setup pre-selected zone if available
        if setup.selected_zone_bottom is not None and setup.selected_zone_top is not None:
            z_id = setup.order_block_id or setup.fvg_id or f"ZONE_{setup.setup_id}"
            z_type = (
                EntryZoneType.ORDER_BLOCK if setup.order_block_id else EntryZoneType.NORMAL_FVG
            )
            z_avail = setup.zone_available_time or setup.setup_available_time
            return z_id, z_type, setup.selected_zone_bottom, setup.selected_zone_top, z_avail, None

        return None, None, None, None, None, TradePlanRejectionReason.ENTRY_ZONE_MISSING

    # Handle NARROWEST_VALID_OVERLAP policy
    if policy == EntryZoneSelectionPolicy.NARROWEST_VALID_OVERLAP:
        zone_candidates: list[tuple[str, EntryZoneType, Decimal, Decimal, datetime]] = []
        if candidate_fvg:
            z_type = (
                EntryZoneType.INVERTED_FVG
                if getattr(candidate_fvg, "is_inverted", False)
                else EntryZoneType.NORMAL_FVG
            )
            zone_candidates.append(
                (
                    candidate_fvg.id,
                    z_type,
                    candidate_fvg.bottom_price,
                    candidate_fvg.top_price,
                    candidate_fvg.computable_at_utc,
                )
            )
        if candidate_ob and candidate_ob.is_active:
            zone_candidates.append(
                (
                    candidate_ob.order_block_id,
                    EntryZoneType.ORDER_BLOCK,
                    candidate_ob.zone_bottom,
                    candidate_ob.zone_top,
                    candidate_ob.confirmation_time,
                )
            )
        if candidate_ote:
            zone_candidates.append(
                (
                    candidate_ote.ote_id,
                    EntryZoneType.OTE_OVERLAP,
                    candidate_ote.zone_bottom,
                    candidate_ote.zone_top,
                    candidate_ote.range_available_at,
                )
            )

        geometric_overlaps: list[
            tuple[str, EntryZoneType, Decimal, Decimal, datetime, Decimal]
        ] = []

        # 3-way intersection if all 3 exist
        if len(zone_candidates) == 3:
            z1, z2, z3 = zone_candidates
            ov_bot = max(z1[2], z2[2], z3[2])
            ov_top = min(z1[3], z2[3], z3[3])
            if ov_top > ov_bot:
                ov_time = max(z1[4], z2[4], z3[4])
                ov_id = f"OVERLAP_{z1[0]}_{z2[0]}_{z3[0]}"
                ov_width = ov_top - ov_bot
                geometric_overlaps.append(
                    (ov_id, EntryZoneType.CONFLUENCE_OVERLAP, ov_bot, ov_top, ov_time, ov_width)
                )

        # 2-way pairwise intersections
        for i in range(len(zone_candidates)):
            for j in range(i + 1, len(zone_candidates)):
                zi, zj = zone_candidates[i], zone_candidates[j]
                ov_bot = max(zi[2], zj[2])
                ov_top = min(zi[3], zj[3])
                if ov_top > ov_bot:
                    ov_time = max(zi[4], zj[4])
                    ov_id = f"OVERLAP_{zi[0]}_{zj[0]}"
                    ov_width = ov_top - ov_bot
                    geometric_overlaps.append(
                        (
                            ov_id,
                            EntryZoneType.CONFLUENCE_OVERLAP,
                            ov_bot,
                            ov_top,
                            ov_time,
                            ov_width,
                        )
                    )

        if geometric_overlaps:
            geometric_overlaps.sort(key=lambda x: (x[5], x[0]))
            best_ov = geometric_overlaps[0]
            return best_ov[0], best_ov[1], best_ov[2], best_ov[3], best_ov[4], None

        # Fallback to narrowest single zone if disjoint or no overlap
        if zone_candidates:
            single_options = [(z[0], z[1], z[2], z[3], z[4], z[3] - z[2]) for z in zone_candidates]
            single_options.sort(key=lambda x: (x[5], x[0]))
            best_single = single_options[0]
            return (
                best_single[0],
                best_single[1],
                best_single[2],
                best_single[3],
                best_single[4],
                None,
            )

    # Handle Default FVG_FIRST policy
    if candidate_fvg:
        return (
            candidate_fvg.id,
            EntryZoneType.NORMAL_FVG,
            candidate_fvg.bottom_price,
            candidate_fvg.top_price,
            candidate_fvg.computable_at_utc,
            None,
        )

    # Fallback to OB if no FVG
    if candidate_ob:
        if not candidate_ob.is_active:
            reason = (
                TradePlanRejectionReason.ENTRY_ZONE_FULLY_MITIGATED
                if candidate_ob.status == OrderBlockStatus.FULLY_MITIGATED
                else TradePlanRejectionReason.ENTRY_ZONE_INVALIDATED
            )
            return None, None, None, None, None, reason
        return (
            candidate_ob.order_block_id,
            EntryZoneType.ORDER_BLOCK,
            candidate_ob.zone_bottom,
            candidate_ob.zone_top,
            candidate_ob.confirmation_time,
            None,
        )

    # Fallback to setup pre-selected zone
    if setup.selected_zone_bottom is not None and setup.selected_zone_top is not None:
        z_id = setup.fvg_id or setup.order_block_id or f"ZONE_{setup.setup_id}"
        z_type = EntryZoneType.ORDER_BLOCK if setup.order_block_id else EntryZoneType.NORMAL_FVG
        z_avail = setup.zone_available_time or setup.setup_available_time
        return z_id, z_type, setup.selected_zone_bottom, setup.selected_zone_top, z_avail, None

    return None, None, None, None, None, TradePlanRejectionReason.ENTRY_ZONE_MISSING


# ── Public Engine Functions ──────────────────────────────────────────────────
def evaluate_trade_plan_lifecycle(
    plan: IctTradePlan,
    candles: Sequence[Candle],
    *,
    setup_invalidation_time: datetime | None = None,
    as_of_utc: datetime | None = None,
    max_waiting_bars: int = 30,
) -> IctTradePlan:
    """Evaluate closed candle interaction with the plan's entry zone chronologically up to as_of_utc.

    Detects:
    1. First touch of zone boundaries (`candle.low <= zone_top and candle.high >= zone_bottom`).
    2. Analytical entry reference price reached (`candle.low <= entry_reference_price` for LONG,
       `candle.high >= entry_reference_price` for SHORT).
    3. Zone violation / invalidation (`close < zone_bottom` for LONG, `close > zone_top` for SHORT,
       or underlying setup invalidation).
    4. Expiration if waiting bars exceed `max_waiting_bars` without touch.
    """
    cutoff = ensure_utc(as_of_utc) if as_of_utc is not None else datetime.max.replace(tzinfo=UTC)

    # If plan was already blocked, return unchanged
    if plan.plan_status == TradePlanStatus.BLOCKED:
        return plan

    # Only evaluate closed candles formed after plan becomes computable
    eligible_candles = [
        c
        for c in candles
        if c.is_closed and c.open_time >= plan.plan_available_time and c.close_time <= cutoff
    ]

    current_status = TradePlanStatus.WAITING_FOR_RETRACEMENT
    touch_open: datetime | None = None
    touch_detect: datetime | None = None
    touch_px: Decimal | None = None
    entry_reached = False
    entry_reached_t: datetime | None = None
    entry_reached_open_t: datetime | None = None
    inval_t: datetime | None = None
    events: list[TradePlanLifecycleEvent] = []
    bars_waited = 0

    for c in eligible_candles:
        # Check invalidation: setup invalidation or close beyond distal boundary
        is_setup_inval = (
            setup_invalidation_time is not None and c.close_time >= setup_invalidation_time
        )
        is_zone_inval = False
        if plan.direction == TradePlanDirection.LONG:
            if c.close < plan.entry_zone_bottom:
                is_zone_inval = True
        else:
            if c.close > plan.entry_zone_top:
                is_zone_inval = True

        if is_zone_inval or is_setup_inval:
            prev_status = current_status
            current_status = TradePlanStatus.INVALIDATED
            inval_t = c.close_time
            reason = (
                "Candle closed beyond distal entry zone boundary"
                if is_zone_inval
                else "Underlying ICT setup invalidated during market action"
            )
            ev = TradePlanLifecycleEvent(
                event_id=f"EV_INVAL_{plan.deterministic_trade_plan_id}_{c.close_time.strftime('%Y%m%d%H%M%S')}",
                plan_id=plan.deterministic_trade_plan_id,
                bar_open_time=c.open_time,
                bar_close_time=c.close_time,
                status_before=prev_status,
                status_after=current_status,
                trigger_price=c.close,
                reason=reason,
                event_time_utc=c.close_time,
            )
            events.append(ev)
            break

        # Check touch if not yet touched
        if touch_open is None:
            bars_waited += 1
            has_touched = c.low <= plan.entry_zone_top and c.high >= plan.entry_zone_bottom
            if has_touched:
                prev_status = current_status
                current_status = TradePlanStatus.TOUCH_OBSERVED
                touch_open = c.open_time
                touch_detect = c.close_time
                # Proximal touch price
                touch_px = (
                    plan.entry_zone_top
                    if plan.direction == TradePlanDirection.LONG
                    else plan.entry_zone_bottom
                )
                ev = TradePlanLifecycleEvent(
                    event_id=f"EV_TOUCH_{plan.deterministic_trade_plan_id}_{c.close_time.strftime('%Y%m%d%H%M%S')}",
                    plan_id=plan.deterministic_trade_plan_id,
                    bar_open_time=c.open_time,
                    bar_close_time=c.close_time,
                    status_before=prev_status,
                    status_after=current_status,
                    trigger_price=touch_px,
                    reason="Closed candle entered entry zone boundaries",
                    event_time_utc=c.close_time,
                )
                events.append(ev)
            elif bars_waited >= max_waiting_bars:
                prev_status = current_status
                current_status = TradePlanStatus.EXPIRED
                inval_t = c.close_time
                ev = TradePlanLifecycleEvent(
                    event_id=f"EV_EXP_{plan.deterministic_trade_plan_id}_{c.close_time.strftime('%Y%m%d%H%M%S')}",
                    plan_id=plan.deterministic_trade_plan_id,
                    bar_open_time=c.open_time,
                    bar_close_time=c.close_time,
                    status_before=prev_status,
                    status_after=current_status,
                    trigger_price=c.close,
                    reason=f"Exceeded {max_waiting_bars} bars waiting for zone retracement",
                    event_time_utc=c.close_time,
                )
                events.append(ev)
                break

        # Item A: Check if analytical entry reference price was reached on this closed candle
        if not entry_reached and touch_open is not None:
            px_reached = False
            if plan.direction == TradePlanDirection.LONG:
                if c.low <= plan.entry_reference_price:
                    px_reached = True
            else:
                if c.high >= plan.entry_reference_price:
                    px_reached = True

            if px_reached:
                entry_reached = True
                entry_reached_t = c.close_time
                entry_reached_open_t = c.open_time
                ev_reach = TradePlanLifecycleEvent(
                    event_id=f"EV_ENTRY_REACHED_{plan.deterministic_trade_plan_id}_{c.close_time.strftime('%Y%m%d%H%M%S')}",
                    plan_id=plan.deterministic_trade_plan_id,
                    bar_open_time=c.open_time,
                    bar_close_time=c.close_time,
                    status_before=current_status,
                    status_after=current_status,
                    trigger_price=plan.entry_reference_price,
                    reason="Entry reference price reached on closed candle",
                    event_time_utc=c.close_time,
                )
                events.append(ev_reach)

    # If entry mode requires confirmed reaction, determine reaction
    entry_conf_time: datetime | None = None
    if (
        plan.entry_mode == TradePlanEntryMode.CONFIRMED_REACTION_REFERENCE
        and touch_detect is not None
    ):
        post_touch_candles = [c for c in eligible_candles if c.open_time >= touch_detect]
        for pc in post_touch_candles:
            favorable = (
                (pc.close > plan.entry_zone_top)
                if plan.direction == TradePlanDirection.LONG
                else (pc.close < plan.entry_zone_bottom)
            )
            if favorable:
                prev_status = current_status
                current_status = TradePlanStatus.REACTION_CONFIRMED
                entry_conf_time = pc.close_time
                ev = TradePlanLifecycleEvent(
                    event_id=f"EV_REACT_{plan.deterministic_trade_plan_id}_{pc.close_time.strftime('%Y%m%d%H%M%S')}",
                    plan_id=plan.deterministic_trade_plan_id,
                    bar_open_time=pc.open_time,
                    bar_close_time=pc.close_time,
                    status_before=prev_status,
                    status_after=current_status,
                    trigger_price=pc.close,
                    reason="Directional reaction confirmed on closed candle",
                    event_time_utc=pc.close_time,
                )
                events.append(ev)
                break

    last_eval = cutoff if cutoff != datetime.max.replace(tzinfo=UTC) else plan.plan_available_time
    final_entry_conf = entry_conf_time or (entry_reached_t if entry_reached else None)

    return replace(
        plan,
        plan_status=current_status,
        entry_condition_confirmation_time=final_entry_conf,
        lifecycle_events=tuple(events),
        touch_bar_open_time=touch_open,
        touch_detection_time=touch_detect,
        touch_price=touch_px,
        is_entry_price_reached=entry_reached,
        entry_price_reached_time=entry_reached_t,
        entry_price_reached_bar_open_time=entry_reached_open_t,
        invalidation_time=inval_t,
        last_evaluation_time=last_eval,
    )


def generate_ict_trade_plans(
    setups: Sequence[IctSetupCandidate],
    candles: Sequence[Candle],
    *,
    fvg_zones: Sequence[FvgZone] | None = None,
    order_blocks: Sequence[OrderBlockRecord] | None = None,
    ote_zones: Sequence[OteZone] | None = None,
    fvg_respect_summaries: Sequence[FvgRespectSummary] | None = None,
    config: TradePlanConfig | None = None,
    as_of_utc: datetime | None = None,
) -> list[IctTradePlan]:
    """Generate deterministic, PIT-safe ICT Trade Plans from confirmed setup candidates.

    Guarantees:
    - Directional alignment: Bullish -> LONG, Bearish -> SHORT.
    - Zero future leakage: plan cannot exist before max(setup_available_time, entry_zone_available_time).
    - Strict Isolation: Matching instrument, source, timeframe, price_type.
    - Entry Zone Safety: Reconstructs Order Blocks at decision time; rejected if invalidated/fully mitigated.
    """
    cfg = config or TradePlanConfig()
    norm_as_of = ensure_utc(as_of_utc) if as_of_utc is not None else None

    # Dictionaries for O(1) zone lookups
    fvg_dict = {f.id: f for f in (fvg_zones or ())}
    ob_dict = {o.order_block_id: o for o in (order_blocks or ())}
    ote_dict = {o.ote_id: o for o in (ote_zones or ())}

    generated_plans: list[IctTradePlan] = []
    seen_plan_ids: set[str] = set()

    for s in setups:
        # Determine decision time for this setup
        decision_time = norm_as_of if norm_as_of is not None else s.last_evaluation_time
        rejection_reasons: list[TradePlanRejectionReason] = []

        # Map direction
        plan_dir = (
            TradePlanDirection.LONG
            if s.direction == IctSetupDirection.BULLISH
            else TradePlanDirection.SHORT
        )

        # Select entry zone at decision_time
        (
            zone_id,
            zone_type,
            z_bottom,
            z_top,
            z_avail,
            zone_rejection,
        ) = _select_entry_zone(
            setup=s,
            policy=cfg.entry_zone_policy,
            fvg_dict=fvg_dict,
            ob_dict=ob_dict,
            ote_dict=ote_dict,
            decision_time=decision_time,
            explicit_id=cfg.explicit_entry_zone_id,
        )

        if zone_rejection is not None:
            rejection_reasons.append(zone_rejection)

        # Determine availability times
        zone_available = z_avail if z_avail is not None else s.setup_available_time
        plan_avail = max(s.setup_available_time, zone_available)

        # Item B: Check setup status eligibility at plan inception
        setup_inval_time: datetime | None = None
        if s.status == IctSetupStatus.WAITING_FOR_CONFIRMATION:
            rejection_reasons.append(TradePlanRejectionReason.SETUP_NOT_CONFIRMED)
        elif s.status == IctSetupStatus.INVALIDATED:
            # If invalidation happened at or before plan could become available, reject at inception.
            # If invalidation happened after plan availability, the setup was legitimate at inception
            # and transitions to INVALIDATED during market action in lifecycle evaluation.
            if s.invalidation_time is None or s.invalidation_time <= plan_avail:
                rejection_reasons.append(TradePlanRejectionReason.SETUP_INVALIDATED)
            else:
                setup_inval_time = s.invalidation_time
        elif s.status == IctSetupStatus.EXPIRED and (
            s.invalidation_time is None or s.invalidation_time <= plan_avail
        ):
            rejection_reasons.append(TradePlanRejectionReason.SETUP_EXPIRED)

        # If strict PIT requested and setup is research estimated, reject
        if cfg.strict_pit and s.availability_confidence != AvailabilityConfidence.VERIFIED:
            rejection_reasons.append(TradePlanRejectionReason.UNAVAILABLE_DATA)

        # Respect condition check if configured
        if cfg.require_fvg_respect and fvg_respect_summaries:
            respect_found = any(
                r.fvg_id == zone_id and r.confirmed_respect_count >= cfg.min_respect_count
                for r in fvg_respect_summaries
            )
            if not respect_found:
                rejection_reasons.append(TradePlanRejectionReason.RESPECT_CONDITION_NOT_MET)

        # If as_of_utc is specified and plan is not yet available, skip for this snapshot
        if norm_as_of is not None and plan_avail > norm_as_of:
            continue

        # If any rejection reason exists, mark as BLOCKED with dummy or fallback boundaries
        is_blocked = len(rejection_reasons) > 0
        final_status = (
            TradePlanStatus.BLOCKED if is_blocked else TradePlanStatus.WAITING_FOR_RETRACEMENT
        )

        final_z_bottom = (
            z_bottom if z_bottom is not None else (s.selected_zone_bottom or Decimal("0"))
        )
        final_z_top = z_top if z_top is not None else (s.selected_zone_top or Decimal("1"))
        final_zone_id = zone_id or (s.order_block_id or s.fvg_id or "NO_ZONE")
        final_zone_type = zone_type or EntryZoneType.NORMAL_FVG

        # Calculate entry reference price
        ref_price = _calculate_entry_reference_price(
            mode=cfg.entry_mode,
            direction=plan_dir,
            zone_bottom=final_z_bottom,
            zone_top=final_z_top,
        )

        # Deterministic ID
        cfg_fingerprint = (
            f"{cfg.entry_zone_policy.value}:{cfg.entry_mode.value}:{cfg.max_waiting_bars}"
        )
        plan_id = generate_trade_plan_id(
            instrument_id=s.instrument_id,
            source_id=s.source_id,
            price_type=s.price_type,
            timeframe=s.timeframe,
            direction=plan_dir,
            setup_id=s.setup_id,
            entry_zone_id=final_zone_id,
            entry_mode=cfg.entry_mode,
            config_fingerprint=cfg_fingerprint,
        )

        # Avoid duplicate plans
        if plan_id in seen_plan_ids:
            continue
        seen_plan_ids.add(plan_id)

        # Related events
        rel_events = [s.structure_event_id]
        if s.sweep_event_id:
            rel_events.append(s.sweep_event_id)
        if s.mss_confirmation_id:
            rel_events.append(s.mss_confirmation_id)
        if s.displacement_event_id:
            rel_events.append(s.displacement_event_id)
        if final_zone_id:
            rel_events.append(final_zone_id)

        entry_conds = (
            f"Direction={plan_dir.value}",
            f"ZoneType={final_zone_type.value}",
            f"EntryMode={cfg.entry_mode.value}",
            f"Policy={cfg.entry_zone_policy.value}",
        )

        raw_plan = IctTradePlan(
            deterministic_trade_plan_id=plan_id,
            source_setup_id=s.setup_id,
            instrument_id=s.instrument_id,
            source_id=s.source_id,
            timeframe=s.timeframe,
            price_type=s.price_type,
            setup_type=s.setup_type,
            direction=plan_dir,
            entry_mode=cfg.entry_mode,
            entry_zone_id=final_zone_id,
            entry_zone_type=final_zone_type,
            entry_zone_bottom=final_z_bottom,
            entry_zone_top=final_z_top,
            entry_reference_price=ref_price,
            entry_conditions=entry_conds,
            plan_status=final_status,
            setup_available_time=s.setup_available_time,
            entry_zone_available_time=zone_available,
            plan_available_time=plan_avail,
            plan_decision_time=decision_time,
            entry_condition_confirmation_time=None,
            as_of_utc=norm_as_of,
            last_evaluation_time=decision_time,
            d1_session_mode=s.d1_session_mode,
            availability_confidence=s.availability_confidence,
            related_event_ids=tuple(rel_events),
            rejection_reasons=tuple(rejection_reasons),
        )

        # Evaluate candle lifecycle interaction
        evaluated_plan = evaluate_trade_plan_lifecycle(
            plan=raw_plan,
            candles=candles,
            setup_invalidation_time=setup_inval_time,
            as_of_utc=norm_as_of,
            max_waiting_bars=cfg.max_waiting_bars,
        )

        generated_plans.append(evaluated_plan)

    return generated_plans


def filter_trade_plans_as_of(
    plans: Sequence[IctTradePlan],
    as_of_utc: datetime,
) -> list[IctTradePlan]:
    """Reconstruct point-in-time state of trade plans at a historical snapshot timestamp.

    Guarantees:
    - Plans with `plan_available_time > as_of_utc` are completely excluded.
    - Status and lifecycle events are reconstructed strictly using events with `event_time <= as_of_utc`.
    - Future touch, reaction, or invalidation events never leak into historical snapshots.
    """
    cutoff = ensure_utc(as_of_utc)
    filtered: list[IctTradePlan] = []

    for p in plans:
        # Exclude plans not yet known at cutoff
        if p.plan_available_time > cutoff:
            continue

        # If blocked at inception, retain as blocked
        if p.plan_status == TradePlanStatus.BLOCKED:
            filtered.append(
                replace(
                    p, as_of_utc=cutoff, plan_decision_time=cutoff, last_evaluation_time=cutoff
                )
            )
            continue

        # Filter lifecycle events up to cutoff
        valid_events = tuple(ev for ev in p.lifecycle_events if ev.event_time_utc <= cutoff)

        if not valid_events:
            # No events occurred yet -> waiting for retracement
            reconstructed_status = TradePlanStatus.WAITING_FOR_RETRACEMENT
            touch_open = None
            touch_detect = None
            touch_px = None
            inval_t = None
            entry_conf = None
        else:
            last_ev = valid_events[-1]
            reconstructed_status = last_ev.status_after

            # Find earliest touch event up to cutoff
            touch_ev = next(
                (e for e in valid_events if e.status_after == TradePlanStatus.TOUCH_OBSERVED), None
            )
            touch_open = touch_ev.bar_open_time if touch_ev else None
            touch_detect = touch_ev.bar_close_time if touch_ev else None
            touch_px = touch_ev.trigger_price if touch_ev else None

            # Find invalidation/expiry event up to cutoff
            term_ev = next(
                (
                    e
                    for e in valid_events
                    if e.status_after in (TradePlanStatus.INVALIDATED, TradePlanStatus.EXPIRED)
                ),
                None,
            )
            inval_t = term_ev.event_time_utc if term_ev else None

            # Entry confirmation event up to cutoff
            react_ev = next(
                (e for e in valid_events if e.status_after == TradePlanStatus.REACTION_CONFIRMED),
                None,
            )
            reach_ev = next(
                (e for e in valid_events if e.event_id.startswith("EV_ENTRY_REACHED_")),
                None,
            )
            is_reached = reach_ev is not None
            reach_t = reach_ev.bar_close_time if reach_ev else None
            reach_open_t = reach_ev.bar_open_time if reach_ev else None

            entry_conf = react_ev.event_time_utc if react_ev else (reach_t if is_reached else None)

        reconstructed_plan = replace(
            p,
            plan_status=reconstructed_status,
            as_of_utc=cutoff,
            plan_decision_time=cutoff,
            last_evaluation_time=cutoff,
            lifecycle_events=valid_events,
            touch_bar_open_time=touch_open,
            touch_detection_time=touch_detect,
            touch_price=touch_px,
            is_entry_price_reached=is_reached if valid_events else False,
            entry_price_reached_time=reach_t if valid_events else None,
            entry_price_reached_bar_open_time=reach_open_t if valid_events else None,
            invalidation_time=inval_t,
            entry_condition_confirmation_time=entry_conf,
        )
        filtered.append(reconstructed_plan)

    return filtered


def summarize_trade_plans(plans: Sequence[IctTradePlan]) -> TradePlanSummary:
    """Produce an aggregate statistical summary across a collection of trade plans."""
    total = len(plans)
    active = sum(
        1
        for p in plans
        if p.plan_status
        in (TradePlanStatus.WAITING_FOR_RETRACEMENT, TradePlanStatus.TOUCH_OBSERVED)
    )
    waiting = sum(1 for p in plans if p.plan_status == TradePlanStatus.WAITING_FOR_RETRACEMENT)
    touch = sum(1 for p in plans if p.plan_status == TradePlanStatus.TOUCH_OBSERVED)
    reaction = sum(1 for p in plans if p.plan_status == TradePlanStatus.REACTION_CONFIRMED)
    invalidated = sum(1 for p in plans if p.plan_status == TradePlanStatus.INVALIDATED)
    expired = sum(1 for p in plans if p.plan_status == TradePlanStatus.EXPIRED)
    blocked = sum(1 for p in plans if p.plan_status == TradePlanStatus.BLOCKED)

    longs = sum(1 for p in plans if p.direction == TradePlanDirection.LONG)
    shorts = sum(1 for p in plans if p.direction == TradePlanDirection.SHORT)

    fvg_cnt = sum(
        1
        for p in plans
        if p.entry_zone_type in (EntryZoneType.NORMAL_FVG, EntryZoneType.INVERTED_FVG)
    )
    ob_cnt = sum(1 for p in plans if p.entry_zone_type == EntryZoneType.ORDER_BLOCK)
    ote_cnt = sum(1 for p in plans if p.entry_zone_type == EntryZoneType.OTE_OVERLAP)

    unique_ids = len({p.deterministic_trade_plan_id for p in plans})

    reached_cnt = sum(1 for p in plans if p.is_entry_price_reached)
    confluence_cnt = sum(1 for p in plans if p.entry_zone_type == EntryZoneType.CONFLUENCE_OVERLAP)

    return TradePlanSummary(
        total_candidates=total,
        active_plans=active,
        waiting_for_retracement_count=waiting,
        touch_observed_count=touch,
        reaction_confirmed_count=reaction,
        invalidated_count=invalidated,
        expired_count=expired,
        blocked_count=blocked,
        long_plans_count=longs,
        short_plans_count=shorts,
        fvg_zone_count=fvg_cnt,
        ob_zone_count=ob_cnt,
        ote_zone_count=ote_cnt,
        unique_plan_ids=unique_ids,
        entry_price_reached_count=reached_cnt,
        confluence_overlap_zone_count=confluence_cnt,
    )


def explain_trade_plan(plan: IctTradePlan) -> str:
    """Generate a comprehensive, human-readable explanatory audit card for a trade plan."""
    rej_str = (
        ", ".join(r.value for r in plan.rejection_reasons) if plan.rejection_reasons else "NONE"
    )
    touch_str = (
        plan.touch_detection_time.strftime("%Y-%m-%d %H:%M:%S UTC")
        if plan.touch_detection_time
        else "NOT_TOUCHED"
    )
    inval_str = (
        plan.invalidation_time.strftime("%Y-%m-%d %H:%M:%S UTC")
        if plan.invalidation_time
        else "NONE"
    )
    entry_reached_str = (
        f"YES (at {plan.entry_price_reached_time.strftime('%Y-%m-%d %H:%M:%S UTC')})"
        if plan.is_entry_price_reached and plan.entry_price_reached_time
        else (
            "NO (Zone touched only)"
            if plan.touch_detection_time
            else "NO (Waiting for retracement)"
        )
    )

    return (
        f"=== ICT TRADE PLAN [{plan.direction.value}] ===\n"
        f"Plan ID:                {plan.deterministic_trade_plan_id}\n"
        f"Status:                 {plan.plan_status.value}\n"
        f"Source Setup ID:        {plan.source_setup_id}\n"
        f"Setup Type:             {plan.setup_type.value}\n"
        f"Instrument & PriceType: {plan.instrument_id} | {plan.price_type.value} | {plan.timeframe.name}\n"
        f"Entry Zone:             {plan.entry_zone_type.value} ({plan.entry_zone_id})\n"
        f"Zone Boundaries:        [{plan.entry_zone_bottom} - {plan.entry_zone_top}]\n"
        f"Entry Mode:             {plan.entry_mode.value}\n"
        f"Entry Reference Price:  {plan.entry_reference_price}\n"
        f"Entry Price Reached:    {entry_reached_str}\n"
        f"Setup Available Time:   {plan.setup_available_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Zone Available Time:    {plan.entry_zone_available_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Plan Available Time:    {plan.plan_available_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Plan Decision Time:     {plan.plan_decision_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Touch Observed Time:    {touch_str}\n"
        f"Invalidation Time:      {inval_str}\n"
        f"Rejection Reasons:      {rej_str}\n"
        f"Related Structural IDs: {', '.join(plan.related_event_ids)}\n"
        f"Lifecycle Transitions:  {len(plan.lifecycle_events)} events recorded"
    )
