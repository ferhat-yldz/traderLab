"""Point-in-Time Safe ICT Setup Candidate & Confluence Engine.

Financial Theory & Architecture:
    1. **Strict Pure Domain Logic** — Operates exclusively on immutable domain models
       and candle sequences. Zero dependencies on database, filesystem, or UI.
    2. **Point-in-Time Correctness** — A setup candidate is only available at
       `setup_available_time = max(structure, displacement, zone, sweep)`.
       Future prices, future retests, future invalidations, or future confluences
       can NEVER leak into historical decision snapshots (`as_of_utc`).
    3. **Deterministic Causality** — Reversal setups strictly require confirmed Liquidity Sweeps
       preceding a Change of Character (CHoCH) with confirmed Market Structure Shift (MSS).
       Continuation setups strictly require confirmed Break of Structure (BOS) with causally
       linked displacement and an eligible, direction-aligned price zone (FVG or Order Block).
    4. **Separation of Concerns** — This engine identifies and analyzes candidate setups only.
       It generates NO orders, executes NO trades, calculates NO position sizing,
       and assumes NO future win probability.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass, field
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
    DisplacementEvent,
)
from traderlab.domain.market_structure.fvg import (
    FvgZone,
)
from traderlab.domain.market_structure.fvg_respect import (
    FvgRespectSummary,
)
from traderlab.domain.market_structure.liquidity_sweep import (
    LiquiditySide,
    LiquiditySweepEvent,
    LiquiditySweepStatus,
)
from traderlab.domain.market_structure.market_structure import (
    StructureBreakEvent,
    StructureBreakType,
    StructureDirection,
)
from traderlab.domain.market_structure.mss_confirmation import (
    MssConfirmationRecord,
    MssStatus,
)
from traderlab.domain.market_structure.order_block import (
    OrderBlockRecord,
    filter_order_blocks_as_of,
)
from traderlab.domain.market_structure.ote import (
    OTEZoneRecord,
)
from traderlab.domain.market_structure.premium_discount import (
    DealingRangeRecord,
)


# ── Exceptions ───────────────────────────────────────────────────────────────
class IctSetupError(TraderLabError):
    """Base exception for ICT Setup Candidate domain errors."""

    def __init__(self, message: str, *, code: str = "ICT_SETUP_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidIctSetupParameterError(IctSetupError):
    """Raised when setup configuration parameters are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_ICT_SETUP_PARAMETER")


