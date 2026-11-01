r"""Point-in-Time Safe Order Block Detection and Lifecycle Engine.

Provides mathematically rigorous, deterministic, and Point-in-Time compliant
Order Block (OB) identification and lifecycle tracking for institutional SMC/ICT research.

Key Architectural & Financial Principles:
1. **Operational Definition**:
   - **Bullish Order Block**: The last bearish candle (close < open) prior to a qualified
     bullish displacement movement that achieves structural confirmation (BOS or confirmed MSS).
   - **Bearish Order Block**: The last bullish candle (close > open) prior to a qualified
     bearish displacement movement that achieves structural confirmation (BOS or confirmed MSS).
2. **Qualified Structure & Displacement Foundation**:
   - Every confirmed Order Block MUST be causally anchored to a confirmed Break of Structure (BOS)
     or a fully confirmed Market Structure Shift (MSS_CONFIRMED).
   - An unconfirmed CHoCH or MSS_CANDIDATE alone CANNOT create a confirmed Order Block.
   - Leverages verified Faz 3D `DisplacementEvent` and Faz 3C/3D structural events without reinventing momentum.
3. **Deterministic Selection**:
   - When multiple opposing candles precede displacement within `max_origin_lookback_bars`,
     the chronologically closest opposing candle immediately preceding displacement is selected.
   - If no valid opposing candle exists within lookback, no Order Block is created.
4. **Point-in-Time Usability & Confirmation Timing**:
   - Origin candle occurred historically, but the Order Block is only confirmed when the
     structural break and displacement are both confirmed:
     `confirmation_time = max(origin.close_time, structure.detection_time, displacement.detection_time)`.
   - Invisible prior to `confirmation_time`.
5. **Zone Pricing Modes**:
   - `FULL_CANDLE_RANGE`: zone_bottom = low, zone_top = high.
   - `BODY_ONLY`: zone_bottom = min(open, close), zone_top = max(open, close).
   - Midpoint 50% (Consequent Encroachment) = (zone_bottom + zone_top) / 2.
6. **Immutable Lifecycle State Machine**:
   - `UNMITIGATED` -> `TOUCHED` -> `PARTIALLY_MITIGATED` -> `FULLY_MITIGATED` / `INVALIDATED`.
   - Invalidation requires bar close beyond zone boundary (body close); wick breaches alone do NOT invalidate.
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
from traderlab.domain.market_structure.displacement import (
    DisplacementDirection,
    DisplacementEvent,
)
from traderlab.domain.market_structure.fvg import FvgZone
from traderlab.domain.market_structure.market_structure import (
    StructureBreakEvent,
    StructureBreakType,
    StructureDirection,
)
from traderlab.domain.market_structure.mss_confirmation import (
    MssConfirmationRecord,
    MssStatus,
)


# ── Exceptions ───────────────────────────────────────────────────────────────
class OrderBlockError(TraderLabError):
    """Base exception for Order Block domain errors."""

    def __init__(self, message: str, *, code: str = "ORDER_BLOCK_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidOrderBlockParameterError(OrderBlockError):
    """Raised when Order Block configuration parameters are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_ORDER_BLOCK_PARAMETER")


