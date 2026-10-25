"""Contract tests for domain ports using async fake adapters.

Validates that concrete adapters can fulfill the Protocol contracts,
ensuring Dependency Inversion Principle (DIP) and async I/O safety.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    ProviderId,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.instruments.data_source import DataSource
from traderlab.domain.instruments.instrument import Instrument
from traderlab.domain.ports import (
    CandleRepository,
    DataSourceRepository,
    InstrumentRepository,
    MarketDataProvider,
)


# ── Fake In-Memory Adapters ──────────────────────────────────────────────────
class FakeInstrumentRepository:
    def __init__(self) -> None:
        self._store: dict[InstrumentId, Instrument] = {}

    async def get_by_id(self, instrument_id: InstrumentId) -> Instrument | None:
        return self._store.get(instrument_id)

    async def list_all(self) -> Sequence[Instrument]:
        return list(self._store.values())

    async def save(self, instrument: Instrument) -> None:
        self._store[instrument.id] = instrument


class FakeDataSourceRepository:
    def __init__(self) -> None:
        self._store: dict[DataSourceId, DataSource] = {}

    async def get_by_id(self, source_id: DataSourceId) -> DataSource | None:
        return self._store.get(source_id)

    async def list_for_instrument(self, instrument_id: InstrumentId) -> Sequence[DataSource]:
        return [ds for ds in self._store.values() if ds.instrument_id == instrument_id]

    async def save(self, source: DataSource) -> None:
        self._store[source.id] = source


class FakeCandleRepository:
    def __init__(self) -> None:
        self._candles: list[Candle] = []

    async def save_candles(self, candles: Sequence[Candle]) -> int:
        self._candles.extend(candles)
        return len(candles)

    async def get_candles(
        self,
        source_id: DataSourceId,
        timeframe: Timeframe,
        start_utc: datetime,
        end_utc: datetime,
        *,
        instrument_id: InstrumentId | None = None,
        price_type: PriceType | None = None,
        as_of_utc: datetime | None = None,
        knowledge_snapshot_time: datetime | None = None,
        only_closed: bool = True,
    ) -> Sequence[Candle]:
        candidates: list[Candle] = []
        for c in self._candles:
            if c.source_id != source_id or c.timeframe != timeframe:
                continue
            if instrument_id is not None and c.instrument_id != instrument_id:
                continue
            if c.open_time < start_utc or c.open_time >= end_utc:
                continue
            if price_type is not None and c.price_type != price_type:
                continue
            if only_closed and not c.is_closed:
                continue
            if as_of_utc is not None:
                if not c.is_available_at(
                    as_of_utc,
                    policy=PitPolicy.RESEARCH_ESTIMATED,
                    knowledge_snapshot_time=knowledge_snapshot_time,
                ):
                    continue
            elif (
                knowledge_snapshot_time is not None
                and c.ingested_at is not None
                and c.ingested_at > knowledge_snapshot_time
            ):
                continue

            candidates.append(c)

        # Deduplicate multiple revisions: retain highest valid revision per (open_time, price_type)
        grouped: dict[tuple[datetime, PriceType], Candle] = {}
        for c in candidates:
            key = (c.open_time, c.price_type)
            if key not in grouped or c.revision > grouped[key].revision:
                grouped[key] = c

        return sorted(grouped.values(), key=lambda x: (x.open_time, x.price_type.value))


class FakeMarketDataProvider:
    def __init__(self, provider_id: ProviderId) -> None:
        self._provider_id = provider_id
        self._data: list[Candle] = []

    @property
    def provider_id(self) -> ProviderId:
        return self._provider_id

    def add_candle(self, candle: Candle) -> None:
        self._data.append(candle)

    async def fetch_candles(
        self,
        instrument_id: InstrumentId,
        timeframe: Timeframe,
        start_utc: datetime,
        end_utc: datetime,
        *,
        price_type: PriceType,
    ) -> Sequence[Candle]:
        return [
            c
            for c in self._data
            if c.instrument_id == instrument_id
            and c.timeframe == timeframe
            and c.price_type == price_type
            and start_utc <= c.open_time < end_utc
        ]


# ── Tests ────────────────────────────────────────────────────────────────────
class TestInstrumentRepositoryContract:
    def test_protocol_conformance(self) -> None:
        repo = FakeInstrumentRepository()
        assert isinstance(repo, InstrumentRepository)

    @pytest.mark.asyncio
    async def test_save_and_retrieve(self, xauusd: Instrument) -> None:
        repo = FakeInstrumentRepository()
        assert await repo.get_by_id(xauusd.id) is None

        await repo.save(xauusd)
        retrieved = await repo.get_by_id(xauusd.id)
        assert retrieved == xauusd

        all_items = await repo.list_all()
        assert len(all_items) == 1
        assert all_items[0] == xauusd


class TestDataSourceRepositoryContract:
    def test_protocol_conformance(self) -> None:
        repo = FakeDataSourceRepository()
        assert isinstance(repo, DataSourceRepository)

    @pytest.mark.asyncio
    async def test_save_and_retrieve(self, sample_source: DataSource) -> None:
        repo = FakeDataSourceRepository()
        assert await repo.get_by_id(sample_source.id) is None

        await repo.save(sample_source)
        retrieved = await repo.get_by_id(sample_source.id)
        assert retrieved == sample_source

        by_inst = await repo.list_for_instrument(sample_source.instrument_id)
        assert len(by_inst) == 1
        assert by_inst[0] == sample_source

        empty_inst = await repo.list_for_instrument(InstrumentId("EURUSD"))
        assert len(empty_inst) == 0


class TestCandleRepositoryContract:
    def test_protocol_conformance(self) -> None:
        repo = FakeCandleRepository()
        assert isinstance(repo, CandleRepository)

    @pytest.mark.asyncio
    async def test_save_and_query_bitemporal(self) -> None:
        repo = FakeCandleRepository()
        xau_id = InstrumentId("XAUUSD")
        source_id = DataSourceId("test-feed")
        t10 = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        t11 = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)

        candle_v0 = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t10,
            close_time=t11,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            available_at=datetime(2024, 1, 15, 11, 0, 5, tzinfo=UTC),
            ingested_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
            availability_confidence=AvailabilityConfidence.VERIFIED,
            revision=0,
        )

        saved = await repo.save_candles([candle_v0])
        assert saved == 1

        # Query within window
        bars = await repo.get_candles(
            source_id,
            Timeframe.H1,
            datetime(2024, 1, 15, 9, 0, tzinfo=UTC),
            datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
        )
        assert len(bars) == 1

        # Query with as_of before bar closed: should return empty
        bars_before = await repo.get_candles(
            source_id,
            Timeframe.H1,
            datetime(2024, 1, 15, 9, 0, tzinfo=UTC),
            datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
            as_of_utc=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
        )
        assert len(bars_before) == 0

        # Query with snapshot before ingestion: should be filtered out
        bars_early_snapshot = await repo.get_candles(
            source_id,
            Timeframe.H1,
            datetime(2024, 1, 15, 9, 0, tzinfo=UTC),
            datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
            knowledge_snapshot_time=datetime(2025, 12, 31, 0, 0, tzinfo=UTC),
        )
        assert len(bars_early_snapshot) == 0

    @pytest.mark.asyncio
    async def test_multiple_revisions_returns_highest_valid_revision(self) -> None:
        """When multiple revisions exist for the same bar, query returns exactly one highest valid revision."""
        repo = FakeCandleRepository()
        xau_id = InstrumentId("XAUUSD")
        source_id = DataSourceId("test-feed")
        t10 = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        t11 = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)

        rev0 = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t10,
            close_time=t11,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            ingested_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
            revision=0,
        )
        rev1 = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t10,
            close_time=t11,
            open=Decimal("2030.00"),
            high=Decimal("2037.00"),  # corrected high
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
            ingested_at=datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC),
            revision=1,
        )

        await repo.save_candles([rev0, rev1])

        # As of Jan 15 snapshot: rev1 was not ingested yet, rev0 must be returned
        jan_snapshot = datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
        bars_jan = await repo.get_candles(
            source_id,
            Timeframe.H1,
            datetime(2024, 1, 15, 9, 0, tzinfo=UTC),
            datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
            knowledge_snapshot_time=jan_snapshot,
        )
        assert len(bars_jan) == 1
        assert bars_jan[0].revision == 0
        assert bars_jan[0].high == Decimal("2035.00")

        # As of Feb 15 snapshot: rev1 is known, must supersede rev0 (and no duplicates!)
        feb_snapshot = datetime(2026, 2, 15, 0, 0, tzinfo=UTC)
        bars_feb = await repo.get_candles(
            source_id,
            Timeframe.H1,
            datetime(2024, 1, 15, 9, 0, tzinfo=UTC),
            datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
            knowledge_snapshot_time=feb_snapshot,
        )
        assert len(bars_feb) == 1
        assert bars_feb[0].revision == 1
        assert bars_feb[0].high == Decimal("2037.00")


class TestMarketDataProviderContract:
    def test_protocol_conformance(self) -> None:
        provider = FakeMarketDataProvider(ProviderId("oanda"))
        assert isinstance(provider, MarketDataProvider)
        assert provider.provider_id == "oanda"

    @pytest.mark.asyncio
    async def test_fetch_candles(self) -> None:
        provider = FakeMarketDataProvider(ProviderId("oanda"))
        xau_id = InstrumentId("XAUUSD")
        source_id = DataSourceId("test-feed")
        t10 = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        t11 = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)

        candle = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t10,
            close_time=t11,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
        )
        provider.add_candle(candle)

        fetched = await provider.fetch_candles(
            xau_id,
            Timeframe.H1,
            datetime(2024, 1, 15, 9, 0, tzinfo=UTC),
            datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
            price_type=PriceType.BID,
        )
        assert len(fetched) == 1
        assert fetched[0] == candle

        empty = await provider.fetch_candles(
            InstrumentId("EURUSD"),
            Timeframe.H1,
            datetime(2024, 1, 15, 9, 0, tzinfo=UTC),
            datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
            price_type=PriceType.BID,
        )
        assert len(empty) == 0

    @pytest.mark.asyncio
    async def test_fetch_candles_price_type_filtering(self) -> None:
        provider = FakeMarketDataProvider(ProviderId("oanda"))
        xau_id = InstrumentId("XAUUSD")
        source_id = DataSourceId("test-feed")
        t10 = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
        t11 = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)

        bid_candle = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t10,
            close_time=t11,
            open=Decimal("2030.00"),
            high=Decimal("2035.00"),
            low=Decimal("2028.00"),
            close=Decimal("2033.00"),
            volume=Decimal("100"),
            price_type=PriceType.BID,
        )
        ask_candle = Candle(
            instrument_id=xau_id,
            source_id=source_id,
            timeframe=Timeframe.H1,
            open_time=t10,
            close_time=t11,
            open=Decimal("2030.30"),
            high=Decimal("2035.30"),
            low=Decimal("2028.30"),
            close=Decimal("2033.30"),
            volume=Decimal("100"),
            price_type=PriceType.ASK,
        )
        provider.add_candle(bid_candle)
        provider.add_candle(ask_candle)

        bid_results = await provider.fetch_candles(
            xau_id,
            Timeframe.H1,
            datetime(2024, 1, 15, 9, 0, tzinfo=UTC),
            datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
            price_type=PriceType.BID,
        )
        assert len(bid_results) == 1
        assert bid_results[0].price_type == PriceType.BID

        ask_results = await provider.fetch_candles(
            xau_id,
            Timeframe.H1,
            datetime(2024, 1, 15, 9, 0, tzinfo=UTC),
            datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
            price_type=PriceType.ASK,
        )
        assert len(ask_results) == 1
        assert ask_results[0].price_type == PriceType.ASK
