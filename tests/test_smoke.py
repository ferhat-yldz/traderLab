"""Smoke tests — verify project structure, imports, and basic wiring.

These tests require NO external services (no DB, no network).
They should run in < 1 second total.
"""

from __future__ import annotations

import importlib

import pytest

# ── Import smoke tests ─────────────────────────────────────────────────────
PACKAGES_TO_IMPORT = [
    "traderlab",
    "traderlab.core",
    "traderlab.core.types",
    "traderlab.core.errors",
    "traderlab.core.config",
    "traderlab.domain",
    "traderlab.domain.instruments",
    "traderlab.domain.instruments.instrument",
    "traderlab.domain.instruments.candle",
    "traderlab.domain.instruments.data_source",
    "traderlab.domain.ports",
    "traderlab.domain.market_structure",
    "traderlab.domain.strategies",
    "traderlab.domain.risk",
    "traderlab.domain.trade_plans",
    "traderlab.application",
    "traderlab.application.ingestion",
    "traderlab.application.analysis",
    "traderlab.infrastructure",
    "traderlab.infrastructure.database",
    "traderlab.infrastructure.database.config",
    "traderlab.infrastructure.providers",
    "traderlab.infrastructure.providers.dukascopy",
    "traderlab.infrastructure.providers.market_data_lab",
    "traderlab.infrastructure.calendars",
    "traderlab.markets",
    "traderlab.markets.forex",
    "traderlab.markets.forex.instruments",
    "traderlab.markets.crypto",
    "traderlab.markets.bist",
    "traderlab.markets.us_stocks",
    "traderlab.research",
    "traderlab.research.features",
    "traderlab.research.analogs",
    "traderlab.research.backtesting",
    "traderlab.research.evaluation",
    "traderlab.interfaces",
    "traderlab.interfaces.cli",
    "traderlab.interfaces.api",
    "traderlab.interfaces.dashboard",
    "traderlab.agents",
]


@pytest.mark.smoke
@pytest.mark.parametrize("package", PACKAGES_TO_IMPORT)
def test_package_imports(package: str) -> None:
    """Every package in the project must be importable without side effects."""
    mod = importlib.import_module(package)
    assert mod is not None


@pytest.mark.smoke
def test_version_exists() -> None:
    import traderlab

    assert hasattr(traderlab, "__version__")
    assert isinstance(traderlab.__version__, str)
    assert len(traderlab.__version__) > 0