class IctSetupDataError(IctSetupError):
    """Raised when candle sequences or structural event data are malformed or incompatible."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="ICT_SETUP_DATA_ERROR")


# ── Enumerations ────────────────────────────────────────────────────────────
class IctSetupType(enum.Enum):
    """Operational classification of the ICT trading setup."""

    REVERSAL = "REVERSAL"
    """Market reversal setup: Sweep + CHoCH + MSS + Displacement + Entry Zone."""

    CONTINUATION = "CONTINUATION"
    """Trend continuation setup: Trend + BOS + Displacement + Entry Zone."""


class IctSetupDirection(enum.Enum):
    """Trading bias orientation of the setup."""

    BULLISH = "BULLISH"
    """Bullish setup expecting upward market expansion."""

    BEARISH = "BEARISH"
    """Bearish setup expecting downward market expansion."""


class IctSetupStatus(enum.Enum):
    """Lifecycle state of an ICT Setup Candidate."""

    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    """Structure break detected but required displacement or zone not yet fully confirmed."""

    STRUCTURE_CONFIRMED = "STRUCTURE_CONFIRMED"
    """Structural break and displacement confirmed; zone search in progress."""

    ZONE_IDENTIFIED = "ZONE_IDENTIFIED"
    """Eligible entry zone (FVG or OB) identified and available; awaiting price test."""

    ZONE_TESTED = "ZONE_TESTED"
    """A subsequent closed candle has entered and tested the identified zone."""

    INVALIDATED = "INVALIDATED"
    """Price closed beyond the zone boundary, terminating setup validity."""

    EXPIRED = "EXPIRED"
    """Maximum testing window elapsed without price reaching the identified zone."""


class IctConfluenceType(enum.Enum):
    """Category of supporting technical confluence evidence."""

    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"
    """Liquidity sweep of a key swing level."""

    BOS = "BOS"
    """Break of Structure confirming trend continuation."""

    MSS = "MSS"
    """Market Structure Shift confirmed with displacement."""

    DISPLACEMENT = "DISPLACEMENT"
    """Displacement candle indicating institutional orderflow."""

    FVG = "FVG"
    """Fair Value Gap providing an active imbalance zone."""

    IFVG = "IFVG"
    """Inversion Fair Value Gap acting in inverted role."""

    ORDER_BLOCK = "ORDER_BLOCK"
    """Order Block origin candle supply/demand zone."""

    PREMIUM_DISCOUNT = "PREMIUM_DISCOUNT"
    """Price is in Discount (< 50%) for Bullish or Premium (> 50%) for Bearish."""

    OTE = "OTE"
    """Optimal Trade Entry (62% - 79% Fibonacci retracement) zone overlap."""

    FVG_RESPECT = "FVG_RESPECT"
    """Underlying FVG has historical confirmed respects."""

    MULTIPLE_RETEST = "MULTIPLE_RETEST"
    """Underlying FVG has withstood multiple retests."""

    HIGHER_TIMEFRAME_CONTEXT = "HIGHER_TIMEFRAME_CONTEXT"
    """Higher timeframe directional alignment."""


# ── Deterministic Identifier Generator ───────────────────────────────────────
def generate_ict_setup_id(
    instrument_id: InstrumentId | str,
    source_id: DataSourceId | str,
    price_type: PriceType | str,
    timeframe: Timeframe | str,
    setup_type: IctSetupType | str,
    direction: IctSetupDirection | str,
    structure_event_id: str,
    anchor_timestamp: datetime,
) -> str:
    """Generate a collision-resistant deterministic identifier for an ICT setup candidate."""
    inst_str = str(instrument_id)
    src_str = str(source_id)
    pt_str = price_type.value if isinstance(price_type, PriceType) else str(price_type)
    tf_str = timeframe.name if isinstance(timeframe, Timeframe) else str(timeframe)
    st_str = setup_type.value if isinstance(setup_type, IctSetupType) else str(setup_type)
    dir_str = direction.value if isinstance(direction, IctSetupDirection) else str(direction)
    t_str = ensure_utc(anchor_timestamp).strftime("%Y%m%d%H%M%S")
    return (
        f"{inst_str}:{src_str}:{pt_str}:{tf_str}:{st_str}:{dir_str}:{structure_event_id}:{t_str}"
    )


# ── Domain Models ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class IctConfluenceEvidence:
    """An immutable record representing a concrete piece of technical confluence."""

    evidence_type: IctConfluenceType
    source_event_id: str
    source_timeframe: Timeframe
    direction: IctSetupDirection | None
    event_time: datetime
    available_time: datetime
    price_bottom: Decimal | None = None
    price_top: Decimal | None = None
    is_satisfied: bool = True
    explanation: str = ""


@dataclass(frozen=True, slots=True)
class IctSetupLifecycleEvent:
    """A point-in-time state transition record for an ICT Setup Candidate."""

    event_id: str
    setup_id: str
    bar_open_time: datetime
    bar_close_time: datetime
    status_before: IctSetupStatus
    status_after: IctSetupStatus
    trigger_price: Decimal | None
    reason: str
    event_time_utc: datetime


@dataclass(frozen=True, slots=True)
class IctSetupConfig:
    """Configuration parameters for deterministic ICT Setup detection."""

    max_bos_displacement_bars: int = 5
    """Maximum bar distance between BOS detection and causally linked displacement."""

    max_zone_distance_bars: int = 20
    """Maximum bar lookback for linking an eligible FVG or OB to a structure break."""

    max_testing_bars: int = 30
    """Maximum number of subsequent bars allowed to test the zone before setup expires."""

    require_fvg_for_reversal: bool = True
    """Whether MSS FVG requirement must be enforced for reversal setups."""

    require_displacement_for_continuation: bool = True
    """Whether continuation setups require a verified displacement event."""

    require_ote: bool = False
    """If True, only candidates with positive OTE zone overlap are accepted."""

    require_premium_discount: bool = False
    """If True, enforces Discount for Bullish and Premium for Bearish."""

    require_order_block: bool = False
    """If True, requires an Order Block zone."""

    require_fvg_respect: bool = False
    """If True, requires the underlying FVG to have >= 1 confirmed respect."""

    min_confluence_count: int = 0
    """Minimum number of optional confluences required to qualify a candidate."""

    prefer_order_block_over_fvg: bool = False
    """If both OB and FVG exist, select OB as primary zone if True, FVG if False."""

    policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED
    """Point-in-Time evaluation policy."""

    strict_pit: bool = False
    """If True, strictly enforces VERIFIED availability on all inputs."""

    tick_size: Decimal = Decimal("0.01")
    """Instrument tick size in price units."""

    def __post_init__(self) -> None:
        if self.max_bos_displacement_bars < 1:
            raise InvalidIctSetupParameterError("max_bos_displacement_bars must be >= 1")
        if self.max_zone_distance_bars < 1:
            raise InvalidIctSetupParameterError("max_zone_distance_bars must be >= 1")
        if self.max_testing_bars < 1:
            raise InvalidIctSetupParameterError("max_testing_bars must be >= 1")
        if self.min_confluence_count < 0:
            raise InvalidIctSetupParameterError("min_confluence_count cannot be negative")
        if self.tick_size <= Decimal("0"):
            raise InvalidIctSetupParameterError("tick_size must be strictly positive Decimal")


@dataclass(frozen=True, slots=True)
class IctSetupCandidate:
    """Immutable domain representation of an ICT Setup Candidate.

    Guarantees:
    - **Point-in-Time Integrity**: `setup_available_time == max(all required component times)`.
    - **Strict Causality**: Fully traceable IDs to underlying structural events.
    - **Zero Lookahead**: Candidate cannot exist in any snapshot `as_of < setup_available_time`.
    """

    setup_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    price_type: PriceType
    setup_type: IctSetupType
    direction: IctSetupDirection
    status: IctSetupStatus

    structure_event_id: str
    structure_confirmation_time: datetime
    setup_available_time: datetime
    last_evaluation_time: datetime

    sweep_event_id: str | None = None
    mss_confirmation_id: str | None = None
    displacement_event_id: str | None = None
    fvg_id: str | None = None
    order_block_id: str | None = None
    ote_id: str | None = None
    d1_session_mode: str | None = None

    selected_zone_bottom: Decimal | None = None
    selected_zone_top: Decimal | None = None
    selected_zone_midpoint: Decimal | None = None
    zone_available_time: datetime | None = None

    availability_confidence: AvailabilityConfidence = AvailabilityConfidence.ESTIMATED
    confluences: tuple[IctConfluenceEvidence, ...] = field(default_factory=tuple)
    rejection_reasons: tuple[str, ...] = field(default_factory=tuple)
    lifecycle_events: tuple[IctSetupLifecycleEvent, ...] = field(default_factory=tuple)

    test_bar_open_time: datetime | None = None
    test_detection_time: datetime | None = None
    test_price: Decimal | None = None
    invalidation_time: datetime | None = None
    confluence_score: int = 0

    def __post_init__(self) -> None:
        if (
            self.selected_zone_bottom is not None
            and self.selected_zone_top is not None
            and self.selected_zone_bottom >= self.selected_zone_top
        ):
            raise IctSetupError(
                f"Invalid zone: bottom ({self.selected_zone_bottom}) must be < top ({self.selected_zone_top})"
            )
        if self.setup_available_time < self.structure_confirmation_time:
            raise IctSetupError(
                f"PIT violation: setup_available_time ({self.setup_available_time}) "
                f"cannot precede structure_confirmation_time ({self.structure_confirmation_time})"
            )

    @property
    def is_reversal(self) -> bool:
        return self.setup_type == IctSetupType.REVERSAL

    @property
    def is_continuation(self) -> bool:
        return self.setup_type == IctSetupType.CONTINUATION

    @property
    def is_bullish(self) -> bool:
        return self.direction == IctSetupDirection.BULLISH

    @property
    def is_bearish(self) -> bool:
        return self.direction == IctSetupDirection.BEARISH

    @property
    def is_active(self) -> bool:
        return self.status in (IctSetupStatus.ZONE_IDENTIFIED, IctSetupStatus.ZONE_TESTED)

    @property
    def is_tested(self) -> bool:
        return self.status == IctSetupStatus.ZONE_TESTED

    @property
    def is_invalidated(self) -> bool:
        return self.status == IctSetupStatus.INVALIDATED

    @property
    def is_expired(self) -> bool:
        return self.status == IctSetupStatus.EXPIRED


@dataclass(frozen=True, slots=True)
class IctSetupSummary:
    """Aggregated analytical summary of detected ICT Setup Candidates."""

    total_candidates: int
    reversal_count: int
    continuation_count: int
    bullish_count: int
    bearish_count: int
    structure_confirmed_count: int
    zone_identified_count: int
    zone_tested_count: int
    invalidated_count: int
    expired_count: int
    avg_confluence_score: Decimal
    candidates: tuple[IctSetupCandidate, ...] = field(default_factory=tuple)


# ── Geometric Helper Functions ──────────────────────────────────────────────
def compute_zone_overlap(
    bottom_a: Decimal,
    top_a: Decimal,
    bottom_b: Decimal,
    top_b: Decimal,
) -> tuple[Decimal, Decimal, Decimal, bool]:
    """Calculate geometric intersection between two price zones.

    Returns:
        (intersection_bottom, intersection_top, intersection_width, is_touch_only)
    """
    inter_bottom = max(bottom_a, bottom_b)
    inter_top = min(top_a, top_b)
    if inter_bottom < inter_top:
        width = inter_top - inter_bottom
        return (inter_bottom, inter_top, width, False)
    elif inter_bottom == inter_top:
        return (inter_bottom, inter_top, Decimal("0"), True)
    else:
        return (Decimal("0"), Decimal("0"), Decimal("0"), False)


# ── Core Domain Functions ───────────────────────────────────────────────────
def _validate_candles(candles: Sequence[Candle], strict_pit: bool, allow_synthetic: bool) -> None:
    """Validate homogeneous, closed, chronological candle sequence."""
    if not candles:
        return

    first_inst = candles[0].instrument_id
    first_src = candles[0].source_id
    first_tf = candles[0].timeframe
    first_pt = candles[0].price_type

    prev_close: datetime | None = None
    for idx, c in enumerate(candles):
        if not c.is_closed:
            raise IctSetupDataError(f"Partial unclosed candle at index {idx}: {c.open_time}")
        if c.is_synthetic and not allow_synthetic:
            raise IctSetupDataError(f"Synthetic candle rejected at index {idx}: {c.open_time}")
        if strict_pit and c.availability_confidence != AvailabilityConfidence.VERIFIED:
            raise IctSetupDataError(
                f"STRICT_PIT violation: unverified bar at index {idx}: {c.open_time}"
            )
        if (
            c.instrument_id != first_inst
            or c.source_id != first_src
            or c.timeframe != first_tf
            or c.price_type != first_pt
        ):
            raise IctSetupDataError(f"Heterogeneous candle sequence detected at index {idx}")
        if prev_close is not None and c.open_time < prev_close:
            raise IctSetupDataError(f"Non-chronological candles at index {idx}: {c.open_time}")
        prev_close = c.close_time


def evaluate_ict_setup_lifecycle(
    candidate: IctSetupCandidate,
    subsequent_candles: Sequence[Candle],
    config: IctSetupConfig,
    as_of_utc: datetime | None = None,
) -> IctSetupCandidate:
    """Evaluate price interaction with the identified entry zone across subsequent candles."""
    if candidate.selected_zone_bottom is None or candidate.selected_zone_top is None:
        return candidate

    z_bot = candidate.selected_zone_bottom
    z_top = candidate.selected_zone_top
    is_bull = candidate.is_bullish

    current_status: IctSetupStatus = candidate.status
    lifecycle_events = list(candidate.lifecycle_events)
    test_open_time = candidate.test_bar_open_time
    test_detect_time = candidate.test_detection_time
    test_price = candidate.test_price
    inval_time = candidate.invalidation_time
    bars_elapsed = 0

    for c in subsequent_candles:
        if as_of_utc is not None and c.close_time > as_of_utc:
            break

        # Only evaluate bars strictly starting at or after zone availability
        if candidate.zone_available_time and c.open_time < candidate.zone_available_time:
            continue

        bars_elapsed += 1

        # Check Touch / Test
        if is_bull:
            touched = (c.low <= z_top) and (c.high >= z_bot)
            touch_p = c.low
            invalidated = c.close < z_bot
        else:
            touched = (c.high >= z_bot) and (c.low <= z_top)
            touch_p = c.high
            invalidated = c.close > z_top

        # State transition: ZONE_IDENTIFIED -> ZONE_TESTED
        status_before: IctSetupStatus = current_status
        if touched and current_status == IctSetupStatus.ZONE_IDENTIFIED:
            current_status = IctSetupStatus.ZONE_TESTED
            test_open_time = c.open_time
            test_detect_time = c.close_time
            test_price = touch_p

            ev = IctSetupLifecycleEvent(
                event_id=f"{candidate.setup_id}:TEST:{c.open_time.strftime('%Y%m%d%H%M%S')}",
                setup_id=candidate.setup_id,
                bar_open_time=c.open_time,
                bar_close_time=c.close_time,
                status_before=status_before,
                status_after=current_status,
                trigger_price=touch_p,
                reason="Price touched identified zone boundaries",
                event_time_utc=c.close_time,
            )
            lifecycle_events.append(ev)

        # State transition: -> INVALIDATED (Body close beyond opposite zone boundary)
        if invalidated:
            status_before = current_status
            current_status = IctSetupStatus.INVALIDATED
            inval_time = c.close_time

            ev = IctSetupLifecycleEvent(
                event_id=f"{candidate.setup_id}:INVAL:{c.open_time.strftime('%Y%m%d%H%M%S')}",
                setup_id=candidate.setup_id,
                bar_open_time=c.open_time,
                bar_close_time=c.close_time,
                status_before=status_before,
                status_after=current_status,
                trigger_price=c.close,
                reason=f"Price closed beyond zone opposite boundary ({c.close})",
                event_time_utc=c.close_time,
            )
            lifecycle_events.append(ev)
            break  # Terminal state

        # State transition: -> EXPIRED
        if (
            current_status == IctSetupStatus.ZONE_IDENTIFIED
            and bars_elapsed >= config.max_testing_bars
        ):
            status_before = current_status
            current_status = IctSetupStatus.EXPIRED

            ev = IctSetupLifecycleEvent(
                event_id=f"{candidate.setup_id}:EXPIRE:{c.open_time.strftime('%Y%m%d%H%M%S')}",
                setup_id=candidate.setup_id,
                bar_open_time=c.open_time,
                bar_close_time=c.close_time,
                status_before=status_before,
                status_after=current_status,
                trigger_price=None,
                reason=f"Zone not tested within {config.max_testing_bars} bars",
                event_time_utc=c.close_time,
            )
            lifecycle_events.append(ev)
            break  # Terminal state

    return IctSetupCandidate(
        setup_id=candidate.setup_id,
        instrument_id=candidate.instrument_id,
        source_id=candidate.source_id,
        timeframe=candidate.timeframe,
        price_type=candidate.price_type,
        setup_type=candidate.setup_type,
        direction=candidate.direction,
        status=current_status,
        structure_event_id=candidate.structure_event_id,
        structure_confirmation_time=candidate.structure_confirmation_time,
        setup_available_time=candidate.setup_available_time,
        last_evaluation_time=as_of_utc
        or (
            subsequent_candles[-1].close_time
            if subsequent_candles
            else candidate.setup_available_time
        ),
        sweep_event_id=candidate.sweep_event_id,
        mss_confirmation_id=candidate.mss_confirmation_id,
        displacement_event_id=candidate.displacement_event_id,
        fvg_id=candidate.fvg_id,
        order_block_id=candidate.order_block_id,
        ote_id=candidate.ote_id,
        d1_session_mode=candidate.d1_session_mode,
        selected_zone_bottom=candidate.selected_zone_bottom,
        selected_zone_top=candidate.selected_zone_top,
        selected_zone_midpoint=candidate.selected_zone_midpoint,
        zone_available_time=candidate.zone_available_time,
        availability_confidence=candidate.availability_confidence,
        confluences=candidate.confluences,
        rejection_reasons=candidate.rejection_reasons,
        lifecycle_events=tuple(lifecycle_events),
        test_bar_open_time=test_open_time,
        test_detection_time=test_detect_time,
        test_price=test_price,
        invalidation_time=inval_time,
        confluence_score=candidate.confluence_score,
    )


def detect_ict_setup_candidates(
    candles: Sequence[Candle],
    *,
    sweeps: Sequence[LiquiditySweepEvent] = (),
    structure_breaks: Sequence[StructureBreakEvent] = (),
    mss_records: Sequence[MssConfirmationRecord] = (),
    displacements: Sequence[DisplacementEvent] = (),
    fvg_zones: Sequence[FvgZone] = (),
    order_blocks: Sequence[OrderBlockRecord] = (),
    ote_zones: Sequence[OTEZoneRecord] = (),
    fvg_respect_summaries: Sequence[FvgRespectSummary] = (),
    dealing_ranges: Sequence[DealingRangeRecord] = (),
    config: IctSetupConfig | None = None,
    as_of_utc: datetime | None = None,
    d1_session_mode: str | None = None,
) -> tuple[IctSetupCandidate, ...]:
    """Detect deterministically qualified ICT Reversal and Continuation setup candidates."""
    cfg = config or IctSetupConfig()
    _validate_candles(candles, cfg.strict_pit, False)

    if not candles:
        return ()

    inst = candles[0].instrument_id
    src = candles[0].source_id
    tf = candles[0].timeframe
    pt = candles[0].price_type

    cutoff = ensure_utc(as_of_utc) if as_of_utc is not None else None
    candidates: list[IctSetupCandidate] = []

    # Map lookups
    disp_map = {d.event_id: d for d in displacements}
    fvg_map = {f.id: f for f in fvg_zones}
    respect_map = {r.fvg_id: r for r in fvg_respect_summaries}

    # Weakest-link confidence calculation helper
    def resolve_confidence(conf_list: list[AvailabilityConfidence]) -> AvailabilityConfidence:
        if not conf_list:
            return AvailabilityConfidence.ESTIMATED
        if any(c == AvailabilityConfidence.UNKNOWN for c in conf_list):
            return AvailabilityConfidence.UNKNOWN
        if any(c == AvailabilityConfidence.ESTIMATED for c in conf_list):
            return AvailabilityConfidence.ESTIMATED
        return AvailabilityConfidence.VERIFIED

    # =========================================================================
    # 1. ICT REVERSAL SETUP DETECTION
    # =========================================================================
    # Reversal requires:
    # 1. Confirmed MSS record (which ensures CHoCH + displacement + FVG confirmation)
    # 2. Confirmed preceding Liquidity Sweep (Sell-Side for Bullish, Buy-Side for Bearish)
    # 3. sweep.detection_time < choch.detection_time (no same-candle ambiguous order)
    # 4. Direction-aligned eligible entry zone (FVG or Order Block)
    for mss in mss_records:
        if mss.status != MssStatus.MSS_CONFIRMED or mss.final_confirmation_time is None:
            continue

        is_bullish = mss.direction == StructureDirection.BULLISH
        setup_dir = IctSetupDirection.BULLISH if is_bullish else IctSetupDirection.BEARISH

        # Link source CHoCH
        choch = next(
            (sb for sb in structure_breaks if sb.event_id == mss.source_choch_event_id), None
        )
        if (
            choch is None
            or choch.break_type != StructureBreakType.CHOCH
            or choch.confirmation_status != "CONFIRMED"
        ):
            continue

        # Link preceding Liquidity Sweep
        # Must be SWEEP (not TOUCH, BREACH, AMBIGUOUS)
        required_side = LiquiditySide.SELL_SIDE if is_bullish else LiquiditySide.BUY_SIDE
        eligible_sweeps = [
            s
            for s in sweeps
            if s.status == LiquiditySweepStatus.SWEEP
            and s.liquidity_side == required_side
            and s.detection_time
            < choch.detection_time  # Strict causality: sweep strictly precedes CHoCH
        ]
        if not eligible_sweeps:
            continue  # Reversal strictly requires confirmed liquidity sweep

        # Pick the most recent preceding sweep
        sweep = max(eligible_sweeps, key=lambda s: s.detection_time)

        # Select primary zone: FVG or Order Block
        linked_fvg: FvgZone | None = None
        if mss.associated_fvg_id and mss.associated_fvg_id in fvg_map:
            linked_fvg = fvg_map[mss.associated_fvg_id]
        elif mss.displacement_time:
            linked_fvg = next(
                (
                    f
                    for f in fvg_zones
                    if f.candle_2_open == mss.displacement_time
                    and f.direction.value == setup_dir.value
                ),
                None,
            )

        eval_t = max(choch.detection_time, mss.final_confirmation_time)
        matching_obs = [
            o
            for o in order_blocks
            if (
                o.mss_confirmation_id == mss.record_id
                or o.structure_break_event_id == choch.event_id
            )
            and o.direction.value == setup_dir.value
            and o.instrument_id == inst
            and o.source_id == src
            and o.price_type == pt
            and o.timeframe == tf
            and o.confirmation_time <= eval_t
        ]
        linked_ob: OrderBlockRecord | None = None
        for m_ob in matching_obs:
            reconstructed_list = filter_order_blocks_as_of([m_ob], eval_t)
            if reconstructed_list and reconstructed_list[0].is_active:
                linked_ob = reconstructed_list[0]
                break

        # Enforce zone selection
        if cfg.prefer_order_block_over_fvg and linked_ob:
            z_bot = linked_ob.zone_bottom
            z_top = linked_ob.zone_top
            z_mid = linked_ob.midpoint_50
            z_avail = linked_ob.confirmation_time
        elif linked_fvg:
            z_bot = linked_fvg.bottom_price
            z_top = linked_fvg.top_price
            z_mid = linked_fvg.ce_price
            z_avail = linked_fvg.formation_time
        elif linked_ob:
            z_bot = linked_ob.zone_bottom
            z_top = linked_ob.zone_top
            z_mid = linked_ob.midpoint_50
            z_avail = linked_ob.confirmation_time
        else:
            continue  # No eligible entry zone available

        # Check setup_available_time
        setup_avail = max(
            choch.detection_time,
            mss.final_confirmation_time,
            sweep.detection_time,
            z_avail,
        )

        if cutoff is not None and setup_avail > cutoff:
            continue  # Invisible prior to setup_available_time

        # Confidence calculation
        conf_sources: list[AvailabilityConfidence] = [AvailabilityConfidence.ESTIMATED]
        if linked_ob:
            conf_sources.append(linked_ob.availability_confidence)
        cand_conf = resolve_confidence(conf_sources)

        if cfg.strict_pit and cand_conf != AvailabilityConfidence.VERIFIED:
            continue

        # Gather Confluences
        confluences: list[IctConfluenceEvidence] = []

        # 1. Sweep Confluence
        confluences.append(
            IctConfluenceEvidence(
                evidence_type=IctConfluenceType.LIQUIDITY_SWEEP,
                source_event_id=sweep.event_id,
                source_timeframe=sweep.timeframe,
                direction=setup_dir,
                event_time=sweep.sweep_bar_open_time,
                available_time=sweep.detection_time,
                price_bottom=sweep.level_price,
                price_top=sweep.penetration_price if is_bullish else sweep.level_price,
                explanation=f"Confirmed {sweep.liquidity_side.value} Sweep at {sweep.level_price}",
            )
        )

        # 2. MSS Confluence
        confluences.append(
            IctConfluenceEvidence(
                evidence_type=IctConfluenceType.MSS,
                source_event_id=mss.record_id,
                source_timeframe=mss.timeframe,
                direction=setup_dir,
                event_time=mss.candidate_time,
                available_time=mss.final_confirmation_time,
                explanation=f"Confirmed MSS in {setup_dir.value} direction",
            )
        )

        # 3. Displacement Confluence
        if mss.displacement_event_id and mss.displacement_event_id in disp_map:
            disp = disp_map[mss.displacement_event_id]
            confluences.append(
                IctConfluenceEvidence(
                    evidence_type=IctConfluenceType.DISPLACEMENT,
                    source_event_id=disp.event_id,
                    source_timeframe=disp.timeframe,
                    direction=setup_dir,
                    event_time=disp.candle_open_time,
                    available_time=disp.detection_time,
                    price_bottom=disp.low_price,
                    price_top=disp.high_price,
                    explanation=f"Confirmed institutional displacement (Ratio: {disp.body_ratio:.2f})",
                )
            )

        # 4. FVG Confluence
        if linked_fvg:
            confluences.append(
                IctConfluenceEvidence(
                    evidence_type=IctConfluenceType.FVG,
                    source_event_id=linked_fvg.id,
                    source_timeframe=linked_fvg.timeframe,
                    direction=setup_dir,
                    event_time=linked_fvg.formation_time,
                    available_time=linked_fvg.formation_time,
                    price_bottom=linked_fvg.bottom_price,
                    price_top=linked_fvg.top_price,
                    explanation=f"Aligned {linked_fvg.direction.value} FVG zone [{linked_fvg.bottom_price} - {linked_fvg.top_price}]",
                )
            )

        # 5. Order Block Confluence
        if linked_ob:
            confluences.append(
                IctConfluenceEvidence(
                    evidence_type=IctConfluenceType.ORDER_BLOCK,
                    source_event_id=linked_ob.order_block_id,
                    source_timeframe=linked_ob.timeframe,
                    direction=setup_dir,
                    event_time=linked_ob.origin_candle_open_time,
                    available_time=linked_ob.confirmation_time,
                    price_bottom=linked_ob.zone_bottom,
                    price_top=linked_ob.zone_top,
                    explanation=f"Aligned {linked_ob.direction.value} Order Block [{linked_ob.zone_bottom} - {linked_ob.zone_top}]",
                )
            )

        # 6. OTE Overlap Confluence
        linked_ote: OTEZoneRecord | None = None
        for ote in ote_zones:
            if ote.range_available_at > setup_avail:
                continue
            if ote.direction.value == setup_dir.value:
                # Geometric intersection
                inter_bot, inter_top, inter_w, is_touch = compute_zone_overlap(
                    z_bot, z_top, ote.zone_bottom, ote.zone_top
                )
                if inter_w > Decimal("0") and not is_touch:
                    linked_ote = ote
                    confluences.append(
                        IctConfluenceEvidence(
                            evidence_type=IctConfluenceType.OTE,
                            source_event_id=ote.ote_id,
                            source_timeframe=ote.timeframe,
                            direction=setup_dir,
                            event_time=ote.range_available_at,
                            available_time=ote.range_available_at,
                            price_bottom=inter_bot,
                            price_top=inter_top,
                            explanation=f"Optimal Trade Entry overlap [{inter_bot} - {inter_top}] (Width: {inter_w})",
                        )
                    )
                    break

        if cfg.require_ote and linked_ote is None:
            continue

        # 7. Premium / Discount Confluence
        for dr in dealing_ranges:
            if dr.range_available_at > setup_avail:
                continue
            if is_bullish and z_top <= dr.equilibrium:
                confluences.append(
                    IctConfluenceEvidence(
                        evidence_type=IctConfluenceType.PREMIUM_DISCOUNT,
                        source_event_id=dr.range_id,
                        source_timeframe=dr.timeframe,
                        direction=setup_dir,
                        event_time=dr.range_available_at,
                        available_time=dr.range_available_at,
                        price_bottom=dr.range_low,
                        price_top=dr.equilibrium,
                        explanation=f"Zone is in Discount below Equilibrium ({dr.equilibrium})",
                    )
                )
                break
            elif (not is_bullish) and z_bot >= dr.equilibrium:
                confluences.append(
                    IctConfluenceEvidence(
                        evidence_type=IctConfluenceType.PREMIUM_DISCOUNT,
                        source_event_id=dr.range_id,
                        source_timeframe=dr.timeframe,
                        direction=setup_dir,
                        event_time=dr.range_available_at,
                        available_time=dr.range_available_at,
                        price_bottom=dr.equilibrium,
                        price_top=dr.range_high,
                        explanation=f"Zone is in Premium above Equilibrium ({dr.equilibrium})",
                    )
                )
                break

        # 8. FVG Respect / Multiple Retest Confluence
        if linked_fvg and linked_fvg.id in respect_map:
            resp = respect_map[linked_fvg.id]
            # Respects known as of setup_available_time
            valid_respects = [
                e
                for e in resp.retest_events
                if e.reaction_confirmation_time
                and e.reaction_confirmation_time <= setup_avail
                and e.is_confirmed_respect
            ]
            if valid_respects:
                confluences.append(
                    IctConfluenceEvidence(
                        evidence_type=IctConfluenceType.FVG_RESPECT,
                        source_event_id=linked_fvg.id,
                        source_timeframe=linked_fvg.timeframe,
                        direction=setup_dir,
                        event_time=valid_respects[-1].touch_detection_time,
                        available_time=valid_respects[-1].reaction_confirmation_time,  # type: ignore[arg-type]
                        explanation=f"FVG has {len(valid_respects)} confirmed historical respect(s)",
                    )
                )
            if len(valid_respects) >= 2:
                confluences.append(
                    IctConfluenceEvidence(
                        evidence_type=IctConfluenceType.MULTIPLE_RETEST,
                        source_event_id=linked_fvg.id,
                        source_timeframe=linked_fvg.timeframe,
                        direction=setup_dir,
                        event_time=valid_respects[-1].touch_detection_time,
                        available_time=valid_respects[-1].reaction_confirmation_time,  # type: ignore[arg-type]
                        explanation=f"Multiple retest resilience confirmed ({len(valid_respects)} respects)",
                    )
                )

        setup_id = generate_ict_setup_id(
            inst, src, pt, tf, IctSetupType.REVERSAL, setup_dir, choch.event_id, setup_avail
        )

        candidate = IctSetupCandidate(
            setup_id=setup_id,
            instrument_id=inst,
            source_id=src,
            timeframe=tf,
            price_type=pt,
            setup_type=IctSetupType.REVERSAL,
            direction=setup_dir,
            status=IctSetupStatus.ZONE_IDENTIFIED,
            structure_event_id=choch.event_id,
            structure_confirmation_time=choch.detection_time,
            setup_available_time=setup_avail,
            last_evaluation_time=cutoff or setup_avail,
            sweep_event_id=sweep.event_id,
            mss_confirmation_id=mss.record_id,
            displacement_event_id=mss.displacement_event_id,
            fvg_id=linked_fvg.id if linked_fvg else None,
            order_block_id=linked_ob.order_block_id if linked_ob else None,
            ote_id=linked_ote.ote_id if linked_ote else None,
            d1_session_mode=d1_session_mode,
            selected_zone_bottom=z_bot,
            selected_zone_top=z_top,
            selected_zone_midpoint=z_mid,
            zone_available_time=z_avail,
            availability_confidence=cand_conf,
            confluences=tuple(confluences),
            rejection_reasons=(),
            lifecycle_events=(),
            confluence_score=len(confluences),
        )

        # Subsequent candles for lifecycle
        subsequent = [c for c in candles if c.open_time >= z_avail]
        candidate = evaluate_ict_setup_lifecycle(candidate, subsequent, cfg, cutoff)
        candidates.append(candidate)

    # =========================================================================
    # 2. ICT CONTINUATION SETUP DETECTION
    # =========================================================================
    # Continuation requires:
    # 1. Confirmed BOS structure break
    # 2. Causally linked displacement within configurable window
    # 3. Direction-aligned eligible entry zone (FVG or Order Block)
    cand_by_open = {c.open_time: idx for idx, c in enumerate(candles)}

    for bos in structure_breaks:
        if bos.break_type != StructureBreakType.BOS or bos.confirmation_status != "CONFIRMED":
            continue

        is_bullish = bos.structure_direction == StructureDirection.BULLISH
        setup_dir = IctSetupDirection.BULLISH if is_bullish else IctSetupDirection.BEARISH

        bos_idx = cand_by_open.get(bos.break_bar_open_time)

        # Find causally linked displacement
        eligible_disps = [
            d
            for d in displacements
            if d.direction.value == setup_dir.value
            and (
                bos_idx is not None
                and cand_by_open.get(d.candle_open_time) is not None
                and abs(cand_by_open[d.candle_open_time] - bos_idx)
                <= cfg.max_bos_displacement_bars
            )
        ]
        if cfg.require_displacement_for_continuation and not eligible_disps:
            continue

        cont_disp = eligible_disps[0] if eligible_disps else None

        matching_cont_obs = [
            o
            for o in order_blocks
            if o.structure_break_event_id == bos.event_id
            and o.direction.value == setup_dir.value
            and o.instrument_id == inst
            and o.source_id == src
            and o.price_type == pt
            and o.timeframe == tf
            and o.confirmation_time <= bos.detection_time
        ]
        cont_ob: OrderBlockRecord | None = None
        for m_ob in matching_cont_obs:
            reconstructed_list = filter_order_blocks_as_of([m_ob], bos.detection_time)
            if reconstructed_list and reconstructed_list[0].is_active:
                cont_ob = reconstructed_list[0]
                break

        cont_fvg: FvgZone | None = None
        if cont_disp:
            cont_fvg = next(
                (
                    f
                    for f in fvg_zones
                    if f.candle_2_open == cont_disp.candle_open_time
                    and f.direction.value == setup_dir.value
                ),
                None,
            )
        if not cont_fvg:
            # Check FVGs formed close to the BOS
            cont_fvg = next(
                (
                    f
                    for f in fvg_zones
                    if f.direction.value == setup_dir.value
                    and bos_idx is not None
                    and cand_by_open.get(f.candle_1_open) is not None
                    and abs(cand_by_open[f.candle_1_open] - bos_idx) <= cfg.max_zone_distance_bars
                ),
                None,
            )

        if cfg.prefer_order_block_over_fvg and cont_ob:
            z_bot = cont_ob.zone_bottom
            z_top = cont_ob.zone_top
            z_mid = cont_ob.midpoint_50
            z_avail = cont_ob.confirmation_time
        elif cont_fvg:
            z_bot = cont_fvg.bottom_price
            z_top = cont_fvg.top_price
            z_mid = cont_fvg.ce_price
            z_avail = cont_fvg.formation_time
        elif cont_ob:
            z_bot = cont_ob.zone_bottom
            z_top = cont_ob.zone_top
            z_mid = cont_ob.midpoint_50
            z_avail = cont_ob.confirmation_time
        else:
            continue  # No eligible zone

        disp_time = cont_disp.detection_time if cont_disp else bos.detection_time
        setup_avail = max(bos.detection_time, disp_time, z_avail)

        if cutoff is not None and setup_avail > cutoff:
            continue

        conf_sources = [AvailabilityConfidence.ESTIMATED]
        if cont_ob:
            conf_sources.append(cont_ob.availability_confidence)
        cand_conf = resolve_confidence(conf_sources)

        if cfg.strict_pit and cand_conf != AvailabilityConfidence.VERIFIED:
            continue

        # Gather Confluences
        confluences = []

        # 1. BOS Confluence
        confluences.append(
            IctConfluenceEvidence(
                evidence_type=IctConfluenceType.BOS,
                source_event_id=bos.event_id,
                source_timeframe=bos.timeframe,
                direction=setup_dir,
                event_time=bos.break_bar_open_time,
                available_time=bos.detection_time,
                price_bottom=bos.reference_level_price if not is_bullish else bos.break_price,
                price_top=bos.break_price if is_bullish else bos.reference_level_price,
                explanation=f"Confirmed {setup_dir.value} Break of Structure",
            )
        )

        # 2. Displacement Confluence
        if cont_disp:
            confluences.append(
                IctConfluenceEvidence(
                    evidence_type=IctConfluenceType.DISPLACEMENT,
                    source_event_id=cont_disp.event_id,
                    source_timeframe=cont_disp.timeframe,
                    direction=setup_dir,
                    event_time=cont_disp.candle_open_time,
                    available_time=cont_disp.detection_time,
                    price_bottom=cont_disp.low_price,
                    price_top=cont_disp.high_price,
                    explanation=f"Aligned {setup_dir.value} Displacement (Body Ratio: {cont_disp.body_ratio:.2f})",
                )
            )

        # 3. FVG Confluence
        if cont_fvg:
            confluences.append(
                IctConfluenceEvidence(
                    evidence_type=IctConfluenceType.FVG,
                    source_event_id=cont_fvg.id,
                    source_timeframe=cont_fvg.timeframe,
                    direction=setup_dir,
                    event_time=cont_fvg.formation_time,
                    available_time=cont_fvg.formation_time,
                    price_bottom=cont_fvg.bottom_price,
                    price_top=cont_fvg.top_price,
                    explanation=f"Aligned {cont_fvg.direction.value} FVG zone [{cont_fvg.bottom_price} - {cont_fvg.top_price}]",
                )
            )

        # 4. Order Block Confluence
        if cont_ob:
            confluences.append(
                IctConfluenceEvidence(
                    evidence_type=IctConfluenceType.ORDER_BLOCK,
                    source_event_id=cont_ob.order_block_id,
                    source_timeframe=cont_ob.timeframe,
                    direction=setup_dir,
                    event_time=cont_ob.origin_candle_open_time,
                    available_time=cont_ob.confirmation_time,
                    price_bottom=cont_ob.zone_bottom,
                    price_top=cont_ob.zone_top,
                    explanation=f"Aligned {cont_ob.direction.value} Order Block [{cont_ob.zone_bottom} - {cont_ob.zone_top}]",
                )
            )

        # 5. OTE Confluence
        linked_ote = None
        for ote in ote_zones:
            if ote.range_available_at > setup_avail:
                continue
            if ote.direction.value == setup_dir.value:
                inter_bot, inter_top, inter_w, is_touch = compute_zone_overlap(
                    z_bot, z_top, ote.zone_bottom, ote.zone_top
                )
                if inter_w > Decimal("0") and not is_touch:
                    linked_ote = ote
                    confluences.append(
                        IctConfluenceEvidence(
                            evidence_type=IctConfluenceType.OTE,
                            source_event_id=ote.ote_id,
                            source_timeframe=ote.timeframe,
                            direction=setup_dir,
                            event_time=ote.range_available_at,
                            available_time=ote.range_available_at,
                            price_bottom=inter_bot,
                            price_top=inter_top,
                            explanation=f"Optimal Trade Entry overlap [{inter_bot} - {inter_top}] (Width: {inter_w})",
                        )
                    )
                    break

        if cfg.require_ote and linked_ote is None:
            continue

        setup_id = generate_ict_setup_id(
            inst, src, pt, tf, IctSetupType.CONTINUATION, setup_dir, bos.event_id, setup_avail
        )

        candidate = IctSetupCandidate(
            setup_id=setup_id,
            instrument_id=inst,
            source_id=src,
            timeframe=tf,
            price_type=pt,
            setup_type=IctSetupType.CONTINUATION,
            direction=setup_dir,
            status=IctSetupStatus.ZONE_IDENTIFIED,
            structure_event_id=bos.event_id,
            structure_confirmation_time=bos.detection_time,
            setup_available_time=setup_avail,
            last_evaluation_time=cutoff or setup_avail,
            sweep_event_id=None,
            mss_confirmation_id=None,
            displacement_event_id=cont_disp.event_id if cont_disp else None,
            fvg_id=cont_fvg.id if cont_fvg else None,
            order_block_id=cont_ob.order_block_id if cont_ob else None,
            ote_id=linked_ote.ote_id if linked_ote else None,
            d1_session_mode=d1_session_mode,
            selected_zone_bottom=z_bot,
            selected_zone_top=z_top,
            selected_zone_midpoint=z_mid,
            zone_available_time=z_avail,
            availability_confidence=cand_conf,
            confluences=tuple(confluences),
            rejection_reasons=(),
            lifecycle_events=(),
            confluence_score=len(confluences),
        )

        subsequent = [c for c in candles if c.open_time >= z_avail]
        candidate = evaluate_ict_setup_lifecycle(candidate, subsequent, cfg, cutoff)
        candidates.append(candidate)

    # Sort deterministically: by setup_available_time ascending, then setup_id
    candidates.sort(key=lambda x: (x.setup_available_time, x.setup_id))
    return tuple(candidates)


def filter_ict_setups_as_of(
    candidates: Sequence[IctSetupCandidate],
    as_of_utc: datetime,
) -> tuple[IctSetupCandidate, ...]:
    """Filter setup candidates and their internal state as of a historical snapshot timestamp.

    Guarantees:
    - Any candidate where `setup_available_time > as_of_utc` is completely omitted.
    - Any lifecycle events with `event_time_utc > as_of_utc` are stripped.
    - Confluences with `available_time > as_of_utc` are masked.
    - Candidate status is reverted to its point-in-time state as of `as_of_utc`.
    """
    cutoff = ensure_utc(as_of_utc)
    filtered: list[IctSetupCandidate] = []

    for c in candidates:
        if c.setup_available_time > cutoff:
            continue

        # Mask confluences
        valid_confluences = tuple(conf for conf in c.confluences if conf.available_time <= cutoff)

        # Mask lifecycle events
        valid_events = tuple(ev for ev in c.lifecycle_events if ev.event_time_utc <= cutoff)

        # Determine status at cutoff
        status_at_as_of: IctSetupStatus
        if valid_events:
            status_at_as_of = valid_events[-1].status_after
        else:
            status_at_as_of = IctSetupStatus.ZONE_IDENTIFIED

        test_bar_open = (
            c.test_bar_open_time
            if (c.test_detection_time and c.test_detection_time <= cutoff)
            else None
        )
        test_detect = (
            c.test_detection_time
            if (c.test_detection_time and c.test_detection_time <= cutoff)
            else None
        )
        test_p = (
            c.test_price if (c.test_detection_time and c.test_detection_time <= cutoff) else None
        )
        inval_t = (
            c.invalidation_time
            if (c.invalidation_time and c.invalidation_time <= cutoff)
            else None
        )

        reconstructed = IctSetupCandidate(
            setup_id=c.setup_id,
            instrument_id=c.instrument_id,
            source_id=c.source_id,
            timeframe=c.timeframe,
            price_type=c.price_type,
            setup_type=c.setup_type,
            direction=c.direction,
            status=status_at_as_of,
            structure_event_id=c.structure_event_id,
            structure_confirmation_time=c.structure_confirmation_time,
            setup_available_time=c.setup_available_time,
            last_evaluation_time=cutoff,
            sweep_event_id=c.sweep_event_id,
            mss_confirmation_id=c.mss_confirmation_id,
            displacement_event_id=c.displacement_event_id,
            fvg_id=c.fvg_id,
            order_block_id=c.order_block_id,
            ote_id=c.ote_id,
            d1_session_mode=c.d1_session_mode,
            selected_zone_bottom=c.selected_zone_bottom,
            selected_zone_top=c.selected_zone_top,
            selected_zone_midpoint=c.selected_zone_midpoint,
            zone_available_time=c.zone_available_time,
            availability_confidence=c.availability_confidence,
            confluences=valid_confluences,
            rejection_reasons=c.rejection_reasons,
            lifecycle_events=valid_events,
            test_bar_open_time=test_bar_open,
            test_detection_time=test_detect,
            test_price=test_p,
            invalidation_time=inval_t,
            confluence_score=len(valid_confluences),
        )
        filtered.append(reconstructed)

    filtered.sort(key=lambda x: (x.setup_available_time, x.setup_id))
    return tuple(filtered)


def summarize_ict_setups(candidates: Sequence[IctSetupCandidate]) -> IctSetupSummary:
    """Summarize counts, status distributions, and average confluences of ICT setup candidates."""
    total = len(candidates)
    if total == 0:
        return IctSetupSummary(
            total_candidates=0,
            reversal_count=0,
            continuation_count=0,
            bullish_count=0,
            bearish_count=0,
            structure_confirmed_count=0,
            zone_identified_count=0,
            zone_tested_count=0,
            invalidated_count=0,
            expired_count=0,
            avg_confluence_score=Decimal("0"),
            candidates=(),
        )

    rev_cnt = sum(1 for c in candidates if c.is_reversal)
    cont_cnt = sum(1 for c in candidates if c.is_continuation)
    bull_cnt = sum(1 for c in candidates if c.is_bullish)
    bear_cnt = sum(1 for c in candidates if c.is_bearish)

    struct_cnt = sum(1 for c in candidates if c.status == IctSetupStatus.STRUCTURE_CONFIRMED)
    zone_id_cnt = sum(1 for c in candidates if c.status == IctSetupStatus.ZONE_IDENTIFIED)
    zone_test_cnt = sum(1 for c in candidates if c.status == IctSetupStatus.ZONE_TESTED)
    inval_cnt = sum(1 for c in candidates if c.status == IctSetupStatus.INVALIDATED)
    exp_cnt = sum(1 for c in candidates if c.status == IctSetupStatus.EXPIRED)

    avg_conf = Decimal(sum(c.confluence_score for c in candidates)) / Decimal(total)

    return IctSetupSummary(
        total_candidates=total,
        reversal_count=rev_cnt,
        continuation_count=cont_cnt,
        bullish_count=bull_cnt,
        bearish_count=bear_cnt,
        structure_confirmed_count=struct_cnt,
        zone_identified_count=zone_id_cnt,
        zone_tested_count=zone_test_cnt,
        invalidated_count=inval_cnt,
        expired_count=exp_cnt,
        avg_confluence_score=avg_conf.quantize(Decimal("0.01")),
        candidates=tuple(candidates),
    )


def explain_ict_setup(candidate: IctSetupCandidate) -> str:
    """Generate a structured, human-readable explanatory text for an ICT Setup Candidate."""
    lines = [
        f"=== ICT {candidate.setup_type.value} — {candidate.direction.value} ===",
        f"Setup ID:           {candidate.setup_id}",
        f"Instrument/TF/Type: {candidate.instrument_id} | {candidate.timeframe.name} | {candidate.price_type.value}",
        f"Current Status:     {candidate.status.value}",
        f"Available Time:     {candidate.setup_available_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Structure Link:     {candidate.structure_event_id} (Confirmed: {candidate.structure_confirmation_time.strftime('%Y-%m-%d %H:%M:%S UTC')})",
    ]

    if candidate.sweep_event_id:
        lines.append(f"Liquidity Sweep:    {candidate.sweep_event_id}")
    if candidate.mss_confirmation_id:
        lines.append(f"MSS Confirmation:   {candidate.mss_confirmation_id}")
    if candidate.displacement_event_id:
        lines.append(f"Displacement Link:  {candidate.displacement_event_id}")

    if candidate.selected_zone_bottom is not None and candidate.selected_zone_top is not None:
        lines.append(
            f"Entry Zone:         [{candidate.selected_zone_bottom} - {candidate.selected_zone_top}] "
            f"(Midpoint: {candidate.selected_zone_midpoint})"
        )
        if candidate.fvg_id:
            lines.append(f"  -> Linked FVG:    {candidate.fvg_id}")
        if candidate.order_block_id:
            lines.append(f"  -> Linked OB:     {candidate.order_block_id}")
        if candidate.ote_id:
            lines.append(f"  -> Linked OTE:    {candidate.ote_id}")

    lines.append(f"Confluences ({len(candidate.confluences)} verified):")
    for idx, conf in enumerate(candidate.confluences, 1):
        lines.append(f"  {idx}. [{conf.evidence_type.value}] {conf.explanation}")

    if candidate.lifecycle_events:
        lines.append(f"Lifecycle Transitions ({len(candidate.lifecycle_events)}):")
        for ev in candidate.lifecycle_events:
            lines.append(
                f"  * {ev.event_time_utc.strftime('%Y-%m-%d %H:%M UTC')}: {ev.status_before.value} -> "
                f"{ev.status_after.value} ({ev.reason})"
            )

    return "\n".join(lines)
