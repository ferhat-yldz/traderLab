"""Point-in-Time Safe Market Structure Engine.

Provides mathematically rigorous, deterministic, and Point-in-Time compliant
Market Structure identification for ICT/SMC quantitative trading research.

Key architectural and financial principles:
1. **Market Structure States**:
   - `UPTREND`: Higher Highs (HH) and Higher Lows (HL) established; bullish continuation.
   - `DOWNTREND`: Lower Highs (LH) and Lower Lows (LL) established; bearish continuation.
   - `RANGING`: Non-directional consolidation or conflicting structural swings.
   - `POTENTIAL_REVERSAL`: Change of Character (CHoCH) breached the protected level;
     trend change is tentative until separately confirmed.
   - `UNDETERMINED`: Insufficient confirmed swings or uninitialized structure.
2. **Structural Swing Classifications**:
   - `HH` (Higher High): Confirmed high > previous confirmed high (+ threshold).
   - `HL` (Higher Low): Confirmed low > previous confirmed low (+ threshold).
   - `LH` (Lower High): Confirmed high < previous confirmed high (- threshold).
   - `LL` (Lower Low): Confirmed low < previous confirmed low (- threshold).
   - `EQUAL_HIGH` / `EQUAL_LOW`: Price within threshold equality.
3. **Break of Structure (BOS)**:
   - Trend continuation breakout beyond active structural extreme.
   - Bullish BOS: in `UPTREND`, candle close > active structural high + threshold.
   - Bearish BOS: in `DOWNTREND`, candle close < active structural low - threshold.
   - Wick penetration without body close is strictly NOT a BOS.
4. **Change of Character (CHoCH)**:
   - Initial structural break against the existing trend.
   - Bearish CHoCH: in `UPTREND`, candle close < protected swing low - threshold.
   - Bullish CHoCH: in `DOWNTREND`, candle close > protected swing high + threshold.
   - CHoCH transitions structure to `POTENTIAL_REVERSAL`, NOT an auto-confirmed new trend.
5. **MSS Semantics (Market Structure Shift)**:
   - CHoCH events carry an associated `MSS_CANDIDATE` identifier.
   - Final confirmed MSS requires displacement verification (out of scope for Phase 3C).
   - `MSS_CONFIRMED` status is never produced in this phase.
6. **Protected Swing Levels**:
   - In `UPTREND`: Protected Swing Low (safeguarding the bullish impulse leg).
   - In `DOWNTREND`: Protected Swing High (safeguarding the bearish impulse leg).
   - Every structural event explicitly records its active `protected_level_id`.
7. **Point-in-Time Safety**:
   - `structural_level.confirmation_time <= break_bar.open_time`.
   - `detection_time = break_bar.close_time`.
   - Historical queries with `as_of_utc` hide all future events and transitions.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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
    SwingConfirmationStatus,
    SwingPoint,
    SwingType,
    _is_calendar_break,
    _validate_candle_sequence,
)

if TYPE_CHECKING:
    from traderlab.domain.instruments.data_source import DataSource
    from traderlab.domain.instruments.instrument import Instrument


# ── Exceptions ───────────────────────────────────────────────────────────────
class MarketStructureError(TraderLabError):
    """Base exception for Market Structure domain errors."""

    def __init__(self, message: str, *, code: str = "MARKET_STRUCTURE_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidMarketStructureParameterError(MarketStructureError):
    """Raised when market structure configuration parameters are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_MARKET_STRUCTURE_PARAMETER")


