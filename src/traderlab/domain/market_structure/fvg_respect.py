"""Deterministic Point-in-Time Safe FVG Respect, Retest, and Reaction Analysis Engine.

Financial Theory & Architecture:
    1. **Strict Pure Domain Logic** — Operates exclusively on immutable domain ``Candle``
       sequences and ``FvgZone`` domain objects. Zero dependencies on infrastructure (SQLAlchemy,
       PostgreSQL), filesystem, or UI (Streamlit, Plotly).
    2. **Point-in-Time Correctness** — Retest events and respect confirmations are strictly
       anchored to candle close timestamps (``close_time <= as_of_utc``). Future bars, future reactions,
       and future lifecycle transitions (e.g. inversion) are strictly invisible in historical queries.
    3. **Separation of Touch and Respect** — ``TOUCH != RESPECT``. A touch represents price entry into
       an active FVG zone. A respect requires subsequent confirmation by a closed bar in the effective
       reaction direction within a defined reaction window without prior invalidation.
    4. **Independent Retest Identification** — Consecutive bars remaining inside or touching the zone
       belong to a single continuous retest episode. A new independent retest requires meaningful
       departure (minimum bars outside and minimum departure ticks).
    5. **Normal vs Inversion FVG Roles** — Normal Bullish FVG acts as Bullish Support; Normal Bearish
       FVG acts as Bearish Resistance. Upon confirmed inversion, Inverted Bullish FVG transitions to
       Bearish Resistance, and Inverted Bearish FVG transitions to Bullish Support. Pre-inversion and
       post-inversion retest events are strictly segregated.
    6. **Deterministic Identity & Full History Preservation** — Every retest event receives a unique,
       deterministic identifier. Slicing retest limits for display (e.g. ``max_reported_retests = 4``)
       never deletes domain history: all events remain preserved in the full event record.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
    ensure_utc,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.fvg import (
    FvgDirection,
    FvgError,
    FvgLifecycleEvent,
    FvgValidityState,
    FvgZone,
    track_fvg_lifecycle,
)


# ── Exceptions ───────────────────────────────────────────────────────────────
class FvgRespectError(FvgError):
    """Base exception for FVG respect and retest domain errors."""

    def __init__(self, message: str, *, code: str = "FVG_RESPECT_ERROR") -> None:
        super().__init__(message, code=code)


class FvgRespectDataError(FvgRespectError):
    """Raised when candle sequence is invalid, mixed, unclosed, or synthetic."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="FVG_RESPECT_DATA_ERROR")


# ── Enumerations ────────────────────────────────────────────────────────────
class FvgRetestStatus(enum.Enum):
    """Status of an FVG retest interaction."""

    PENDING = "PENDING"
    """Touch has occurred, but reaction window has not yet concluded."""

    TOUCH_CONFIRMED = "TOUCH_CONFIRMED"
    """Touch has been confirmed, awaiting reaction window outcome."""

    RESPECT_CONFIRMED = "RESPECT_CONFIRMED"
    """Price respected the zone with a confirmed closed bar in the reaction direction."""

    FAILED_REACTION = "FAILED_REACTION"
    """Reaction window elapsed without price achieving the required reaction threshold."""

    INVALIDATED = "INVALIDATED"
    """Zone was invalidated by an opposing body close before or during reaction confirmation."""

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    """Insufficient candles available to complete retest evaluation."""


class FvgReactionDirection(enum.Enum):
    """Effective reaction direction expected from the FVG zone."""

    BULLISH_SUPPORT = "BULLISH_SUPPORT"
    """Zone is acting as support (expects bullish bounce upwards)."""

    BEARISH_RESISTANCE = "BEARISH_RESISTANCE"
    """Zone is acting as resistance (expects bearish rejection downwards)."""


