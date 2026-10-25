# ADR-001: Modular Monolith Architecture

**Status:** Accepted
**Date:** 2026-10-19
**Context:**

The platform needs to support multiple markets (Forex, Crypto, BIST, US Stocks),
multiple analysis methodologies (technical analysis, ICT/SMC, statistical validation),
and multiple interfaces (CLI, API, future dashboards).

**Decision:**

We chose a **modular monolith** over microservices.

**Rationale:**

1. **Simplicity** — A single deployment unit is easier to develop, test, and debug for a research platform.
2. **Refactoring agility** — Module boundaries can be adjusted without distributed-system overhead.
3. **Performance** — In-process calls eliminate network serialisation latency for computation-heavy workflows.
4. **Right-sized for the team** — Microservices add operational complexity that is not justified at this scale.
5. **Migration path** — Clean module boundaries (enforced by tests) allow extracting services later if needed.

**Consequences:**

- All modules live in a single Python package with clear import boundaries.
- Architectural boundary tests enforce dependency rules at CI time.
- If the system grows beyond the capacity of a single process, modules can be extracted into services along existing port boundaries.

---

# ADR-002: Hexagonal Architecture with Domain Ports

**Status:** Accepted
**Date:** 2026-10-19
**Context:**

The domain layer must remain independent of databases, broker APIs, and LLM services to ensure testability, determinism, and long-term maintainability.

**Decision:**

Apply **Hexagonal Architecture** (Ports & Adapters) with **Dependency Inversion**:
- Domain defines abstract ports (Python `Protocol` classes)
- Infrastructure implements these ports as concrete adapters
- Application layer orchestrates domain and infrastructure via ports

**Rationale:**

1. Domain logic can be tested without any external services
2. Data providers can be swapped without changing business rules
3. The risk engine remains independent of LLM or AI components
4. Backtesting uses the same domain logic as live analysis

**Consequences:**

- All external integrations must go through port interfaces
- Domain tests are fast and deterministic
- Some indirection cost (protocols + adapters), but justified by flexibility

---

# ADR-003: Decimal Prices and UTC Timestamps

**Status:** Accepted
**Date:** 2026-10-19
**Context:**

Financial calculations require exact precision. Floating-point arithmetic introduces rounding errors that can compound in backtesting and P&L calculations.

**Decision:**

- All prices and monetary amounts use `decimal.Decimal`
- All timestamps are UTC-aware `datetime` objects (enforced at model level)

**Rationale:**

1. `Decimal` eliminates IEEE-754 rounding issues
2. UTC-only timestamps prevent timezone bugs in multi-session analysis
3. Both constraints are enforced at the value-object level (fail-fast)

**Consequences:**

- Slightly more verbose code when constructing test data
- Need Decimal-aware serialisation in API responses (future)
- Database columns must use `NUMERIC` / `TIMESTAMPTZ` types

---

# ADR-004: Instrument vs DataSource Separation

**Status:** Accepted
**Date:** 2026-10-19
**Context:**

The same financial instrument (e.g., XAUUSD) may have different price feeds from different brokers. These feeds can differ in tick size, spread, and even OHLCV values.

**Decision:**

Separate `Instrument` (the thing) from `DataSource` (a specific price feed for that thing).

**Rationale:**

1. Enables cross-broker data comparison and validation
2. Maintains clear data lineage for reproducibility
3. Allows backtesting on one data source while validating on another

**Consequences:**

- All candle data must reference both `instrument_id` and `source_id`
- Slightly more complex data model, but essential for research integrity

---

# ADR-005: Point-in-Time Data Access

**Status:** Accepted
**Date:** 2026-10-19
**Context:**

Lookahead bias is one of the most common and devastating errors in backtesting. Using data that would not have been available at the time of a simulated decision invalidates all results.

**Decision:**

The `CandleRepository` protocol includes an `as_of_utc` parameter that limits returned data to what was available at that timestamp.

**Rationale:**

1. Prevents lookahead bias by design
2. Supports historical data revision tracking
3. Enables reproducible point-in-time analysis

**Consequences:**

- Candle storage must track ingestion timestamps
- Queries are slightly more complex
- This constraint must be honoured by all repository implementations

---

# ADR-006: Explicit Price Types (BID/ASK/MID) and Geometric Range Midpoint

**Status:** Accepted
**Date:** 2026-10-19
**Context:**

In Forex and OTC CFD markets, no centralized single transaction price exists; brokers stream separate Bid and Ask prices. Standard retail charts typically display Bid prices, but short positions enter at Bid and exit at Ask. Treating all bars as homogenous or assuming `(high + low) / 2` represents market spread midpoint leads to severe backtesting inaccuracies and execution bias.

**Decision:**

