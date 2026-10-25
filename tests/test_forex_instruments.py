"""Unit tests for forex instrument definitions."""

from __future__ import annotations

from decimal import Decimal

from traderlab.core.types import MarketType
from traderlab.markets.forex.instruments import eurusd, xauusd


class TestForexInstruments:
    def test_xauusd(self) -> None:
        inst = xauusd()
        assert inst.id == "XAUUSD"
        assert inst.base.symbol == "XAU"
        assert inst.quote.symbol == "USD"
        assert inst.market_type == MarketType.FOREX
        assert inst.tick_size == Decimal("0.01")
        assert inst.pip_size == Decimal("0.10")
        assert inst.pip_value_ratio == Decimal("10")
        assert inst.monetary_pip_value == Decimal("0.10")
        assert inst.price_precision == 2
        assert inst.lot_size == Decimal("1")

    def test_eurusd(self) -> None:
        inst = eurusd()
        assert inst.id == "EURUSD"
        assert inst.base.symbol == "EUR"
        assert inst.market_type == MarketType.FOREX
        assert inst.tick_size == Decimal("0.00001")
        assert inst.pip_size == Decimal("0.0001")
        assert inst.pip_value_ratio == Decimal("10")
        assert inst.monetary_pip_value == Decimal("10.0000")
        assert inst.price_precision == 5
        assert inst.lot_size == Decimal("100000")

    def test_instruments_are_distinct(self) -> None:
        assert xauusd() != eurusd()
