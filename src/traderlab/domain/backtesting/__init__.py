"""Deterministic Point-in-Time Event-Driven Backtesting & Execution Engine.

This package provides pure domain components for simulating historical trade execution,
enforcing causal time boundaries, dual BID/ASK microstructure matching, conservative
intrabar resolution, risk ledger accounting, and opportunity deduplication.
"""

from __future__ import annotations

from traderlab.domain.backtesting.backtest_audit import (
    format_backtest_run_markdown_report,
    generate_trade_audit_card,
)
from traderlab.domain.backtesting.backtest_engine import run_deterministic_backtest
from traderlab.domain.backtesting.backtest_models import (
    BacktestCashLedgerEntry,
    BacktestConfig,
    BacktestError,
    BacktestEvent,
    BacktestEventType,
    BacktestFill,
    BacktestFillQuality,
    BacktestOrder,
    BacktestOrderStatus,
    BacktestPortfolioSnapshot,
    BacktestPosition,
    BacktestPositionStatus,
    BacktestRun,
    BacktestRunSummary,
    EntryTriggerPolicy,
    IntrabarAmbiguityPolicy,
    IntrabarExecutionError,
    InvalidBacktestConfigError,
    MismatchedQuoteTimestampError,
    MissingQuoteDataError,
    NegativeSpreadError,
    OrderType,
    RiskOverrunError,
    SimulationStatus,
    TimeInForce,
)
from traderlab.domain.backtesting.event_clock import (
    EVENT_TYPE_PRIORITY,
    DeterministicEventQueue,
)
from traderlab.domain.backtesting.execution_simulator import (
    AlignedM1Quote,
    align_m1_candles,
    evaluate_order_execution,
    evaluate_position_exit,
)
from traderlab.domain.backtesting.position_ledger import PositionLedger

__all__ = [
    "EVENT_TYPE_PRIORITY",
    "AlignedM1Quote",
    "BacktestCashLedgerEntry",
    "BacktestConfig",
    "BacktestError",
    "BacktestEvent",
    "BacktestEventType",
    "BacktestFill",
    "BacktestFillQuality",
    "BacktestOrder",
    "BacktestOrderStatus",
    "BacktestPortfolioSnapshot",
    "BacktestPosition",
    "BacktestPositionStatus",
    "BacktestRun",
    "BacktestRunSummary",
    "DeterministicEventQueue",
    "EntryTriggerPolicy",
    "IntrabarAmbiguityPolicy",
    "IntrabarExecutionError",
    "InvalidBacktestConfigError",
    "MismatchedQuoteTimestampError",
    "MissingQuoteDataError",
    "NegativeSpreadError",
    "OrderType",
    "PositionLedger",
    "RiskOverrunError",
    "SimulationStatus",
    "TimeInForce",
    "align_m1_candles",
    "evaluate_order_execution",
    "evaluate_position_exit",
    "format_backtest_run_markdown_report",
    "generate_trade_audit_card",
    "run_deterministic_backtest",
]
