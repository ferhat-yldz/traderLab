# TraderLab — System Architecture

## Overview

TraderLab is a **modular monolith** designed for multi-market algorithmic trading research.
It follows **Hexagonal Architecture** (Ports & Adapters) with **Domain-Driven Design** principles.

## Layer Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                         INTERFACES                                   │
│                    (CLI, REST API)                                    │
├──────────────────────────────────────────────────────────────────────┤
│                         APPLICATION                                  │
│              (Use Cases: Ingestion, Analysis)                        │
├──────────────────────────────────────────────────────────────────────┤
│                          DOMAIN                                      │
│    (Instruments, Market Structure, Strategies, Risk, Trade Plans)    │
│    (Ports — abstract protocols for infrastructure)                   │
├──────────────────────────────────────────────────────────────────────┤
│                       INFRASTRUCTURE                                 │
│          (Database, Providers, Calendars)                            │
├──────────────────────────────────────────────────────────────────────┤
│          MARKETS                    │         RESEARCH               │
│   (Forex, Crypto, BIST, US Stocks) │ (Features, Analogs, Backtest)  │
├─────────────────────────────────────┴────────────────────────────────┤
│                          CORE                                        │
│            (Types, Errors, Config — shared foundation)               │
└──────────────────────────────────────────────────────────────────────┘
```

## Dependency Rules

Dependencies flow **inward** — outer layers depend on inner layers, never the reverse.

```
interfaces → application → domain ← infrastructure
                              ↑
                           core (shared)
```

### Allowed Dependencies

| Layer              | May depend on                           | Must NOT depend on                         |
|--------------------|-----------------------------------------|--------------------------------------------|
| `core`             | Python stdlib only (+pydantic for config)| Any other traderlab module                 |
| `domain`           | `core`, Python stdlib                   | `infrastructure`, `application`, `markets`, `interfaces`, `agents`, SQLAlchemy, psycopg, HTTP clients |
| `application`      | `domain`, `core`                        | `infrastructure` (via ports only), `interfaces` |
| `infrastructure`   | `domain` (implements ports), `core`     | `application`, `interfaces`                |
| `markets`          | `domain`, `core`                        | `infrastructure` directly                  |
| `research`         | `domain`, `core`, `application`         | `interfaces`                               |
| `interfaces`       | `application`, `domain`, `core`         | `infrastructure` directly                  |
| `agents`           | `application`, `domain`, `core`         | `infrastructure` directly                  |

### Key Design Decisions

1. **Domain has zero infrastructure dependencies** — enforced by automated AST-based tests
2. **Ports (protocols)** in `domain/ports.py` define abstract interfaces; concrete adapters live in `infrastructure/`
3. **Dependency Inversion**: Application layer depends on domain ports, infrastructure implements them
4. **Configuration** uses `pydantic-settings` with `SecretStr` for credentials

## Module Responsibilities

### `core/`
- Shared type aliases (`InstrumentId`, `DataSourceId`, `ProviderId`)
- Enumerations (`MarketType`, `Timeframe`, `SessionName`, `PriceType`, `AvailabilityConfidence`, `PitPolicy`)
- Exception hierarchy (`TraderLabError` → `DomainError` / `InfrastructureError` / `ConfigurationError`)
- Application configuration (`AppSettings`, `Environment`) — pure application settings without database coupling
- Utility functions (`utc_now`, `ensure_utc`)

### `domain/`
- **`instruments/`** — `Asset`, `Instrument`, `Candle`, `DataSource` immutable value objects
  - `Instrument`: canonical definition with `tick_size`, `pip_size`, `pip_value_ratio`, `price_precision`, `lot_size`
  - `DataSource`: broker-specific feed identity with microstructure overrides (`tick_size_override`, `pip_size_override`, etc.) and `effective_*` resolution methods
  - `Candle`: explicit `PriceType` (BID/ASK/MID), `range_midpoint`, time-varying `spread`, `is_synthetic`, 6-timestamp bitemporal model, revision counter, and `is_available_at` PIT evaluation
- **`market_structure/`** — Swing detection, BOS, MSS, FVG (future)
- **`strategies/`** — Signal generation logic (future)
- **`risk/`** — Position sizing, risk limits (future)
- **`trade_plans/`** — Trade plan lifecycle (future)
- **`ports.py`** — Abstract async repository (`InstrumentRepository`, `DataSourceRepository`, `CandleRepository`) and provider (`MarketDataProvider`) protocols

### `application/`
- **`ingestion/`** — Data fetching and storage orchestration
- **`analysis/`** — Technical analysis workflows

### `infrastructure/`
- **`database/`**
  - **`config.py`** — PostgreSQL connection parameters (`DatabaseSettings`), DSN builder (`build_dsn`), credential secrets (`SecretStr`)
  - Engine factory, session management, repository adapters (future)
- **`providers/`** — External data provider adapters (broker APIs, data vendors)
- **`calendars/`** — Market-specific trading calendars

### `markets/`
Market-specific adaptations that encapsulate differences in:
- Trading sessions and calendars
- Canonical instrument definitions (`xauusd`, `eurusd` in `forex/instruments.py`)
- Price precision and tick sizes
- Volume semantics (real vs tick volume)
- Cost models (spread, commission, swap, funding rate)

### `research/`
- **`features/`** — Technical indicator computation (strictly synchronous)
- **`analogs/`** — Historical pattern similarity analysis
- **`backtesting/`** — Deterministic strategy simulation (strictly synchronous)
- **`evaluation/`** — Walk-forward validation, performance metrics

### `interfaces/`
- **`cli/`** — Command-line interface
- **`api/`** — REST API (future)

### `agents/`
- Future AI agent integration point (LLM-based analysis)
- Will consume domain outputs, never bypass risk engine

## Data Integrity Principles

1. **Point-in-Time Access (Dual Time Axes)**:
   - `decision_time` (`as_of_utc`): The simulated execution moment on the market timeline.
   - `knowledge_snapshot_time`: The storage state cutoff timestamp.
   - `STRICT_PIT`: Enforces verified availability and live ingestion invariants.
   - `RESEARCH_ESTIMATED`: Permits historical backfill research on completed bars without being blocked by post-event ingestion timestamps, while snapshot isolation prevents revisions from leaking into older backtests.
2. **Explicit Price Types** — `BID`, `ASK`, `MID`, `UNKNOWN` explicitly modeled; geometric midpoint (`range_midpoint`) distinguished from market spread midpoint.
3. **UTC-Only Timestamps** — Enforced at the `Candle` model level (`ensure_utc`).
4. **Closed Candles Only** — `is_closed` flag prevents analysis on incomplete bars.
5. **Data Source Identity** — Every price record tracks its provider origin (`source_id`).
6. **Decimal Precision** — All prices and quantities use `Decimal`, never `float`.
7. **Deterministic Computation** — Computational core is synchronous, pure, and deterministic.
8. **Async I/O Boundaries** — Database and external network I/O are strictly async protocols.
9. **Immutable Value Objects** — All domain models are frozen dataclasses.

## Security

- Database credentials managed via `.env` (never in source code)
- `SecretStr` prevents password leakage in logs and `repr()`
- Minimum-privilege database user recommended
- Schema managed via Alembic migrations (future)