class MarketStructureDataError(MarketStructureError):
    """Raised when candle sequences are mixed, non-chronological, unclosed, or have data gaps."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="MARKET_STRUCTURE_DATA_ERROR")


# ── Enumerations ────────────────────────────────────────────────────────────
class MarketTrend(enum.Enum):
    """Market structure trend state."""

    UPTREND = "UPTREND"
    """Higher highs and higher lows established; bullish continuation."""

    DOWNTREND = "DOWNTREND"
    """Lower highs and lower lows established; bearish continuation."""

    RANGING = "RANGING"
    """Non-directional consolidation or conflicting structural swings."""

    POTENTIAL_REVERSAL = "POTENTIAL_REVERSAL"
    """A Change of Character (CHoCH) breached the protected level, awaiting trend confirmation."""

    UNDETERMINED = "UNDETERMINED"
    """Insufficient confirmed swings or uninitialized market structure."""


class StructureBreakType(enum.Enum):
    """Classification of a structural breakout event."""

    BOS = "BOS"
    """Break of Structure — trend continuation breakout beyond active structural extreme."""

    CHOCH = "CHOCH"
    """Change of Character — first structural breakout against trend beyond protected level."""

    MSS_CANDIDATE = "MSS_CANDIDATE"
    """Market Structure Shift candidate — exploratory state pending displacement confirmation."""


class StructureDirection(enum.Enum):
    """Directional orientation of the structural break event."""

    BULLISH = "BULLISH"
    """Bullish structure break (breakout above resistance / structural high)."""

    BEARISH = "BEARISH"
    """Bearish structure break (breakout below support / structural low)."""


class StructuralSwingType(enum.Enum):
    """Classification of an individual swing point relative to preceding swings of the same type."""

    HH = "HH"
    """Higher High."""

    HL = "HL"
    """Higher Low."""

    LH = "LH"
    """Lower High."""

    LL = "LL"
    """Lower Low."""

    EQUAL_HIGH = "EQUAL_HIGH"
    """Equal High within threshold."""

    EQUAL_LOW = "EQUAL_LOW"
    """Equal Low within threshold."""

    UNKNOWN = "UNKNOWN"
    """Initial swing point or unclassified."""


# ── Deterministic Identifier Generators ──────────────────────────────────────
def generate_structure_break_id(
    instrument_id: InstrumentId | str,
    source_id: DataSourceId | str,
    price_type: PriceType | str,
    timeframe: Timeframe | str,
    break_type: StructureBreakType | str,
    structure_direction: StructureDirection | str,
    source_swing_id: str,
    break_bar_open_time: datetime,
) -> str:
    """Generate a deterministic, globally unique string identifier for a structural break event.

    Distinguishes:
    - Instrument and data source.
    - Price series (BID vs ASK).
    - Timeframe (e.g. D1 vs H4 vs H2).
    - Break type (BOS vs CHOCH vs MSS_CANDIDATE).
    - Direction (BULLISH vs BEARISH).
    - Source swing ID that was broken.
    - Break bar opening timestamp.
    """
    inst_str = str(instrument_id)
    src_str = str(source_id)
    pt_str = price_type.value if isinstance(price_type, PriceType) else str(price_type)
    tf_str = timeframe.name if isinstance(timeframe, Timeframe) else str(timeframe)
    bt_str = break_type.value if isinstance(break_type, StructureBreakType) else str(break_type)
    sd_str = (
        structure_direction.value
        if isinstance(structure_direction, StructureDirection)
        else str(structure_direction)
    )
    t_str = break_bar_open_time.strftime("%Y%m%dT%H%M%SZ")
    return f"{inst_str}_{src_str}_{pt_str}_{tf_str}_{bt_str}_{sd_str}_{t_str}_{source_swing_id}"


def generate_mss_candidate_id(choch_event_id: str) -> str:
    """Generate a deterministic identifier for an MSS candidate derived from a CHoCH event."""
    return f"MSS_CANDIDATE_{choch_event_id}"


# ── Domain Models ────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class StructuralSwingPoint:
    """Immutable representation of a confirmed SwingPoint classified by its structural role."""

    swing_point: SwingPoint
    structural_type: StructuralSwingType
    previous_swing_id: str | None = None
    price_delta: Decimal | None = None

    @property
    def id(self) -> str:
        return self.swing_point.id

    @property
    def pivot_price(self) -> Decimal:
        return self.swing_point.pivot_price

    @property
    def pivot_time(self) -> datetime:
        return self.swing_point.pivot_time

    @property
    def confirmation_time(self) -> datetime | None:
        return self.swing_point.confirmation_time

    @property
    def is_swing_high(self) -> bool:
        return self.swing_point.is_swing_high

    @property
    def is_swing_low(self) -> bool:
        return self.swing_point.is_swing_low


@dataclass(frozen=True, slots=True)
class ProtectedSwingReference:
    """Immutable reference to an active protected swing level."""

    swing_id: str
    swing_type: SwingType
    price: Decimal
    pivot_time: datetime
    confirmation_time: datetime


@dataclass(frozen=True, slots=True)
class StructureBreakEvent:
    """Immutable domain event representing a confirmed structural breakout (BOS, CHoCH, MSS Candidate).

    Guarantees:
    - **Point-in-Time Safety**: Break can only be detected when candle closes.
      `detection_time == break_bar_close_time`.
    - **Protected Level Accountability**: Every event links the active protected level.
    - **MSS Separation**: CHoCH carries linked `mss_candidate_id` without double counting.
    """

    event_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    price_type: PriceType
    structure_direction: StructureDirection
    break_type: StructureBreakType
    source_swing_id: str
    protected_level_id: str | None
    reference_level_price: Decimal
    break_price: Decimal
    break_bar_open_time: datetime
    break_bar_close_time: datetime
    detection_time: datetime
    trend_before: MarketTrend
    trend_after: MarketTrend
    confirmation_status: str  # "CONFIRMED" or "CANDIDATE"
    mss_candidate_id: str | None = None
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.detection_time != self.break_bar_close_time:
            raise MarketStructureError(
                f"detection_time ({self.detection_time}) must equal "
                f"break_bar_close_time ({self.break_bar_close_time})"
            )
        if self.break_bar_close_time <= self.break_bar_open_time:
            raise MarketStructureError(
                f"break_bar_close_time ({self.break_bar_close_time}) must be strictly after "
                f"break_bar_open_time ({self.break_bar_open_time})"
            )
        if self.confirmation_status not in ("CONFIRMED", "CANDIDATE"):
            raise MarketStructureError(
                f"Invalid confirmation_status: {self.confirmation_status}. Must be CONFIRMED or CANDIDATE"
            )

    @property
    def is_bos(self) -> bool:
        """Return True if this event is a Break of Structure."""
        return self.break_type == StructureBreakType.BOS

    @property
    def is_choch(self) -> bool:
        """Return True if this event is a Change of Character."""
        return self.break_type == StructureBreakType.CHOCH

    @property
    def is_mss_candidate(self) -> bool:
        """Return True if this event is an MSS Candidate."""
        return self.break_type == StructureBreakType.MSS_CANDIDATE

    @property
    def is_bullish(self) -> bool:
        """Return True if bullish direction."""
        return self.structure_direction == StructureDirection.BULLISH

    @property
    def is_bearish(self) -> bool:
        """Return True if bearish direction."""
        return self.structure_direction == StructureDirection.BEARISH

    def to_mss_candidate(self) -> StructureBreakEvent:
        """Convert a CHoCH event to an explicit MSS_CANDIDATE representation.

        Note: MSS requires subsequent Displacement verification before it can be confirmed.
        In Phase 3C, status remains strictly CANDIDATE.
        """
        if self.break_type != StructureBreakType.CHOCH:
            raise MarketStructureError("Only CHoCH events can be converted to MSS_CANDIDATE")

        cand_id = self.mss_candidate_id or generate_mss_candidate_id(self.event_id)
        return StructureBreakEvent(
            event_id=cand_id,
            instrument_id=self.instrument_id,
            source_id=self.source_id,
            timeframe=self.timeframe,
            price_type=self.price_type,
            structure_direction=self.structure_direction,
            break_type=StructureBreakType.MSS_CANDIDATE,
            source_swing_id=self.source_swing_id,
            protected_level_id=self.protected_level_id,
            reference_level_price=self.reference_level_price,
            break_price=self.break_price,
            break_bar_open_time=self.break_bar_open_time,
            break_bar_close_time=self.break_bar_close_time,
            detection_time=self.detection_time,
            trend_before=self.trend_before,
            trend_after=self.trend_after,
            confirmation_status="CANDIDATE",
            mss_candidate_id=cand_id,
            flags=self.flags,
        )


@dataclass(frozen=True, slots=True)
class MarketStructureState:
    """Immutable point-in-time snapshot of the market structure state."""

    trend: MarketTrend
    active_structural_high: SwingPoint | None
    active_structural_low: SwingPoint | None
    protected_swing_high: SwingPoint | None
    protected_swing_low: SwingPoint | None
    last_break_event: StructureBreakEvent | None
    last_bos_event: StructureBreakEvent | None
    last_choch_event: StructureBreakEvent | None
    as_of_time: datetime
    confirmed_swings_count: int
    structural_swings: tuple[StructuralSwingPoint, ...]

    @property
    def is_uptrend(self) -> bool:
        return self.trend == MarketTrend.UPTREND

    @property
    def is_downtrend(self) -> bool:
        return self.trend == MarketTrend.DOWNTREND

    @property
    def is_ranging(self) -> bool:
        return self.trend == MarketTrend.RANGING

    @property
    def is_potential_reversal(self) -> bool:
        return self.trend == MarketTrend.POTENTIAL_REVERSAL

    @property
    def is_undetermined(self) -> bool:
        return self.trend == MarketTrend.UNDETERMINED

    @property
    def active_protected_level(self) -> SwingPoint | None:
        """Return active protected swing level for current trend direction."""
        if self.trend == MarketTrend.UPTREND:
            return self.protected_swing_low
        elif self.trend == MarketTrend.DOWNTREND:
            return self.protected_swing_high
        return None


# ── Configuration Model ──────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class MarketStructureConfig:
    """Configuration settings for the Market Structure detection engine."""

    tick_size: Decimal
    break_threshold_ticks: int = 1
    policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED
    strict_pit: bool = False
    allow_synthetic: bool = False
    allow_weekend_gap: bool = True
    allow_rollover_gap: bool = True
    confirm_trend_on_choch: bool = False
    min_swings_for_trend: int = 3

    def __post_init__(self) -> None:
        if self.tick_size <= Decimal("0"):
            raise InvalidMarketStructureParameterError(
                f"tick_size must be strictly positive Decimal, got {self.tick_size}"
            )
        if self.break_threshold_ticks < 0:
            raise InvalidMarketStructureParameterError(
                f"break_threshold_ticks cannot be negative, got {self.break_threshold_ticks}"
            )
        if self.min_swings_for_trend < 1:
            raise InvalidMarketStructureParameterError(
                f"min_swings_for_trend must be >= 1, got {self.min_swings_for_trend}"
            )

    @property
    def break_threshold(self) -> Decimal:
        """Absolute price threshold required beyond level to confirm a break."""
        return Decimal(self.break_threshold_ticks) * self.tick_size

    @classmethod
    def from_instrument_or_source(
        cls,
        instrument: Instrument | None = None,
        data_source: DataSource | None = None,
        *,
        tick_size: Decimal | None = None,
        break_threshold_ticks: int = 1,
        policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED,
        strict_pit: bool = False,
        allow_synthetic: bool = False,
        allow_weekend_gap: bool = True,
        allow_rollover_gap: bool = True,
        confirm_trend_on_choch: bool = False,
        min_swings_for_trend: int = 3,
    ) -> MarketStructureConfig:
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
            raise InvalidMarketStructureParameterError(
                "Cannot resolve tick_size: provide tick_size directly, or pass an Instrument or DataSource"
            )

        return cls(
            tick_size=resolved_tick,
            break_threshold_ticks=break_threshold_ticks,
            policy=policy,
            strict_pit=strict_pit,
            allow_synthetic=allow_synthetic,
            allow_weekend_gap=allow_weekend_gap,
            allow_rollover_gap=allow_rollover_gap,
            confirm_trend_on_choch=confirm_trend_on_choch,
            min_swings_for_trend=min_swings_for_trend,
        )


# ── Structural Swing Classification ──────────────────────────────────────────
def classify_structural_swings(
    swings: Sequence[SwingPoint],
    *,
    tick_size: Decimal,
    threshold_ticks: int = 0,
) -> list[StructuralSwingPoint]:
    """Classify confirmed swing points into HH, HL, LH, LL, or EQUAL types.

    Sequential comparison is performed against the preceding confirmed swing
    of the exact same type (high compared against high, low against low).
    """
    confirmed = [
        s
        for s in swings
        if s.confirmation_status == SwingConfirmationStatus.CONFIRMED
        and s.confirmation_time is not None
    ]
    # Sort chronologically by pivot_time, then confirmation_time
    sorted_swings = sorted(confirmed, key=lambda s: (s.pivot_time, s.confirmation_time))

    classified: list[StructuralSwingPoint] = []
    last_high: SwingPoint | None = None
    last_low: SwingPoint | None = None
    threshold = Decimal(threshold_ticks) * tick_size

    for s in sorted_swings:
        if s.is_swing_high:
            if last_high is None:
                classified.append(
                    StructuralSwingPoint(
                        swing_point=s,
                        structural_type=StructuralSwingType.UNKNOWN,
                        previous_swing_id=None,
                        price_delta=None,
                    )
                )
            else:
                diff = s.pivot_price - last_high.pivot_price
                if diff > threshold:
                    stype = StructuralSwingType.HH
                elif diff < -threshold:
                    stype = StructuralSwingType.LH
                else:
                    stype = StructuralSwingType.EQUAL_HIGH

                classified.append(
                    StructuralSwingPoint(
                        swing_point=s,
                        structural_type=stype,
                        previous_swing_id=last_high.id,
                        price_delta=diff,
                    )
                )
            last_high = s

        elif s.is_swing_low:
            if last_low is None:
                classified.append(
                    StructuralSwingPoint(
                        swing_point=s,
                        structural_type=StructuralSwingType.UNKNOWN,
                        previous_swing_id=None,
                        price_delta=None,
                    )
                )
            else:
                diff = s.pivot_price - last_low.pivot_price
                if diff > threshold:
                    stype = StructuralSwingType.HL
                elif diff < -threshold:
                    stype = StructuralSwingType.LL
                else:
                    stype = StructuralSwingType.EQUAL_LOW

                classified.append(
                    StructuralSwingPoint(
                        swing_point=s,
                        structural_type=stype,
                        previous_swing_id=last_low.id,
                        price_delta=diff,
                    )
                )
            last_low = s

    return classified


def determine_trend_from_structural_swings(
    structural_swings: Sequence[StructuralSwingPoint],
    min_swings: int = 3,
) -> MarketTrend:
    """Determine market trend from a sequence of classified structural swings.

    Rules:
    - Latest High is HH and Latest Low is HL -> UPTREND
    - Latest High is LH and Latest Low is LL -> DOWNTREND
    - Conflicting (HH + LL or LH + HL) or EQUAL -> RANGING
    - Fewer than min_swings or incomplete high/low pair -> UNDETERMINED
    """
    if len(structural_swings) < min_swings:
        return MarketTrend.UNDETERMINED

    highs = [
        ss
        for ss in structural_swings
        if ss.is_swing_high and ss.structural_type != StructuralSwingType.UNKNOWN
    ]
    lows = [
        ss
        for ss in structural_swings
        if ss.is_swing_low and ss.structural_type != StructuralSwingType.UNKNOWN
    ]

    if not highs or not lows:
        return MarketTrend.UNDETERMINED

    latest_h = highs[-1].structural_type
    latest_l = lows[-1].structural_type

    if latest_h == StructuralSwingType.HH and latest_l == StructuralSwingType.HL:
        return MarketTrend.UPTREND
    if latest_h == StructuralSwingType.LH and latest_l == StructuralSwingType.LL:
        return MarketTrend.DOWNTREND
    if latest_h in (StructuralSwingType.HH, StructuralSwingType.LH) and latest_l in (
        StructuralSwingType.HL,
        StructuralSwingType.LL,
    ):
        return MarketTrend.RANGING
    if latest_h == StructuralSwingType.EQUAL_HIGH or latest_l == StructuralSwingType.EQUAL_LOW:
        return MarketTrend.RANGING

    return MarketTrend.UNDETERMINED


# ── Core Market Structure Detection Algorithm ─────────────────────────────────
def detect_market_structure(
    candles: Sequence[Candle],
    swings: Sequence[SwingPoint],
    *,
    config: MarketStructureConfig | None = None,
    tick_size: Decimal | None = None,
    break_threshold_ticks: int = 1,
    as_of_utc: datetime | None = None,
    policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED,
    strict_pit: bool = False,
    allow_synthetic: bool = False,
    allow_weekend_gap: bool = True,
    allow_rollover_gap: bool = True,
    confirm_trend_on_choch: bool = False,
    min_swings_for_trend: int = 3,
    instrument: Instrument | None = None,
    data_source: DataSource | None = None,
) -> tuple[list[StructureBreakEvent], MarketStructureState]:
    """Detect Point-in-Time safe Market Structure breaks (BOS, CHoCH, MSS Candidates).

    Evaluates candle closing prices against actively confirmed structural swing levels
    in strict chronological order.

    Returns:
        tuple of (events_list, final_market_structure_state).
    """
    if config is None:
        config = MarketStructureConfig.from_instrument_or_source(
            instrument=instrument,
            data_source=data_source,
            tick_size=tick_size,
            break_threshold_ticks=break_threshold_ticks,
            policy=policy,
            strict_pit=strict_pit,
            allow_synthetic=allow_synthetic,
            allow_weekend_gap=allow_weekend_gap,
            allow_rollover_gap=allow_rollover_gap,
            confirm_trend_on_choch=confirm_trend_on_choch,
            min_swings_for_trend=min_swings_for_trend,
        )

    as_of_norm = ensure_utc(as_of_utc) if as_of_utc is not None else None
    eval_time = as_of_norm if as_of_norm is not None else datetime.now(UTC)

    if not candles or not swings:
        empty_state = MarketStructureState(
            trend=MarketTrend.UNDETERMINED,
            active_structural_high=None,
            active_structural_low=None,
            protected_swing_high=None,
            protected_swing_low=None,
            last_break_event=None,
            last_bos_event=None,
            last_choch_event=None,
            as_of_time=eval_time,
            confirmed_swings_count=0,
            structural_swings=(),
        )
        return [], empty_state

    # 1. Validate candle sequence
    _validate_candle_sequence(candles)

    # 2. Filter candles by PIT criteria, closure, and synthetic rules
    effective_candles: list[Candle] = []
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
        empty_state = MarketStructureState(
            trend=MarketTrend.UNDETERMINED,
            active_structural_high=None,
            active_structural_low=None,
            protected_swing_high=None,
            protected_swing_low=None,
            last_break_event=None,
            last_bos_event=None,
            last_choch_event=None,
            as_of_time=eval_time,
            confirmed_swings_count=0,
            structural_swings=(),
        )
        return [], empty_state

    first_candle = effective_candles[0]

    # 3. Filter and validate confirmed swings
    eligible_swings: list[SwingPoint] = []
    for s in swings:
        if s.price_type != first_candle.price_type:
            raise MarketStructureDataError(
                f"PriceType mismatch: Candle price_type {first_candle.price_type.value} "
                f"!= Swing price_type {s.price_type.value}"
            )
        if s.timeframe != first_candle.timeframe:
            raise MarketStructureDataError(
                f"Timeframe mismatch: Candle timeframe {first_candle.timeframe.name} "
                f"!= Swing timeframe {s.timeframe.name}"
            )
        if s.instrument_id != first_candle.instrument_id:
            raise MarketStructureDataError(
                f"InstrumentId mismatch: Candle instrument {first_candle.instrument_id} "
                f"!= Swing instrument {s.instrument_id}"
            )
        if (
            s.confirmation_status != SwingConfirmationStatus.CONFIRMED
            or s.confirmation_time is None
        ):
            continue
        if as_of_norm is not None and s.confirmation_time > as_of_norm:
            continue
        eligible_swings.append(s)

    eligible_swings.sort(key=lambda s: (s.pivot_time, s.confirmation_time))

    events: list[StructureBreakEvent] = []
    broken_swing_ids: set[str] = set()

    current_trend = MarketTrend.UNDETERMINED
    active_high: SwingPoint | None = None
    active_low: SwingPoint | None = None
    protected_high: SwingPoint | None = None
    protected_low: SwingPoint | None = None
    last_break_ev: StructureBreakEvent | None = None
    last_bos_ev: StructureBreakEvent | None = None
    last_choch_ev: StructureBreakEvent | None = None

    threshold = config.break_threshold

    # 4. Bar-by-bar chronological evaluation
    for bar_idx, bar in enumerate(effective_candles):
        # Gap check
        gap_flag = False
        if bar_idx > 0:
            gap_flag = _is_calendar_break(
                effective_candles[bar_idx - 1],
                bar,
                bar.timeframe,
                allow_weekend_gap=config.allow_weekend_gap,
                allow_rollover_gap=config.allow_rollover_gap,
            )
        gap_flags = ("DATA_GAP_PRECEDING",) if gap_flag else ()

        # Swings confirmed strictly before or at bar.open_time
        known_swings = [
            s
            for s in eligible_swings
            if s.confirmation_time is not None and s.confirmation_time <= bar.open_time
        ]

        # Update structural swings classification
        curr_classified = classify_structural_swings(known_swings, tick_size=config.tick_size)

        # Initial trend determination if undetermined or ranging
        if current_trend in (MarketTrend.UNDETERMINED, MarketTrend.RANGING):
            trend_candidate = determine_trend_from_structural_swings(
                curr_classified, min_swings=config.min_swings_for_trend
            )
            if trend_candidate != MarketTrend.UNDETERMINED:
                current_trend = trend_candidate

        # Update structural reference levels based on known swings
        known_highs = [s for s in known_swings if s.is_swing_high]
        known_lows = [s for s in known_swings if s.is_swing_low]

        if current_trend == MarketTrend.UPTREND:
            # Active structural high: most recent confirmed swing high not yet broken
            unbroken_highs = [s for s in known_highs if s.id not in broken_swing_ids]
            active_high = unbroken_highs[-1] if unbroken_highs else None

            # Protected swing low: swing low formed prior to active structural high
            if active_high is not None:
                candidate_lows = [
                    s
                    for s in known_lows
                    if s.pivot_time < active_high.pivot_time and s.id not in broken_swing_ids
                ]
                if candidate_lows:
                    protected_low = candidate_lows[-1]
                else:
                    unbroken_lows = [s for s in known_lows if s.id not in broken_swing_ids]
                    protected_low = unbroken_lows[-1] if unbroken_lows else None
            else:
                unbroken_lows = [s for s in known_lows if s.id not in broken_swing_ids]
                protected_low = unbroken_lows[-1] if unbroken_lows else None
            protected_high = None

        elif current_trend == MarketTrend.DOWNTREND:
            # Active structural low: most recent confirmed swing low not yet broken
            unbroken_lows = [s for s in known_lows if s.id not in broken_swing_ids]
            active_low = unbroken_lows[-1] if unbroken_lows else None

            # Protected swing high: swing high formed prior to active structural low
            if active_low is not None:
                candidate_highs = [
                    s
                    for s in known_highs
                    if s.pivot_time < active_low.pivot_time and s.id not in broken_swing_ids
                ]
                if candidate_highs:
                    protected_high = candidate_highs[-1]
                else:
                    unbroken_highs = [s for s in known_highs if s.id not in broken_swing_ids]
                    protected_high = unbroken_highs[-1] if unbroken_highs else None
            else:
                unbroken_highs = [s for s in known_highs if s.id not in broken_swing_ids]
                protected_high = unbroken_highs[-1] if unbroken_highs else None
            protected_low = None

        elif current_trend == MarketTrend.POTENTIAL_REVERSAL:
            # Check for reversal confirmation via newly confirmed swings
            rev_trend = determine_trend_from_structural_swings(
                curr_classified, min_swings=config.min_swings_for_trend
            )
            if rev_trend in (MarketTrend.UPTREND, MarketTrend.DOWNTREND):
                current_trend = rev_trend
                if current_trend == MarketTrend.UPTREND:
                    unbroken_highs = [s for s in known_highs if s.id not in broken_swing_ids]
                    active_high = unbroken_highs[-1] if unbroken_highs else None
                    unbroken_lows = [s for s in known_lows if s.id not in broken_swing_ids]
                    protected_low = unbroken_lows[-1] if unbroken_lows else None
                    protected_high = None
                else:
                    unbroken_lows = [s for s in known_lows if s.id not in broken_swing_ids]
                    active_low = unbroken_lows[-1] if unbroken_lows else None
                    unbroken_highs = [s for s in known_highs if s.id not in broken_swing_ids]
                    protected_high = unbroken_highs[-1] if unbroken_highs else None
                    protected_low = None

        # ── Check for Breakout on Completed Candle `bar` ─────────────────────
        # A. UPTREND Checks
        if current_trend == MarketTrend.UPTREND:
            # 1. Bearish CHoCH Check against Protected Low
            if protected_low is not None and protected_low.id not in broken_swing_ids:
                ref_p = protected_low.pivot_price
                if bar.close < ref_p - threshold:
                    # Bearish CHoCH event
                    ev_id = generate_structure_break_id(
                        bar.instrument_id,
                        bar.source_id,
                        bar.price_type,
                        bar.timeframe,
                        StructureBreakType.CHOCH,
                        StructureDirection.BEARISH,
                        protected_low.id,
                        bar.open_time,
                    )
                    mss_id = generate_mss_candidate_id(ev_id)
                    next_trend = (
                        MarketTrend.DOWNTREND
                        if config.confirm_trend_on_choch
                        else MarketTrend.POTENTIAL_REVERSAL
                    )
                    ev = StructureBreakEvent(
                        event_id=ev_id,
                        instrument_id=bar.instrument_id,
                        source_id=bar.source_id,
                        timeframe=bar.timeframe,
                        price_type=bar.price_type,
                        structure_direction=StructureDirection.BEARISH,
                        break_type=StructureBreakType.CHOCH,
                        source_swing_id=protected_low.id,
                        protected_level_id=protected_low.id,
                        reference_level_price=ref_p,
                        break_price=bar.close,
                        break_bar_open_time=bar.open_time,
                        break_bar_close_time=bar.close_time,
                        detection_time=bar.close_time,
                        trend_before=MarketTrend.UPTREND,
                        trend_after=next_trend,
                        confirmation_status="CONFIRMED",
                        mss_candidate_id=mss_id,
                        flags=gap_flags,
                    )
                    events.append(ev)
                    broken_swing_ids.add(protected_low.id)
                    current_trend = next_trend
                    last_break_ev = ev
                    last_choch_ev = ev

            # 2. Bullish BOS Check against Active Structural High (if no CHoCH on this bar)
            if (
                current_trend == MarketTrend.UPTREND
                and active_high is not None
                and active_high.id not in broken_swing_ids
            ):
                ref_p = active_high.pivot_price
                if bar.close > ref_p + threshold:
                    # Bullish BOS event
                    ev_id = generate_structure_break_id(
                        bar.instrument_id,
                        bar.source_id,
                        bar.price_type,
                        bar.timeframe,
                        StructureBreakType.BOS,
                        StructureDirection.BULLISH,
                        active_high.id,
                        bar.open_time,
                    )
                    ev = StructureBreakEvent(
                        event_id=ev_id,
                        instrument_id=bar.instrument_id,
                        source_id=bar.source_id,
                        timeframe=bar.timeframe,
                        price_type=bar.price_type,
                        structure_direction=StructureDirection.BULLISH,
                        break_type=StructureBreakType.BOS,
                        source_swing_id=active_high.id,
                        protected_level_id=protected_low.id if protected_low else None,
                        reference_level_price=ref_p,
                        break_price=bar.close,
                        break_bar_open_time=bar.open_time,
                        break_bar_close_time=bar.close_time,
                        detection_time=bar.close_time,
                        trend_before=MarketTrend.UPTREND,
                        trend_after=MarketTrend.UPTREND,
                        confirmation_status="CONFIRMED",
                        flags=gap_flags,
                    )
                    events.append(ev)
                    for high_pt in known_highs:
                        if high_pt.pivot_time <= active_high.pivot_time:
                            broken_swing_ids.add(high_pt.id)
                    last_break_ev = ev
                    last_bos_ev = ev

        # B. DOWNTREND Checks
        elif current_trend == MarketTrend.DOWNTREND:
            # 1. Bullish CHoCH Check against Protected High
            if protected_high is not None and protected_high.id not in broken_swing_ids:
                ref_p = protected_high.pivot_price
                if bar.close > ref_p + threshold:
                    # Bullish CHoCH event
                    ev_id = generate_structure_break_id(
                        bar.instrument_id,
                        bar.source_id,
                        bar.price_type,
                        bar.timeframe,
                        StructureBreakType.CHOCH,
                        StructureDirection.BULLISH,
                        protected_high.id,
                        bar.open_time,
                    )
                    mss_id = generate_mss_candidate_id(ev_id)
                    next_trend = (
                        MarketTrend.UPTREND
                        if config.confirm_trend_on_choch
                        else MarketTrend.POTENTIAL_REVERSAL
                    )
                    ev = StructureBreakEvent(
                        event_id=ev_id,
                        instrument_id=bar.instrument_id,
                        source_id=bar.source_id,
                        timeframe=bar.timeframe,
                        price_type=bar.price_type,
                        structure_direction=StructureDirection.BULLISH,
                        break_type=StructureBreakType.CHOCH,
                        source_swing_id=protected_high.id,
                        protected_level_id=protected_high.id,
                        reference_level_price=ref_p,
                        break_price=bar.close,
                        break_bar_open_time=bar.open_time,
                        break_bar_close_time=bar.close_time,
                        detection_time=bar.close_time,
                        trend_before=MarketTrend.DOWNTREND,
                        trend_after=next_trend,
                        confirmation_status="CONFIRMED",
                        mss_candidate_id=mss_id,
                        flags=gap_flags,
                    )
                    events.append(ev)
                    broken_swing_ids.add(protected_high.id)
                    current_trend = next_trend
                    last_break_ev = ev
                    last_choch_ev = ev

            # 2. Bearish BOS Check against Active Structural Low (if no CHoCH on this bar)
            if (
                current_trend == MarketTrend.DOWNTREND
                and active_low is not None
                and active_low.id not in broken_swing_ids
            ):
                ref_p = active_low.pivot_price
                if bar.close < ref_p - threshold:
                    # Bearish BOS event
                    ev_id = generate_structure_break_id(
                        bar.instrument_id,
                        bar.source_id,
                        bar.price_type,
                        bar.timeframe,
                        StructureBreakType.BOS,
                        StructureDirection.BEARISH,
                        active_low.id,
                        bar.open_time,
                    )
                    ev = StructureBreakEvent(
                        event_id=ev_id,
                        instrument_id=bar.instrument_id,
                        source_id=bar.source_id,
                        timeframe=bar.timeframe,
                        price_type=bar.price_type,
                        structure_direction=StructureDirection.BEARISH,
                        break_type=StructureBreakType.BOS,
                        source_swing_id=active_low.id,
                        protected_level_id=protected_high.id if protected_high else None,
                        reference_level_price=ref_p,
                        break_price=bar.close,
                        break_bar_open_time=bar.open_time,
                        break_bar_close_time=bar.close_time,
                        detection_time=bar.close_time,
                        trend_before=MarketTrend.DOWNTREND,
                        trend_after=MarketTrend.DOWNTREND,
                        confirmation_status="CONFIRMED",
                        flags=gap_flags,
                    )
                    events.append(ev)
                    for low_pt in known_lows:
                        if low_pt.pivot_time <= active_low.pivot_time:
                            broken_swing_ids.add(low_pt.id)
                    last_break_ev = ev
                    last_bos_ev = ev

        # C. POTENTIAL_REVERSAL Checks (Breakout continuation confirming reversal)
        elif current_trend == MarketTrend.POTENTIAL_REVERSAL:
            # Check if price breaks a newly confirmed swing in the reversal direction
            if last_choch_ev is not None:
                if last_choch_ev.structure_direction == StructureDirection.BEARISH:
                    # Looking to confirm DOWNTREND via Bearish BOS of active low
                    unbroken_lows = [
                        s
                        for s in known_lows
                        if s.id not in broken_swing_ids
                        and s.pivot_time > last_choch_ev.break_bar_open_time
                    ]
                    if unbroken_lows:
                        target_low = unbroken_lows[-1]
                        if bar.close < target_low.pivot_price - threshold:
                            # Bearish BOS confirming downtrend
                            ev_id = generate_structure_break_id(
                                bar.instrument_id,
                                bar.source_id,
                                bar.price_type,
                                bar.timeframe,
                                StructureBreakType.BOS,
                                StructureDirection.BEARISH,
                                target_low.id,
                                bar.open_time,
                            )
                            ev = StructureBreakEvent(
                                event_id=ev_id,
                                instrument_id=bar.instrument_id,
                                source_id=bar.source_id,
                                timeframe=bar.timeframe,
                                price_type=bar.price_type,
                                structure_direction=StructureDirection.BEARISH,
                                break_type=StructureBreakType.BOS,
                                source_swing_id=target_low.id,
                                protected_level_id=protected_high.id if protected_high else None,
                                reference_level_price=target_low.pivot_price,
                                break_price=bar.close,
                                break_bar_open_time=bar.open_time,
                                break_bar_close_time=bar.close_time,
                                detection_time=bar.close_time,
                                trend_before=MarketTrend.POTENTIAL_REVERSAL,
                                trend_after=MarketTrend.DOWNTREND,
                                confirmation_status="CONFIRMED",
                                flags=gap_flags,
                            )
                            events.append(ev)
                            broken_swing_ids.add(target_low.id)
                            current_trend = MarketTrend.DOWNTREND
                            last_break_ev = ev
                            last_bos_ev = ev

                elif last_choch_ev.structure_direction == StructureDirection.BULLISH:
                    # Looking to confirm UPTREND via Bullish BOS of active high
                    unbroken_highs = [
                        s
                        for s in known_highs
                        if s.id not in broken_swing_ids
                        and s.pivot_time > last_choch_ev.break_bar_open_time
                    ]
                    if unbroken_highs:
                        target_high = unbroken_highs[-1]
                        if bar.close > target_high.pivot_price + threshold:
                            # Bullish BOS confirming uptrend
                            ev_id = generate_structure_break_id(
                                bar.instrument_id,
                                bar.source_id,
                                bar.price_type,
                                bar.timeframe,
                                StructureBreakType.BOS,
                                StructureDirection.BULLISH,
                                target_high.id,
                                bar.open_time,
                            )
                            ev = StructureBreakEvent(
                                event_id=ev_id,
                                instrument_id=bar.instrument_id,
                                source_id=bar.source_id,
                                timeframe=bar.timeframe,
                                price_type=bar.price_type,
                                structure_direction=StructureDirection.BULLISH,
                                break_type=StructureBreakType.BOS,
                                source_swing_id=target_high.id,
                                protected_level_id=protected_low.id if protected_low else None,
                                reference_level_price=target_high.pivot_price,
                                break_price=bar.close,
                                break_bar_open_time=bar.open_time,
                                break_bar_close_time=bar.close_time,
                                detection_time=bar.close_time,
                                trend_before=MarketTrend.POTENTIAL_REVERSAL,
                                trend_after=MarketTrend.UPTREND,
                                confirmation_status="CONFIRMED",
                                flags=gap_flags,
                            )
                            events.append(ev)
                            broken_swing_ids.add(target_high.id)
                            current_trend = MarketTrend.UPTREND
                            last_break_ev = ev
                            last_bos_ev = ev

    # 5. Final classified swings and snapshot state
    final_classified = classify_structural_swings(eligible_swings, tick_size=config.tick_size)

    final_state = MarketStructureState(
        trend=current_trend,
        active_structural_high=active_high,
        active_structural_low=active_low,
        protected_swing_high=protected_high,
        protected_swing_low=protected_low,
        last_break_event=last_break_ev,
        last_bos_event=last_bos_ev,
        last_choch_event=last_choch_ev,
        as_of_time=effective_candles[-1].close_time,
        confirmed_swings_count=len(eligible_swings),
        structural_swings=tuple(final_classified),
    )

    return events, final_state


# ── Query & Filtering Helpers ────────────────────────────────────────────────
def filter_confirmed_structure_breaks(
    events: Sequence[StructureBreakEvent],
) -> list[StructureBreakEvent]:
    """Filter for confirmed structural break events (BOS and CHoCH)."""
    return [
        e
        for e in events
        if e.confirmation_status == "CONFIRMED"
        and e.break_type in (StructureBreakType.BOS, StructureBreakType.CHOCH)
    ]


def filter_mss_candidates(
    events: Sequence[StructureBreakEvent],
) -> list[StructureBreakEvent]:
    """Extract MSS candidate representations from CHoCH events without double counting."""
    return [e.to_mss_candidate() for e in events if e.break_type == StructureBreakType.CHOCH]


def summarize_market_structure_events(
    events: Sequence[StructureBreakEvent],
) -> dict[str, int]:
    """Compile aggregated count metrics for market structure events."""
    bullish_bos = 0
    bearish_bos = 0
    bullish_choch = 0
    bearish_choch = 0
    mss_candidates = 0

    for e in events:
        if e.break_type == StructureBreakType.BOS:
            if e.structure_direction == StructureDirection.BULLISH:
                bullish_bos += 1
            else:
                bearish_bos += 1
        elif e.break_type == StructureBreakType.CHOCH:
            if e.structure_direction == StructureDirection.BULLISH:
                bullish_choch += 1
            else:
                bearish_choch += 1
            mss_candidates += 1

    return {
        "total_events": len(events),
        "bullish_bos": bullish_bos,
        "bearish_bos": bearish_bos,
        "bullish_choch": bullish_choch,
        "bearish_choch": bearish_choch,
        "mss_candidate": mss_candidates,
    }
