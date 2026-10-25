"""Unit tests for domain models — Instrument, Candle, DataSource."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from traderlab.core.errors import InvalidCandleError
from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    MarketType,
    PriceType,
    ProviderId,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.instruments.data_source import DataSource, DataSourceMetadata
from traderlab.domain.instruments.instrument import Asset, Instrument


class TestAsset:
    def test_frozen(self) -> None:
        asset = Asset(symbol="XAU", name="Gold")
        with pytest.raises(AttributeError):
            asset.symbol = "AG"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = Asset(symbol="XAU", name="Gold")
        b = Asset(symbol="XAU", name="Gold")
        assert a == b


class TestInstrument:
    def test_creation(self, xauusd: Instrument) -> None:
        assert xauusd.id == "XAUUSD"
        assert xauusd.base.symbol == "XAU"
        assert xauusd.quote.symbol == "USD"
        assert xauusd.market_type == MarketType.FOREX
        assert xauusd.tick_size == Decimal("0.01")
        assert xauusd.pip_size == Decimal("0.10")
        assert xauusd.pip_value_ratio == Decimal("10")
        assert xauusd.monetary_pip_value == Decimal("0.10")
        assert xauusd.price_precision == 2
        assert xauusd.lot_size == Decimal("1")

    def test_frozen(self, xauusd: Instrument) -> None:
        with pytest.raises(AttributeError):
            xauusd.id = InstrumentId("CHANGED")  # type: ignore[misc]

    def test_invalid_tick_size(self, xau: Asset, usd: Asset) -> None:
        with pytest.raises(ValueError, match="tick_size must be positive"):
            Instrument(
                id=InstrumentId("BAD"),
                base=xau,
                quote=usd,
                market_type=MarketType.FOREX,
                tick_size=Decimal("0"),
                pip_size=Decimal("0.10"),
                price_precision=2,
                lot_size=Decimal("1"),
            )

    def test_invalid_negative_tick_size(self, xau: Asset, usd: Asset) -> None:
        with pytest.raises(ValueError, match="tick_size must be positive"):
            Instrument(
                id=InstrumentId("BAD"),
                base=xau,
                quote=usd,
                market_type=MarketType.FOREX,
                tick_size=Decimal("-0.01"),
                pip_size=Decimal("0.10"),
                price_precision=2,
                lot_size=Decimal("1"),
            )

    def test_invalid_pip_size(self, xau: Asset, usd: Asset) -> None:
        with pytest.raises(ValueError, match="pip_size must be positive"):
            Instrument(
                id=InstrumentId("BAD"),
                base=xau,
                quote=usd,
                market_type=MarketType.FOREX,
                tick_size=Decimal("0.01"),
                pip_size=Decimal("0"),
                price_precision=2,
                lot_size=Decimal("1"),
            )

    def test_invalid_precision(self, xau: Asset, usd: Asset) -> None:
        with pytest.raises(ValueError, match="price_precision must be >= 0"):
            Instrument(
                id=InstrumentId("BAD"),
                base=xau,
                quote=usd,
                market_type=MarketType.FOREX,
                tick_size=Decimal("0.01"),
                pip_size=Decimal("0.10"),
                price_precision=-1,
                lot_size=Decimal("1"),
            )

    def test_invalid_lot_size(self, xau: Asset, usd: Asset) -> None:
        with pytest.raises(ValueError, match="lot_size must be positive"):
            Instrument(
                id=InstrumentId("BAD"),
                base=xau,
                quote=usd,
                market_type=MarketType.FOREX,
                tick_size=Decimal("0.01"),
                pip_size=Decimal("0.10"),
                price_precision=2,
                lot_size=Decimal("0"),
            )


class TestCandle:
    def test_creation(self, sample_candle: Candle) -> None:
        assert sample_candle.instrument_id == "XAUUSD"
        assert sample_candle.price_type == PriceType.BID
        assert sample_candle.is_closed is True
        assert sample_candle.is_bullish is True
        assert sample_candle.is_bearish is False

    def test_body_size(self, sample_candle: Candle) -> None:
        expected = abs(Decimal("2033.25") - Decimal("2030.50"))
        assert sample_candle.body_size == expected

    def test_range(self, sample_candle: Candle) -> None:
        expected = Decimal("2035.75") - Decimal("2028.00")
        assert sample_candle.range == expected

    def test_mid_price(self, sample_candle: Candle) -> None:
        expected = (Decimal("2035.75") + Decimal("2028.00")) / 2
        assert sample_candle.mid_price == expected
        assert sample_candle.range_midpoint == expected

    def test_frozen(self, sample_candle: Candle) -> None:
        with pytest.raises(AttributeError):
            sample_candle.close = Decimal("9999")  # type: ignore[misc]

    def test_naive_open_time_rejected(self) -> None:
        with pytest.raises(InvalidCandleError, match="UTC-aware"):
            Candle(
                instrument_id=InstrumentId("XAUUSD"),
                source_id=DataSourceId("test"),
                timeframe=Timeframe.H1,
                open_time=datetime(2024, 1, 1, 10, 0),  # naive!
                close_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
                open=Decimal("2000"),
                high=Decimal("2010"),
                low=Decimal("1990"),
                close=Decimal("2005"),
                volume=Decimal("100"),
                price_type=PriceType.BID,
            )

    def test_close_before_open_rejected(self) -> None:
        with pytest.raises(InvalidCandleError, match="close_time must be after"):
            Candle(
                instrument_id=InstrumentId("XAUUSD"),
                source_id=DataSourceId("test"),
                timeframe=Timeframe.H1,
                open_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
                close_time=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                open=Decimal("2000"),
                high=Decimal("2010"),
                low=Decimal("1990"),
                close=Decimal("2005"),
                volume=Decimal("100"),
                price_type=PriceType.BID,
            )

    def test_high_less_than_low_rejected(self) -> None:
        with pytest.raises(InvalidCandleError, match="high must be >= low"):
            Candle(
                instrument_id=InstrumentId("XAUUSD"),
                source_id=DataSourceId("test"),
                timeframe=Timeframe.H1,
                open_time=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                close_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
                open=Decimal("2000"),
                high=Decimal("1990"),  # high < low
                low=Decimal("2010"),
                close=Decimal("2005"),
                volume=Decimal("100"),
                price_type=PriceType.BID,
            )

    def test_negative_volume_rejected(self) -> None:
        with pytest.raises(InvalidCandleError, match="volume must be >= 0"):
            Candle(
                instrument_id=InstrumentId("XAUUSD"),
                source_id=DataSourceId("test"),
                timeframe=Timeframe.H1,
                open_time=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                close_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
                open=Decimal("2000"),
                high=Decimal("2010"),
                low=Decimal("1990"),
                close=Decimal("2005"),
                volume=Decimal("-1"),
                price_type=PriceType.BID,
            )

    def test_high_less_than_open_rejected(self) -> None:
        with pytest.raises(InvalidCandleError, match="high must be >= open"):
            Candle(
                instrument_id=InstrumentId("XAUUSD"),
                source_id=DataSourceId("test"),
                timeframe=Timeframe.H1,
                open_time=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
                close_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
                open=Decimal("2010"),
                high=Decimal("2005"),  # high < open
                low=Decimal("2000"),
                close=Decimal("2003"),
                volume=Decimal("100"),
                price_type=PriceType.BID,
            )

    def test_bearish_candle(self) -> None:
        c = Candle(
            instrument_id=InstrumentId("XAUUSD"),
            source_id=DataSourceId("test"),
            timeframe=Timeframe.H1,
            open_time=datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            close_time=datetime(2024, 1, 1, 11, 0, tzinfo=UTC),
            open=Decimal("2010"),
            high=Decimal("2015"),
            low=Decimal("2000"),
            close=Decimal("2005"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
        )
        assert c.is_bearish is True
        assert c.is_bullish is False


class TestDataSource:
    def test_creation(self, sample_source: DataSource) -> None:
        assert sample_source.instrument_id == "XAUUSD"
        assert sample_source.provider_id == "test-broker"
        assert str(sample_source) == "test-broker:XAUUSD.test"

    def test_frozen(self, sample_source: DataSource) -> None:
        with pytest.raises(AttributeError):
            sample_source.provider_id = "other"  # type: ignore[misc]

    def test_effective_defaults(self, sample_source: DataSource, xauusd: Instrument) -> None:
        assert sample_source.effective_tick_size(xauusd) == Decimal("0.01")
        assert sample_source.effective_pip_size(xauusd) == Decimal("0.10")
        assert sample_source.effective_lot_size(xauusd) == Decimal("1")
        assert sample_source.effective_price_precision(xauusd) == 2

    def test_effective_with_overrides(self, xauusd: Instrument) -> None:
        custom_source = DataSource(
            id=DataSourceId("oanda-xauusd"),
            instrument_id=InstrumentId("XAUUSD"),
            provider_id=ProviderId("oanda"),
            provider_symbol="XAU_USD",
            tick_size_override=Decimal("0.001"),
            pip_size_override=Decimal("0.01"),
            lot_size_override=Decimal("100"),
            price_precision_override=3,
            typical_spread=Decimal("0.25"),
        )
        assert custom_source.effective_tick_size(xauusd) == Decimal("0.001")
        assert custom_source.effective_pip_size(xauusd) == Decimal("0.01")
        assert custom_source.effective_lot_size(xauusd) == Decimal("100")
        assert custom_source.effective_price_precision(xauusd) == 3
        assert custom_source.typical_spread == Decimal("0.25")

    def test_instrument_mismatch_rejected(self, sample_source: DataSource) -> None:
        mismatched = Instrument(
            id=InstrumentId("EURUSD"),
            base=Asset("EUR", "Euro"),
            quote=Asset("USD", "US Dollar"),
            market_type=MarketType.FOREX,
            tick_size=Decimal("0.00001"),
            pip_size=Decimal("0.0001"),
            price_precision=5,
            lot_size=Decimal("100000"),
        )
        with pytest.raises(ValueError, match="Instrument id mismatch"):
            sample_source.effective_tick_size(mismatched)

    def test_invalid_overrides_rejected(self) -> None:
        with pytest.raises(ValueError, match="tick_size_override must be positive"):
            DataSource(
                id=DataSourceId("bad"),
                instrument_id=InstrumentId("XAUUSD"),
                provider_id=ProviderId("bad"),
                provider_symbol="BAD",
                tick_size_override=Decimal("0"),
            )
        with pytest.raises(ValueError, match="typical_spread cannot be negative"):
            DataSource(
                id=DataSourceId("bad"),
                instrument_id=InstrumentId("XAUUSD"),
                provider_id=ProviderId("bad"),
                provider_symbol="BAD",
                typical_spread=Decimal("-0.01"),
            )


class TestDataSourceMetadata:
    def test_defaults(self) -> None:
        meta = DataSourceMetadata(source_id=DataSourceId("test"))
        assert meta.earliest_utc is None
        assert meta.latest_utc is None
        assert meta.total_candles == 0
        assert meta.last_ingestion_utc is None