class OrderBlockDataError(OrderBlockError):
    """Raised when input candle series or structural events are inconsistent."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="ORDER_BLOCK_DATA_ERROR")


# ── Enums ────────────────────────────────────────────────────────────────────
class OrderBlockDirection(enum.Enum):
    """Directional orientation of the Order Block."""

    BULLISH = "BULLISH"
    """Demand zone: Last bearish candle before bullish expansion; expected support."""

    BEARISH = "BEARISH"
    """Supply zone: Last bullish candle before bearish expansion; expected resistance."""


class OrderBlockStatus(enum.Enum):
    """Lifecycle state of an Order Block."""

    CANDIDATE = "CANDIDATE"
    """Identified origin candle awaiting confirmation from structure or displacement."""

    CONFIRMED = "CONFIRMED"
    """Structure break and displacement confirmed; ready for market testing."""

    UNMITIGATED = "UNMITIGATED"
    """Active confirmed zone with zero subsequent price testing."""

    TOUCHED = "TOUCHED"
    """Price touched the outer boundary of the zone without reaching midpoint (50%)."""

    PARTIALLY_MITIGATED = "PARTIALLY_MITIGATED"
    """Price penetrated into the zone reaching or exceeding midpoint (50%) without full breach."""

    FULLY_MITIGATED = "FULLY_MITIGATED"
    """Price penetrated completely through the zone to the opposing boundary without closing beyond."""

    INVALIDATED = "INVALIDATED"
    """Price closed beyond the zone boundary (body close violation), terminating the Order Block."""


class OrderBlockZoneMode(enum.Enum):
    """Method used to define the price boundaries of the Order Block zone."""

    FULL_CANDLE_RANGE = "FULL_CANDLE_RANGE"
    """Zone spans from origin candle low to high (including wicks)."""

    BODY_ONLY = "BODY_ONLY"
    """Zone spans strictly across origin candle body: min(open, close) to max(open, close)."""


# ── Deterministic Identifier Generators ──────────────────────────────────────
def generate_order_block_id(
    instrument_id: InstrumentId | str,
    source_id: DataSourceId | str,
    price_type: PriceType | str,
    timeframe: Timeframe | str,
    direction: OrderBlockDirection | str,
    origin_candle_open_time: datetime,
    structure_reference_id: str,
) -> str:
    """Generate a deterministic, globally unique string identifier for an Order Block."""
    inst_str = str(instrument_id)
    src_str = str(source_id)
    pt_str = price_type.value if isinstance(price_type, PriceType) else str(price_type)
    tf_str = timeframe.name if isinstance(timeframe, Timeframe) else str(timeframe)
    dir_str = direction.value if isinstance(direction, OrderBlockDirection) else str(direction)
    t_str = origin_candle_open_time.strftime("%Y%m%dT%H%M%SZ")
    return f"{inst_str}_{src_str}_{pt_str}_{tf_str}_{dir_str}_OB_{t_str}_{structure_reference_id}"


def generate_lifecycle_event_id(
    order_block_id: str,
    bar_open_time: datetime,
    status_after: OrderBlockStatus,
) -> str:
    """Generate a deterministic identifier for an Order Block lifecycle transition event."""
    t_str = bar_open_time.strftime("%Y%m%dT%H%M%SZ")
    return f"EV_{order_block_id}_{t_str}_{status_after.value}"


# ── Domain Models ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class OrderBlockLifecycleEvent:
    """Immutable record of an individual lifecycle state transition."""

    event_id: str
    order_block_id: str
    event_time: datetime
    bar_open_time: datetime
    bar_close_time: datetime
    status_before: OrderBlockStatus
    status_after: OrderBlockStatus
    trigger_price: Decimal
    penetration_depth: Decimal
    penetration_percentage: Decimal
    description: str


@dataclass(frozen=True, slots=True)
class OrderBlockRecord:
    """Immutable domain representation of a confirmed Order Block and its lifecycle.

    Guarantees:
    - **Point-in-Time Integrity**: `confirmation_time == max(origin, structure, displacement, fvg)`.
    - **Strict Causality**: Linked to concrete structure breaks and displacements.
    - **Zero Lookahead**: Inactive and invisible prior to `confirmation_time`.
    """

    order_block_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    price_type: PriceType
    direction: OrderBlockDirection
    zone_mode: OrderBlockZoneMode

    # Zone Boundaries
    zone_bottom: Decimal
    zone_top: Decimal
    midpoint_50: Decimal

    # Origin Bar References
    origin_candle_open_time: datetime
    origin_candle_close_time: datetime
    origin_candle_id: str

    # Confirmation Timestamps & Causal Links
    confirmation_time: datetime
    displacement_event_id: str
    structure_break_event_id: str | None
    mss_confirmation_id: str | None
    associated_fvg_id: str | None

    # Lifecycle State
    status: OrderBlockStatus
    penetration_depth: Decimal = Decimal("0")
    max_penetration_percentage: Decimal = Decimal("0")
    touch_time: datetime | None = None
    invalidation_time: datetime | None = None
    full_mitigation_time: datetime | None = None
    lifecycle_events: tuple[OrderBlockLifecycleEvent, ...] = ()

    availability_confidence: AvailabilityConfidence = AvailabilityConfidence.ESTIMATED
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.zone_bottom >= self.zone_top:
            raise OrderBlockError(
                f"Invalid zone: zone_bottom ({self.zone_bottom}) must be < zone_top ({self.zone_top})"
            )
        expected_mid = (self.zone_bottom + self.zone_top) / Decimal("2")
        if self.midpoint_50 != expected_mid:
            raise OrderBlockError(
                f"Invalid midpoint: midpoint_50 ({self.midpoint_50}) must equal ({expected_mid})"
            )
        if self.origin_candle_close_time <= self.origin_candle_open_time:
            raise OrderBlockError("origin_candle_close_time must be strictly after open_time")
        if self.confirmation_time < self.origin_candle_close_time:
            raise OrderBlockError(
                f"confirmation_time ({self.confirmation_time}) cannot precede origin_candle_close_time ({self.origin_candle_close_time})"
            )

    @property
    def is_bullish(self) -> bool:
        return self.direction == OrderBlockDirection.BULLISH

    @property
    def is_bearish(self) -> bool:
        return self.direction == OrderBlockDirection.BEARISH

    @property
    def zone_height(self) -> Decimal:
        return self.zone_top - self.zone_bottom

    @property
    def is_active(self) -> bool:
        return self.status in (
            OrderBlockStatus.CONFIRMED,
            OrderBlockStatus.UNMITIGATED,
            OrderBlockStatus.TOUCHED,
            OrderBlockStatus.PARTIALLY_MITIGATED,
        )

    @property
    def is_invalidated(self) -> bool:
        return self.status == OrderBlockStatus.INVALIDATED

    @property
    def is_fully_mitigated(self) -> bool:
        return self.status == OrderBlockStatus.FULLY_MITIGATED

    def is_available_at(self, as_of: datetime) -> bool:
        """Check if this Order Block was confirmed and visible at decision time `as_of`."""
        return self.confirmation_time <= ensure_utc(as_of)


# ── Configuration Model ──────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class OrderBlockConfig:
    """Configuration settings for Order Block detection and lifecycle tracking."""

    max_origin_lookback_bars: int = 6
    zone_mode: OrderBlockZoneMode = OrderBlockZoneMode.FULL_CANDLE_RANGE
    tick_size: Decimal = Decimal("0.01")
    invalidation_threshold_ticks: int = 0
    require_fvg: bool = False
    policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED
    strict_pit: bool = False
    allow_synthetic: bool = False
    allow_weekend_gap: bool = True
    allow_rollover_gap: bool = True

    def __post_init__(self) -> None:
        if self.max_origin_lookback_bars < 1:
            raise InvalidOrderBlockParameterError(
                f"max_origin_lookback_bars must be >= 1, got {self.max_origin_lookback_bars}"
            )
        if self.tick_size <= Decimal("0"):
            raise InvalidOrderBlockParameterError(
                f"tick_size must be strictly positive Decimal, got {self.tick_size}"
            )
        if self.invalidation_threshold_ticks < 0:
            raise InvalidOrderBlockParameterError(
                f"invalidation_threshold_ticks must be >= 0, got {self.invalidation_threshold_ticks}"
            )

    @property
    def invalidation_threshold(self) -> Decimal:
        return self.tick_size * Decimal(self.invalidation_threshold_ticks)


# ── Helper Validation ────────────────────────────────────────────────────────
def _validate_candle_sequence(candles: Sequence[Candle]) -> None:
    if not candles:
        return
    first = candles[0]
    for i, c in enumerate(candles):
        if c.timeframe != first.timeframe:
            raise OrderBlockDataError(
                f"Timeframe mismatch at bar {i}: {c.timeframe} != {first.timeframe}"
            )
        if c.instrument_id != first.instrument_id:
            raise OrderBlockDataError(
                f"InstrumentId mismatch at bar {i}: {c.instrument_id} != {first.instrument_id}"
            )
        if c.price_type != first.price_type:
            raise OrderBlockDataError(
                f"PriceType mismatch at bar {i}: {c.price_type} != {first.price_type}"
            )
        if i > 0 and c.open_time < candles[i - 1].open_time:
            raise OrderBlockDataError(f"Candles not chronologically sorted at bar {i}")


# ── Core Order Block Detection Engine ─────────────────────────────────────────
def detect_order_blocks(
    candles: Sequence[Candle],
    *,
    structure_breaks: Sequence[StructureBreakEvent] | None = None,
    displacements: Sequence[DisplacementEvent] | None = None,
    mss_confirmations: Sequence[MssConfirmationRecord] | None = None,
    fvg_zones: Sequence[FvgZone] | None = None,
    config: OrderBlockConfig | None = None,
    as_of_utc: datetime | None = None,
) -> list[OrderBlockRecord]:
    """Detect all confirmed Order Blocks causally linked to qualified structure breaks and displacements.

    Operational Rules:
    - **Bullish OB**: Last bearish candle (close < open) prior to qualified bullish displacement.
    - **Bearish OB**: Last bullish candle (close > open) prior to qualified bearish displacement.
    - **Qualified Movement**:
      - Must have a confirmed BOS (`StructureBreakType.BOS`) OR a fully confirmed MSS (`MSS_CONFIRMED`).
      - An unconfirmed CHoCH / MSS_CANDIDATE alone CANNOT create an Order Block.
    - **Origin Search Window**: Lookback up to `max_origin_lookback_bars` before displacement.
    - **Zone Calculation**:
      - `FULL_CANDLE_RANGE`: [origin.low, origin.high]
      - `BODY_ONLY`: [min(origin.open, origin.close), max(origin.open, origin.close)]
    - **Point-in-Time Confirmation**:
      `confirmation_time = max(origin.close_time, structure.detection_time, displacement.detection_time, fvg.formation_time)`
    """
    if config is None:
        config = OrderBlockConfig()

    if not candles:
        return []

    _validate_candle_sequence(candles)

    as_of_norm = ensure_utc(as_of_utc) if as_of_utc is not None else None
    effective_policy = PitPolicy.STRICT_PIT if config.strict_pit else config.policy

    # 1. Filter candles by PIT and quality
    effective_candles: list[Candle] = []
    for c in candles:
        if not c.is_closed:
            continue
        if not config.allow_synthetic and c.is_synthetic:
            continue
        if c.timeframe == Timeframe.D1 and c.open_time.weekday() == 6 and c.open_time.hour == 0:
            continue
        if as_of_norm is not None and c.close_time > as_of_norm:
            continue
        if not c.is_available_at(as_of_norm or c.close_time, policy=effective_policy):
            continue
        effective_candles.append(c)

    if not effective_candles:
        return []

    first_candle = effective_candles[0]
    candle_idx_map = {c.open_time: idx for idx, c in enumerate(effective_candles)}

    # 2. Filter available events by PIT and metadata
    eligible_breaks = [
        b
        for b in (structure_breaks or [])
        if b.instrument_id == first_candle.instrument_id
        and b.source_id == first_candle.source_id
        and b.timeframe == first_candle.timeframe
        and b.price_type == first_candle.price_type
        and (as_of_norm is None or b.detection_time <= as_of_norm)
    ]

    eligible_disps = [
        d
        for d in (displacements or [])
        if d.instrument_id == first_candle.instrument_id
        and d.source_id == first_candle.source_id
        and d.timeframe == first_candle.timeframe
        and d.price_type == first_candle.price_type
        and (as_of_norm is None or d.detection_time <= as_of_norm)
    ]

    eligible_mss = [
        m
        for m in (mss_confirmations or [])
        if m.instrument_id == first_candle.instrument_id
        and m.source_id == first_candle.source_id
        and m.timeframe == first_candle.timeframe
        and m.price_type == first_candle.price_type
        and m.status == MssStatus.MSS_CONFIRMED
        and (
            as_of_norm is None
            or (m.final_confirmation_time is not None and m.final_confirmation_time <= as_of_norm)
        )
    ]

    eligible_fvgs = [
        f
        for f in (fvg_zones or [])
        if f.instrument_id == first_candle.instrument_id
        and f.source_id == first_candle.source_id
        and f.timeframe == first_candle.timeframe
        and f.price_type == first_candle.price_type
        and (as_of_norm is None or f.formation_time <= as_of_norm)
    ]
    fvg_by_candle2 = {f.candle_2_open: f for f in eligible_fvgs}

    disp_by_open = {d.candle_open_time: d for d in eligible_disps}

    # 3. Identify Qualified Structural Movements
    # A qualified movement has: (direction, displacement_event, structure_break_event, mss_confirmation)
    qualified_movements: list[
        tuple[
            OrderBlockDirection,
            DisplacementEvent,
            StructureBreakEvent | None,
            MssConfirmationRecord | None,
        ]
    ] = []

    # Pathway A: From Confirmed MSS Records
    for mss in eligible_mss:
        if mss.displacement_time is None or mss.displacement_event_id is None:
            continue
        # Find the displacement event
        disp_ev = next(
            (d for d in eligible_disps if d.event_id == mss.displacement_event_id),
            None,
        )
        if disp_ev is None:
            continue

        direction = (
            OrderBlockDirection.BULLISH
            if mss.direction == StructureDirection.BULLISH
            else OrderBlockDirection.BEARISH
        )
        qualified_movements.append((direction, disp_ev, None, mss))

    # Pathway B: From Confirmed BOS Records
    for bos in eligible_breaks:
        if bos.break_type != StructureBreakType.BOS or bos.confirmation_status != "CONFIRMED":
            continue

        direction = (
            OrderBlockDirection.BULLISH
            if bos.structure_direction == StructureDirection.BULLISH
            else OrderBlockDirection.BEARISH
        )
        expected_disp_dir = (
            DisplacementDirection.BULLISH
            if direction == OrderBlockDirection.BULLISH
            else DisplacementDirection.BEARISH
        )

        # Look for displacement associated with this BOS:
        # Either the break candle itself was displacement, or a displacement within +/- 2 bars of the BOS
        if bos.break_bar_open_time not in candle_idx_map:
            continue
        bos_idx = candle_idx_map[bos.break_bar_open_time]

        # Search window for displacement around BOS: from break_idx - 1 to break_idx + 2
        start_d = max(0, bos_idx - 1)
        end_d = min(len(effective_candles) - 1, bos_idx + 2)

        matched_disp: DisplacementEvent | None = None
        for i in range(start_d, end_d + 1):
            t_open = effective_candles[i].open_time
            if t_open in disp_by_open:
                cand_disp = disp_by_open[t_open]
                if cand_disp.direction == expected_disp_dir:
                    matched_disp = cand_disp
                    break

        if matched_disp is not None:
            qualified_movements.append((direction, matched_disp, bos, None))

    # Sort movements chronologically
    qualified_movements.sort(key=lambda m: (m[1].candle_open_time, m[1].event_id))

    # 4. Form Order Block records from qualified movements
    detected_obs: list[OrderBlockRecord] = []
    seen_ob_ids: set[str] = set()

    for direction, disp, bos_ev, mss_rec in qualified_movements:
        if disp.candle_open_time not in candle_idx_map:
            continue
        disp_idx = candle_idx_map[disp.candle_open_time]

        # Search backwards from displacement for the last opposing candle
        # Lookback range: disp_idx - 1 down to max(0, disp_idx - max_origin_lookback_bars)
        min_search_idx = max(0, disp_idx - config.max_origin_lookback_bars)
        origin_candle: Candle | None = None

        for idx in range(disp_idx - 1, min_search_idx - 1, -1):
            cand = effective_candles[idx]
            if direction == OrderBlockDirection.BULLISH:
                # Need last bearish candle: close < open
                if cand.close < cand.open:
                    origin_candle = cand
                    break
            else:
                # Need last bullish candle: close > open
                if cand.close > cand.open:
                    origin_candle = cand
                    break

        if origin_candle is None:
            # No valid opposing candle in lookback window; zero OB produced
            continue

        # Optional FVG requirement check
        associated_fvg: FvgZone | None = None
        if disp.candle_open_time in fvg_by_candle2:
            associated_fvg = fvg_by_candle2[disp.candle_open_time]

        if config.require_fvg and associated_fvg is None:
            continue

        # Compute price zone boundaries
        if config.zone_mode == OrderBlockZoneMode.FULL_CANDLE_RANGE:
            z_bottom = origin_candle.low
            z_top = origin_candle.high
        else:
            # BODY_ONLY
            z_bottom = min(origin_candle.open, origin_candle.close)
            z_top = max(origin_candle.open, origin_candle.close)

        # Guard against zero/negative range
        if z_bottom >= z_top:
            continue

        midpoint = (z_bottom + z_top) / Decimal("2")

        # Determine confirmation time
        # Must be max(origin.close_time, structure_detection_time, displacement_detection_time, fvg_formation_time)
        times_to_max = [origin_candle.close_time, disp.detection_time]
        struct_ref_id: str
        if mss_rec is not None:
            struct_ref_id = mss_rec.mss_candidate_id
            times_to_max.append(mss_rec.candidate_time)
            if mss_rec.final_confirmation_time is not None:
                times_to_max.append(mss_rec.final_confirmation_time)
        elif bos_ev is not None:
            struct_ref_id = bos_ev.event_id
            times_to_max.append(bos_ev.detection_time)
        else:
            struct_ref_id = disp.event_id

        if config.require_fvg and associated_fvg is not None:
            times_to_max.append(associated_fvg.formation_time)

        confirmation_dt = max(times_to_max)

        # Point-in-Time filter: confirmation must be <= as_of_norm
        if as_of_norm is not None and confirmation_dt > as_of_norm:
            continue

        ob_id = generate_order_block_id(
            first_candle.instrument_id,
            first_candle.source_id,
            first_candle.price_type,
            first_candle.timeframe,
            direction,
            origin_candle.open_time,
            struct_ref_id,
        )

        if ob_id in seen_ob_ids:
            continue
        seen_ob_ids.add(ob_id)

        origin_candle_id = f"CANDLE_{first_candle.instrument_id}_{first_candle.timeframe}_{origin_candle.open_time.strftime('%Y%m%dT%H%M%SZ')}"

        rec = OrderBlockRecord(
            order_block_id=ob_id,
            instrument_id=first_candle.instrument_id,
            source_id=first_candle.source_id,
            timeframe=first_candle.timeframe,
            price_type=first_candle.price_type,
            direction=direction,
            zone_mode=config.zone_mode,
            zone_bottom=z_bottom,
            zone_top=z_top,
            midpoint_50=midpoint,
            origin_candle_open_time=origin_candle.open_time,
            origin_candle_close_time=origin_candle.close_time,
            origin_candle_id=origin_candle_id,
            confirmation_time=confirmation_dt,
            displacement_event_id=disp.event_id,
            structure_break_event_id=bos_ev.event_id if bos_ev else None,
            mss_confirmation_id=mss_rec.record_id if mss_rec else None,
            associated_fvg_id=associated_fvg.id if associated_fvg else None,
            status=OrderBlockStatus.UNMITIGATED,
            availability_confidence=first_candle.availability_confidence,
        )
        detected_obs.append(rec)

    # 5. Evaluate Lifecycle for all detected confirmed Order Blocks
    obs_with_lifecycle: list[OrderBlockRecord] = []
    for ob in detected_obs:
        evaluated_ob = evaluate_order_block_lifecycle(
            effective_candles,
            ob,
            config=config,
            as_of_utc=as_of_norm,
        )
        obs_with_lifecycle.append(evaluated_ob)

    obs_with_lifecycle.sort(key=lambda r: (r.confirmation_time, r.order_block_id))
    return obs_with_lifecycle


# ── Lifecycle Tracking State Machine ──────────────────────────────────────────
def evaluate_order_block_lifecycle(
    candles: Sequence[Candle],
    order_block: OrderBlockRecord,
    *,
    config: OrderBlockConfig | None = None,
    as_of_utc: datetime | None = None,
) -> OrderBlockRecord:
    """Evaluate chronological lifecycle progression for a confirmed Order Block.

    Guarantees:
    - Pure Point-in-Time: Only processes closed candles with `close_time <= as_of_utc`.
    - No Testing Before Confirmation: Testing only begins strictly after `confirmation_time`.
    - Invalidation Rule: Bullish invalidates on bar.close < zone_bottom - threshold.
      Bearish invalidates on bar.close > zone_top + threshold.
      Wick penetrations alone do NOT invalidate.
    - Deterministic transition sequence with full immutable audit events.
    """
    if config is None:
        config = OrderBlockConfig()

    as_of_norm = ensure_utc(as_of_utc) if as_of_utc is not None else None
    conf_time = order_block.confirmation_time

    # Find subsequent candles that closed strictly AFTER confirmation_time
    test_candles = [
        c
        for c in candles
        if c.is_closed
        and c.open_time >= conf_time
        and (as_of_norm is None or c.close_time <= as_of_norm)
    ]

    current_status = OrderBlockStatus.UNMITIGATED
    touch_time: datetime | None = None
    invalidation_time: datetime | None = None
    full_mitigation_time: datetime | None = None

    max_pen_depth = Decimal("0")
    max_pen_pct = Decimal("0")
    zone_h = order_block.zone_height
    inv_threshold = config.invalidation_threshold

    events: list[OrderBlockLifecycleEvent] = []

    for bar in test_candles:
        status_before_bar = current_status

        if order_block.is_bullish:
            # Bullish Demand Zone: [zone_bottom, zone_top]
            # Tests occur when price drops into the zone (bar.low <= zone_top)
            tested = bar.low <= order_block.zone_top

            if tested:
                # Compute penetration depth
                pen_depth = max(Decimal("0"), order_block.zone_top - bar.low)
                pen_pct = (pen_depth / zone_h) * Decimal("100")
                if pen_depth > max_pen_depth:
                    max_pen_depth = pen_depth
                if pen_pct > max_pen_pct:
                    max_pen_pct = pen_pct

                # Check Invalidation first: body close below bottom - threshold
                if bar.close < (order_block.zone_bottom - inv_threshold):
                    current_status = OrderBlockStatus.INVALIDATED
                    invalidation_time = bar.close_time
                    ev = OrderBlockLifecycleEvent(
                        event_id=generate_lifecycle_event_id(
                            order_block.order_block_id, bar.open_time, current_status
                        ),
                        order_block_id=order_block.order_block_id,
                        event_time=bar.close_time,
                        bar_open_time=bar.open_time,
                        bar_close_time=bar.close_time,
                        status_before=status_before_bar,
                        status_after=current_status,
                        trigger_price=bar.close,
                        penetration_depth=max_pen_depth,
                        penetration_percentage=max_pen_pct,
                        description=f"Invalidated: Bearish body close {bar.close} < {order_block.zone_bottom - inv_threshold}",
                    )
                    events.append(ev)
                    # Once invalidated, lifecycle terminates
                    break

                # Check Fully Mitigated: reached or pierced bottom but closed above invalidation threshold
                elif bar.low <= order_block.zone_bottom:
                    if current_status != OrderBlockStatus.FULLY_MITIGATED:
                        current_status = OrderBlockStatus.FULLY_MITIGATED
                        if full_mitigation_time is None:
                            full_mitigation_time = bar.close_time
                        if touch_time is None:
                            touch_time = bar.close_time
                        ev = OrderBlockLifecycleEvent(
                            event_id=generate_lifecycle_event_id(
                                order_block.order_block_id, bar.open_time, current_status
                            ),
                            order_block_id=order_block.order_block_id,
                            event_time=bar.close_time,
                            bar_open_time=bar.open_time,
                            bar_close_time=bar.close_time,
                            status_before=status_before_bar,
                            status_after=current_status,
                            trigger_price=bar.low,
                            penetration_depth=max_pen_depth,
                            penetration_percentage=max_pen_pct,
                            description=f"Fully Mitigated: Low {bar.low} reached bottom {order_block.zone_bottom} (closed {bar.close})",
                        )
                        events.append(ev)

                # Check Partially Mitigated: reached midpoint (50%) but stayed above bottom
                elif bar.low <= order_block.midpoint_50:
                    if current_status not in (
                        OrderBlockStatus.PARTIALLY_MITIGATED,
                        OrderBlockStatus.FULLY_MITIGATED,
                    ):
                        current_status = OrderBlockStatus.PARTIALLY_MITIGATED
                        if touch_time is None:
                            touch_time = bar.close_time
                        ev = OrderBlockLifecycleEvent(
                            event_id=generate_lifecycle_event_id(
                                order_block.order_block_id, bar.open_time, current_status
                            ),
                            order_block_id=order_block.order_block_id,
                            event_time=bar.close_time,
                            bar_open_time=bar.open_time,
                            bar_close_time=bar.close_time,
                            status_before=status_before_bar,
                            status_after=current_status,
                            trigger_price=bar.low,
                            penetration_depth=max_pen_depth,
                            penetration_percentage=max_pen_pct,
                            description=f"Partially Mitigated: Low {bar.low} reached midpoint {order_block.midpoint_50}",
                        )
                        events.append(ev)

                # Check Touched: penetrated into zone but did not reach midpoint
                else:
                    if current_status == OrderBlockStatus.UNMITIGATED:
                        current_status = OrderBlockStatus.TOUCHED
                        touch_time = bar.close_time
                        ev = OrderBlockLifecycleEvent(
                            event_id=generate_lifecycle_event_id(
                                order_block.order_block_id, bar.open_time, current_status
                            ),
                            order_block_id=order_block.order_block_id,
                            event_time=bar.close_time,
                            bar_open_time=bar.open_time,
                            bar_close_time=bar.close_time,
                            status_before=status_before_bar,
                            status_after=current_status,
                            trigger_price=bar.low,
                            penetration_depth=max_pen_depth,
                            penetration_percentage=max_pen_pct,
                            description=f"Touched: Low {bar.low} entered zone <= {order_block.zone_top}",
                        )
                        events.append(ev)

        else:
            # Bearish Supply Zone: [zone_bottom, zone_top]
            # Tests occur when price rises into the zone (bar.high >= zone_bottom)
            tested = bar.high >= order_block.zone_bottom

            if tested:
                pen_depth = max(Decimal("0"), bar.high - order_block.zone_bottom)
                pen_pct = (pen_depth / zone_h) * Decimal("100")
                if pen_depth > max_pen_depth:
                    max_pen_depth = pen_depth
                if pen_pct > max_pen_pct:
                    max_pen_pct = pen_pct

                # Check Invalidation first: body close above top + threshold
                if bar.close > (order_block.zone_top + inv_threshold):
                    current_status = OrderBlockStatus.INVALIDATED
                    invalidation_time = bar.close_time
                    ev = OrderBlockLifecycleEvent(
                        event_id=generate_lifecycle_event_id(
                            order_block.order_block_id, bar.open_time, current_status
                        ),
                        order_block_id=order_block.order_block_id,
                        event_time=bar.close_time,
                        bar_open_time=bar.open_time,
                        bar_close_time=bar.close_time,
                        status_before=status_before_bar,
                        status_after=current_status,
                        trigger_price=bar.close,
                        penetration_depth=max_pen_depth,
                        penetration_percentage=max_pen_pct,
                        description=f"Invalidated: Bullish body close {bar.close} > {order_block.zone_top + inv_threshold}",
                    )
                    events.append(ev)
                    break

                # Check Fully Mitigated: reached top but closed below invalidation threshold
                elif bar.high >= order_block.zone_top:
                    if current_status != OrderBlockStatus.FULLY_MITIGATED:
                        current_status = OrderBlockStatus.FULLY_MITIGATED
                        if full_mitigation_time is None:
                            full_mitigation_time = bar.close_time
                        if touch_time is None:
                            touch_time = bar.close_time
                        ev = OrderBlockLifecycleEvent(
                            event_id=generate_lifecycle_event_id(
                                order_block.order_block_id, bar.open_time, current_status
                            ),
                            order_block_id=order_block.order_block_id,
                            event_time=bar.close_time,
                            bar_open_time=bar.open_time,
                            bar_close_time=bar.close_time,
                            status_before=status_before_bar,
                            status_after=current_status,
                            trigger_price=bar.high,
                            penetration_depth=max_pen_depth,
                            penetration_percentage=max_pen_pct,
                            description=f"Fully Mitigated: High {bar.high} reached top {order_block.zone_top} (closed {bar.close})",
                        )
                        events.append(ev)

                # Check Partially Mitigated: reached midpoint (50%)
                elif bar.high >= order_block.midpoint_50:
                    if current_status not in (
                        OrderBlockStatus.PARTIALLY_MITIGATED,
                        OrderBlockStatus.FULLY_MITIGATED,
                    ):
                        current_status = OrderBlockStatus.PARTIALLY_MITIGATED
                        if touch_time is None:
                            touch_time = bar.close_time
                        ev = OrderBlockLifecycleEvent(
                            event_id=generate_lifecycle_event_id(
                                order_block.order_block_id, bar.open_time, current_status
                            ),
                            order_block_id=order_block.order_block_id,
                            event_time=bar.close_time,
                            bar_open_time=bar.open_time,
                            bar_close_time=bar.close_time,
                            status_before=status_before_bar,
                            status_after=current_status,
                            trigger_price=bar.high,
                            penetration_depth=max_pen_depth,
                            penetration_percentage=max_pen_pct,
                            description=f"Partially Mitigated: High {bar.high} reached midpoint {order_block.midpoint_50}",
                        )
                        events.append(ev)

                # Check Touched
                else:
                    if current_status == OrderBlockStatus.UNMITIGATED:
                        current_status = OrderBlockStatus.TOUCHED
                        touch_time = bar.close_time
                        ev = OrderBlockLifecycleEvent(
                            event_id=generate_lifecycle_event_id(
                                order_block.order_block_id, bar.open_time, current_status
                            ),
                            order_block_id=order_block.order_block_id,
                            event_time=bar.close_time,
                            bar_open_time=bar.open_time,
                            bar_close_time=bar.close_time,
                            status_before=status_before_bar,
                            status_after=current_status,
                            trigger_price=bar.high,
                            penetration_depth=max_pen_depth,
                            penetration_percentage=max_pen_pct,
                            description=f"Touched: High {bar.high} entered zone >= {order_block.zone_bottom}",
                        )
                        events.append(ev)

    return OrderBlockRecord(
        order_block_id=order_block.order_block_id,
        instrument_id=order_block.instrument_id,
        source_id=order_block.source_id,
        timeframe=order_block.timeframe,
        price_type=order_block.price_type,
        direction=order_block.direction,
        zone_mode=order_block.zone_mode,
        zone_bottom=order_block.zone_bottom,
        zone_top=order_block.zone_top,
        midpoint_50=order_block.midpoint_50,
        origin_candle_open_time=order_block.origin_candle_open_time,
        origin_candle_close_time=order_block.origin_candle_close_time,
        origin_candle_id=order_block.origin_candle_id,
        confirmation_time=order_block.confirmation_time,
        displacement_event_id=order_block.displacement_event_id,
        structure_break_event_id=order_block.structure_break_event_id,
        mss_confirmation_id=order_block.mss_confirmation_id,
        associated_fvg_id=order_block.associated_fvg_id,
        status=current_status,
        penetration_depth=max_pen_depth,
        max_penetration_percentage=max_pen_pct,
        touch_time=touch_time,
        invalidation_time=invalidation_time,
        full_mitigation_time=full_mitigation_time,
        lifecycle_events=tuple(events),
        availability_confidence=order_block.availability_confidence,
        flags=order_block.flags,
    )


# ── Query & Filtering Helpers ────────────────────────────────────────────────
def filter_order_blocks_as_of(
    order_blocks: Sequence[OrderBlockRecord],
    as_of_utc: datetime,
) -> list[OrderBlockRecord]:
    """Filter and reconstruct Order Blocks to reflect their exact point-in-time state as of `as_of_utc`."""
    as_of_norm = ensure_utc(as_of_utc)
    reconstructed_obs: list[OrderBlockRecord] = []
    for ob in order_blocks:
        if not ob.is_available_at(as_of_norm):
            continue
        valid_events = tuple(ev for ev in ob.lifecycle_events if ev.event_time <= as_of_norm)
        if valid_events:
            status_at_as_of = valid_events[-1].status_after
            max_pen = valid_events[-1].penetration_depth
            max_pct = valid_events[-1].penetration_percentage
            touch_t = ob.touch_time if (ob.touch_time and ob.touch_time <= as_of_norm) else None
            inval_t = (
                ob.invalidation_time
                if (ob.invalidation_time and ob.invalidation_time <= as_of_norm)
                else None
            )
            full_mit_t = (
                ob.full_mitigation_time
                if (ob.full_mitigation_time and ob.full_mitigation_time <= as_of_norm)
                else None
            )
        else:
            if (ob.invalidation_time is not None and ob.invalidation_time > as_of_norm) or (
                ob.full_mitigation_time is not None and ob.full_mitigation_time > as_of_norm
            ):
                status_at_as_of = (
                    OrderBlockStatus.CONFIRMED
                    if ob.status == OrderBlockStatus.CONFIRMED
                    else OrderBlockStatus.UNMITIGATED
                )
            elif ob.status in (OrderBlockStatus.INVALIDATED, OrderBlockStatus.FULLY_MITIGATED):
                status_at_as_of = ob.status
            else:
                status_at_as_of = (
                    OrderBlockStatus.CONFIRMED
                    if ob.status == OrderBlockStatus.CONFIRMED
                    else OrderBlockStatus.UNMITIGATED
                )
            max_pen = Decimal("0")
            max_pct = Decimal("0")
            touch_t = None
            inval_t = (
                ob.invalidation_time
                if (ob.invalidation_time and ob.invalidation_time <= as_of_norm)
                else None
            )
            full_mit_t = (
                ob.full_mitigation_time
                if (ob.full_mitigation_time and ob.full_mitigation_time <= as_of_norm)
                else None
            )

        reconstructed_obs.append(
            OrderBlockRecord(
                order_block_id=ob.order_block_id,
                instrument_id=ob.instrument_id,
                source_id=ob.source_id,
                timeframe=ob.timeframe,
                price_type=ob.price_type,
                direction=ob.direction,
                zone_mode=ob.zone_mode,
                zone_bottom=ob.zone_bottom,
                zone_top=ob.zone_top,
                midpoint_50=ob.midpoint_50,
                origin_candle_open_time=ob.origin_candle_open_time,
                origin_candle_close_time=ob.origin_candle_close_time,
                origin_candle_id=ob.origin_candle_id,
                confirmation_time=ob.confirmation_time,
                displacement_event_id=ob.displacement_event_id,
                structure_break_event_id=ob.structure_break_event_id,
                mss_confirmation_id=ob.mss_confirmation_id,
                associated_fvg_id=ob.associated_fvg_id,
                status=status_at_as_of,
                penetration_depth=max_pen,
                max_penetration_percentage=max_pct,
                touch_time=touch_t,
                invalidation_time=inval_t,
                full_mitigation_time=full_mit_t,
                lifecycle_events=valid_events,
                availability_confidence=ob.availability_confidence,
                flags=ob.flags,
            )
        )
    return reconstructed_obs


def filter_active_order_blocks(
    order_blocks: Sequence[OrderBlockRecord],
) -> list[OrderBlockRecord]:
    """Filter Order Blocks for those currently active (not invalidated or fully mitigated)."""
    return [ob for ob in order_blocks if ob.is_active]


def filter_unmitigated_order_blocks(
    order_blocks: Sequence[OrderBlockRecord],
) -> list[OrderBlockRecord]:
    """Filter Order Blocks that have not been tested at all (UNMITIGATED)."""
    return [ob for ob in order_blocks if ob.status == OrderBlockStatus.UNMITIGATED]


def summarize_order_blocks(
    order_blocks: Sequence[OrderBlockRecord],
) -> dict[str, int]:
    """Compile aggregated count metrics for Order Blocks."""
    bullish = sum(1 for ob in order_blocks if ob.is_bullish)
    bearish = sum(1 for ob in order_blocks if ob.is_bearish)
    unmitigated = sum(1 for ob in order_blocks if ob.status == OrderBlockStatus.UNMITIGATED)
    touched = sum(1 for ob in order_blocks if ob.status == OrderBlockStatus.TOUCHED)
    partially_mitigated = sum(
        1 for ob in order_blocks if ob.status == OrderBlockStatus.PARTIALLY_MITIGATED
    )
    fully_mitigated = sum(
        1 for ob in order_blocks if ob.status == OrderBlockStatus.FULLY_MITIGATED
    )
    invalidated = sum(1 for ob in order_blocks if ob.status == OrderBlockStatus.INVALIDATED)

    return {
        "total_order_blocks": len(order_blocks),
        "bullish_order_blocks": bullish,
        "bearish_order_blocks": bearish,
        "unmitigated": unmitigated,
        "touched": touched,
        "partially_mitigated": partially_mitigated,
        "fully_mitigated": fully_mitigated,
        "invalidated": invalidated,
    }
