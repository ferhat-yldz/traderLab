r"""Point-in-Time Safe Market Structure Shift (MSS) Confirmation Engine.

Evaluates Faz 3C Change of Character (CHoCH) and MSS Candidate events
against multi-factor Displacement and Fair Value Gap (FVG) confirmation rules.

Key architectural and financial principles:
1. **Separation of Candidate and Confirmed States**:
   - `PENDING`: Awaiting displacement and/or FVG within confirmation window.
   - `DISPLACEMENT_CONFIRMED`: Displacement verified in matching direction, awaiting FVG.
   - `MSS_CONFIRMED`: Full multi-factor confirmation (CHoCH + Displacement + FVG).
   - `REJECTED`: Confirmation window expired without criteria, or wrong-direction movement.
   - `INSUFFICIENT_DATA`: Data stream ended before confirmation window could elapse.
2. **Point-in-Time Confirmation Timing**:
   $$\text{final\_confirmation\_time} = \max(\text{choch\_detection\_time}, \text{displacement\_detection\_time}, \text{fvg\_formation\_time})$$
   MSS_CONFIRMED status can never be observed or queried prior to `final_confirmation_time`.
3. **Deterministic FVG & Displacement Proximity**:
   - The displacement candle must align with the candidate direction and occur within `max_confirmation_bars`.
   - The FVG must be formed in the displacement leg (e.g. candle 2 of FVG is displacement bar).
4. **Preservation of Market Structure Records**:
   - Original CHoCH and MSS candidate records are preserved without mutation.
   - No duplicate MSS_CONFIRMED records generated for the same candidate.
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
from traderlab.domain.market_structure.displacement import (
    DisplacementConfig,
    DisplacementDirection,
    DisplacementEvent,
    detect_displacements,
)
from traderlab.domain.market_structure.fvg import (
    FvgDirection,
    FvgZone,
    detect_fair_value_gaps,
)
from traderlab.domain.market_structure.market_structure import (
    StructureBreakEvent,
    StructureBreakType,
    StructureDirection,
)
from traderlab.domain.market_structure.swing import _validate_candle_sequence

if TYPE_CHECKING:
    from traderlab.domain.market_structure.liquidity_sweep import LiquiditySweepEvent


# ── Exceptions ───────────────────────────────────────────────────────────────
class MssConfirmationError(TraderLabError):
    """Base exception for MSS Confirmation domain errors."""

    def __init__(self, message: str, *, code: str = "MSS_CONFIRMATION_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidMssConfirmationParameterError(MssConfirmationError):
    """Raised when MSS confirmation configuration parameters are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_MSS_CONFIRMATION_PARAMETER")


