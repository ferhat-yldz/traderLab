"""PostgreSQL implementation of the CandleRepository protocol.

Guarantees bitemporal point-in-time correctness, idempotent ingestion,
multi-price-type differentiation, and version selection via append-only revisions.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
    ensure_utc,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.infrastructure.database.models import CandleModel
from traderlab.infrastructure.database.session import session_scope


def _normalize_dt(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC)
    return ensure_utc(dt)


def _is_candle_data_identical(row: CandleModel, candle: Candle) -> bool:
    """Check if the persisted CandleModel contains identical market, volume, and metadata.

    Domain Data Change Contract:
    A new revision is required if ANY domain-meaningful field differs:
    - Core OHLC: open, high, low, close
    - Volume: volume, tick_volume
    - Microstructure: spread
    - Bar State: is_closed, is_synthetic
    - PIT & Lineage Metadata: availability_confidence, available_at, published_at, received_at

    Technical fields (such as database insertion wall-clock time `ingested_at`)
    do NOT constitute domain data changes and must NOT trigger a new revision.
    """
    row_avail = _normalize_dt(row.available_at)
    c_avail = _normalize_dt(candle.available_at)

    row_pub = _normalize_dt(row.published_at)
    c_pub = _normalize_dt(candle.published_at)

    row_rec = _normalize_dt(row.received_at)
    c_rec = _normalize_dt(candle.received_at)

    return (
        row.open == candle.open
        and row.high == candle.high
        and row.low == candle.low
        and row.close == candle.close
        and row.volume == candle.volume
        and row.tick_volume == candle.tick_volume
        and row.spread == candle.spread
        and row.is_closed == candle.is_closed
        and row.is_synthetic == candle.is_synthetic
        and row.availability_confidence == candle.availability_confidence.value
        and row_avail == c_avail
        and row_pub == c_pub
        and row_rec == c_rec
    )


class PostgresCandleRepository:
    """Production-grade PostgreSQL candle repository with bitemporal PIT query capabilities.

    Adheres strictly to the ``traderlab.domain.ports.CandleRepository`` protocol.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_candles(self, candles: Sequence[Candle]) -> int:
        """Persist a sequence of domain candles idempotently with revision tracking.

        If a candle with identical key and data already exists, it is ignored safely.
        If a candle arrives with the same natural key but different data at revision 0,
        it is saved as a new revision (revision = current_max + 1) to avoid silent data loss.

        Returns:
            int: Number of new rows actually written to storage.
        """
        if not candles:
            return 0

        # Enforce intra-batch DataSource-to-Instrument consistency:
        # A single DataSourceId cannot represent multiple distinct instruments.
        batch_source_map: dict[str, str] = {}
        for c in candles:
            sid = str(c.source_id)
            iid = str(c.instrument_id)
            if sid in batch_source_map and batch_source_map[sid] != iid:
                raise ValueError(
                    f"DataSourceId '{sid}' is bound to multiple instruments in batch: "
                    f"'{batch_source_map[sid]}' and '{iid}'"
                )
            batch_source_map[sid] = iid

        # Sort candles deterministically by natural key to guarantee global lock acquisition order,
        # completely preventing lock-ordering deadlocks between concurrent workers.
        sorted_candles = sorted(
            candles,
            key=lambda c: (
                str(c.instrument_id),
                str(c.source_id),
                int(c.timeframe.value),
                ensure_utc(c.open_time),
                c.price_type.value,
                c.revision,
            ),
        )

        unique_sources = sorted({str(c.source_id) for c in sorted_candles})

        saved_count = 0
        async with session_scope(self._session_factory) as session:
            # 1. Acquire transactional advisory lock for each unique source_id in sorted order.
            # This guarantees atomic serialization of DataSourceId-to-Instrument binding across
            # concurrent transactions, completely preventing cross-instrument race conditions on new feeds.
            for sid in unique_sources:
                source_lock_key = f"source_binding:{sid}"
                await session.execute(
                    select(func.pg_advisory_xact_lock(func.hashtext(source_lock_key)))
                )

            for candle in sorted_candles:
                norm_open = ensure_utc(candle.open_time)
                norm_close = ensure_utc(candle.close_time)

                # Verify that this source_id in the database is not already bound to a different instrument
                existing_source_instr = await session.scalar(
                    select(CandleModel.instrument_id)
                    .where(CandleModel.source_id == str(candle.source_id))
                    .limit(1)
                )
                if existing_source_instr is not None and existing_source_instr != str(
                    candle.instrument_id
                ):
                    raise ValueError(
                        f"DataSourceId '{candle.source_id}' is already bound to instrument "
                        f"'{existing_source_instr}', cannot persist candle for instrument '{candle.instrument_id}'"
                    )

                # 2. Acquire transactional advisory lock for this natural candle bar key.
                # This guarantees that concurrent transactions updating the same candle bar
                # serialize cleanly, preventing race conditions on revision bumping.
                lock_key = (
                    f"{candle.instrument_id}:{candle.source_id}:{candle.timeframe.value}:"
                    f"{norm_open.isoformat()}:{candle.price_type.value}"
                )
                await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(lock_key))))

                # Check for existing record at this natural key
                query = (
                    select(CandleModel)
                    .where(
                        CandleModel.instrument_id == str(candle.instrument_id),
                        CandleModel.source_id == str(candle.source_id),
                        CandleModel.timeframe == int(candle.timeframe.value),
                        CandleModel.open_time == norm_open,
                        CandleModel.price_type == candle.price_type.value,
                    )
                    .order_by(CandleModel.revision.desc())
                )
                result = await session.execute(query)
                existing_rows = list(result.scalars().all())

                target_revision = candle.revision

                if existing_rows:
                    latest = existing_rows[0]
                    # Check if identical data exists at same revision or in latest revision
                    if _is_candle_data_identical(latest, candle):
                        # Identical candle already active; idempotent no-op (no redundant revision)
                        continue

                    match_found = any(
                        row.revision == target_revision and _is_candle_data_identical(row, candle)
                        for row in existing_rows
                    )
                    if match_found:
                        # Identical candle already persisted at target revision; idempotent no-op
                        continue

                    # If caller didn't explicitly specify a higher revision, bump revision to prevent silent loss
                    if target_revision <= latest.revision:
                        target_revision = latest.revision + 1

                # Build model
                model = CandleModel.from_domain(candle)
                model.open_time = norm_open
                model.close_time = norm_close
                model.revision = target_revision

                # Use PostgreSQL ON CONFLICT DO NOTHING for concurrent race safety
                stmt = (
                    insert(CandleModel)
                    .values(
                        instrument_id=model.instrument_id,
                        source_id=model.source_id,
                        timeframe=model.timeframe,
                        open_time=model.open_time,
                        close_time=model.close_time,
                        price_type=model.price_type,
                        revision=model.revision,
                        open=model.open,
                        high=model.high,
                        low=model.low,
                        close=model.close,
                        volume=model.volume,
                        tick_volume=model.tick_volume,
                        spread=model.spread,
                        is_closed=model.is_closed,
                        is_synthetic=model.is_synthetic,
                        published_at=model.published_at,
                        received_at=model.received_at,
                        available_at=model.available_at,
                        ingested_at=model.ingested_at,
                        availability_confidence=model.availability_confidence,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            "instrument_id",
                            "source_id",
                            "timeframe",
                            "open_time",
                            "price_type",
                            "revision",
                        ]
                    )
                    .returning(CandleModel.instrument_id)
                )
                res = await session.execute(stmt)
                if res.scalar_one_or_none() is not None:
                    saved_count += 1
                    # Flush immediately so subsequent candles in the same batch see the updated revision
                    await session.flush()

        return saved_count

    async def get_candles(
        self,
        source_id: DataSourceId,
        timeframe: Timeframe,
        start_utc: datetime,
        end_utc: datetime,
        *,
        instrument_id: InstrumentId | None = None,
        policy: PitPolicy = PitPolicy.STRICT_PIT,
        price_type: PriceType | None = None,
        as_of_utc: datetime | None = None,
        knowledge_snapshot_time: datetime | None = None,
        only_closed: bool = True,
    ) -> Sequence[Candle]:
        """Query candles with full bitemporal point-in-time guarantees.

        Args:
            source_id: Unique data source identifier.
            timeframe: Candle duration/resolution.
            start_utc: Inclusive start time (UTC).
            end_utc: Exclusive end time (UTC).
            instrument_id: Optional instrument filter.
            policy: PitPolicy.STRICT_PIT or RESEARCH_ESTIMATED.
            price_type: Optional PriceType filter (BID, ASK, MID, UNKNOWN).
            as_of_utc: Decision-time timestamp. Required under STRICT_PIT.
            knowledge_snapshot_time: System knowledge snapshot timestamp.
                If None under STRICT_PIT, defaults to as_of_utc.
                If None under RESEARCH_ESTIMATED, unconstrained (historical backfill).
            only_closed: If True, filters out uncompleted or future bars.

        Returns:
            Sequence[Candle]: Chronologically sorted list of unique candles,
            with the highest valid revision selected for each bar.
        """
        start_norm = ensure_utc(start_utc)
        end_norm = ensure_utc(end_utc)
        as_of_norm = ensure_utc(as_of_utc) if as_of_utc is not None else None
        snapshot_norm = (
            ensure_utc(knowledge_snapshot_time) if knowledge_snapshot_time is not None else None
        )

        async with session_scope(self._session_factory) as session:
            filters = [
                CandleModel.source_id == str(source_id),
                CandleModel.timeframe == int(timeframe.value),
                CandleModel.open_time >= start_norm,
                CandleModel.open_time < end_norm,
            ]

            if instrument_id is not None:
                filters.append(CandleModel.instrument_id == str(instrument_id))

            if price_type is not None:
                filters.append(CandleModel.price_type == price_type.value)

            if only_closed:
                filters.append(CandleModel.is_closed.is_(True))

            # ── Decision-Time Filtering ─────────────────────────────────────
            if as_of_norm is not None:
                # Under all policies, an uncompleted bar at decision time cannot be used as closed
                if only_closed:
                    filters.append(CandleModel.close_time <= as_of_norm)

                if policy == PitPolicy.STRICT_PIT:
                    filters.append(CandleModel.availability_confidence == "VERIFIED")
                    filters.append(CandleModel.available_at.is_not(None))
                    filters.append(CandleModel.available_at <= as_of_norm)
                elif policy == PitPolicy.RESEARCH_ESTIMATED:
                    filters.append(
                        func.coalesce(CandleModel.available_at, CandleModel.close_time)
                        <= as_of_norm
                    )

            # ── Knowledge Snapshot Filtering (Bitemporal) ───────────────────
            effective_snapshot = snapshot_norm
            if effective_snapshot is None and policy == PitPolicy.STRICT_PIT:
                effective_snapshot = as_of_norm

            if effective_snapshot is not None:
                filters.append(CandleModel.ingested_at <= effective_snapshot)

            # ── Single Valid Revision Selection (DISTINCT ON) ───────────────
            # Using PostgreSQL DISTINCT ON (instrument_id, open_time, price_type) ordered by revision DESC
            # ensures we return exactly ONE row per (instrument_id, open_time, price_type) bar: the latest
            # revision that was known before the knowledge snapshot time.
            stmt = (
                select(CandleModel)
                .where(*filters)
                .ext(
                    distinct_on(
                        CandleModel.instrument_id,
                        CandleModel.open_time,
                        CandleModel.price_type,
                    )
                )
                .order_by(
                    CandleModel.instrument_id,
                    CandleModel.open_time,
                    CandleModel.price_type,
                    CandleModel.revision.desc(),
                )
            )

            result = await session.execute(stmt)
            models = list(result.scalars().all())

            # Sort chronologically by open_time, price_type, instrument_id for consistent downstream order
            models.sort(key=lambda m: (m.open_time, m.price_type, m.instrument_id))
            return [m.to_domain() for m in models]
