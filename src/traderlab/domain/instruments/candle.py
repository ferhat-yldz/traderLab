"""Candle (OHLCV) domain model with explicit price types and bitemporal point-in-time tracking.

Key architectural and financial principles:
    1. **Immutable** — value object with `frozen=True, slots=True`.
    2. **Explicit Price Types** — BID, ASK, MID, UNKNOWN. Never silently defaulted to BID.
       `PriceType.UNKNOWN` can be stored in raw feeds, but `validate_for_trading()` rejects
       it for execution and strategy calculations.
    3. **Bitemporal Point-in-Time** — separates event time (open/close) from publication,
       receipt, decision availability, and storage ingestion times.
    4. **Separation of Domain and Persistence** — `ingested_at` is `None` on in-memory domain
       objects until stamped by an infrastructure repository upon database commit.
    5. **PIT Evaluation Policies** — `STRICT_PIT` requires verified availability timestamps;
       `RESEARCH_ESTIMATED` permits conservative close_time heuristics for historical backfills.
    6. **Range Midpoint vs True Midpoint** — `(high + low) / 2` is the geometric range
       midpoint, distinct from true market bid/ask midpoint.
    7. **Spread Semantics** — spread is time-varying; missing spread is represented as `None`
       and NEVER assumed to be zero.
    8. **Synthetic vs Raw** — synthetic/interpolated candles are explicitly flagged (`is_synthetic`).
    9. **Strict UTC Validation** — all datetimes normalized to UTC; naive datetimes rejected.
    10. **Price Positivity** — enforced for standard instruments (e.g. XAUUSD, EURUSD), while
        permitting negative prices only when explicitly configured (`allow_negative_prices=True`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from traderlab.core.errors import InvalidCandleError
from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
    ensure_utc,
)


@dataclass(frozen=True, slots=True)
class Candle:
    """A single OHLCV bar with explicit market semantics and bitemporal point-in-time tracking."""

    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    open_time: datetime  # UTC-aware, bar open
    close_time: datetime  # UTC-aware, bar close
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None  # None if unknown; Decimal(0) if explicitly 0 volume
    price_type: PriceType  # Explicit: BID, ASK, MID, UNKNOWN. No silent default!

    # ── Point-in-Time and Bitemporal tracking ──────────────────────────────
    published_at: datetime | None = None  # Provider publication timestamp
    received_at: datetime | None = None  # Network gateway reception timestamp
    available_at: datetime | None = None  # Earliest confirmed decision-time timestamp
    ingested_at: datetime | None = None  # Storage commit timestamp (None before persistence)
    availability_confidence: AvailabilityConfidence = AvailabilityConfidence.UNKNOWN

    # ── Market microstructure semantics ──────────────────────────────────
    spread: Decimal | None = None  # None if unknown; NEVER assumed to be 0!
    tick_volume: Decimal | None = None  # Number of price updates if reported by broker
    is_closed: bool = True  # True if bar is finalized; incomplete bars cannot be used as closed
    is_synthetic: bool = False  # True if derived/interpolated (never treated as raw feed)
    revision: int = 0  # Monotonically increasing revision counter (0 for original)
    allow_negative_prices: bool = False  # False for Forex/Gold; True for negative commodities

    def __post_init__(self) -> None:
        self._validate_and_normalize()

    # ── Validation and Normalization ────────────────────────────────────
    def _validate_and_normalize(self) -> None:
        if not isinstance(self.price_type, PriceType):
            raise InvalidCandleError(
                f"price_type must be an instance of PriceType enum, got {self.price_type!r}"
            )

        # Normalize open_time and close_time
        try:
            norm_open = ensure_utc(self.open_time)
            object.__setattr__(self, "open_time", norm_open)
        except ValueError as exc:
            raise InvalidCandleError(f"open_time invalid: {exc}") from exc

        try:
            norm_close = ensure_utc(self.close_time)
            object.__setattr__(self, "close_time", norm_close)
        except ValueError as exc:
            raise InvalidCandleError(f"close_time invalid: {exc}") from exc

        if self.close_time <= self.open_time:
            raise InvalidCandleError("close_time must be after open_time")

        # Normalize optional timestamps if provided
        for ts_name in ("published_at", "received_at", "available_at", "ingested_at"):
            val = getattr(self, ts_name)
            if val is not None:
                try:
                    object.__setattr__(self, ts_name, ensure_utc(val))
                except ValueError as exc:
                    raise InvalidCandleError(f"{ts_name} invalid: {exc}") from exc

        # OHLC logic
        if self.high < self.low:
            raise InvalidCandleError("high must be >= low")
        if self.high < self.open or self.high < self.close:
            raise InvalidCandleError("high must be >= open and close")
        if self.low > self.open or self.low > self.close:
            raise InvalidCandleError("low must be <= open and close")

        # Price positivity
        if not self.allow_negative_prices and (
            self.open <= 0 or self.high <= 0 or self.low <= 0 or self.close <= 0
        ):
            raise InvalidCandleError("prices must be strictly positive for standard instruments")

        # Volume
        if self.volume is not None and self.volume < 0:
            raise InvalidCandleError("volume must be >= 0")

        # Tick Volume
        if self.tick_volume is not None and self.tick_volume < 0:
            raise InvalidCandleError("tick_volume must be >= 0")

        # Spread
        if self.spread is not None and self.spread < 0:
            raise InvalidCandleError("spread cannot be negative")

        # Revision
        if self.revision < 0:
            raise InvalidCandleError("revision cannot be negative")

    # ── Derived properties ──────────────────────────────────────────────
    @property
    def body_size(self) -> Decimal:
        """Absolute size of the candle body."""
        return abs(self.close - self.open)

    @property
    def range(self) -> Decimal:
        """High-to-low range of the bar."""
        return self.high - self.low

    @property
    def range_midpoint(self) -> Decimal:
        """Midpoint of high and low: (high + low) / 2.

        NOTE: This is the bar's geometric range midpoint, NOT the true bid/ask
        spread midpoint of the market. To obtain true bid/ask mid, evaluate
        simultaneous BID and ASK candles.
        """
        return (self.high + self.low) / 2

    @property
    def mid_price(self) -> Decimal:
        """Legacy alias for range_midpoint."""
        return self.range_midpoint

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def is_tradable_price(self) -> bool:
        """Return True if price_type is an explicit, verified market series (BID, ASK, or MID)."""
        return self.price_type in (PriceType.BID, PriceType.ASK, PriceType.MID)

    def validate_for_trading(self) -> None:
        """Ensure candle has an actionable, explicit price series for trading and backtesting.

        Raises:
            InvalidCandleError: If price_type is PriceType.UNKNOWN.
        """
        if self.price_type == PriceType.UNKNOWN:
            raise InvalidCandleError(
                "Candle has PriceType.UNKNOWN; cannot be used for trade execution or pricing calculations"
            )

    # ── Point-in-Time Usability ─────────────────────────────────────────
    def is_available_at(
        self,
        as_of: datetime,
        *,
        policy: PitPolicy = PitPolicy.STRICT_PIT,
        knowledge_snapshot_time: datetime | None = None,
    ) -> bool:
        """Check if this candle was available at decision time `as_of`.

        Args:
            as_of: Decision-time timestamp (simulated execution time, must be UTC-aware).
            policy: PitPolicy.STRICT_PIT (default) or PitPolicy.RESEARCH_ESTIMATED.
            knowledge_snapshot_time: Bitemporal system snapshot timestamp.
                If provided, filters out any candle or revision committed to storage after
                this time (`ingested_at <= knowledge_snapshot_time`).
                Under `STRICT_PIT`, if omitted, defaults to `as_of` (live system invariant:
                a live decision cannot use data not yet committed to storage).
                Under `RESEARCH_ESTIMATED`, omitted means unconstrained historical snapshot
                (permits historical backfill research where ingestion occurred after event).

        Rules:
        1. If the candle is not closed (is_closed=False), it cannot be used
           for closed-bar historical analysis under any policy.
        2. Snapshot leakage check:
           If `knowledge_snapshot_time` is active, reject if `ingested_at > snapshot_time`.
        3. Under STRICT_PIT:
           - Requires `availability_confidence == AvailabilityConfidence.VERIFIED`.
           - Requires `available_at is not None` and `available_at <= as_of_norm`.
           - Rejects UNKNOWN or ESTIMATED confidence bars.
        4. Under RESEARCH_ESTIMATED:
           - If `available_at` is present, requires `available_at <= as_of_norm`.
           - If `available_at` is None, allows usage if `close_time <= as_of_norm`
             (conservative completed-bar heuristic for historical bulk backfills).
        """
        as_of_norm = ensure_utc(as_of)
        if not self.is_closed:
            return False

        # Determine effective knowledge snapshot time
        effective_snapshot = (
            ensure_utc(knowledge_snapshot_time)
            if knowledge_snapshot_time is not None
            else (as_of_norm if policy == PitPolicy.STRICT_PIT else None)
        )

        if (
            effective_snapshot is not None
            and self.ingested_at is not None
            and self.ingested_at > effective_snapshot
        ):
            return False

        if policy == PitPolicy.STRICT_PIT:
            if self.availability_confidence != AvailabilityConfidence.VERIFIED:
                return False
            if self.available_at is None:
                return False
            return self.available_at <= as_of_norm

        if policy == PitPolicy.RESEARCH_ESTIMATED:
            if self.available_at is not None:
                return self.available_at <= as_of_norm
            return self.close_time <= as_of_norm

        return False