class MssConfirmationDataError(MssConfirmationError):
    """Raised when candle sequences or event streams are mismatched or invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="MSS_CONFIRMATION_DATA_ERROR")


# ── Enumerations ────────────────────────────────────────────────────────────
class MssStatus(enum.Enum):
    """Classification status of an MSS candidate evaluation."""

    PENDING = "PENDING"
    """Within confirmation window, awaiting displacement or FVG."""

    DISPLACEMENT_CONFIRMED = "DISPLACEMENT_CONFIRMED"
    """Displacement verified in matching direction; awaiting FVG formation."""

    MSS_CONFIRMED = "MSS_CONFIRMED"
    """Fully confirmed Market Structure Shift with verified displacement and FVG."""

    REJECTED = "REJECTED"
    """Confirmation window expired without meeting criteria, or wrong-direction movement."""

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    """Insufficient candles or data stream truncated before window could be evaluated."""


# ── Domain Models ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class MssConfirmationRecord:
    """Immutable domain record representing the full evaluation of an MSS candidate.

    Guarantees:
    - **Point-in-Time Safety**: `final_confirmation_time == max(choch, displacement, fvg)`.
    - **Traceability**: Explicitly links `source_choch_event_id`, `displacement_event_id`,
      and `associated_fvg_id`.
    """

    record_id: str
    mss_candidate_id: str
    source_choch_event_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    price_type: PriceType
    direction: StructureDirection
    candidate_time: datetime
    displacement_event_id: str | None
    displacement_time: datetime | None
    associated_fvg_id: str | None
    fvg_confirmation_time: datetime | None
    associated_sweep_id: str | None
    final_confirmation_time: datetime | None
    status: MssStatus
    rejection_reason: str | None = None
    bars_elapsed: int = 0
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status == MssStatus.MSS_CONFIRMED:
            if self.final_confirmation_time is None:
                raise MssConfirmationError(
                    "final_confirmation_time cannot be None when status is MSS_CONFIRMED"
                )
            if self.final_confirmation_time < self.candidate_time:
                raise MssConfirmationError(
                    f"final_confirmation_time ({self.final_confirmation_time}) must be >= "
                    f"candidate_time ({self.candidate_time})"
                )

    @property
    def is_confirmed(self) -> bool:
        """Return True if fully confirmed MSS."""
        return self.status == MssStatus.MSS_CONFIRMED

    @property
    def is_pending(self) -> bool:
        """Return True if pending evaluation."""
        return self.status == MssStatus.PENDING

    @property
    def is_displacement_confirmed(self) -> bool:
        """Return True if displacement confirmed awaiting FVG."""
        return self.status == MssStatus.DISPLACEMENT_CONFIRMED

    @property
    def is_rejected(self) -> bool:
        """Return True if rejected."""
        return self.status == MssStatus.REJECTED

    @property
    def is_insufficient_data(self) -> bool:
        """Return True if insufficient data."""
        return self.status == MssStatus.INSUFFICIENT_DATA


@dataclass(frozen=True, slots=True)
class MssConfirmationConfig:
    """Configuration settings for MSS Candidate Confirmation Engine."""

    max_confirmation_bars: int = 3
    require_displacement: bool = True
    require_fvg_confirmation: bool = True
    min_fvg_width: Decimal | None = None
    displacement_config: DisplacementConfig | None = None
    policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED
    strict_pit: bool = False
    allow_synthetic: bool = False
    allow_weekend_gap: bool = True
    allow_rollover_gap: bool = True

    def __post_init__(self) -> None:
        if self.max_confirmation_bars < 1:
            raise InvalidMssConfirmationParameterError(
                f"max_confirmation_bars must be >= 1, got {self.max_confirmation_bars}"
            )


# ── Core MSS Candidate Evaluation Algorithm ──────────────────────────────────
def evaluate_mss_confirmations(
    candles: Sequence[Candle],
    choch_events: Sequence[StructureBreakEvent],
    *,
    displacements: Sequence[DisplacementEvent] | None = None,
    fvgs: Sequence[FvgZone] | None = None,
    sweeps: Sequence[LiquiditySweepEvent] | None = None,
    config: MssConfirmationConfig | None = None,
    as_of_utc: datetime | None = None,
    policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED,
    strict_pit: bool = False,
    allow_synthetic: bool = False,
    allow_weekend_gap: bool = True,
    allow_rollover_gap: bool = True,
) -> list[MssConfirmationRecord]:
    """Evaluate all CHoCH / MSS_CANDIDATE events for Displacement and FVG confirmation.

    Guarantees:
    - Pure Point-in-Time: No future displacement or FVG can leak into historical evaluation.
    - Zero Mutation: Original CHoCH records remain untouched.
    - Deterministic Output: Same inputs produce identical records and timestamps.

    Returns:
        Chronologically sorted list of `MssConfirmationRecord` objects.
    """
    if config is None:
        config = MssConfirmationConfig(
            policy=policy,
            strict_pit=strict_pit,
            allow_synthetic=allow_synthetic,
            allow_weekend_gap=allow_weekend_gap,
            allow_rollover_gap=allow_rollover_gap,
        )

    if not choch_events or not candles:
        return []

    # 1. Validate candle sequence
    _validate_candle_sequence(candles)

    # 2. Filter candles by PIT criteria
    effective_candles: list[Candle] = []
    as_of_norm = ensure_utc(as_of_utc) if as_of_utc is not None else None
    effective_policy = PitPolicy.STRICT_PIT if config.strict_pit else config.policy

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

    # 3. Filter eligible CHoCH events
    eligible_chochs: list[StructureBreakEvent] = []
    for ev in choch_events:
        if ev.price_type != first_candle.price_type:
            raise MssConfirmationDataError(
                f"PriceType mismatch: Candle {first_candle.price_type.value} != Event {ev.price_type.value}"
            )
        if ev.timeframe != first_candle.timeframe:
            raise MssConfirmationDataError(
                f"Timeframe mismatch: Candle {first_candle.timeframe.name} != Event {ev.timeframe.name}"
            )
        if ev.instrument_id != first_candle.instrument_id:
            raise MssConfirmationDataError(
                f"InstrumentId mismatch: Candle {first_candle.instrument_id} != Event {ev.instrument_id}"
            )
        if ev.break_type not in (StructureBreakType.CHOCH, StructureBreakType.MSS_CANDIDATE):
            continue
        if as_of_norm is not None and ev.detection_time > as_of_norm:
            continue
        eligible_chochs.append(ev)

    if not eligible_chochs:
        return []

    # 4. Compute Displacements and FVGs if not provided, or enforce PIT filter on provided items
    if displacements is None:
        displacements = detect_displacements(
            effective_candles,
            config=config.displacement_config,
            as_of_utc=as_of_norm,
            policy=config.policy,
            strict_pit=config.strict_pit,
            allow_synthetic=config.allow_synthetic,
            allow_weekend_gap=config.allow_weekend_gap,
            allow_rollover_gap=config.allow_rollover_gap,
        )
    else:
        displacements = [
            d for d in displacements if as_of_norm is None or d.detection_time <= as_of_norm
        ]

    if fvgs is None and config.require_fvg_confirmation:
        raw_fvgs = detect_fair_value_gaps(
            effective_candles,
            min_width=config.min_fvg_width,
            allow_weekend_gap=config.allow_weekend_gap,
            allow_rollover_gap=config.allow_rollover_gap,
        )
        # Point-in-time filter for FVGs
        fvgs = [f for f in raw_fvgs if as_of_norm is None or f.formation_time <= as_of_norm]
    elif fvgs is not None:
        fvgs = [f for f in fvgs if as_of_norm is None or f.formation_time <= as_of_norm]
    else:
        fvgs = []

    if sweeps is not None:
        sweeps = [s for s in sweeps if as_of_norm is None or s.sweep_bar_close_time <= as_of_norm]

    candle_idx_map = {c.open_time: idx for idx, c in enumerate(effective_candles)}
    records: list[MssConfirmationRecord] = []
    seen_candidate_ids: set[str] = set()

    # 5. Evaluate each CHoCH event
    for choch in eligible_chochs:
        cand_id = choch.mss_candidate_id or f"MSS_CANDIDATE_{choch.event_id}"
        # Prevent duplicate records for the exact same candidate
        if cand_id in seen_candidate_ids:
            continue
        seen_candidate_ids.add(cand_id)

        # Check if break candle exists in candle series
        if choch.break_bar_open_time not in candle_idx_map:
            rec = MssConfirmationRecord(
                record_id=f"CONF_{cand_id}",
                mss_candidate_id=cand_id,
                source_choch_event_id=choch.event_id,
                instrument_id=choch.instrument_id,
                source_id=choch.source_id,
                timeframe=choch.timeframe,
                price_type=choch.price_type,
                direction=choch.structure_direction,
                candidate_time=choch.detection_time,
                displacement_event_id=None,
                displacement_time=None,
                associated_fvg_id=None,
                fvg_confirmation_time=None,
                associated_sweep_id=None,
                final_confirmation_time=None,
                status=MssStatus.INSUFFICIENT_DATA,
                rejection_reason="BREAK_CANDLE_NOT_IN_STREAM",
                bars_elapsed=0,
            )
            records.append(rec)
            continue

        break_idx = candle_idx_map[choch.break_bar_open_time]
        end_idx = min(break_idx + config.max_confirmation_bars, len(effective_candles) - 1)
        window_candles = effective_candles[break_idx : end_idx + 1]
        window_open_times = {c.open_time for c in window_candles}

        bars_after_break = len(effective_candles) - 1 - break_idx
        window_complete = bars_after_break >= config.max_confirmation_bars

        # Direction alignment
        if choch.structure_direction == StructureDirection.BULLISH:
            expected_disp_dir = DisplacementDirection.BULLISH
            expected_fvg_dir = FvgDirection.BULLISH
        else:
            expected_disp_dir = DisplacementDirection.BEARISH
            expected_fvg_dir = FvgDirection.BEARISH

        # Check for opposing displacement within confirmation window
        opposite_disps = [
            d
            for d in displacements
            if d.direction != expected_disp_dir
            and d.candle_open_time in window_open_times
            and d.candle_open_time > choch.break_bar_open_time
        ]

        if opposite_disps:
            # Immediate invalidation by opposite displacement
            rec = MssConfirmationRecord(
                record_id=f"CONF_{cand_id}",
                mss_candidate_id=cand_id,
                source_choch_event_id=choch.event_id,
                instrument_id=choch.instrument_id,
                source_id=choch.source_id,
                timeframe=choch.timeframe,
                price_type=choch.price_type,
                direction=choch.structure_direction,
                candidate_time=choch.detection_time,
                displacement_event_id=opposite_disps[0].event_id,
                displacement_time=opposite_disps[0].detection_time,
                associated_fvg_id=None,
                fvg_confirmation_time=None,
                associated_sweep_id=None,
                final_confirmation_time=None,
                status=MssStatus.REJECTED,
                rejection_reason="OPPOSITE_DISPLACEMENT_DETECTED",
                bars_elapsed=bars_after_break,
            )
            records.append(rec)
            continue

        # Check for matching displacement within confirmation window
        matching_disps = [
            d
            for d in displacements
            if d.direction == expected_disp_dir and d.candle_open_time in window_open_times
        ]

        selected_disp: DisplacementEvent | None = None
        reason: str | None = None
        if config.require_displacement:
            if matching_disps:
                selected_disp = matching_disps[0]
            else:
                if window_complete:
                    status = MssStatus.REJECTED
                    reason = "WINDOW_EXPIRED_NO_DISPLACEMENT"
                else:
                    status = MssStatus.PENDING
                    reason = "AWAITING_DISPLACEMENT"

                rec = MssConfirmationRecord(
                    record_id=f"CONF_{cand_id}",
                    mss_candidate_id=cand_id,
                    source_choch_event_id=choch.event_id,
                    instrument_id=choch.instrument_id,
                    source_id=choch.source_id,
                    timeframe=choch.timeframe,
                    price_type=choch.price_type,
                    direction=choch.structure_direction,
                    candidate_time=choch.detection_time,
                    displacement_event_id=None,
                    displacement_time=None,
                    associated_fvg_id=None,
                    fvg_confirmation_time=None,
                    associated_sweep_id=None,
                    final_confirmation_time=None,
                    status=status,
                    rejection_reason=reason,
                    bars_elapsed=bars_after_break,
                )
                records.append(rec)
                continue

        disp_id = selected_disp.event_id if selected_disp else None
        disp_time = selected_disp.detection_time if selected_disp else choch.detection_time

        # Check for matching FVG confirmation
        selected_fvg: FvgZone | None = None
        if config.require_fvg_confirmation:
            # FVG must match direction, metadata, and be causally anchored
            matching_fvgs = []
            max_fvg_idx = min(end_idx + 1, len(effective_candles) - 1)
            max_allowed_fvg_time = effective_candles[max_fvg_idx].close_time

            for f in fvgs:
                # 1. Direction and metadata compatibility
                if f.direction != expected_fvg_dir:
                    continue
                if f.instrument_id != choch.instrument_id:
                    continue
                if f.source_id != choch.source_id:
                    continue
                if f.timeframe != choch.timeframe:
                    continue
                if f.price_type != choch.price_type:
                    continue

                # 2. Temporal boundaries: must form at or after CHoCH detection, and not after allowed window
                if f.formation_time < choch.detection_time:
                    continue
                if f.formation_time > max_allowed_fvg_time:
                    continue

                # 3. Anchor linkage
                if config.require_displacement:
                    # Strict SMC: selected_disp must be verified, and FVG Candle 2 MUST strictly match selected_disp.candle_open_time
                    if selected_disp is None:
                        continue
                    if f.candle_2_open != selected_disp.candle_open_time:
                        continue
                    matching_fvgs.append(f)
                else:
                    # Optional displacement mode:
                    if selected_disp is not None:
                        if f.candle_2_open != selected_disp.candle_open_time:
                            continue
                    else:
                        if f.candle_1_open not in window_open_times:
                            continue
                    matching_fvgs.append(f)

            if matching_fvgs:
                selected_fvg = matching_fvgs[0]
                status = MssStatus.MSS_CONFIRMED
                fvg_id = selected_fvg.id
                fvg_time = selected_fvg.formation_time
                final_time = max(choch.detection_time, disp_time, fvg_time)
                reason = None
            else:
                fvg_id = None
                fvg_time = None
                final_time = None
                if window_complete:
                    status = MssStatus.REJECTED
                    reason = "WINDOW_EXPIRED_NO_FVG"
                else:
                    status = MssStatus.DISPLACEMENT_CONFIRMED
                    reason = "AWAITING_FVG_CONFIRMATION"

        else:
            # FVG not required
            status = MssStatus.MSS_CONFIRMED
            fvg_id = None
            fvg_time = None
            final_time = max(choch.detection_time, disp_time)
            reason = None

        # Check optional liquidity sweep context
        associated_sweep_id: str | None = None
        if sweeps:
            for sw in sweeps:
                if sw.source_swing_id == choch.source_swing_id:
                    associated_sweep_id = sw.event_id
                    break

        rec = MssConfirmationRecord(
            record_id=f"CONF_{cand_id}",
            mss_candidate_id=cand_id,
            source_choch_event_id=choch.event_id,
            instrument_id=choch.instrument_id,
            source_id=choch.source_id,
            timeframe=choch.timeframe,
            price_type=choch.price_type,
            direction=choch.structure_direction,
            candidate_time=choch.detection_time,
            displacement_event_id=disp_id,
            displacement_time=disp_time if selected_disp else None,
            associated_fvg_id=fvg_id,
            fvg_confirmation_time=fvg_time,
            associated_sweep_id=associated_sweep_id,
            final_confirmation_time=final_time,
            status=status,
            rejection_reason=reason,
            bars_elapsed=bars_after_break,
        )
        records.append(rec)

    records.sort(key=lambda r: (r.candidate_time, r.record_id))
    return records


# ── Query & Filtering Helpers ────────────────────────────────────────────────
def filter_confirmed_mss(
    records: Sequence[MssConfirmationRecord],
) -> list[MssConfirmationRecord]:
    """Filter records for fully confirmed MSS (MSS_CONFIRMED)."""
    return [r for r in records if r.is_confirmed]


def filter_pending_mss(
    records: Sequence[MssConfirmationRecord],
) -> list[MssConfirmationRecord]:
    """Filter records for pending evaluations (PENDING or DISPLACEMENT_CONFIRMED)."""
    return [r for r in records if r.is_pending or r.is_displacement_confirmed]


def filter_rejected_mss(
    records: Sequence[MssConfirmationRecord],
) -> list[MssConfirmationRecord]:
    """Filter records for rejected candidates."""
    return [r for r in records if r.is_rejected]


def summarize_mss_confirmations(
    records: Sequence[MssConfirmationRecord],
) -> dict[str, int]:
    """Compile aggregated count metrics for MSS candidate confirmation evaluations."""
    confirmed = sum(1 for r in records if r.is_confirmed)
    disp_only = sum(1 for r in records if r.is_displacement_confirmed)
    pending = sum(1 for r in records if r.is_pending)
    rejected = sum(1 for r in records if r.is_rejected)
    insufficient = sum(1 for r in records if r.is_insufficient_data)

    return {
        "total_evaluated_candidates": len(records),
        "mss_confirmed": confirmed,
        "displacement_confirmed": disp_only,
        "pending": pending,
        "rejected": rejected,
        "insufficient_data": insufficient,
    }