# ── Configuration Model ─────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class FvgRespectConfig:
    """Configuration parameters for independent retest counting and respect confirmation."""

    min_departure_ticks: Decimal = Decimal("1")
    """Minimum price distance (in ticks) price must depart beyond the zone boundary to allow a new retest."""

    min_bars_outside_zone: int = 1
    """Minimum number of consecutive closed bars outside the zone required to confirm departure."""

    min_bars_between_retests: int = 1
    """Minimum number of bars between the end of a previous retest episode and the start of a new one."""

    reaction_confirmation_bars: int = 3
    """Maximum number of subsequent closed bars in which reaction confirmation is evaluated."""

    min_reaction_ticks: Decimal = Decimal("1")
    """Minimum distance (in ticks) beyond the zone boundary in the reaction direction required for respect."""

    max_reported_retests: int = 4
    """Maximum number of retests to surface in standard summary reports (does not delete full domain history)."""

    tick_size: Decimal = Decimal("0.01")
    """Instrument tick size in price units (default 0.01 for XAUUSD)."""

    def __post_init__(self) -> None:
        if self.min_departure_ticks < Decimal("0"):
            raise FvgRespectError("min_departure_ticks cannot be negative")
        if self.min_bars_outside_zone < 1:
            raise FvgRespectError("min_bars_outside_zone must be at least 1")
        if self.min_bars_between_retests < 0:
            raise FvgRespectError("min_bars_between_retests cannot be negative")
        if self.reaction_confirmation_bars < 1:
            raise FvgRespectError("reaction_confirmation_bars must be at least 1")
        if self.min_reaction_ticks < Decimal("0"):
            raise FvgRespectError("min_reaction_ticks cannot be negative")
        if self.max_reported_retests < 1:
            raise FvgRespectError("max_reported_retests must be at least 1")
        if self.tick_size <= Decimal("0"):
            raise FvgRespectError("tick_size must be strictly positive")


# ── Deterministic Identifier Generator ───────────────────────────────────────
def generate_fvg_retest_event_id(
    fvg_id: str,
    sequence_number: int,
    touch_bar_open_time: datetime,
) -> str:
    """Generate a collision-resistant deterministic ID for an FVG retest event."""
    norm_time = ensure_utc(touch_bar_open_time).strftime("%Y%m%d%H%M%S")
    return f"{fvg_id}:RETEST:{sequence_number}:{norm_time}"


# ── Domain Models ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class FvgReactionRecord:
    """Detailed record of the price reaction following an FVG touch."""

    event_id: str
    fvg_id: str
    retest_sequence_number: int
    reaction_bar_open_time: datetime | None
    reaction_bar_close_time: datetime | None
    reaction_price: Decimal | None
    reaction_distance: Decimal | None
    favorable_distance: Decimal | None
    status: FvgRetestStatus
    is_confirmed: bool


@dataclass(frozen=True, slots=True)
class FvgRetestEvent:
    """An immutable domain record representing an independent price retest of an FVG zone."""

    event_id: str
    fvg_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    price_type: PriceType
    touch_bar_open_time: datetime
    touch_detection_time: datetime
    effective_direction: FvgReactionDirection
    zone_bottom: Decimal
    zone_top: Decimal
    touch_price: Decimal
    penetration_depth: Decimal
    penetration_percentage: Decimal
    retest_sequence_number: int
    reaction_confirmation_time: datetime | None
    reaction_distance: Decimal | None
    status: FvgRetestStatus
    status_reason: str
    lifecycle_phase: str = "NORMAL"
    favorable_excursion: Decimal | None = None
    reaction_record: FvgReactionRecord | None = None

    def __post_init__(self) -> None:
        if self.zone_top <= self.zone_bottom:
            raise FvgRespectError(
                f"zone_top ({self.zone_top}) must be strictly greater than zone_bottom ({self.zone_bottom})"
            )
        if self.penetration_depth < Decimal("0"):
            raise FvgRespectError("penetration_depth cannot be negative")
        if self.penetration_percentage < Decimal("0"):
            raise FvgRespectError("penetration_percentage cannot be negative")
        if self.retest_sequence_number < 1:
            raise FvgRespectError("retest_sequence_number must be at least 1 (1-indexed)")

    @property
    def is_confirmed_respect(self) -> bool:
        return self.status == FvgRetestStatus.RESPECT_CONFIRMED

    @property
    def is_failed(self) -> bool:
        return self.status == FvgRetestStatus.FAILED_REACTION

    @property
    def is_pending(self) -> bool:
        return self.status == FvgRetestStatus.PENDING

    @property
    def is_invalidated(self) -> bool:
        return self.status == FvgRetestStatus.INVALIDATED


