"""DataSource value object.

Separates the *instrument* from the *provider supplying its price feed*.

The same XAUUSD instrument may have subtly different OHLCV data from
different brokers or data vendors.  Keeping this distinction explicit is
critical for:
    - Data lineage and reproducibility
    - Point-in-time accuracy
    - Cross-source comparison / validation
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from traderlab.core.types import DataSourceId, InstrumentId, ProviderId
from traderlab.domain.instruments.instrument import Instrument


@dataclass(frozen=True, slots=True)
class DataSource:
    """Identifies a specific price-feed for a specific instrument.

    Supports broker-specific microstructure overrides (pip, tick, lot, precision, spread).
    When an override is None, the canonical Instrument definition is used.
    """

    id: DataSourceId
    instrument_id: InstrumentId
    provider_id: ProviderId
    provider_symbol: str  # Symbol as known by the provider (e.g. "XAUUSD.sml")
    description: str = ""
    tick_size_override: Decimal | None = None
    pip_size_override: Decimal | None = None
    lot_size_override: Decimal | None = None
    price_precision_override: int | None = None
    typical_spread: Decimal | None = None

    def __post_init__(self) -> None:
        if self.tick_size_override is not None and self.tick_size_override <= 0:
            raise ValueError(f"tick_size_override must be positive, got {self.tick_size_override}")
        if self.pip_size_override is not None and self.pip_size_override <= 0:
            raise ValueError(f"pip_size_override must be positive, got {self.pip_size_override}")
        if self.lot_size_override is not None and self.lot_size_override <= 0:
            raise ValueError(f"lot_size_override must be positive, got {self.lot_size_override}")
        if self.price_precision_override is not None and self.price_precision_override < 0:
            raise ValueError(
                f"price_precision_override must be >= 0, got {self.price_precision_override}"
            )
        if self.typical_spread is not None and self.typical_spread < 0:
            raise ValueError(f"typical_spread cannot be negative, got {self.typical_spread}")

    def _verify_instrument(self, instrument: Instrument) -> None:
        if instrument.id != self.instrument_id:
            raise ValueError(
                f"Instrument id mismatch: DataSource requires '{self.instrument_id}', "
                f"got '{instrument.id}'"
            )

    def effective_tick_size(self, instrument: Instrument) -> Decimal:
        """Return broker-specific tick size if overridden, else instrument tick size."""
        self._verify_instrument(instrument)
        if self.tick_size_override is not None:
            return self.tick_size_override
        return instrument.tick_size

    def effective_pip_size(self, instrument: Instrument) -> Decimal:
        """Return broker-specific pip size if overridden, else instrument pip size."""
        self._verify_instrument(instrument)
        if self.pip_size_override is not None:
            return self.pip_size_override
        return instrument.pip_size

    def effective_lot_size(self, instrument: Instrument) -> Decimal:
        """Return broker-specific lot size if overridden, else instrument lot size."""
        self._verify_instrument(instrument)
        if self.lot_size_override is not None:
            return self.lot_size_override
        return instrument.lot_size

    def effective_price_precision(self, instrument: Instrument) -> int:
        """Return broker-specific price precision if overridden, else instrument precision."""
        self._verify_instrument(instrument)
        if self.price_precision_override is not None:
            return self.price_precision_override
        return instrument.price_precision

    def __str__(self) -> str:
        return f"{self.provider_id}:{self.provider_symbol}"


@dataclass(frozen=True, slots=True)
class DataSourceMetadata:
    """Metadata about the data we hold from a given source.

    Used for data-quality tracking and ingestion orchestration.
    """

    source_id: DataSourceId
    earliest_utc: datetime | None = None
    latest_utc: datetime | None = None
    total_candles: int = 0
    last_ingestion_utc: datetime | None = None
