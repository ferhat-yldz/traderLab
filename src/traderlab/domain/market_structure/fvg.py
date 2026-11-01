"""Deterministic Fair Value Gap (FVG) and Inversion FVG (IFVG) domain calculation engine.

Financial Theory & Architecture:
    1. **Strict Pure Domain Logic** — Operates exclusively on immutable domain ``Candle``
       sequences. Zero dependencies on infrastructure (PostgreSQL, filesystem) or UI (Streamlit, Plotly).
    2. **Point-in-Time Correctness** — An FVG is formed ONLY upon the complete close of the
       third bar (`candle_3.close_time`). Formation metrics (`top_price`, `bottom_price`, `ce_price`,
       `width`, `formation_time`) are immutable and NEVER altered by future bars.
    3. **Separation of Detection and Lifecycle** — `detect_fair_value_gaps` identifies fresh
       imbalances at formation. `track_fvg_lifecycle` evaluates subsequent price interaction
       (mitigation, partial/full fill, invalidation, and inversion) chronologically.
    4. **Fill vs Validity Independence** — A deep wick piercing through an entire zone reaches
       `FULLY_FILLED`, but DOES NOT invalidate the zone unless an eligible bar body closes beyond
       the opposite boundary (`close < bottom_price` for Bullish, `close > top_price` for Bearish).
    5. **Collision-Resistant Deterministic Identity** — Every `FvgZone` is assigned a unique ID
       derived deterministically from its instrument, data source, price type, timeframe, formation
       timestamp, direction, and data revision.
    6. **Session & Calendar Gap Awareness** — Prevents synthetic market-closed gaps (e.g. Friday NY
       close to Sunday NY open) from masquerading as intraday 3-bar displacement imbalances.
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
class FvgError(TraderLabError):
    """Base exception for FVG domain processing errors."""

    def __init__(self, message: str, *, code: str = "FVG_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidCandleSequenceError(FvgError):
    """Raised when an invalid, mixed, non-chronological, or empty candle sequence is provided."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_CANDLE_SEQUENCE")


# ── Enumerations ────────────────────────────────────────────────────────────
class FvgDirection(enum.Enum):
    """Direction of the market imbalance."""

    BULLISH = "BULLISH"
    """Bullish imbalance: candle_3.low > candle_1.high. Zone spans [candle_1.high, candle_3.low]."""

    BEARISH = "BEARISH"
    """Bearish imbalance: candle_3.high < candle_1.low. Zone spans [candle_3.high, candle_1.low]."""


class FvgFillState(enum.Enum):
    """Cumulative fill / mitigation status of the imbalance zone."""

    UNTOUCHED = "UNTOUCHED"
    """Price has not entered the zone since formation."""

    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    """Price wick has entered the zone, but has not reached the 50% CE level."""

    CE_REACHED = "CE_REACHED"
    """Price wick has reached or traversed the 50% Consequent Encroachment (CE) level."""

    FULLY_FILLED = "FULLY_FILLED"
    """Price wick has completely traversed the entire zone to the opposite boundary."""


class FvgValidityState(enum.Enum):
    """Validity state of the Fair Value Gap as an active structural reference."""

    VALID = "VALID"
    """Zone is structurally valid and respected in its original directional role."""

    INVALIDATED = "INVALIDATED"
    """Zone boundary has been violated by a bar closing beyond it."""

    INVERTED = "INVERTED"
    """Zone has transitioned into a confirmed Inversion Fair Value Gap (IFVG)."""


# ── Deterministic Identifier Generator ───────────────────────────────────────
def generate_fvg_id(
    instrument_id: InstrumentId | str,
    source_id: DataSourceId | str,
    price_type: PriceType,
    timeframe: Timeframe,
    formation_time: datetime,
    direction: FvgDirection,
    revision: int = 0,
) -> str:
    """Generate a collision-resistant, deterministic identifier for an FVG zone.

    Format:
        {instrument}:{source}:{price_type}:{timeframe}:{formation_iso}:{direction}:r{revision}
    """
    norm_time = ensure_utc(formation_time).strftime("%Y%m%d%H%M%S")
    return (
        f"{instrument_id}:{source_id}:{price_type.value}:"
        f"{timeframe.name}:{norm_time}:{direction.value}:r{revision}"
    )


