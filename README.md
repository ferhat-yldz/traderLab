# TraderLab

Professional algorithmic trading research platform.

## Overview

TraderLab is a modular monolith designed for multi-market algorithmic trading research.
It combines technical analysis, ICT/SMC structures, historical analog analysis,
statistical validation, market regime detection, and risk management into a single
research platform.

**Initial market:** Forex (XAUUSD)
**Target markets:** Forex, Crypto, BIST, US Stocks, Futures

## Architecture

- **Domain-Driven Design** with clean boundaries
- **Hexagonal Architecture** (Ports & Adapters)
- **No direct infrastructure dependencies** in the domain layer
- See [`docs/architecture.md`](docs/architecture.md) for full details

## Quick Start

```bash
# Create virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows

# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest -v

# Lint & format
ruff check src/ tests/
ruff format src/ tests/

# Type check
mypy src/traderlab/
```

## Project Structure

```
src/traderlab/       # Application source code
├── core/            # Shared value objects, types, errors
├── domain/          # Pure business logic (no infra deps)
├── application/     # Use cases / orchestration
├── infrastructure/  # Database, external providers
├── markets/         # Market-specific adaptations
├── research/        # Feature engineering, backtesting
├── interfaces/      # CLI, REST API
└── agents/          # Future AI agent integration
```

## Documentation

- [Architecture](docs/architecture.md)
- [Roadmap](docs/roadmap.md)
- [Architecture Decision Records](docs/decisions/)

## License

Proprietary. All rights reserved.
