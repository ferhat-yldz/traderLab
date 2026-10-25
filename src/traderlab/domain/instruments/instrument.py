"""Instrument domain model.

Key design decisions:
    1. **Asset ≠ Instrument**: An *Asset* is the thing being traded (Gold, EUR).
       An *Instrument* is a specific tradeable representation on a specific market
       (XAUUSD on Forex ≠ GLD ETF on NYSE).

    2. **Instrument ≠ DataSource**: The same instrument may have different price
       feeds from different brokers.  ``DataSource`` (see data_source.py) captures
       this distinction.

    3. **Market-specific properties** (tick size, pip value, session hours, cost
       model) live in market-specific adapters under ``traderlab.markets``, NOT
       here.  The domain model holds only the *universally applicable* attributes.

    4. All models are **frozen** dataclasses — value objects that are compared by
       content, not identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from traderlab.core.types import InstrumentId, MarketType


@dataclass(frozen=True, slots=True)
class Asset:
    """A tradeable asset (e.g. Gold, Euro, Bitcoin, Apple Inc.)."""

    symbol: str  # Canonical short name: "XAU", "EUR", "BTC", "AAPL"
    name: str  # Human-readable: "Gold", "Euro", "Bitcoin"


@dataclass(frozen=True, slots=True)
class Instrument:
    """A specific tradeable instrument on a specific market.

    Examples:
        Instrument(
            id=InstrumentId("XAUUSD"),
            base=Asset("XAU", "Gold"),
            quote=Asset("USD", "US Dollar"),
            market_type=MarketType.FOREX,
            tick_size=Decimal("0.01"),
            pip_size=Decimal("0.10"),
            price_precision=2,
            lot_size=Decimal("1"),
        )
    """

    id: InstrumentId
    base: Asset
    quote: Asset
    market_type: MarketType
    tick_size: Decimal
    pip_size: Decimal
    price_precision: int  # number of decimal places in prices
    lot_size: Decimal  # minimum tradeable quantity / contract unit

    def __post_init__(self) -> None:
        if self.tick_size <= 0:
            raise ValueError(f"tick_size must be positive, got {self.tick_size}")
        if self.pip_size <= 0:
            raise ValueError(f"pip_size must be positive, got {self.pip_size}")
        if self.price_precision < 0:
            raise ValueError(f"price_precision must be >= 0, got {self.price_precision}")
        if self.lot_size <= 0:
            raise ValueError(f"lot_size must be positive, got {self.lot_size}")

    @property
    def pip_value_ratio(self) -> Decimal:
        """Unitless ratio of pip size to tick size (e.g. 10 for EURUSD 0.0001 / 0.00001).

        NOTE: This is the ratio of tick quantization per pip, NOT the monetary cash
        value of a pip in quote currency. For cash pip value, use `monetary_pip_value`.
        """
        return self.pip_size / self.tick_size

    @property
    def monetary_pip_value(self) -> Decimal:
        """Cash value of 1 pip for 1 standard lot (lot_size) in quote currency.

        Calculated as: `lot_size * pip_size`.
        Examples:
            - EURUSD (100,000 lot): 100,000 * 0.0001 = $10.00 USD
            - XAUUSD (1 oz lot): 1 * 0.10 = $0.10 USD
        """
        return self.lot_size * self.pip_size
