"""Forex instrument registry — canonical instrument definitions for Forex.

Provides factory functions for well-known forex instruments.
These are the "source of truth" definitions used throughout the system.
"""

from __future__ import annotations

from decimal import Decimal

from traderlab.core.types import InstrumentId, MarketType
from traderlab.domain.instruments.instrument import Asset, Instrument

# ── Well-known assets ───────────────────────────────────────────────────────
XAU = Asset(symbol="XAU", name="Gold")
USD = Asset(symbol="USD", name="US Dollar")
EUR = Asset(symbol="EUR", name="Euro")


def xauusd() -> Instrument:
    """XAUUSD — Gold vs US Dollar (Forex CFD).

    Typical properties:
        - tick_size: 0.01 (1 cent)
        - pip_size: 0.10 (10 cents / 10 ticks for standard FX CFD)
        - price_precision: 2 decimal places
        - lot_size: 1 (1 troy ounce for micro accounts, varies by broker)
    """
    return Instrument(
        id=InstrumentId("XAUUSD"),
        base=XAU,
        quote=USD,
        market_type=MarketType.FOREX,
        tick_size=Decimal("0.01"),
        pip_size=Decimal("0.10"),
        price_precision=2,
        lot_size=Decimal("1"),
    )


def eurusd() -> Instrument:
    """EURUSD — Euro vs US Dollar."""
    return Instrument(
        id=InstrumentId("EURUSD"),
        base=EUR,
        quote=USD,
        market_type=MarketType.FOREX,
        tick_size=Decimal("0.00001"),
        pip_size=Decimal("0.0001"),
        price_precision=5,
        lot_size=Decimal("100000"),
    )