@dataclass(frozen=True, slots=True)
class FvgRespectSummary:
    """Aggregated analytical summary of all independent retests and respect events for an FVG."""

    fvg_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    price_type: PriceType
    timeframe: Timeframe
    fvg_direction: FvgDirection
    effective_direction: FvgReactionDirection
    formation_time: datetime
    inversion_confirmed_at: datetime | None
    zone_bottom: Decimal
    zone_top: Decimal
    zone_width: Decimal
    total_touch_count: int
    confirmed_respect_count: int
    failed_reaction_count: int
    pending_reaction_count: int
    consecutive_respect_count: int
    maximum_respect_streak: int
    last_touch_time: datetime | None
    last_respect_time: datetime | None
    final_status: FvgRetestStatus
    total_retest_count: int
    reported_retest_count: int
    remaining_retests: int
    retest_events: tuple[FvgRetestEvent, ...]

    @property
    def total_touches(self) -> int:
        """Alias matching Stage 9 metric name."""
        return self.total_touch_count

    @property
    def total_confirmed_respects(self) -> int:
        """Alias matching Stage 9 metric name."""
        return self.confirmed_respect_count

    @property
    def failed_reactions(self) -> int:
        """Alias matching Stage 9 metric name."""
        return self.failed_reaction_count

    @property
    def pending_reactions(self) -> int:
        """Alias matching Stage 9 metric name."""
        return self.pending_reaction_count

    @property
    def reported_retest_events(self) -> tuple[FvgRetestEvent, ...]:
        """Slices retest events up to reported limit (zero domain events are deleted)."""
        return self.retest_events[: self.reported_retest_count]

    @property
    def normal_retest_events(self) -> tuple[FvgRetestEvent, ...]:
        """Events that occurred during the pre-inversion normal FVG lifecycle phase."""
        return tuple(e for e in self.retest_events if e.lifecycle_phase == "NORMAL")

    @property
    def inverted_retest_events(self) -> tuple[FvgRetestEvent, ...]:
        """Events that occurred after confirmed inversion (Inversion FVG phase)."""
        return tuple(e for e in self.retest_events if e.lifecycle_phase == "INVERTED")

    @property
    def invalidated_retest_count(self) -> int:
        """Count of independent retest episodes that concluded with INVALIDATED status."""
        return sum(1 for e in self.retest_events if e.status == FvgRetestStatus.INVALIDATED)

    @property
    def normal_invalidated_retest_count(self) -> int:
        """Count of retest episodes in NORMAL phase that concluded with INVALIDATED status."""
        return sum(
            1
            for e in self.retest_events
            if e.status == FvgRetestStatus.INVALIDATED and e.lifecycle_phase == "NORMAL"
        )

    @property
    def inverted_invalidated_retest_count(self) -> int:
        """Count of retest episodes in INVERTED phase that concluded with INVALIDATED status."""
        return sum(
            1
            for e in self.retest_events
            if e.status == FvgRetestStatus.INVALIDATED and e.lifecycle_phase == "INVERTED"
        )

    @property
    def is_inverted(self) -> bool:
        """Return True if this FVG transitioned into a confirmed Inversion FVG (IFVG)."""
        return self.inversion_confirmed_at is not None

    @property
    def is_normal_invalidated(self) -> bool:
        """Return True if this FVG had a retest episode invalidated in its normal phase."""
        return self.normal_invalidated_retest_count > 0


# ── Candle Sequence Validation ──────────────────────────────────────────────
def _validate_respect_candles(
    candles: Sequence[Candle],
    *,
    pit_policy: PitPolicy = PitPolicy.STRICT_PIT,
) -> None:
    """Validate homogeneity, price type consistency, closed bar, and synthetic status."""
    if not candles:
        return

    first = candles[0]
    for c in candles:
        if (
            c.instrument_id != first.instrument_id
            or c.source_id != first.source_id
            or c.price_type != first.price_type
            or c.timeframe != first.timeframe
        ):
            raise FvgRespectDataError(
                f"Heterogeneous candle sequence detected: {c.instrument_id}/{c.price_type}/{c.timeframe} "
                f"vs {first.instrument_id}/{first.price_type}/{first.timeframe}"
            )
        if not c.is_closed:
            raise FvgRespectDataError(
                f"Partial unclosed candle at {c.open_time} rejected in Point-in-Time FVG respect analysis"
            )
        if c.is_synthetic:
            raise FvgRespectDataError(
                f"Synthetic candle at {c.open_time} rejected under {pit_policy.value}"
            )


