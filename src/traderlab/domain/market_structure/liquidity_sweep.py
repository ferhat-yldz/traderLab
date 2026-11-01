"""Point-in-Time Safe Liquidity Sweep Detection Engine.

Provides mathematically rigorous, deterministic, and Point-in-Time compliant
Liquidity Sweep (Buy-Side Liquidity & Sell-Side Liquidity) identification for
ICT/SMC market structure analysis.

Key architectural and financial principles:
1. **Strict Decoupling of Liquidity Sides**:
   - Buy-Side Liquidity (BSL): Formed above confirmed Swing High levels.
   - Sell-Side Liquidity (SSL): Formed below confirmed Swing Low levels.
2. **Point-in-Time (PIT) Integrity**:
   - A liquidity sweep can ONLY reference a swing level that was CONFIRMED
     before the sweep candle began:
     `swing.confirmation_time <= sweep_bar.open_time`
   - A sweep event CANNOT be known or acted upon prior to `sweep_bar.close_time`.
     `detection_time = sweep_bar.close_time`.
3. **Event Categorization & Demarcation**:
   - `SWEEP`: Price penetrates beyond level by at least `min_penetration_ticks`
     and closes back within the level (single-candle reclaim).
   - `TOUCH`: Price reaches or penetrates less than threshold, closing within level.
   - `BREACH`: Price penetrates and closes beyond the level (breakout without reclaim).
     (Note: Breaches are NOT labeled as BOS; BOS is reserved for future phases).
   - `AMBIGUOUS`: Single candle extremes breach multiple opposing levels where
     intra-bar execution order cannot be determined from OHLC alone.
4. **Swing Level Lifecycle & Consumption**:
   - Tracks level state chronologically: UNTESTED -> TOUCHED -> SWEPT / BREACHED.
   - Once breached by a body close, the level is invalidated for future sweeps.
   - Configurable level consumption: by default, a level is consumed after its
     first confirmed sweep (`consume_level_after_sweep=True`).
5. **No Hardcoded Constants**:
   - `tick_size` is resolved dynamically from `Instrument` or `DataSource` contracts.
   - Exact `Decimal` arithmetic is used for all price and threshold comparisons.
6. **Data Quality & Microstructure Guards**:
   - Closed candles only (`is_closed=True`).
   - Synthetic bars cannot trigger sweeps.
   - BID and ASK series are strictly isolated.
   - D1, H4, and H2 timeframes are strictly isolated.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

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
from traderlab.domain.instruments.data_source import DataSource
from traderlab.domain.instruments.instrument import Instrument
from traderlab.domain.market_structure.swing import (
    SwingConfirmationStatus,
    SwingLevel,
    SwingPoint,
    SwingType,
    _is_calendar_break,
    _validate_candle_sequence,
)


# ── Exceptions ───────────────────────────────────────────────────────────────
class LiquiditySweepError(TraderLabError):
    """Base exception for Liquidity Sweep detection and market structure errors."""

    def __init__(self, message: str, *, code: str = "LIQUIDITY_SWEEP_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidLiquiditySweepParameterError(LiquiditySweepError):
    """Raised when sweep detection parameters (e.g. tick_size, threshold) are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_SWEEP_PARAMETER")


