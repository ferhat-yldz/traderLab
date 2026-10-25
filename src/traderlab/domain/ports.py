"""Domain ports — abstract interfaces (protocols) for infrastructure adapters.

These protocols define the contracts that infrastructure must fulfil.
The domain NEVER imports concrete implementations; it depends only on
these abstractions (Dependency Inversion Principle).

Protocols are preferred over ABC because they support structural subtyping
and do not require explicit inheritance in the adapter classes.

Async/Sync Architecture:
- I/O Operations (PostgreSQL storage, broker/vendor network calls) are ASYNC.
  Adapters are responsible for timeouts, connection pooling, cancellation,
  and proper resource cleanup (e.g. closing network sessions and connection pools).
- Computational Core (indicators, market structure, risk models, backtest execution)
  is strictly SYNC, deterministic, and free of I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from traderlab.core.types import DataSourceId, InstrumentId, PriceType, ProviderId, Timeframe
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.instruments.data_source import DataSource
from traderlab.domain.instruments.instrument import Instrument


@runtime_checkable
class InstrumentRepository(Protocol):
    """Port for instrument persistence (async I/O)."""

    async def get_by_id(self, instrument_id: InstrumentId) -> Instrument | None: ...
    async def list_all(self) -> Sequence[Instrument]: ...
    async def save(self, instrument: Instrument) -> None: ...


@runtime_checkable
class DataSourceRepository(Protocol):
    """Port for data-source persistence (async I/O)."""

    async def get_by_id(self, source_id: DataSourceId) -> DataSource | None: ...
    async def list_for_instrument(self, instrument_id: InstrumentId) -> Sequence[DataSource]: ...
    async def save(self, source: DataSource) -> None: ...


@runtime_checkable
class CandleRepository(Protocol):
    """Port for OHLCV candle persistence (async I/O).

    Bitemporal querying semantics:
    - Event Time (Valid Time): `[start_utc, end_utc]` filters candles by `open_time`.
    - Decision Time (Point-in-Time): `as_of_utc` filters candles to only those
      that were available/knowable on or before that simulated decision timestamp
      (`available_at <= as_of_utc`). If `available_at` is not recorded, `close_time <= as_of_utc`
      is used under RESEARCH_ESTIMATED policy.
    - System State Time: `knowledge_snapshot_time` filters records to only those
      committed to storage on or before that snapshot timestamp (`ingested_at <= knowledge_snapshot_time`),
      preventing hindsight leakage from subsequent corrections.
    """

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
    ) -> Sequence[Candle]: ...

    async def save_candles(self, candles: Sequence[Candle]) -> int: ...


@runtime_checkable
class MarketDataProvider(Protocol):
    """Port for external market data fetching (broker API, data vendor).

    Implementations live in ``traderlab.infrastructure.providers``.
    Adapters are responsible for timeout enforcement, request rate-limiting,
    network error translation into TraderLabError, and session lifecycle.
    """

    @property
    def provider_id(self) -> ProviderId: ...

    async def fetch_candles(
        self,
        instrument_id: InstrumentId,
        timeframe: Timeframe,
        start_utc: datetime,
        end_utc: datetime,
        *,
        price_type: PriceType,
    ) -> Sequence[Candle]: ...