# ── FVG Respect Evaluation Engine ───────────────────────────────────────────
def evaluate_fvg_respect(
    fvg: FvgZone,
    subsequent_candles: Sequence[Candle],
    *,
    config: FvgRespectConfig | None = None,
    as_of_utc: datetime | None = None,
    pit_policy: PitPolicy = PitPolicy.STRICT_PIT,
) -> FvgRespectSummary:
    """Evaluate all independent retests, departures, and respect confirmations for an FVG.

    Lifecycle and Retest Rules:
        1. **Post-Formation Only** — No touches or retests are counted before or at FVG formation.
        2. **Single Continuous Touch Episodes** — Consecutive bars touching or inside the zone are
           aggregated into a single independent retest event.
        3. **Departure Requirement** — A new retest cannot start until price departs the zone by at least
           ``min_bars_outside_zone`` closing at or beyond ``min_departure_ticks * tick_size``.
        4. **Respect Confirmation** — Evaluated in the subsequent ``reaction_confirmation_bars`` after the
           touch bar. Requires a closed bar beyond the zone boundary in the effective reaction direction
           by at least ``min_reaction_ticks * tick_size`` without a prior invalidating body close.
        5. **Normal vs IFVG Separation** — Pre-inversion touches are evaluated according to the original
           role. Post-inversion touches are evaluated according to the inverted role. Historical events
           are never retroactively modified upon later inversion.
        6. **Point-in-Time Safety** — If ``as_of_utc`` is specified, candles closing after ``as_of_utc``
           are masked, and reactions in-flight at ``as_of_utc`` remain ``PENDING``.

    Args:
        fvg: The target FvgZone to evaluate.
        subsequent_candles: Candles occurring at or after FVG formation.
        config: Evaluation configuration (uses defaults if None).
        as_of_utc: Optional decision-time ceiling (UTC).
        pit_policy: Point-in-Time enforcement policy.

    Returns:
        FvgRespectSummary containing complete retest event history and metrics.
    """
    cfg = config or FvgRespectConfig()
    _validate_respect_candles(subsequent_candles, pit_policy=pit_policy)

    norm_as_of = ensure_utc(as_of_utc) if as_of_utc is not None else None

    # Filter candles strictly to post-formation and within as_of boundary
    eval_candles = [
        c
        for c in subsequent_candles
        if c.open_time >= fvg.formation_time and (norm_as_of is None or c.close_time <= norm_as_of)
    ]

    default_eff_dir = (
        FvgReactionDirection.BULLISH_SUPPORT
        if fvg.is_bullish
        else FvgReactionDirection.BEARISH_RESISTANCE
    )

    if not eval_candles:
        return FvgRespectSummary(
            fvg_id=fvg.id,
            instrument_id=fvg.instrument_id,
            source_id=fvg.source_id,
            price_type=fvg.price_type,
            timeframe=fvg.timeframe,
            fvg_direction=fvg.direction,
            effective_direction=default_eff_dir,
            formation_time=fvg.formation_time,
            inversion_confirmed_at=None,
            zone_bottom=fvg.bottom_price,
            zone_top=fvg.top_price,
            zone_width=fvg.width,
            total_touch_count=0,
            confirmed_respect_count=0,
            failed_reaction_count=0,
            pending_reaction_count=0,
            consecutive_respect_count=0,
            maximum_respect_streak=0,
            last_touch_time=None,
            last_respect_time=None,
            final_status=FvgRetestStatus.PENDING,
            total_retest_count=0,
            reported_retest_count=0,
            remaining_retests=0,
            retest_events=(),
        )

    # Track FVG lifecycle events to determine invalidation and inversion timings
    lifecycle_events = track_fvg_lifecycle(fvg, eval_candles, as_of_utc=norm_as_of)
    lifecycle_map = {e.bar_open_time: e for e in lifecycle_events}

    # Find earliest invalidation and inversion timestamps within available scope
    first_invalidation_open: datetime | None = None
    first_inversion_close: datetime | None = None
    for ev in lifecycle_events:
        if (
            ev.validity_state in (FvgValidityState.INVALIDATED, FvgValidityState.INVERTED)
            and first_invalidation_open is None
            and ev.invalidated_by_candle_open is not None
        ):
            first_invalidation_open = ev.invalidated_by_candle_open
        if (
            ev.validity_state == FvgValidityState.INVERTED
            and first_inversion_close is None
            and ev.inversion_confirmed_at is not None
        ):
            first_inversion_close = ev.inversion_confirmed_at

    departure_dist = cfg.min_departure_ticks * cfg.tick_size
    reaction_dist = cfg.min_reaction_ticks * cfg.tick_size

    events: list[FvgRetestEvent] = []
    in_episode = False
    has_departed = True  # Initially outside zone upon formation
    bars_outside_count = 0
    retest_seq = 0
    last_touch_idx = -999

    n_bars = len(eval_candles)
    i = 0
    while i < n_bars:
        c = eval_candles[i]
        cur_le: FvgLifecycleEvent | None = lifecycle_map.get(c.open_time)

        # ── 1. Determine Effective Role and Lifecycle Phase ───────────────────
        is_inverted = (
            first_inversion_close is not None and c.open_time >= first_inversion_close
        ) or (cur_le is not None and cur_le.validity_state == FvgValidityState.INVERTED)

        lifecycle_phase = "INVERTED" if is_inverted else "NORMAL"

        if is_inverted:
            # Bullish becomes resistance; Bearish becomes support
            eff_dir = (
                FvgReactionDirection.BEARISH_RESISTANCE
                if fvg.is_bullish
                else FvgReactionDirection.BULLISH_SUPPORT
            )
        else:
            # Normal Bullish is support; Normal Bearish is resistance
            eff_dir = (
                FvgReactionDirection.BULLISH_SUPPORT
                if fvg.is_bullish
                else FvgReactionDirection.BEARISH_RESISTANCE
            )

        # Check if zone is invalidated without inversion
        is_invalidated_normal = (
            not is_inverted
            and first_invalidation_open is not None
            and c.open_time > first_invalidation_open
        )

        # ── 2. Check Zone Touch / Overlap ─────────────────────────────────────
        # Touch occurs if price range intersects [fvg.bottom_price, fvg.top_price]
        is_touch = (c.low <= fvg.top_price) and (c.high >= fvg.bottom_price)

        if is_touch:
            # If zone was already invalidated in normal phase, reject new normal retests
            if is_invalidated_normal and not in_episode:
                i += 1
                continue

            if not in_episode:
                # Can only start a new retest if meaningful departure occurred
                if has_departed and (i - last_touch_idx >= cfg.min_bars_between_retests):
                    retest_seq += 1
                    in_episode = True
                    has_departed = False
                    bars_outside_count = 0
                    last_touch_idx = i

                    # Penetration calculation
                    if eff_dir == FvgReactionDirection.BULLISH_SUPPORT:
                        pen_depth = max(Decimal(0), fvg.top_price - c.low)
                        touch_p = c.low
                    else:
                        pen_depth = max(Decimal(0), c.high - fvg.bottom_price)
                        touch_p = c.high

                    pen_pct = (pen_depth / fvg.width) * Decimal(100)
                    evt_id = generate_fvg_retest_event_id(fvg.id, retest_seq, c.open_time)

                    # ── 3. Reaction Window Evaluation ─────────────────────────
                    # Touch bar itself does not evaluate reaction (no intrabar guessing)
                    # Check invalidation on the touch bar body close itself
                    touch_bar_invalidated = False
                    if eff_dir == FvgReactionDirection.BULLISH_SUPPORT:
                        if c.close < fvg.bottom_price:
                            touch_bar_invalidated = True
                    else:
                        if c.close > fvg.top_price:
                            touch_bar_invalidated = True

                    event_status = FvgRetestStatus.PENDING
                    status_reason = "Reaction evaluation in progress"
                    reaction_time: datetime | None = None
                    reaction_distance: Decimal | None = None
                    reaction_price: Decimal | None = None
                    reaction_bar_open: datetime | None = None
                    reaction_bar_close: datetime | None = None
                    is_confirmed = False

                    # Excursion tracking across reaction window
                    window_highs: list[Decimal] = []
                    window_lows: list[Decimal] = []

                    if touch_bar_invalidated:
                        event_status = FvgRetestStatus.INVALIDATED
                        status_reason = "Zone invalidated by body close on touch bar"
                    else:
                        # Evaluate up to reaction_confirmation_bars subsequent bars
                        bars_evaluated = 0
                        for offset in range(1, cfg.reaction_confirmation_bars + 1):
                            target_idx = i + offset
                            if target_idx >= n_bars:
                                break  # Reached end of available candles / as_of ceiling

                            rc = eval_candles[target_idx]
                            bars_evaluated += 1
                            window_highs.append(rc.high)
                            window_lows.append(rc.low)

                            # First check invalidation by close during window
                            if eff_dir == FvgReactionDirection.BULLISH_SUPPORT:
                                if rc.close < fvg.bottom_price:
                                    event_status = FvgRetestStatus.INVALIDATED
                                    status_reason = f"Zone invalidated by body close at bar {rc.open_time.isoformat()}"
                                    break
                                # Check respect confirmation
                                if rc.close >= fvg.top_price + reaction_dist:
                                    event_status = FvgRetestStatus.RESPECT_CONFIRMED
                                    reaction_time = rc.close_time
                                    reaction_distance = rc.close - fvg.top_price
                                    reaction_price = rc.close
                                    reaction_bar_open = rc.open_time
                                    reaction_bar_close = rc.close_time
                                    is_confirmed = True
                                    status_reason = (
                                        f"Respect confirmed at bar {rc.open_time.isoformat()} "
                                        f"with reaction distance {reaction_distance}"
                                    )
                                    break
                            else:
                                if rc.close > fvg.top_price:
                                    event_status = FvgRetestStatus.INVALIDATED
                                    status_reason = f"Zone invalidated by body close at bar {rc.open_time.isoformat()}"
                                    break
                                # Check respect confirmation
                                if rc.close <= fvg.bottom_price - reaction_dist:
                                    event_status = FvgRetestStatus.RESPECT_CONFIRMED
                                    reaction_time = rc.close_time
                                    reaction_distance = fvg.bottom_price - rc.close
                                    reaction_price = rc.close
                                    reaction_bar_open = rc.open_time
                                    reaction_bar_close = rc.close_time
                                    is_confirmed = True
                                    status_reason = (
                                        f"Respect confirmed at bar {rc.open_time.isoformat()} "
                                        f"with reaction distance {reaction_distance}"
                                    )
                                    break

                        if event_status == FvgRetestStatus.PENDING:
                            if bars_evaluated >= cfg.reaction_confirmation_bars:
                                event_status = FvgRetestStatus.FAILED_REACTION
                                status_reason = (
                                    f"Reaction failed: no bar confirmed reaction within "
                                    f"{cfg.reaction_confirmation_bars} bars"
                                )
                            else:
                                status_reason = (
                                    f"Reaction pending ({bars_evaluated}/{cfg.reaction_confirmation_bars} "
                                    f"bars evaluated as of cutoff)"
                                )

                    # Compute favorable excursion
                    favorable_dist: Decimal | None = None
                    if window_highs and eff_dir == FvgReactionDirection.BULLISH_SUPPORT:
                        favorable_dist = max(Decimal(0), max(window_highs) - fvg.top_price)
                    elif window_lows and eff_dir == FvgReactionDirection.BEARISH_RESISTANCE:
                        favorable_dist = max(Decimal(0), fvg.bottom_price - min(window_lows))

                    reaction_rec = FvgReactionRecord(
                        event_id=evt_id,
                        fvg_id=fvg.id,
                        retest_sequence_number=retest_seq,
                        reaction_bar_open_time=reaction_bar_open,
                        reaction_bar_close_time=reaction_bar_close,
                        reaction_price=reaction_price,
                        reaction_distance=reaction_distance,
                        favorable_distance=favorable_dist,
                        status=event_status,
                        is_confirmed=is_confirmed,
                    )

                    event = FvgRetestEvent(
                        event_id=evt_id,
                        fvg_id=fvg.id,
                        instrument_id=fvg.instrument_id,
                        source_id=fvg.source_id,
                        timeframe=fvg.timeframe,
                        price_type=fvg.price_type,
                        touch_bar_open_time=c.open_time,
                        touch_detection_time=c.close_time,
                        effective_direction=eff_dir,
                        zone_bottom=fvg.bottom_price,
                        zone_top=fvg.top_price,
                        touch_price=touch_p,
                        penetration_depth=pen_depth,
                        penetration_percentage=pen_pct,
                        retest_sequence_number=retest_seq,
                        reaction_confirmation_time=reaction_time,
                        reaction_distance=reaction_distance,
                        status=event_status,
                        status_reason=status_reason,
                        lifecycle_phase=lifecycle_phase,
                        favorable_excursion=favorable_dist,
                        reaction_record=reaction_rec,
                    )
                    events.append(event)
            else:
                # Existing continuous touch episode: update deepest penetration if applicable
                last_touch_idx = i
                if events:
                    cur_event = events[-1]
                    if eff_dir == FvgReactionDirection.BULLISH_SUPPORT:
                        new_pen = max(Decimal(0), fvg.top_price - c.low)
                        if new_pen > cur_event.penetration_depth:
                            events[-1] = FvgRetestEvent(
                                event_id=cur_event.event_id,
                                fvg_id=cur_event.fvg_id,
                                instrument_id=cur_event.instrument_id,
                                source_id=cur_event.source_id,
                                timeframe=cur_event.timeframe,
                                price_type=cur_event.price_type,
                                touch_bar_open_time=cur_event.touch_bar_open_time,
                                touch_detection_time=cur_event.touch_detection_time,
                                effective_direction=cur_event.effective_direction,
                                zone_bottom=cur_event.zone_bottom,
                                zone_top=cur_event.zone_top,
                                touch_price=c.low,
                                penetration_depth=new_pen,
                                penetration_percentage=(new_pen / fvg.width) * Decimal(100),
                                retest_sequence_number=cur_event.retest_sequence_number,
                                reaction_confirmation_time=cur_event.reaction_confirmation_time,
                                reaction_distance=cur_event.reaction_distance,
                                status=cur_event.status,
                                status_reason=cur_event.status_reason,
                                lifecycle_phase=cur_event.lifecycle_phase,
                                favorable_excursion=cur_event.favorable_excursion,
                                reaction_record=cur_event.reaction_record,
                            )
                    else:
                        new_pen = max(Decimal(0), c.high - fvg.bottom_price)
                        if new_pen > cur_event.penetration_depth:
                            events[-1] = FvgRetestEvent(
                                event_id=cur_event.event_id,
                                fvg_id=cur_event.fvg_id,
                                instrument_id=cur_event.instrument_id,
                                source_id=cur_event.source_id,
                                timeframe=cur_event.timeframe,
                                price_type=cur_event.price_type,
                                touch_bar_open_time=cur_event.touch_bar_open_time,
                                touch_detection_time=cur_event.touch_detection_time,
                                effective_direction=cur_event.effective_direction,
                                zone_bottom=cur_event.zone_bottom,
                                zone_top=cur_event.zone_top,
                                touch_price=c.high,
                                penetration_depth=new_pen,
                                penetration_percentage=(new_pen / fvg.width) * Decimal(100),
                                retest_sequence_number=cur_event.retest_sequence_number,
                                reaction_confirmation_time=cur_event.reaction_confirmation_time,
                                reaction_distance=cur_event.reaction_distance,
                                status=cur_event.status,
                                status_reason=cur_event.status_reason,
                                lifecycle_phase=cur_event.lifecycle_phase,
                                favorable_excursion=cur_event.favorable_excursion,
                                reaction_record=cur_event.reaction_record,
                            )

        else:
            # ── 4. Departure Evaluation (Candle does not touch zone) ───────────
            is_departed_candle = False
            if eff_dir == FvgReactionDirection.BULLISH_SUPPORT:
                # Must be completely above zone and close >= top + departure_dist
                if (c.low > fvg.top_price) and (c.close >= fvg.top_price + departure_dist):
                    is_departed_candle = True
            else:
                # Must be completely below zone and close <= bottom - departure_dist
                if (c.high < fvg.bottom_price) and (c.close <= fvg.bottom_price - departure_dist):
                    is_departed_candle = True

            if is_departed_candle:
                bars_outside_count += 1
                if bars_outside_count >= cfg.min_bars_outside_zone:
                    has_departed = True
                    in_episode = False
            else:
                # Outside zone price boundary, but not satisfying departure threshold
                # Does not increment bars_outside_count toward departure
                pass

        i += 1

    # ── 5. Aggregate Summary Statistics ───────────────────────────────────────
    total_touch_count = len(events)
    confirmed_respect_count = sum(
        1 for e in events if e.status == FvgRetestStatus.RESPECT_CONFIRMED
    )
    failed_reaction_count = sum(1 for e in events if e.status == FvgRetestStatus.FAILED_REACTION)
    pending_reaction_count = sum(1 for e in events if e.status == FvgRetestStatus.PENDING)

    # Calculate consecutive respects and maximum streak
    consecutive_respect_count = 0
    max_streak = 0
    current_streak = 0
    for e in events:
        if e.status == FvgRetestStatus.RESPECT_CONFIRMED:
            current_streak += 1
            if current_streak > max_streak:
                max_streak = current_streak
        else:
            current_streak = 0

    # Current streak from end of sequence (ignoring pending)
    for e in reversed(events):
        if e.status == FvgRetestStatus.PENDING:
            continue
        if e.status == FvgRetestStatus.RESPECT_CONFIRMED:
            consecutive_respect_count += 1
        else:
            break

    last_touch_time = events[-1].touch_bar_open_time if events else None
    respect_events = [e for e in events if e.status == FvgRetestStatus.RESPECT_CONFIRMED]
    last_respect_time = respect_events[-1].reaction_confirmation_time if respect_events else None

    final_status = events[-1].status if events else FvgRetestStatus.PENDING
    reported_retest_count = min(total_touch_count, cfg.max_reported_retests)
    remaining_retests = total_touch_count - reported_retest_count

    # Determine final effective direction for summary
    latest_eff_dir = events[-1].effective_direction if events else default_eff_dir

    return FvgRespectSummary(
        fvg_id=fvg.id,
        instrument_id=fvg.instrument_id,
        source_id=fvg.source_id,
        price_type=fvg.price_type,
        timeframe=fvg.timeframe,
        fvg_direction=fvg.direction,
        effective_direction=latest_eff_dir,
        formation_time=fvg.formation_time,
        inversion_confirmed_at=first_inversion_close,
        zone_bottom=fvg.bottom_price,
        zone_top=fvg.top_price,
        zone_width=fvg.width,
        total_touch_count=total_touch_count,
        confirmed_respect_count=confirmed_respect_count,
        failed_reaction_count=failed_reaction_count,
        pending_reaction_count=pending_reaction_count,
        consecutive_respect_count=consecutive_respect_count,
        maximum_respect_streak=max_streak,
        last_touch_time=last_touch_time,
        last_respect_time=last_respect_time,
        final_status=final_status,
        total_retest_count=total_touch_count,
        reported_retest_count=reported_retest_count,
        remaining_retests=remaining_retests,
        retest_events=tuple(events),
    )