1. Explicitly require `PriceType` (`BID`, `ASK`, `MID`, `UNKNOWN`) on every `Candle` instance. Silent defaulting to BID is prohibited.
2. Distinct BID and ASK candles can exist for the same instrument, source, and timeframe.
3. Rename the geometric bar center to `range_midpoint` to clearly distinguish it from true market bid/ask spread midpoint.
4. Model spread as a distinct, time-varying property (`Decimal | None`). Missing spread is preserved as `None` and NEVER assumed to be zero.
5. Synthetic bars derived from MID or interpolated feeds must be explicitly flagged via `is_synthetic=True`.

**Rationale:**

1. Eliminates hidden spread assumptions in quantitative research.
2. Allows realistic simulation of bid-ask execution dynamics.
3. Distinguishes raw broker feeds from synthetic composite feeds.

**Consequences:**

- Callers must be explicit about the price type of candles they construct.
- Candle storage schema in Phase 1 must persist `price_type`, `spread`, and `is_synthetic`.

---

# ADR-007: Six-Timestamp Bitemporal Point-in-Time Model with Dual Time Axes

**Status:** Accepted
**Date:** 2026-10-19
**Context:**

Quantitative research requires strict separation between when an event occurred in the market (event time) and when the market data was knowable or recorded (knowledge/transaction time). Historical backfills downloaded in bulk have timestamps from years ago, but their exact network arrival time is unknown. Conversely, real-time live ingestion captures millisecond-accurate network arrival. Conflating event decision time with knowledge time creates severe lookahead bias or unfairly breaks historical backtesting with post-event downloaded data.

**Decision:**

1. Explicitly model and distinguish six time concepts:
   - `open_time`: Start of the bar period (UTC).
   - `close_time`: End of the bar period (UTC).
   - `published_at`: When upstream broker published the bar (UTC, optional).
   - `received_at`: When system boundary gateway received the data (UTC, optional).
   - `available_at`: Earliest confirmed decision-time timestamp (UTC, optional).
   - `ingested_at`: When the bar was committed to storage (UTC, None before persistence).
2. Clearly separate the two time axes in PIT evaluation:
   - `decision_time` (`as_of`): The simulated execution moment on the market timeline.
   - `knowledge_snapshot_time`: The database state snapshot timestamp.
3. Governed by explicit `PitPolicy`:
   - `STRICT_PIT`: Requires `VERIFIED` confidence, `available_at <= decision_time`, and `ingested_at <= decision_time` (live system invariant). Rejects unverified backfills.
   - `RESEARCH_ESTIMATED`: Permits historical backfill research on completed bars (`close_time <= decision_time`) where ingestion occurred post-event, while `knowledge_snapshot_time` prevents revisions committed after the snapshot from leaking into backtests.
4. Unclosed candles (`is_closed=False`) are rejected from closed-bar decision analysis under all policies.
5. Revisions/corrections are tracked with a monotonically increasing `revision` counter.

**Rationale:**

1. Guarantees mathematical and temporal integrity for backtesting without breaking bulk historical research.
2. Prevents artificial certainty: historical backfills cannot pretend to have verified real-time arrival timestamps.
3. Supports true bitemporal queries (Valid Time vs Knowledge Time).

**Consequences:**

- `Candle.is_available_at` accepts both `as_of` (decision time) and `knowledge_snapshot_time`.
- Repository query protocols carry `as_of_utc` and `knowledge_snapshot_time`.

---

# ADR-008: Async I/O Port Protocols and Synchronous Computational Core

**Status:** Accepted
**Date:** 2026-10-19
**Context:**

TraderLab requires high-throughput data collection from external broker APIs/web sockets and non-blocking database queries via PostgreSQL (asyncpg/SQLAlchemy async), while mathematical computations (indicators, ICT/SMC market structure, risk calculation, backtest execution) are CPU-bound, deterministic, and must remain synchronous and pure.

**Decision:**

1. All I/O-bound domain repository and provider protocols in `src/traderlab/domain/ports.py` are strictly asynchronous (`async def`):
   - `InstrumentRepository` (`get_by_id`, `list_all`, `save`)
   - `DataSourceRepository` (`get_by_id`, `list_for_instrument`, `save`)
   - `CandleRepository` (`get_candles`, `save_candles`)
   - `MarketDataProvider` (`fetch_candles`)
2. Use strongly-typed domain primitives (`InstrumentId`, `ProviderId`, `DataSourceId`) across all port signatures (eliminating primitive obsession).
3. The computational core (features, market structure, risk models, backtesting engine) remains strictly synchronous, deterministic, and free of I/O.
4. Adapter implementations are responsible for connection pooling, request timeouts, cancellation handling, and graceful resource cleanup.

**Rationale:**

1. Prevents blocking I/O bottlenecks in data pipelines and API services.
2. Preserves determinism and testability of mathematical domain logic without async runtime overhead.
3. Clean structural typing via `@runtime_checkable` Python `Protocol`.

**Consequences:**

- Callers at the application layer must `await` repository and provider interactions.
- Computational algorithms remain pure functions or synchronous classes.

