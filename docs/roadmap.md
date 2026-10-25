# TraderLab — Development Roadmap

## Phase 0: Architecture Foundation ✅ (Current)

**Goal:** Establish a clean, testable, and extensible project foundation.

**Acceptance Criteria:**
- [x] Project structure with clean module boundaries
- [x] Domain models: Asset, Instrument, Candle, DataSource
- [x] Domain ports (protocols) for repositories and providers
- [x] Core types, enums, exception hierarchy
- [x] Configuration with env-based settings and SecretStr
- [x] Database DSN construction (no schema/migration yet)
- [x] Architectural boundary tests (domain isolation enforced)
- [x] Smoke tests, unit tests, all passing
- [x] Ruff lint and format compliance
- [x] Documentation: architecture.md, ADRs, roadmap.md
- [x] Forex instrument registry (XAUUSD, EURUSD)

---

## Phase 1: Data Ingestion Pipeline

**Goal:** Fetch, validate, and store historical OHLCV data for XAUUSD.

**Acceptance Criteria:**
- [ ] Alembic migration setup with initial schema
- [ ] PostgreSQL candle storage (CandleRepository implementation)
- [ ] At least one MarketDataProvider adapter (e.g., MetaTrader5, CCXT, or free API)
- [ ] Ingestion use case: fetch → validate → store pipeline
- [ ] Data quality checks (gap detection, duplicate prevention)
- [ ] Ingestion metadata tracking (earliest/latest candle, total count)
- [ ] Point-in-time column (ingestion timestamp) in candle table
- [ ] Multi-timeframe support (M1, M5, M15, H1, H4, D1)
- [ ] Integration tests with real PostgreSQL

---

## Phase 2: Technical Analysis & Market Structure

**Goal:** Implement core technical analysis computations and ICT/SMC structures.

**Acceptance Criteria:**
- [ ] Swing High / Swing Low detection (deterministic)
- [ ] Break of Structure (BOS) identification
- [ ] Market Structure Shift (MSS) detection
- [ ] Fair Value Gap (FVG) and Inverse FVG (IFVG) identification
- [ ] Basic indicator computations (ATR, EMA, RSI)
- [ ] Multi-timeframe analysis support (without lookahead bias)
- [ ] Trading session segmentation (Asian, London, New York)
- [ ] All computations unit tested with known expected outputs

---

## Phase 3: Market Regime & Daily Bias

**Goal:** Classify market conditions and establish directional bias.

**Acceptance Criteria:**
- [ ] Market regime classification (trending, ranging, volatile)
- [ ] Daily bias determination from higher timeframe structure
- [ ] Volatility measurement and regime-adjusted parameters
- [ ] Session-based analysis (kill zones, high-probability windows)
- [ ] Feature engineering pipeline for regime inputs
- [ ] Statistical significance testing for regime classifications

---

## Phase 4: Backtesting Engine

**Goal:** Build a deterministic, realistic backtesting framework.

**Acceptance Criteria:**
- [ ] Event-driven backtesting engine
- [ ] Realistic execution simulation (spread, slippage, commission)
- [ ] Walk-forward validation framework
- [ ] Untouched hold-out period enforcement
- [ ] Performance metrics (Sharpe, Sortino, max drawdown, win rate, etc.)
- [ ] Trade-by-trade logging with full context
- [ ] Backtest reproducibility guarantee (same inputs → same outputs)
- [ ] Clear disclaimers: backtests do not guarantee future performance

---

## Phase 5: Strategy Framework

**Goal:** Enable systematic strategy definition and signal generation.

**Acceptance Criteria:**
- [ ] Strategy protocol / base class
- [ ] Signal generation pipeline
- [ ] Candidate trade plan creation
- [ ] Risk-adjusted position sizing
- [ ] Multiple entry confirmation requirements
- [ ] Strategy parameter management
- [ ] Strategy composition (combine multiple signals)

---

## Phase 6: Risk Management Engine

**Goal:** Independent, deterministic risk management system.

**Acceptance Criteria:**
- [ ] Per-trade risk limits (max loss, R-multiple)
- [ ] Portfolio-level risk constraints (max exposure, correlation limits)
- [ ] Position sizing algorithms (fixed fractional, Kelly criterion)
- [ ] Drawdown-based circuit breakers
- [ ] Risk engine fully independent of LLM/AI components
- [ ] Risk decisions fully auditable and logged

---

## Phase 7: Historical Analog Analysis

**Goal:** Find and evaluate historical patterns similar to current market conditions.

**Acceptance Criteria:**
- [ ] Pattern encoding / feature extraction
- [ ] Similarity search algorithm
- [ ] Statistical evaluation of analog outcomes
- [ ] Confidence scoring for analogs
- [ ] Visualization of analog matches

---

## Phase 8: Multi-Market Expansion

**Goal:** Add support for Crypto, BIST, and US Stock markets.

**Acceptance Criteria:**
- [ ] Crypto market adapter (via CCXT or similar)
- [ ] BIST market adapter
- [ ] US Stocks market adapter
- [ ] Market-specific calendars and session hours
- [ ] Cross-market analysis capabilities
- [ ] Market-specific cost models

---

## Phase 9: AI Agent Integration

**Goal:** Integrate LLM-based analysis while maintaining risk engine independence.

**Acceptance Criteria:**
- [ ] Agent framework with clear domain boundaries
- [ ] LLM integration for pattern analysis
- [ ] Agent outputs validated by risk engine (never bypassed)
- [ ] Full audit trail for AI-assisted decisions
- [ ] Human-in-the-loop approval for live actions

---

## Phase 10: CLI & API Interfaces

**Goal:** Production-ready user interfaces.

**Acceptance Criteria:**
- [ ] CLI for data management, analysis, and backtesting
- [ ] REST API for programmatic access
- [ ] Authentication and authorization
- [ ] Rate limiting and input validation
- [ ] API documentation (OpenAPI)

---

## Guiding Principles Across All Phases

1. **No phase ships without tests** — Every feature has unit tests; integration tests where external systems are involved.
2. **Architectural boundaries are enforced** — The AST-based boundary tests run in CI.
3. **Domain remains pure** — No infrastructure imports in domain code, ever.
4. **Data integrity first** — Point-in-time access, UTC timestamps, Decimal prices.
5. **Risk engine is sovereign** — No AI or external system can bypass risk limits.
6. **Backtest ≠ profit guarantee** — All documentation and output must include appropriate disclaimers.