# ── Domain Models ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class FvgZone:
    """Immutable domain representation of a Fair Value Gap imbalance zone.

    All price boundaries are exact `Decimal` values. All timestamps are UTC-aware.
    Formation attributes are strictly immutable once established.
    """

    id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    price_type: PriceType
    timeframe: Timeframe
    direction: FvgDirection

    # Price Boundaries
    top_price: Decimal
    """Upper boundary of the gap. For Bullish: candle_3.low. For Bearish: candle_1.low."""

    bottom_price: Decimal
    """Lower boundary of the gap. For Bullish: candle_1.high. For Bearish: candle_3.high."""

    ce_price: Decimal
    """Consequent Encroachment: exact 50% midpoint of the gap (top + bottom) / 2."""

    width: Decimal
    """Absolute price width: top_price - bottom_price."""

    # Anchor Timestamps
    candle_1_open: datetime
    candle_2_open: datetime  # Displacement bar open
    candle_3_open: datetime
    formation_time: datetime  # candle_3.close_time
    computable_at_utc: datetime  # Earliest point-in-time timestamp computable (= formation_time)
    revision: int = 0

    def __post_init__(self) -> None:
        if self.top_price <= self.bottom_price:
            raise FvgError(
                f"Invalid FVG boundaries: top_price ({self.top_price}) must be strictly "
                f"greater than bottom_price ({self.bottom_price})"
            )
        if self.width != (self.top_price - self.bottom_price):
            raise FvgError("width must exactly match (top_price - bottom_price)")
        expected_ce = (self.top_price + self.bottom_price) / Decimal(2)
        if self.ce_price != expected_ce:
            raise FvgError(f"ce_price ({self.ce_price}) must equal exact midpoint ({expected_ce})")

    @property
    def is_bullish(self) -> bool:
        """Return True if this is a Bullish Fair Value Gap."""
        return self.direction == FvgDirection.BULLISH

    @property
    def is_bearish(self) -> bool:
        """Return True if this is a Bearish Fair Value Gap."""
        return self.direction == FvgDirection.BEARISH

    @property
    def range_midpoint(self) -> Decimal:
        """Alias matching Candle.range_midpoint for geometric center."""
        return self.ce_price


@dataclass(frozen=True, slots=True)
class FvgLifecycleEvent:
    """A point-in-time snapshot of an FVG's interaction with a subsequent market bar."""

    fvg_id: str
    bar_open_time: datetime
    bar_close_time: datetime
    event_time_utc: datetime

    fill_state: FvgFillState
    validity_state: FvgValidityState
    fill_ratio: Decimal  # 0.0000 to 1.0000 (clamped)
    extreme_penetration_price: Decimal

    mitigated_by_candle_open: datetime | None = None
    invalidated_by_candle_open: datetime | None = None
    inversion_confirmed_at: datetime | None = None

    @property
    def is_untouched(self) -> bool:
        return self.fill_state == FvgFillState.UNTOUCHED

    @property
    def is_partially_filled(self) -> bool:
        return self.fill_state in (
            FvgFillState.PARTIALLY_FILLED,
            FvgFillState.CE_REACHED,
            FvgFillState.FULLY_FILLED,
        )

    @property
    def is_ce_reached(self) -> bool:
        return self.fill_state in (FvgFillState.CE_REACHED, FvgFillState.FULLY_FILLED)

    @property
    def is_fully_filled(self) -> bool:
        return self.fill_state == FvgFillState.FULLY_FILLED

    @property
    def is_valid(self) -> bool:
        return self.validity_state == FvgValidityState.VALID

    @property
    def is_invalidated(self) -> bool:
        return self.validity_state == FvgValidityState.INVALIDATED

    @property
    def is_inverted(self) -> bool:
        return self.validity_state == FvgValidityState.INVERTED