---

# ADR-009: Broker Microstructure Metadata Separation (Instrument vs DataSource)

**Status:** Accepted
**Date:** 2026-10-19
**Context:**

Different brokers and market data providers quote the same underlying financial instrument with varying microstructures (tick sizes, decimal precision, lot sizes, pip conventions, and typical spreads). For example, Gold (XAUUSD) may be quoted with 2 decimals (tick 0.01) by one broker and 3 decimals (tick 0.001) by another. Hardcoding broker-specific assumptions into the canonical `Instrument` violates Domain-Driven Design and prevents multi-broker research.

**Decision:**

1. `Instrument` defines the canonical, market-standard definition:
   - `tick_size: Decimal`
   - `pip_size: Decimal`
   - `price_precision: int`
   - `lot_size: Decimal`
   - `pip_value_ratio -> Decimal` (`pip_size / tick_size`)
2. `DataSource` defines provider/broker-specific microstructure overrides:
   - `tick_size_override: Decimal | None`
   - `pip_size_override: Decimal | None`
   - `lot_size_override: Decimal | None`
   - `price_precision_override: int | None`
   - `typical_spread: Decimal | None`
3. Query methods on `DataSource`:
   - `effective_tick_size(instrument)`
   - `effective_pip_size(instrument)`
   - `effective_lot_size(instrument)`
   - `effective_price_precision(instrument)`
   Fall back to `Instrument` defaults when overrides are absent, and fail fast if an instrument mismatch is supplied.

**Rationale:**

1. Clear separation of concerns between economic instrument definition and broker execution feed.
2. Transparent calculation of pip vs tick movements across varying broker decimalizations.

**Consequences:**

- Data source adapters can specify broker-specific quirks without polluting canonical instrument registries.

---

# ADR-010: Infrastructure Configuration Isolation

**Status:** Accepted
**Date:** 2026-10-19
**Context:**

`DatabaseSettings` was previously defined in `src/traderlab/core/config.py`. This coupled core configuration to a specific relational database implementation (PostgreSQL/psycopg connection strings), violating Clean Architecture layer boundaries.

**Decision:**

1. Relocate `DatabaseSettings` and `build_dsn` to `src/traderlab/infrastructure/database/config.py`.
2. Restrict `src/traderlab/core/config.py` to top-level application configuration (`Environment`, `log_level`).
3. Forbid `traderlab.core.config` and all infrastructure packages in the domain layer via automated AST architectural boundary tests (`tests/test_architecture.py`).

**Rationale:**

1. Preserves strict dependency direction: Domain → Core, Infrastructure → Domain/Core.
2. Prevents domain logic from accidentally importing environmental settings or infrastructure connection state.

**Consequences:**

- Database connection settings are imported exclusively from `traderlab.infrastructure.database.config`.

---

# ADR-011: PostgreSQL Append-Only Bitemporal Schema and Fail-Fast Isolation

**Status:** Accepted  
**Date:** 2026-10-20  
**Context:**  
Faz 1A requires real PostgreSQL time-series storage supporting bitemporal Point-in-Time queries, multiple price types (BID, ASK, MID), explicit volume distinction, and historical bar revisions without two-table synchronization drift or accidental connection to external legacy databases (e.g. `trader_muhendis`).

**Decision:**  
1. **Append-Only Schema Strategy:**  
   Store all candles and their revisions in a unified `candles` table with compound primary key `(instrument_id, source_id, timeframe, open_time, price_type, revision)`.  
   Revisions are inserted with incrementing revision numbers. When data arrives at an existing revision with identical prices, it is treated as an idempotent no-op (`ON CONFLICT DO NOTHING`). When conflicting data arrives, a new revision is created.
2. **Deterministic Bitemporal Selection:**  
   Queries use PostgreSQL `DISTINCT ON (open_time, price_type) ... ORDER BY open_time, price_type, revision DESC` filtered by `ingested_at <= knowledge_snapshot_time` and `available_at / close_time <= as_of_utc`. This guarantees returning exactly one valid revision per bar without duplicate rows or hindsight leakage.
3. **Fail-Fast Database Whitelist:**  
   Enforce an explicit whitelist `{"traderlab", "traderlab_test"}` at both Pydantic configuration (`DatabaseSettings`), connection engine (`create_db_engine`), and migration runner (`migrations/env.py`). Target names containing `muhendis` or `borsa` are aborted immediately.
4. **Session Timezone & Windows Async Support:**  
   Configure all PostgreSQL engine connections with `connect_args={"options": "-c timezone=utc"}` and `WindowsSelectorEventLoopPolicy` on Windows platforms, eliminating local timezone offset drift and proactor event loop incompatibilities.

**Consequences:**  
- Zero risk of two-table split-brain drift between active candles and archive tables.
- Real-time and historical backfill research queries operate under mathematically sound PIT boundaries.
- External production/legacy databases remain completely isolated and protected.

