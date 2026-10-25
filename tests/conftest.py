"""Shared test fixtures."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from decimal import Decimal

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest

from traderlab.core.types import DataSourceId, InstrumentId, MarketType, PriceType, Timeframe
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.instruments.data_source import DataSource
from traderlab.domain.instruments.instrument import Asset, Instrument


# ── Assets ──────────────────────────────────────────────────────────────────
@pytest.fixture
def xau() -> Asset:
    return Asset(symbol="XAU", name="Gold")


@pytest.fixture
def usd() -> Asset:
    return Asset(symbol="USD", name="US Dollar")


# ── Instruments ─────────────────────────────────────────────────────────────
@pytest.fixture
def xauusd(xau: Asset, usd: Asset) -> Instrument:
    return Instrument(
        id=InstrumentId("XAUUSD"),
        base=xau,
        quote=usd,
        market_type=MarketType.FOREX,
        tick_size=Decimal("0.01"),
        pip_size=Decimal("0.10"),
        price_precision=2,
        lot_size=Decimal("1"),
    )


# ── Data Sources ────────────────────────────────────────────────────────────
@pytest.fixture
def sample_source() -> DataSource:
    return DataSource(
        id=DataSourceId("test-broker-xauusd"),
        instrument_id=InstrumentId("XAUUSD"),
        provider_id="test-broker",
        provider_symbol="XAUUSD.test",
        description="Test broker XAUUSD feed",
    )


# ── Candles ─────────────────────────────────────────────────────────────────
@pytest.fixture
def sample_candle() -> Candle:
    """A single valid H1 XAUUSD candle."""
    return Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("test-broker-xauusd"),
        timeframe=Timeframe.H1,
        open_time=datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC),
        close_time=datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC),
        open=Decimal("2030.50"),
        high=Decimal("2035.75"),
        low=Decimal("2028.00"),
        close=Decimal("2033.25"),
        volume=Decimal("1500"),
        price_type=PriceType.BID,
        is_closed=True,
    )