class LiquiditySweepDataError(LiquiditySweepError):
    """Raised when input candle sequences or swing levels are malformed or incompatible."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="SWEEP_DATA_ERROR")


# ── Enumerations ────────────────────────────────────────────────────────────
class LiquiditySide(enum.Enum):
    """Directional side of the market liquidity pool."""

    BUY_SIDE = "BUY_SIDE"
    """Buy-Side Liquidity (BSL) — resting above Swing High levels."""

    SELL_SIDE = "SELL_SIDE"
    """Sell-Side Liquidity (SSL) — resting below Swing Low levels."""


class LiquiditySweepStatus(enum.Enum):
    """Classification status of the liquidity interaction event."""

    SWEEP = "SWEEP"
    """Price penetrated beyond level by >= threshold and closed back inside."""

    TOUCH = "TOUCH"
    """Price touched level or penetrated < threshold, closing inside."""

    BREACH = "BREACH"
    """Price penetrated and closed beyond level (breakout without reclaim)."""

    AMBIGUOUS = "AMBIGUOUS"
    """Same candle reached opposing structural extremes with indeterminate intra-bar order."""


class LevelLifecycleState(enum.Enum):
    """Chronological lifecycle state of a structural swing level."""

    UNTESTED = "UNTESTED"
    """Level confirmed but not yet touched or penetrated by subsequent price action."""

    TOUCHED = "TOUCHED"
    """Level was tested/touched without qualifying as a confirmed sweep."""

    SWEPT = "SWEPT"
    """Level experienced a confirmed single-candle liquidity sweep."""

    BREACHED = "BREACHED"
    """Level was breached by a body close, destroying its structural validity."""

    INVALIDATED = "INVALIDATED"
    """Level invalidated due to data gaps, session breaks, or configuration rules."""


# ── Deterministic Identifier Generator ───────────────────────────────────────
def generate_liquidity_sweep_id(
    instrument_id: InstrumentId | str,
    source_id: DataSourceId | str,
    price_type: PriceType | str,
    timeframe: Timeframe | str,
    liquidity_side: LiquiditySide | str,
    status: LiquiditySweepStatus | str,
    source_swing_id: str,
    sweep_bar_open_time: datetime,
) -> str:
    """Generate a deterministic, globally unique string identifier for a sweep event.

    Distinguishes:
    - Instrument and data source.
    - Price series (BID vs ASK).
    - Timeframe (e.g. D1 vs H4 vs H2).
    - Liquidity side (BUY_SIDE vs SELL_SIDE).
    - Event status (SWEEP, TOUCH, BREACH, AMBIGUOUS).
    - Source swing ID.
    - Sweep bar opening timestamp.
    """
    inst_str = str(instrument_id)
    src_str = str(source_id)
    pt_str = price_type.value if isinstance(price_type, PriceType) else str(price_type)
    tf_str = timeframe.name if isinstance(timeframe, Timeframe) else str(timeframe)
    side_str = (
        liquidity_side.value if isinstance(liquidity_side, LiquiditySide) else str(liquidity_side)
    )
    st_str = status.value if isinstance(status, LiquiditySweepStatus) else str(status)
    t_str = sweep_bar_open_time.strftime("%Y%m%dT%H%M%SZ")
    return f"{inst_str}_{src_str}_{pt_str}_{tf_str}_{side_str}_{st_str}_{source_swing_id}_{t_str}"


# ── Configuration Model ──────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class LiquiditySweepConfig:
    """Configuration parameters for deterministic Liquidity Sweep detection.

    Attributes:
        tick_size: Minimum price movement unit. Must be strictly positive Decimal.
        min_penetration_ticks: Minimum ticks beyond level required for a sweep (default: 1).
        consume_level_after_sweep: If True (default), a level is consumed and deactivated
            after its first confirmed sweep.
        allow_sweeps_after_touch: If True (default), a touched level remains eligible
            for future sweeps until breached.
        policy: Point-in-Time evaluation policy (default: RESEARCH_ESTIMATED).
        strict_pit: If True, enforces strict VERIFIED availability on all candle data.
        allow_synthetic: If True, permits synthetic candles (default: False).
        allow_ambiguous: If True, generates AMBIGUOUS events for same-bar opposing extremes.
        allow_weekend_gap: If True, permits normal weekend boundaries in the scan.
        allow_rollover_gap: If True, permits daily rollover gaps in the scan.
    """

    tick_size: Decimal
    min_penetration_ticks: int = 1
    consume_level_after_sweep: bool = True
    allow_sweeps_after_touch: bool = True
    policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED
    strict_pit: bool = False
    allow_synthetic: bool = False
    allow_ambiguous: bool = True
    allow_weekend_gap: bool = True
    allow_rollover_gap: bool = True

    def __post_init__(self) -> None:
        if self.tick_size <= Decimal("0"):
            raise InvalidLiquiditySweepParameterError(
                f"tick_size must be strictly positive Decimal, got {self.tick_size}"
            )
        if self.min_penetration_ticks < 1:
            raise InvalidLiquiditySweepParameterError(
                f"min_penetration_ticks must be >= 1, got {self.min_penetration_ticks}"
            )

    @property
    def penetration_threshold(self) -> Decimal:
        """Absolute price penetration threshold required to qualify a sweep."""
        return Decimal(self.min_penetration_ticks) * self.tick_size

    @classmethod
    def from_instrument_or_source(
        cls,
        instrument: Instrument | None = None,
        data_source: DataSource | None = None,
        *,
        tick_size: Decimal | None = None,
        min_penetration_ticks: int = 1,
        consume_level_after_sweep: bool = True,
        allow_sweeps_after_touch: bool = True,
        policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED,
        strict_pit: bool = False,
        allow_synthetic: bool = False,
        allow_ambiguous: bool = True,
        allow_weekend_gap: bool = True,
        allow_rollover_gap: bool = True,
    ) -> LiquiditySweepConfig:
        """Resolve tick size dynamically without hardcoding."""
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
            raise InvalidLiquiditySweepParameterError(
                "Cannot resolve tick_size: provide explicit tick_size or Instrument/DataSource"
            )

        return cls(
            tick_size=resolved_tick,
            min_penetration_ticks=min_penetration_ticks,
            consume_level_after_sweep=consume_level_after_sweep,
            allow_sweeps_after_touch=allow_sweeps_after_touch,
            policy=policy,
            strict_pit=strict_pit,
            allow_synthetic=allow_synthetic,
            allow_ambiguous=allow_ambiguous,
            allow_weekend_gap=allow_weekend_gap,
            allow_rollover_gap=allow_rollover_gap,
        )


# ── Domain Event Model ───────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class LiquiditySweepEvent:
    """Immutable domain event representing a liquidity interaction with a Swing Level.

    Guarantees:
    - **Point-in-Time Integrity**:
      `level_confirmation_time <= sweep_bar_open_time` is strictly enforced.
      `detection_time == sweep_bar_close_time` is strictly enforced.
    - **Precision**: Exact `Decimal` prices and tick calculations.
    - **Stable Identification**: Deterministic ID encodes instrument, timeframe,
      source swing, and execution timestamp.
    """

    event_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    price_type: PriceType

    source_swing_id: str
    liquidity_side: LiquiditySide
    level_price: Decimal
    penetration_price: Decimal  # Extreme reached (bar.high for BSL, bar.low for SSL)
    penetration_ticks: Decimal  # abs(penetration_price - level_price) / tick_size
    penetration_threshold: Decimal  # min_penetration_ticks * tick_size

    sweep_bar_open_time: datetime
    sweep_bar_close_time: datetime
    detection_time: datetime  # Earliest PIT accessibility time (= sweep_bar_close_time)

    pivot_time: datetime
    level_confirmation_time: datetime

    status: LiquiditySweepStatus
    data_quality_flags: tuple[str, ...] = ()
    pit_confidence: str = "RESEARCH_ESTIMATED"
    revision: int = 0

    def __post_init__(self) -> None:
        if self.detection_time != self.sweep_bar_close_time:
            raise LiquiditySweepError(
                f"detection_time ({self.detection_time}) must equal "
                f"sweep_bar_close_time ({self.sweep_bar_close_time})"
            )
        if self.sweep_bar_close_time <= self.sweep_bar_open_time:
            raise LiquiditySweepError(
                f"sweep_bar_close_time ({self.sweep_bar_close_time}) must be strictly after "
                f"sweep_bar_open_time ({self.sweep_bar_open_time})"
            )
        if self.level_confirmation_time > self.sweep_bar_open_time:
            raise LiquiditySweepError(
                f"Point-in-Time violation: level_confirmation_time ({self.level_confirmation_time}) "
                f"must be <= sweep_bar_open_time ({self.sweep_bar_open_time})"
            )
        if self.sweep_bar_open_time <= self.pivot_time:
            raise LiquiditySweepError(
                f"Point-in-Time violation: sweep_bar_open_time ({self.sweep_bar_open_time}) "
                f"must be strictly after pivot_time ({self.pivot_time})"
            )
        if self.penetration_ticks < Decimal("0"):
            raise LiquiditySweepError("penetration_ticks cannot be negative")

    @property
    def is_sweep(self) -> bool:
        """Return True if this interaction is a confirmed liquidity sweep."""
        return self.status == LiquiditySweepStatus.SWEEP

    @property
    def is_touch(self) -> bool:
        """Return True if this interaction was a non-sweep touch."""
        return self.status == LiquiditySweepStatus.TOUCH

    @property
    def is_breach(self) -> bool:
        """Return True if this interaction was a body breach without reclaim."""
        return self.status == LiquiditySweepStatus.BREACH

    @property
    def is_ambiguous(self) -> bool:
        """Return True if this interaction is ambiguous due to intra-candle order."""
        return self.status == LiquiditySweepStatus.AMBIGUOUS

    @property
    def is_buy_side(self) -> bool:
        """Return True if this is a Buy-Side Liquidity (BSL) interaction."""
        return self.liquidity_side == LiquiditySide.BUY_SIDE

    @property
    def is_sell_side(self) -> bool:
        """Return True if this is a Sell-Side Liquidity (SSL) interaction."""
        return self.liquidity_side == LiquiditySide.SELL_SIDE


# ── Internal Level State Tracker ─────────────────────────────────────────────
class _LevelTracker:
    """Internal mutable tracking container for evaluating a swing level chronologically."""

    __slots__ = (
        "breach_bar_open",
        "breach_time",
        "last_touch_time",
        "level",
        "state",
        "sweep_count",
        "touch_count",
    )

    def __init__(self, level: SwingLevel) -> None:
        self.level = level
        self.state = LevelLifecycleState.UNTESTED
        self.sweep_count = 0
        self.touch_count = 0
        self.last_touch_time: datetime | None = None
        self.breach_time: datetime | None = None
        self.breach_bar_open: datetime | None = None


# ── Core Sweep Detection Algorithm ───────────────────────────────────────────
def detect_liquidity_sweeps(
    candles: Sequence[Candle],
    swings: Sequence[SwingPoint | SwingLevel],
    *,
    config: LiquiditySweepConfig | None = None,
    tick_size: Decimal | None = None,
    min_penetration_ticks: int = 1,
    consume_level_after_sweep: bool = True,
    allow_sweeps_after_touch: bool = True,
    as_of_utc: datetime | None = None,
    policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED,
    strict_pit: bool = False,
    allow_synthetic: bool = False,
    allow_ambiguous: bool = True,
    allow_weekend_gap: bool = True,
    allow_rollover_gap: bool = True,
    instrument: Instrument | None = None,
    data_source: DataSource | None = None,
) -> list[LiquiditySweepEvent]:
    """Detect all Point-in-Time safe Liquidity Sweep events on confirmed Swing Levels.

    Algorithm Definition:
        For each confirmed swing level $L$ (Buy-Side from Swing High, Sell-Side from Swing Low):
        Only candles where `swing.confirmation_time <= candle.open_time` are evaluated.

        Single-Candle Sweep Evaluation:
        1. **Buy-Side Liquidity Sweep (BSL)**:
           - Requires: `bar.high > level_price + threshold` (significant penetration)
           - AND: `bar.close < level_price` (reclaimed inside on close)
           - If `bar.close >= level_price`, the level is BREACHED (no reclaim).
           - If `level_price <= bar.high <= level_price + threshold`, it is a TOUCH.
        2. **Sell-Side Liquidity Sweep (SSL)**:
           - Requires: `bar.low < level_price - threshold` (significant penetration)
           - AND: `bar.close > level_price` (reclaimed inside on close)
           - If `bar.close <= level_price`, the level is BREACHED (no reclaim).
           - If `level_price - threshold <= bar.low <= level_price`, it is a TOUCH.
        3. **Level Consumption & Breach Deactivation**:
           - Once breached by a body close, a level is deactivated and produces no further sweeps.
           - If `consume_level_after_sweep=True` (default), a level is deactivated after its first sweep.
        4. **Ambiguity Resolution**:
           - If the same candle penetrates opposing high and low levels, events are generated
             with status AMBIGUOUS to prevent fabricating unprovable intra-bar sequences.

    Args:
        candles: Chronologically sorted sequence of uniform Candle objects.
        swings: Sequence of `SwingPoint` or `SwingLevel` objects.
        config: Optional pre-built `LiquiditySweepConfig`.
        tick_size: Explicit minimum price movement unit (if config not provided).
        min_penetration_ticks: Minimum ticks beyond level to count as sweep (default: 1).
        consume_level_after_sweep: Deactivate level after first confirmed sweep (default: True).
        allow_sweeps_after_touch: Permit sweep after a prior touch (default: True).
        as_of_utc: Historical Point-in-Time evaluation cutoff timestamp.
        policy: PIT evaluation policy (RESEARCH_ESTIMATED or STRICT_PIT).
        strict_pit: Enforce strict VERIFIED availability on all candles.
        allow_synthetic: Permit synthetic candles (default: False).
        allow_ambiguous: Generate AMBIGUOUS events for same-bar opposing extremes (default: True).
        allow_weekend_gap: Allow normal weekend gaps in sequence.
        allow_rollover_gap: Allow normal rollover gaps in sequence.
        instrument: Optional canonical Instrument for resolving tick size.
        data_source: Optional DataSource for resolving broker-specific tick size overrides.

    Returns:
        List of detected `LiquiditySweepEvent` objects in chronological order of detection_time.
    """
    if config is None:
        config = LiquiditySweepConfig.from_instrument_or_source(
            instrument=instrument,
            data_source=data_source,
            tick_size=tick_size,
            min_penetration_ticks=min_penetration_ticks,
            consume_level_after_sweep=consume_level_after_sweep,
            allow_sweeps_after_touch=allow_sweeps_after_touch,
            policy=policy,
            strict_pit=strict_pit,
            allow_synthetic=allow_synthetic,
            allow_ambiguous=allow_ambiguous,
            allow_weekend_gap=allow_weekend_gap,
            allow_rollover_gap=allow_rollover_gap,
        )

    if not candles or not swings:
        return []

    _validate_candle_sequence(candles)

    # 1. Filter candles by PIT criteria, closure, and synthetic rules
    effective_candles: list[Candle] = []
    as_of_norm = ensure_utc(as_of_utc) if as_of_utc is not None else None

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

        # Check PIT availability policy
        if not c.is_available_at(as_of_norm or c.close_time, policy=effective_policy):
            continue

        effective_candles.append(c)

    if not effective_candles:
        return []

    # 2. Extract and validate confirmed swing levels
    trackers: list[_LevelTracker] = []
    first_candle = effective_candles[0]

    for s in swings:
        # Convert SwingPoint to SwingLevel if needed
        if isinstance(s, SwingPoint):
            if s.confirmation_status != SwingConfirmationStatus.CONFIRMED:
                continue
            lvl = s.to_level()
        elif isinstance(s, SwingLevel):
            lvl = s
        else:
            raise LiquiditySweepDataError(f"Unsupported swing object type: {type(s)}")

        # Verify series isolation
        if lvl.timeframe != first_candle.timeframe:
            raise LiquiditySweepDataError(
                f"Timeframe mismatch: Candle timeframe {first_candle.timeframe.name} "
                f"!= Swing timeframe {lvl.timeframe.name}"
            )
        if lvl.price_type != first_candle.price_type:
            raise LiquiditySweepDataError(
                f"PriceType mismatch: Candle price_type {first_candle.price_type.value} "
                f"!= Swing price_type {lvl.price_type.value}"
            )

        # Level must be confirmed strictly at or before the end of the data scope
        if as_of_norm is not None and lvl.level_confirmation_time > as_of_norm:
            continue

        trackers.append(_LevelTracker(lvl))

    if not trackers:
        return []

    # Sort trackers deterministically by confirmation time, formation time, level price
    trackers.sort(
        key=lambda t: (
            t.level.level_confirmation_time,
            t.level.level_formation_time,
            t.level.level_price,
        )
    )

    events: list[LiquiditySweepEvent] = []
    threshold = config.penetration_threshold

    # 3. Chronological scan through completed candles
    for bar_idx, bar in enumerate(effective_candles):
        # Data gap check between consecutive bars
        gap_flag = False
        if bar_idx > 0:
            gap_flag = _is_calendar_break(
                effective_candles[bar_idx - 1],
                bar,
                bar.timeframe,
                allow_weekend_gap=config.allow_weekend_gap,
                allow_rollover_gap=config.allow_rollover_gap,
            )

        # Temporary collection for this bar to detect intra-bar ambiguities
        bar_events: list[LiquiditySweepEvent] = []
        bar_bsl_events: list[LiquiditySweepEvent] = []
        bar_ssl_events: list[LiquiditySweepEvent] = []

        for tr in trackers:
            lvl = tr.level

            # CRITICAL PIT RULE (Section 5):
            # Swing confirmation must strictly precede the sweep bar's opening time!
            if lvl.level_confirmation_time > bar.open_time:
                continue

            # Inactive state checks:
            # If already breached, the level is structurally destroyed
            if tr.state == LevelLifecycleState.BREACHED:
                continue

            # If already swept and single-sweep consumption is configured
            if tr.state == LevelLifecycleState.SWEPT and config.consume_level_after_sweep:
                continue

            # If touched and sweeps after touch are disallowed
            if tr.state == LevelLifecycleState.TOUCHED and not config.allow_sweeps_after_touch:
                continue

            lvl_price = lvl.level_price

            # ── A. Evaluate Buy-Side Liquidity (Swing High) ───────────────────
            if lvl.swing_type == SwingType.SWING_HIGH:
                # Does candle reach or exceed the swing high?
                if bar.high >= lvl_price:
                    penetration_amount = bar.high - lvl_price
                    pen_ticks = (
                        (penetration_amount / config.tick_size)
                        if config.tick_size > 0
                        else Decimal("0")
                    )

                    # Equal high is NOT a sweep (Section 3 & 4)
                    if bar.high == lvl_price:
                        # Exact touch
                        tr.state = LevelLifecycleState.TOUCHED
                        tr.touch_count += 1
                        tr.last_touch_time = bar.close_time
                        ev_id = generate_liquidity_sweep_id(
                            bar.instrument_id,
                            bar.source_id,
                            bar.price_type,
                            bar.timeframe,
                            LiquiditySide.BUY_SIDE,
                            LiquiditySweepStatus.TOUCH,
                            lvl.swing_id,
                            bar.open_time,
                        )
                        flags = ("DATA_GAP_PRECEDING",) if gap_flag else ()
                        ev = LiquiditySweepEvent(
                            event_id=ev_id,
                            instrument_id=bar.instrument_id,
                            source_id=bar.source_id,
                            timeframe=bar.timeframe,
                            price_type=bar.price_type,
                            source_swing_id=lvl.swing_id,
                            liquidity_side=LiquiditySide.BUY_SIDE,
                            level_price=lvl_price,
                            penetration_price=bar.high,
                            penetration_ticks=pen_ticks,
                            penetration_threshold=threshold,
                            sweep_bar_open_time=bar.open_time,
                            sweep_bar_close_time=bar.close_time,
                            detection_time=bar.close_time,
                            pivot_time=lvl.level_formation_time,
                            level_confirmation_time=lvl.level_confirmation_time,
                            status=LiquiditySweepStatus.TOUCH,
                            data_quality_flags=flags,
                            pit_confidence=effective_policy.value,
                        )
                        bar_events.append(ev)

                    elif bar.high > lvl_price + threshold:
                        # Significant penetration beyond threshold
                        if bar.close < lvl_price:
                            # Confirmed Buy-Side Liquidity Sweep (reclaimed within candle)
                            tr.state = LevelLifecycleState.SWEPT
                            tr.sweep_count += 1
                            ev_id = generate_liquidity_sweep_id(
                                bar.instrument_id,
                                bar.source_id,
                                bar.price_type,
                                bar.timeframe,
                                LiquiditySide.BUY_SIDE,
                                LiquiditySweepStatus.SWEEP,
                                lvl.swing_id,
                                bar.open_time,
                            )
                            flags = ("DATA_GAP_PRECEDING",) if gap_flag else ()
                            ev = LiquiditySweepEvent(
                                event_id=ev_id,
                                instrument_id=bar.instrument_id,
                                source_id=bar.source_id,
                                timeframe=bar.timeframe,
                                price_type=bar.price_type,
                                source_swing_id=lvl.swing_id,
                                liquidity_side=LiquiditySide.BUY_SIDE,
                                level_price=lvl_price,
                                penetration_price=bar.high,
                                penetration_ticks=pen_ticks,
                                penetration_threshold=threshold,
                                sweep_bar_open_time=bar.open_time,
                                sweep_bar_close_time=bar.close_time,
                                detection_time=bar.close_time,
                                pivot_time=lvl.level_formation_time,
                                level_confirmation_time=lvl.level_confirmation_time,
                                status=LiquiditySweepStatus.SWEEP,
                                data_quality_flags=flags,
                                pit_confidence=effective_policy.value,
                            )
                            bar_events.append(ev)
                            bar_bsl_events.append(ev)
                        else:
                            # Penetration without reclaim -> BREACH / Breakout
                            tr.state = LevelLifecycleState.BREACHED
                            tr.breach_time = bar.close_time
                            tr.breach_bar_open = bar.open_time
                            ev_id = generate_liquidity_sweep_id(
                                bar.instrument_id,
                                bar.source_id,
                                bar.price_type,
                                bar.timeframe,
                                LiquiditySide.BUY_SIDE,
                                LiquiditySweepStatus.BREACH,
                                lvl.swing_id,
                                bar.open_time,
                            )
                            flags = ("DATA_GAP_PRECEDING",) if gap_flag else ()
                            ev = LiquiditySweepEvent(
                                event_id=ev_id,
                                instrument_id=bar.instrument_id,
                                source_id=bar.source_id,
                                timeframe=bar.timeframe,
                                price_type=bar.price_type,
                                source_swing_id=lvl.swing_id,
                                liquidity_side=LiquiditySide.BUY_SIDE,
                                level_price=lvl_price,
                                penetration_price=bar.high,
                                penetration_ticks=pen_ticks,
                                penetration_threshold=threshold,
                                sweep_bar_open_time=bar.open_time,
                                sweep_bar_close_time=bar.close_time,
                                detection_time=bar.close_time,
                                pivot_time=lvl.level_formation_time,
                                level_confirmation_time=lvl.level_confirmation_time,
                                status=LiquiditySweepStatus.BREACH,
                                data_quality_flags=flags,
                                pit_confidence=effective_policy.value,
                            )
                            bar_events.append(ev)

                    else:
                        # Penetration was positive but <= threshold (insufficient penetration)
                        if bar.close <= lvl_price:
                            tr.state = LevelLifecycleState.TOUCHED
                            tr.touch_count += 1
                            tr.last_touch_time = bar.close_time
                            ev_id = generate_liquidity_sweep_id(
                                bar.instrument_id,
                                bar.source_id,
                                bar.price_type,
                                bar.timeframe,
                                LiquiditySide.BUY_SIDE,
                                LiquiditySweepStatus.TOUCH,
                                lvl.swing_id,
                                bar.open_time,
                            )
                            touch_flags: tuple[str, ...] = ("INSUFFICIENT_PENETRATION",)
                            if gap_flag:
                                touch_flags = (*touch_flags, "DATA_GAP_PRECEDING")
                            ev = LiquiditySweepEvent(
                                event_id=ev_id,
                                instrument_id=bar.instrument_id,
                                source_id=bar.source_id,
                                timeframe=bar.timeframe,
                                price_type=bar.price_type,
                                source_swing_id=lvl.swing_id,
                                liquidity_side=LiquiditySide.BUY_SIDE,
                                level_price=lvl_price,
                                penetration_price=bar.high,
                                penetration_ticks=pen_ticks,
                                penetration_threshold=threshold,
                                sweep_bar_open_time=bar.open_time,
                                sweep_bar_close_time=bar.close_time,
                                detection_time=bar.close_time,
                                pivot_time=lvl.level_formation_time,
                                level_confirmation_time=lvl.level_confirmation_time,
                                status=LiquiditySweepStatus.TOUCH,
                                data_quality_flags=touch_flags,
                                pit_confidence=effective_policy.value,
                            )
                            bar_events.append(ev)
                        else:
                            # Closed above level despite small wick -> Breach
                            tr.state = LevelLifecycleState.BREACHED
                            tr.breach_time = bar.close_time
                            tr.breach_bar_open = bar.open_time
                            ev_id = generate_liquidity_sweep_id(
                                bar.instrument_id,
                                bar.source_id,
                                bar.price_type,
                                bar.timeframe,
                                LiquiditySide.BUY_SIDE,
                                LiquiditySweepStatus.BREACH,
                                lvl.swing_id,
                                bar.open_time,
                            )
                            flags = ("DATA_GAP_PRECEDING",) if gap_flag else ()
                            ev = LiquiditySweepEvent(
                                event_id=ev_id,
                                instrument_id=bar.instrument_id,
                                source_id=bar.source_id,
                                timeframe=bar.timeframe,
                                price_type=bar.price_type,
                                source_swing_id=lvl.swing_id,
                                liquidity_side=LiquiditySide.BUY_SIDE,
                                level_price=lvl_price,
                                penetration_price=bar.high,
                                penetration_ticks=pen_ticks,
                                penetration_threshold=threshold,
                                sweep_bar_open_time=bar.open_time,
                                sweep_bar_close_time=bar.close_time,
                                detection_time=bar.close_time,
                                pivot_time=lvl.level_formation_time,
                                level_confirmation_time=lvl.level_confirmation_time,
                                status=LiquiditySweepStatus.BREACH,
                                data_quality_flags=flags,
                                pit_confidence=effective_policy.value,
                            )
                            bar_events.append(ev)

            # ── B. Evaluate Sell-Side Liquidity (Swing Low) ───────────────────
            elif lvl.swing_type == SwingType.SWING_LOW:  # noqa: SIM102
                # Does candle reach or drop below the swing low?
                if bar.low <= lvl_price:
                    penetration_amount = lvl_price - bar.low
                    pen_ticks = (
                        (penetration_amount / config.tick_size)
                        if config.tick_size > 0
                        else Decimal("0")
                    )

                    # Equal low is NOT a sweep (Section 3 & 4)
                    if bar.low == lvl_price:
                        # Exact touch
                        tr.state = LevelLifecycleState.TOUCHED
                        tr.touch_count += 1
                        tr.last_touch_time = bar.close_time
                        ev_id = generate_liquidity_sweep_id(
                            bar.instrument_id,
                            bar.source_id,
                            bar.price_type,
                            bar.timeframe,
                            LiquiditySide.SELL_SIDE,
                            LiquiditySweepStatus.TOUCH,
                            lvl.swing_id,
                            bar.open_time,
                        )
                        flags = ("DATA_GAP_PRECEDING",) if gap_flag else ()
                        ev = LiquiditySweepEvent(
                            event_id=ev_id,
                            instrument_id=bar.instrument_id,
                            source_id=bar.source_id,
                            timeframe=bar.timeframe,
                            price_type=bar.price_type,
                            source_swing_id=lvl.swing_id,
                            liquidity_side=LiquiditySide.SELL_SIDE,
                            level_price=lvl_price,
                            penetration_price=bar.low,
                            penetration_ticks=pen_ticks,
                            penetration_threshold=threshold,
                            sweep_bar_open_time=bar.open_time,
                            sweep_bar_close_time=bar.close_time,
                            detection_time=bar.close_time,
                            pivot_time=lvl.level_formation_time,
                            level_confirmation_time=lvl.level_confirmation_time,
                            status=LiquiditySweepStatus.TOUCH,
                            data_quality_flags=flags,
                            pit_confidence=effective_policy.value,
                        )
                        bar_events.append(ev)

                    elif bar.low < lvl_price - threshold:
                        # Significant penetration beyond threshold
                        if bar.close > lvl_price:
                            # Confirmed Sell-Side Liquidity Sweep (reclaimed within candle)
                            tr.state = LevelLifecycleState.SWEPT
                            tr.sweep_count += 1
                            ev_id = generate_liquidity_sweep_id(
                                bar.instrument_id,
                                bar.source_id,
                                bar.price_type,
                                bar.timeframe,
                                LiquiditySide.SELL_SIDE,
                                LiquiditySweepStatus.SWEEP,
                                lvl.swing_id,
                                bar.open_time,
                            )
                            flags = ("DATA_GAP_PRECEDING",) if gap_flag else ()
                            ev = LiquiditySweepEvent(
                                event_id=ev_id,
                                instrument_id=bar.instrument_id,
                                source_id=bar.source_id,
                                timeframe=bar.timeframe,
                                price_type=bar.price_type,
                                source_swing_id=lvl.swing_id,
                                liquidity_side=LiquiditySide.SELL_SIDE,
                                level_price=lvl_price,
                                penetration_price=bar.low,
                                penetration_ticks=pen_ticks,
                                penetration_threshold=threshold,
                                sweep_bar_open_time=bar.open_time,
                                sweep_bar_close_time=bar.close_time,
                                detection_time=bar.close_time,
                                pivot_time=lvl.level_formation_time,
                                level_confirmation_time=lvl.level_confirmation_time,
                                status=LiquiditySweepStatus.SWEEP,
                                data_quality_flags=flags,
                                pit_confidence=effective_policy.value,
                            )
                            bar_events.append(ev)
                            bar_ssl_events.append(ev)
                        else:
                            # Penetration without reclaim -> BREACH / Breakout
                            tr.state = LevelLifecycleState.BREACHED
                            tr.breach_time = bar.close_time
                            tr.breach_bar_open = bar.open_time
                            ev_id = generate_liquidity_sweep_id(
                                bar.instrument_id,
                                bar.source_id,
                                bar.price_type,
                                bar.timeframe,
                                LiquiditySide.SELL_SIDE,
                                LiquiditySweepStatus.BREACH,
                                lvl.swing_id,
                                bar.open_time,
                            )
                            flags = ("DATA_GAP_PRECEDING",) if gap_flag else ()
                            ev = LiquiditySweepEvent(
                                event_id=ev_id,
                                instrument_id=bar.instrument_id,
                                source_id=bar.source_id,
                                timeframe=bar.timeframe,
                                price_type=bar.price_type,
                                source_swing_id=lvl.swing_id,
                                liquidity_side=LiquiditySide.SELL_SIDE,
                                level_price=lvl_price,
                                penetration_price=bar.low,
                                penetration_ticks=pen_ticks,
                                penetration_threshold=threshold,
                                sweep_bar_open_time=bar.open_time,
                                sweep_bar_close_time=bar.close_time,
                                detection_time=bar.close_time,
                                pivot_time=lvl.level_formation_time,
                                level_confirmation_time=lvl.level_confirmation_time,
                                status=LiquiditySweepStatus.BREACH,
                                data_quality_flags=flags,
                                pit_confidence=effective_policy.value,
                            )
                            bar_events.append(ev)

                    else:
                        # Penetration was positive but <= threshold (insufficient penetration)
                        if bar.close >= lvl_price:
                            tr.state = LevelLifecycleState.TOUCHED
                            tr.touch_count += 1
                            tr.last_touch_time = bar.close_time
                            ev_id = generate_liquidity_sweep_id(
                                bar.instrument_id,
                                bar.source_id,
                                bar.price_type,
                                bar.timeframe,
                                LiquiditySide.SELL_SIDE,
                                LiquiditySweepStatus.TOUCH,
                                lvl.swing_id,
                                bar.open_time,
                            )
                            touch_ssl_flags: tuple[str, ...] = ("INSUFFICIENT_PENETRATION",)
                            if gap_flag:
                                touch_ssl_flags = (*touch_ssl_flags, "DATA_GAP_PRECEDING")
                            ev = LiquiditySweepEvent(
                                event_id=ev_id,
                                instrument_id=bar.instrument_id,
                                source_id=bar.source_id,
                                timeframe=bar.timeframe,
                                price_type=bar.price_type,
                                source_swing_id=lvl.swing_id,
                                liquidity_side=LiquiditySide.SELL_SIDE,
                                level_price=lvl_price,
                                penetration_price=bar.low,
                                penetration_ticks=pen_ticks,
                                penetration_threshold=threshold,
                                sweep_bar_open_time=bar.open_time,
                                sweep_bar_close_time=bar.close_time,
                                detection_time=bar.close_time,
                                pivot_time=lvl.level_formation_time,
                                level_confirmation_time=lvl.level_confirmation_time,
                                status=LiquiditySweepStatus.TOUCH,
                                data_quality_flags=touch_ssl_flags,
                                pit_confidence=effective_policy.value,
                            )
                            bar_events.append(ev)
                        else:
                            # Closed below level despite small wick -> Breach
                            tr.state = LevelLifecycleState.BREACHED
                            tr.breach_time = bar.close_time
                            tr.breach_bar_open = bar.open_time
                            ev_id = generate_liquidity_sweep_id(
                                bar.instrument_id,
                                bar.source_id,
                                bar.price_type,
                                bar.timeframe,
                                LiquiditySide.SELL_SIDE,
                                LiquiditySweepStatus.BREACH,
                                lvl.swing_id,
                                bar.open_time,
                            )
                            flags = ("DATA_GAP_PRECEDING",) if gap_flag else ()
                            ev = LiquiditySweepEvent(
                                event_id=ev_id,
                                instrument_id=bar.instrument_id,
                                source_id=bar.source_id,
                                timeframe=bar.timeframe,
                                price_type=bar.price_type,
                                source_swing_id=lvl.swing_id,
                                liquidity_side=LiquiditySide.SELL_SIDE,
                                level_price=lvl_price,
                                penetration_price=bar.low,
                                penetration_ticks=pen_ticks,
                                penetration_threshold=threshold,
                                sweep_bar_open_time=bar.open_time,
                                sweep_bar_close_time=bar.close_time,
                                detection_time=bar.close_time,
                                pivot_time=lvl.level_formation_time,
                                level_confirmation_time=lvl.level_confirmation_time,
                                status=LiquiditySweepStatus.BREACH,
                                data_quality_flags=flags,
                                pit_confidence=effective_policy.value,
                            )
                            bar_events.append(ev)

        # ── C. Ambiguity Check: Same-Bar Opposing Extremes (Section 10) ───────
        if bar_bsl_events and bar_ssl_events:
            # Both BSL sweep and SSL sweep occurred on the EXACT SAME CANDLE!
            # OHLC cannot determine intra-bar execution order without tick data.
            # Convert or flag as AMBIGUOUS if configured.
            for ev in bar_events:
                if ev in bar_bsl_events or ev in bar_ssl_events:
                    amb_flags = (*ev.data_quality_flags, "AMBIGUOUS_SAME_BAR_EXTREMES")
                    if config.allow_ambiguous:
                        amb_id = generate_liquidity_sweep_id(
                            ev.instrument_id,
                            ev.source_id,
                            ev.price_type,
                            ev.timeframe,
                            ev.liquidity_side,
                            LiquiditySweepStatus.AMBIGUOUS,
                            ev.source_swing_id,
                            ev.sweep_bar_open_time,
                        )
                        amb_ev = LiquiditySweepEvent(
                            event_id=amb_id,
                            instrument_id=ev.instrument_id,
                            source_id=ev.source_id,
                            timeframe=ev.timeframe,
                            price_type=ev.price_type,
                            source_swing_id=ev.source_swing_id,
                            liquidity_side=ev.liquidity_side,
                            level_price=ev.level_price,
                            penetration_price=ev.penetration_price,
                            penetration_ticks=ev.penetration_ticks,
                            penetration_threshold=ev.penetration_threshold,
                            sweep_bar_open_time=ev.sweep_bar_open_time,
                            sweep_bar_close_time=ev.sweep_bar_close_time,
                            detection_time=ev.detection_time,
                            pivot_time=ev.pivot_time,
                            level_confirmation_time=ev.level_confirmation_time,
                            status=LiquiditySweepStatus.AMBIGUOUS,
                            data_quality_flags=amb_flags,
                            pit_confidence=ev.pit_confidence,
                        )
                        events.append(amb_ev)
                    else:
                        events.append(ev)
                else:
                    events.append(ev)
        else:
            events.extend(bar_events)

    # Sort deterministically by detection_time ASC, sweep_bar_open_time ASC, event_id ASC
    events.sort(key=lambda e: (e.detection_time, e.sweep_bar_open_time, e.event_id))
    return events


# ── Query & Filtering Helpers ────────────────────────────────────────────────
def filter_confirmed_sweeps(
    events: Sequence[LiquiditySweepEvent],
    as_of_utc: datetime | None = None,
) -> list[LiquiditySweepEvent]:
    """Filter confirmed liquidity sweep events known as of a Point-in-Time timestamp.

    Guarantees:
    - Only events with `status == LiquiditySweepStatus.SWEEP` are returned.
    - Excludes touches, breaches, and unverified/ambiguous events.
    - Any event whose `detection_time > as_of_utc` is excluded.
    """
    as_of_norm = ensure_utc(as_of_utc) if as_of_utc is not None else None
    confirmed: list[LiquiditySweepEvent] = []
    for ev in events:
        if ev.status != LiquiditySweepStatus.SWEEP:
            continue
        if as_of_norm is not None and ev.detection_time > as_of_norm:
            continue
        confirmed.append(ev)
    return confirmed


def summarize_liquidity_events(
    events: Sequence[LiquiditySweepEvent],
    as_of_utc: datetime | None = None,
) -> dict[str, int]:
    """Summarize counts of sweep events partitioned by type and side."""
    as_of_norm = ensure_utc(as_of_utc) if as_of_utc is not None else None
    counts = {
        "total_events": 0,
        "confirmed_sweeps": 0,
        "buy_side_sweeps": 0,
        "sell_side_sweeps": 0,
        "touches": 0,
        "breaches": 0,
        "ambiguous": 0,
    }
    for ev in events:
        if as_of_norm is not None and ev.detection_time > as_of_norm:
            continue
        counts["total_events"] += 1
        if ev.status == LiquiditySweepStatus.SWEEP:
            counts["confirmed_sweeps"] += 1
            if ev.liquidity_side == LiquiditySide.BUY_SIDE:
                counts["buy_side_sweeps"] += 1
            else:
                counts["sell_side_sweeps"] += 1
        elif ev.status == LiquiditySweepStatus.TOUCH:
            counts["touches"] += 1
        elif ev.status == LiquiditySweepStatus.BREACH:
            counts["breaches"] += 1
        elif ev.status == LiquiditySweepStatus.AMBIGUOUS:
            counts["ambiguous"] += 1

    return counts