# ── Sequence Validation Utility ──────────────────────────────────────────────
def _validate_candle_sequence(candles: Sequence[Candle]) -> None:
    """Validate homogeneity, price type validity, and strict chronological ordering."""
    if not candles:
        raise InvalidCandleSequenceError("Candle sequence cannot be empty")

    first = candles[0]
    if first.price_type == PriceType.UNKNOWN:
        raise InvalidCandleSequenceError(
            "Candles with PriceType.UNKNOWN cannot be used for FVG detection"
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
    """Check if the transition between two bars spans a session break (weekend, rollover, holiday).

    Mathematical rules:
    - If `c_curr.open_time == c_prev.close_time`: contiguous bars, no gap.
    - If `timeframe >= Timeframe.D1`: normal weekend gap between Friday close and Monday open
      is a standard multi-day transition in financial markets (delta <= 4 calendar days).
    - If `timeframe < Timeframe.D1` (intraday):
      - A gap > 4 hours spans across a weekend or holiday break.
      - A gap between 1 and 2 hours typically spans a daily settlement rollover (17:00-18:00 NY).
    """
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


# ── FVG Detection Engine ─────────────────────────────────────────────────────
def detect_fair_value_gaps(
    candles: Sequence[Candle],
    *,
    min_width: Decimal | None = None,
    allow_weekend_gap: bool = False,
    allow_rollover_gap: bool = False,
    require_closed: bool = True,
) -> list[FvgZone]:
    """Detect all genuine Fair Value Gap (FVG) imbalances from a sequence of completed candles.

    Imbalance Formula:
        - Bullish: `candle_3.low > candle_1.high`
          Zone = `[candle_1.high, candle_3.low]`
        - Bearish: `candle_3.high < candle_1.low`
          Zone = `[candle_3.high, candle_1.low]`

    Guarantees:
        - Pure Point-in-Time: created_at = candle_3.close_time.
        - Precision: Width and CE calculated with exact Decimal arithmetic.
        - Session Safety: Suppresses artificial gaps bridging across weekends or rollovers.
        - Synthetic Safety: Rejects 3-bar sequences where any bar is a 100% synthetic market-closed bar.

    Args:
        candles: Chronologically sorted sequence of uniform Candle objects.
        min_width: Optional minimum price width threshold (filters out micro-ticks).
        allow_weekend_gap: If False (default), prevents FVGs bridging across weekend closures.
        allow_rollover_gap: If False (default), prevents FVGs bridging across daily rollovers.
        require_closed: If True (default), only completed bars (`is_closed=True`) can form FVGs.

    Returns:
        List of detected `FvgZone` objects in chronological order of formation.
    """
    if len(candles) < 3:
        return []

    # Filter out Sunday single-hour separate daily stub candles (UTC midnight convention)
    # which distort 3-bar daily displacement analysis and produce false-positive FVGs.
    eligible_candles = [
        c
        for c in candles
        if not (
            c.timeframe == Timeframe.D1 and c.open_time.weekday() == 6 and c.open_time.hour == 0
        )
    ]
    if len(eligible_candles) < 3:
        return []

    _validate_candle_sequence(eligible_candles)

    timeframe = eligible_candles[0].timeframe
    detected: list[FvgZone] = []

    for i in range(2, len(eligible_candles)):
        c1 = eligible_candles[i - 2]
        c2 = eligible_candles[i - 1]
        c3 = eligible_candles[i]

        # 1. Closed bar enforcement (Point-in-Time rule)
        if require_closed and (not c1.is_closed or not c2.is_closed or not c3.is_closed):
            continue

        # 2. Synthetic market-closed bar rejection (Real volume/trade required)
        if c1.is_synthetic or c2.is_synthetic or c3.is_synthetic:
            continue

        # 3. Session continuity checks (Suppresses weekend/rollover gap artifacts)
        if _is_calendar_break(
            c1,
            c2,
            timeframe,
            allow_weekend_gap=allow_weekend_gap,
            allow_rollover_gap=allow_rollover_gap,
        ) or _is_calendar_break(
            c2,
            c3,
            timeframe,
            allow_weekend_gap=allow_weekend_gap,
            allow_rollover_gap=allow_rollover_gap,
        ):
            continue

        direction: FvgDirection | None = None
        top_p: Decimal
        bot_p: Decimal

        # 4. Mathematical Imbalance Evaluation
        if c3.low > c1.high:
            direction = FvgDirection.BULLISH
            top_p = c3.low
            bot_p = c1.high
        elif c3.high < c1.low:
            direction = FvgDirection.BEARISH
            top_p = c1.low
            bot_p = c3.high

        if direction is None:
            continue

        width = top_p - bot_p
        if width <= Decimal(0):
            continue

        if min_width is not None and width < min_width:
            continue

        ce_p = (top_p + bot_p) / Decimal(2)
        f_time = c3.close_time
        max_rev = max(c1.revision, c2.revision, c3.revision)

        zone_id = generate_fvg_id(
            instrument_id=c3.instrument_id,
            source_id=c3.source_id,
            price_type=c3.price_type,
            timeframe=c3.timeframe,
            formation_time=f_time,
            direction=direction,
            revision=max_rev,
        )

        detected.append(
            FvgZone(
                id=zone_id,
                instrument_id=c3.instrument_id,
                source_id=c3.source_id,
                price_type=c3.price_type,
                timeframe=c3.timeframe,
                direction=direction,
                top_price=top_p,
                bottom_price=bot_p,
                ce_price=ce_p,
                width=width,
                candle_1_open=c1.open_time,
                candle_2_open=c2.open_time,
                candle_3_open=c3.open_time,
                formation_time=f_time,
                computable_at_utc=f_time,
                revision=max_rev,
            )
        )

    return detected


# ── FVG Lifecycle Tracking Engine ────────────────────────────────────────────
def track_fvg_lifecycle(
    fvg: FvgZone,
    subsequent_candles: Sequence[Candle],
    *,
    as_of_utc: datetime | None = None,
    invalidation_by_close: bool = True,
    confirm_inversion_on_retest: bool = True,
) -> list[FvgLifecycleEvent]:
    """Track the chronological lifecycle evolution (mitigation, fill, invalidation, IFVG) of an FVG.

    Lifecycle Rules:
        - **Monotonic Fill Evolution**: Once touched, fill state never reverts. Tracks deepest
          penetration across all processed subsequent bars.
        - **Wick Mitigation vs Body Invalidation**:
          - Wick into zone -> `PARTIALLY_FILLED`, `CE_REACHED`, or `FULLY_FILLED`.
          - Bar body closing beyond zone -> `INVALIDATED` (Bullish: close < bottom; Bearish: close > top).
          - A wick piercing past the zone reaches `FULLY_FILLED`, but zone remains `VALID` if bar
            closes inside or above the bottom boundary!
        - **Inversion FVG (IFVG) Transition**:
          - Once invalidated, if `confirm_inversion_on_retest=True`, the zone transitions to
            `INVERTED` when price retests the zone from the opposite side and respects it.
          - If `confirm_inversion_on_retest=False`, invalidation immediately marks it `INVERTED`.
        - **Point-in-Time Safeguard**: If `as_of_utc` is supplied, candles closing after `as_of_utc`
          are completely excluded from analysis, eliminating lookahead bias.

    Args:
        fvg: The target FvgZone to track.
        subsequent_candles: Candles occurring after FVG formation.
        as_of_utc: Optional decision-time ceiling (UTC).
        invalidation_by_close: If True (default), invalidation strictly requires a bar close beyond boundary.
        confirm_inversion_on_retest: If True (default), IFVG requires invalidation followed by retest.

    Returns:
        List of `FvgLifecycleEvent` objects recorded for each subsequent bar that interacts with the zone.
    """
    if not subsequent_candles:
        return []

    _validate_candle_sequence(subsequent_candles)

    norm_as_of = ensure_utc(as_of_utc) if as_of_utc is not None else None

    # Filter to strictly post-formation candles
    eval_candles = [
        c
        for c in subsequent_candles
        if c.open_time >= fvg.formation_time and (norm_as_of is None or c.close_time <= norm_as_of)
    ]

    events: list[FvgLifecycleEvent] = []

    # Current running lifecycle state
    current_fill_state = FvgFillState.UNTOUCHED
    current_validity_state = FvgValidityState.VALID
    max_fill_ratio = Decimal(0)
    extreme_price = fvg.top_price if fvg.is_bullish else fvg.bottom_price
    mitigated_at: datetime | None = None
    invalidated_at: datetime | None = None
    inverted_at: datetime | None = None

    for c in eval_candles:
        bar_penetration_ratio = Decimal(0)
        bar_penetration_price = extreme_price

        # ── 1. Fill State Evaluation (Wick-based) ───────────────────────────
        if fvg.is_bullish:
            # Bullish zone [bottom, top]: price returns downwards into the zone
            if c.low < fvg.top_price:
                if mitigated_at is None:
                    mitigated_at = c.open_time

                penetration = fvg.top_price - c.low
                bar_penetration_ratio = min(Decimal(1), max(Decimal(0), penetration / fvg.width))
                bar_penetration_price = c.low

                if c.low <= fvg.bottom_price:
                    current_fill_state = FvgFillState.FULLY_FILLED
                elif c.low <= fvg.ce_price:
                    if current_fill_state != FvgFillState.FULLY_FILLED:
                        current_fill_state = FvgFillState.CE_REACHED
                else:
                    if current_fill_state not in (
                        FvgFillState.CE_REACHED,
                        FvgFillState.FULLY_FILLED,
                    ):
                        current_fill_state = FvgFillState.PARTIALLY_FILLED

                if bar_penetration_ratio > max_fill_ratio:
                    max_fill_ratio = bar_penetration_ratio
                    extreme_price = bar_penetration_price

        else:
            # Bearish zone [bottom, top]: price returns upwards into the zone
            if c.high > fvg.bottom_price:
                if mitigated_at is None:
                    mitigated_at = c.open_time

                penetration = c.high - fvg.bottom_price
                bar_penetration_ratio = min(Decimal(1), max(Decimal(0), penetration / fvg.width))
                bar_penetration_price = c.high

                if c.high >= fvg.top_price:
                    current_fill_state = FvgFillState.FULLY_FILLED
                elif c.high >= fvg.ce_price:
                    if current_fill_state != FvgFillState.FULLY_FILLED:
                        current_fill_state = FvgFillState.CE_REACHED
                else:
                    if current_fill_state not in (
                        FvgFillState.CE_REACHED,
                        FvgFillState.FULLY_FILLED,
                    ):
                        current_fill_state = FvgFillState.PARTIALLY_FILLED

                if bar_penetration_ratio > max_fill_ratio:
                    max_fill_ratio = bar_penetration_ratio
                    extreme_price = bar_penetration_price

        # ── 2. Validity & Invalidation Evaluation (Close-based) ─────────────
        is_breached = False
        if invalidation_by_close:
            is_breached = (fvg.is_bullish and c.close < fvg.bottom_price) or (
                fvg.is_bearish and c.close > fvg.top_price
            )
        else:
            is_breached = (fvg.is_bullish and c.low < fvg.bottom_price) or (
                fvg.is_bearish and c.high > fvg.top_price
            )

        if is_breached and current_validity_state == FvgValidityState.VALID:
            current_validity_state = FvgValidityState.INVALIDATED
            invalidated_at = c.open_time
            if not confirm_inversion_on_retest:
                current_validity_state = FvgValidityState.INVERTED
                inverted_at = c.close_time
        elif (
            current_validity_state == FvgValidityState.INVALIDATED
            and invalidated_at is not None
            and c.open_time > invalidated_at
            and confirm_inversion_on_retest
        ):
            # ── 3. IFVG (Inversion) Confirmation on subsequent bar ───────────
            # Former Bullish (now resistance) or Former Bearish (now support) holds zone:
            is_inverted_retest = (
                fvg.is_bullish and c.high >= fvg.bottom_price and c.close <= fvg.top_price
            ) or (fvg.is_bearish and c.low <= fvg.top_price and c.close >= fvg.bottom_price)

            if is_inverted_retest:
                current_validity_state = FvgValidityState.INVERTED
                inverted_at = c.close_time

        events.append(
            FvgLifecycleEvent(
                fvg_id=fvg.id,
                bar_open_time=c.open_time,
                bar_close_time=c.close_time,
                event_time_utc=c.close_time,
                fill_state=current_fill_state,
                validity_state=current_validity_state,
                fill_ratio=max_fill_ratio,
                extreme_penetration_price=extreme_price,
                mitigated_by_candle_open=mitigated_at,
                invalidated_by_candle_open=invalidated_at,
                inversion_confirmed_at=inverted_at,
            )
        )

    return events