# ── Bulk Evaluation Utility ──────────────────────────────────────────────────
def evaluate_all_fvgs_respect(
    fvgs: Sequence[FvgZone],
    candles: Sequence[Candle],
    *,
    config: FvgRespectConfig | None = None,
    as_of_utc: datetime | None = None,
    pit_policy: PitPolicy = PitPolicy.STRICT_PIT,
) -> list[FvgRespectSummary]:
    """Evaluate respect, retests, and reactions for a collection of FVG zones deterministically.

    Preserves full domain pool without mutating input parameters.
    """
    if not fvgs:
        return []

    summaries: list[FvgRespectSummary] = []
    for fvg in fvgs:
        summary = evaluate_fvg_respect(
            fvg=fvg,
            subsequent_candles=candles,
            config=config,
            as_of_utc=as_of_utc,
            pit_policy=pit_policy,
        )
        summaries.append(summary)

    return summaries


# ── Point-in-Time Snapshot Filter ────────────────────────────────────────────
def filter_fvg_retest_events_as_of(
    events: Sequence[FvgRetestEvent],
    as_of_utc: datetime,
) -> list[FvgRetestEvent]:
    """Filter pre-computed retest events to a historical point-in-time timestamp.

    Guarantees:
        - Retest events detected after ``as_of_utc`` are completely eliminated.
        - Respect confirmations occurring after ``as_of_utc`` are reverted to ``PENDING``
          with reaction distance and reaction timestamp masked to None.
    """
    norm_as_of = ensure_utc(as_of_utc)
    filtered: list[FvgRetestEvent] = []

    for e in events:
        # Event must have been detected on or before as_of_utc
        if e.touch_detection_time > norm_as_of:
            continue

        # If reaction was confirmed after as_of_utc, mask the future confirmation
        if e.reaction_confirmation_time is not None and e.reaction_confirmation_time > norm_as_of:
            masked_record = None
            if e.reaction_record is not None:
                masked_record = FvgReactionRecord(
                    event_id=e.reaction_record.event_id,
                    fvg_id=e.reaction_record.fvg_id,
                    retest_sequence_number=e.reaction_record.retest_sequence_number,
                    reaction_bar_open_time=None,
                    reaction_bar_close_time=None,
                    reaction_price=None,
                    reaction_distance=None,
                    favorable_distance=None,
                    status=FvgRetestStatus.PENDING,
                    is_confirmed=False,
                )

            masked_event = FvgRetestEvent(
                event_id=e.event_id,
                fvg_id=e.fvg_id,
                instrument_id=e.instrument_id,
                source_id=e.source_id,
                timeframe=e.timeframe,
                price_type=e.price_type,
                touch_bar_open_time=e.touch_bar_open_time,
                touch_detection_time=e.touch_detection_time,
                effective_direction=e.effective_direction,
                zone_bottom=e.zone_bottom,
                zone_top=e.zone_top,
                touch_price=e.touch_price,
                penetration_depth=e.penetration_depth,
                penetration_percentage=e.penetration_percentage,
                retest_sequence_number=e.retest_sequence_number,
                reaction_confirmation_time=None,
                reaction_distance=None,
                status=FvgRetestStatus.PENDING,
                status_reason="Reaction pending as of historical query point-in-time",
                lifecycle_phase=e.lifecycle_phase,
                favorable_excursion=None,
                reaction_record=masked_record,
            )
            filtered.append(masked_event)
        else:
            filtered.append(e)

    return filtered
