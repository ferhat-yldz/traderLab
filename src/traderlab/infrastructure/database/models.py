"""SQLAlchemy ORM models for financial time-series data storage.

Implements the append-only bitemporal candle schema for TraderLab.
All timestamps are strictly UTC-aware, and financial values use high-precision NUMERIC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
    ensure_utc,
)
from traderlab.domain.instruments.candle import Candle


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
    return ensure_utc(dt)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy declarative models."""


class CandleModel(Base):
    """PostgreSQL storage model for financial candles with bitemporal revision tracking.

    Uses an append-only versioning strategy: revisions are persisted as new rows
    with an incremented `revision` integer. This eliminates the risk of two-table
    synchronization drift and ensures an immutable audit log.
    """

    __tablename__ = "candles"

    # Natural Primary Key (Compound)
    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timeframe: Mapped[int] = mapped_column(Integer, primary_key=True)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    price_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)

    # Core OHLC prices (NUMERIC(18, 8) guarantees sub-pip precision without float drift)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)

    # Volume and Microstructure (Nullable to distinguish unknown vs zero volume/spread)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    tick_volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    spread: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)

    # Bar State and Integrity
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Bitemporal & Point-in-Time Timestamps
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.clock_timestamp(),
    )
    availability_confidence: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        Index(
            "idx_candles_pit_lookup",
            "source_id",
            "timeframe",
            "price_type",
            "open_time",
            "ingested_at",
            "available_at",
        ),
        Index(
            "idx_candles_instrument_tf",
            "instrument_id",
            "timeframe",
            "open_time",
        ),
    )

    @classmethod
    def from_domain(cls, candle: Candle) -> CandleModel:
        """Construct an ORM model instance from a frozen domain Candle."""
        return cls(
            instrument_id=str(candle.instrument_id),
            source_id=str(candle.source_id),
            timeframe=int(candle.timeframe.value),
            open_time=ensure_utc(candle.open_time),
            close_time=ensure_utc(candle.close_time),
            price_type=candle.price_type.value,
            revision=candle.revision,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            tick_volume=candle.tick_volume,
            spread=candle.spread,
            is_closed=candle.is_closed,
            is_synthetic=candle.is_synthetic,
            published_at=ensure_utc(candle.published_at) if candle.published_at else None,
            received_at=ensure_utc(candle.received_at) if candle.received_at else None,
            available_at=ensure_utc(candle.available_at) if candle.available_at else None,
            ingested_at=ensure_utc(candle.ingested_at)
            if candle.ingested_at
            else func.clock_timestamp(),
            availability_confidence=candle.availability_confidence.value,
        )

    def to_domain(self) -> Candle:
        """Convert this database row into an immutable domain Candle object."""
        return Candle(
            instrument_id=InstrumentId(self.instrument_id),
            source_id=DataSourceId(self.source_id),
            timeframe=Timeframe(self.timeframe),
            open_time=_to_utc(self.open_time),  # type: ignore[arg-type]
            close_time=_to_utc(self.close_time),  # type: ignore[arg-type]
            open=Decimal(str(self.open)),
            high=Decimal(str(self.high)),
            low=Decimal(str(self.low)),
            close=Decimal(str(self.close)),
            volume=Decimal(str(self.volume)) if self.volume is not None else None,
            price_type=PriceType(self.price_type),
            published_at=_to_utc(self.published_at),
            received_at=_to_utc(self.received_at),
            available_at=_to_utc(self.available_at),
            ingested_at=_to_utc(self.ingested_at),
            availability_confidence=AvailabilityConfidence(self.availability_confidence),
            spread=Decimal(str(self.spread)) if self.spread is not None else None,
            tick_volume=Decimal(str(self.tick_volume)) if self.tick_volume is not None else None,
            is_closed=self.is_closed,
            is_synthetic=self.is_synthetic,
            revision=self.revision,
        )
