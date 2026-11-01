"""Core domain models, enumerations, and exceptions for deterministic event-driven backtesting.

All dataclasses are frozen, slotted, and strictly use Decimal for financial math and
timezone-aware UTC datetimes for timestamps.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from traderlab.core.errors import DomainError
from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
)
from traderlab.domain.trade_plans.ict_trade_plan import TradePlanDirection


# ── Exceptions ───────────────────────────────────────────────────────────────
class BacktestError(DomainError):
    """Base exception for backtesting domain errors."""

    def __init__(self, message: str, *, code: str = "BACKTEST_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidBacktestConfigError(BacktestError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_BACKTEST_CONFIG")


class MissingQuoteDataError(BacktestError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="MISSING_QUOTE_DATA")


class MismatchedQuoteTimestampError(BacktestError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="MISMATCHED_QUOTE_TIMESTAMP")


class NegativeSpreadError(BacktestError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="NEGATIVE_SPREAD_DATA")


class IntrabarExecutionError(BacktestError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="INTRABAR_EXECUTION_ERROR")


class RiskOverrunError(BacktestError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="RISK_OVERRUN_ERROR")


# ── Enumerations ────────────────────────────────────────────────────────────
class BacktestEventType(enum.Enum):
    """Deterministic event classification for the event clock."""

    MARKET_BAR_AVAILABLE = "MARKET_BAR_AVAILABLE"
    STRUCTURAL_EVENT_AVAILABLE = "STRUCTURAL_EVENT_AVAILABLE"
    ICT_SETUP_AVAILABLE = "ICT_SETUP_AVAILABLE"
    TRADE_PLAN_AVAILABLE = "TRADE_PLAN_AVAILABLE"
    RISK_ASSESSMENT_AVAILABLE = "RISK_ASSESSMENT_AVAILABLE"
    STRATEGY_SELECTION_AVAILABLE = "STRATEGY_SELECTION_AVAILABLE"
    SIMULATED_ORDER_SUBMITTED = "SIMULATED_ORDER_SUBMITTED"
    SIMULATED_ORDER_ELIGIBLE = "SIMULATED_ORDER_ELIGIBLE"
    SIMULATED_ORDER_FILLED = "SIMULATED_ORDER_FILLED"
    SIMULATED_ORDER_CANCELLED = "SIMULATED_ORDER_CANCELLED"
    SIMULATED_ORDER_EXPIRED = "SIMULATED_ORDER_EXPIRED"
    SIMULATED_ORDER_REJECTED = "SIMULATED_ORDER_REJECTED"
    STOP_LOSS_TRIGGERED = "STOP_LOSS_TRIGGERED"
    TAKE_PROFIT_TRIGGERED = "TAKE_PROFIT_TRIGGERED"
    POSITION_CLOSED = "POSITION_CLOSED"
    PORTFOLIO_RISK_RELEASED = "PORTFOLIO_RISK_RELEASED"
    SIMULATION_END = "SIMULATION_END"


class OrderType(enum.Enum):
    """Execution type for simulated orders."""

    MARKET_NEXT_ELIGIBLE_OPEN = "MARKET_NEXT_ELIGIBLE_OPEN"
    """Fills at the opening price of the first eligible bar after trigger confirmation."""

    LIMIT_RESTING_ORDER = "LIMIT_RESTING_ORDER"
    """Resting limit order evaluated against subsequent quote extrema."""


class TimeInForce(enum.Enum):
    """Validity lifetime of a resting order."""

    GTC = "GTC"  # Good 'Til Cancelled
    DAY = "DAY"  # Cancelled at end of trading day
    GTD = "GTD"  # Good 'Til Date/Time


class BacktestOrderStatus(enum.Enum):
    """Lifecycle state machine for simulated orders."""

    CREATED = "CREATED"
    PENDING_ELIGIBILITY = "PENDING_ELIGIBILITY"
    ACTIVE = "ACTIVE"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class BacktestFillQuality(enum.Enum):
    """Quality classification of a simulated execution fill."""

    SIMULATED_NEXT_OPEN = "SIMULATED_NEXT_OPEN"
    SIMULATED_LIMIT_TOUCH_ASSUMPTION = "SIMULATED_LIMIT_TOUCH_ASSUMPTION"
    SIMULATED_CONSERVATIVE_FILL = "SIMULATED_CONSERVATIVE_FILL"
    EXECUTION_AMBIGUOUS = "EXECUTION_AMBIGUOUS"
    NOT_FILLED = "NOT_FILLED"


class BacktestPositionStatus(enum.Enum):
    """Lifecycle state machine for simulated positions."""

    OPEN = "OPEN"
    CLOSED_BY_STOP = "CLOSED_BY_STOP"
    CLOSED_BY_TARGET = "CLOSED_BY_TARGET"
    CLOSED_BY_POLICY = "CLOSED_BY_POLICY"
    OPEN_AT_END = "OPEN_AT_END"


class IntrabarAmbiguityPolicy(enum.Enum):
    """Handling policy when both stop-loss and take-profit occur within the same M1 candle."""

    STOP_FIRST_CONSERVATIVE = "STOP_FIRST_CONSERVATIVE"
    """Default conservative quant rule: assumes stop-loss was triggered first."""

    MARK_AMBIGUOUS_NO_DETERMINISTIC_WIN = "MARK_AMBIGUOUS_NO_DETERMINISTIC_WIN"
    """Excludes the trade from deterministic win count and records an ambiguity alert."""


class EntryTriggerPolicy(enum.Enum):
    """Operational criterion for qualifying a trade plan entry."""

    ON_ZONE_TOUCH = "ON_ZONE_TOUCH"
    """Triggers as soon as quote touches the outer boundary of the entry zone."""

    ON_ZONE_MIDPOINT_REACHED = "ON_ZONE_MIDPOINT_REACHED"
    """Triggers when quote reaches the exact midpoint of the entry zone."""

    ON_CONFIRMED_REACTION = "ON_CONFIRMED_REACTION"
    """Requires closed candle reaction/respect confirmation before order eligibility."""


class SimulationStatus(enum.Enum):
    """Provenance and epistemological status of the backtest simulation."""

    HISTORICAL_RESEARCH_SIMULATION = "HISTORICAL_RESEARCH_SIMULATION"
    TEST_SCENARIO = "TEST_SCENARIO"


# ── Configuration Model ─────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Immutable configuration parameters for backtest execution."""

    initial_equity: Decimal = Decimal("100000.00")
    account_currency: str = "USD"
    intrabar_policy: IntrabarAmbiguityPolicy = IntrabarAmbiguityPolicy.STOP_FIRST_CONSERVATIVE
    entry_trigger_policy: EntryTriggerPolicy = EntryTriggerPolicy.ON_ZONE_MIDPOINT_REACHED
    default_order_type: OrderType = OrderType.LIMIT_RESTING_ORDER
    slippage_ticks: int = 0
    tick_size: Decimal = Decimal("0.01")
    units_per_lot: Decimal = Decimal("100")
    commission_per_lot: Decimal = Decimal("2.00")
    max_waiting_bars: int = 30
    warmup_bars: int = 0
    max_portfolio_risk_fraction: Decimal = Decimal("0.05")
    max_single_event_risk_fraction: Decimal = Decimal("0.01")
    daily_risk_limit_fraction: Decimal = Decimal("0.03")
    start_time_utc: datetime | None = None
    end_time_utc: datetime | None = None
    allow_zero_slippage: bool = True
    simulation_status: SimulationStatus = SimulationStatus.HISTORICAL_RESEARCH_SIMULATION

    def __post_init__(self) -> None:
        if self.initial_equity <= Decimal("0"):
            raise InvalidBacktestConfigError("initial_equity must be strictly positive.")
        if self.units_per_lot <= Decimal("0"):
            raise InvalidBacktestConfigError("units_per_lot must be strictly positive.")
        if self.tick_size <= Decimal("0"):
            raise InvalidBacktestConfigError("tick_size must be strictly positive.")
        if self.slippage_ticks < 0:
            raise InvalidBacktestConfigError("slippage_ticks cannot be negative.")
        if self.start_time_utc and self.end_time_utc and self.start_time_utc > self.end_time_utc:
            raise InvalidBacktestConfigError("start_time_utc cannot be after end_time_utc.")


# ── Event Clock Model ───────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class BacktestEvent:
    """Immutable discrete temporal event dispatched by the backtest event clock."""

    event_id: str
    event_time_utc: datetime
    available_at_utc: datetime
    instrument_id: InstrumentId
    source_id: DataSourceId
    event_type: BacktestEventType
    deterministic_sequence: int
    payload_reference: str
    source_event_ids: tuple[str, ...] = field(default_factory=tuple)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, BacktestEvent):
            return NotImplemented
        if self.available_at_utc != other.available_at_utc:
            return self.available_at_utc < other.available_at_utc
        if self.event_time_utc != other.event_time_utc:
            return self.event_time_utc < other.event_time_utc
        # Tie-breaker: deterministic sequence
        return self.deterministic_sequence < other.deterministic_sequence


# ── Simulated Order & Fill Models ───────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class BacktestOrder:
    """Immutable representation of a simulated trade order."""

    order_id: str
    economic_opportunity_id: str
    selected_plan_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    direction: TradePlanDirection
    order_type: OrderType
    analytical_trigger_price: Decimal
    analytical_trigger_side: PriceType
    required_execution_side: PriceType
    limit_price: Decimal | None
    stop_loss_price: Decimal
    take_profit_price: Decimal
    quantity: Decimal
    risk_budget: Decimal
    submitted_at: datetime
    eligible_from: datetime
    expiry_time: datetime | None
    status: BacktestOrderStatus = BacktestOrderStatus.ACTIVE
    rejection_reason: str | None = None
    source_event_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class BacktestFill:
    """Immutable execution fill record produced by the simulator."""

    fill_id: str
    order_id: str
    selected_plan_id: str
    direction: TradePlanDirection
    quote_side: PriceType
    fill_price: Decimal
    quantity: Decimal
    fill_time_utc: datetime
    bar_open_time_utc: datetime
    commission_paid: Decimal
    slippage_ticks: int
    slippage_cost: Decimal
    fill_quality: BacktestFillQuality
    is_exit: bool = False
    source_event_ids: tuple[str, ...] = field(default_factory=tuple)


# ── Position & Ledger Models ────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class BacktestPosition:
    """Immutable simulated position record tracking entry, exit, and PnL."""

    position_id: str
    economic_opportunity_id: str
    selected_plan_id: str
    direction: TradePlanDirection
    quantity: Decimal
    entry_fill: BacktestFill
    entry_price: Decimal
    entry_time: datetime
    stop_level: Decimal
    target_level: Decimal
    exit_fill: BacktestFill | None = None
    exit_price: Decimal | None = None
    exit_time: datetime | None = None
    commission_paid: Decimal = Decimal("0.00")
    slippage_cost: Decimal = Decimal("0.00")
    gross_pnl: Decimal = Decimal("0.00")
    net_pnl: Decimal = Decimal("0.00")
    account_currency: str = "USD"
    status: BacktestPositionStatus = BacktestPositionStatus.OPEN
    is_ambiguous_intrabar: bool = False
    gap_loss_overrun: bool = False
    source_event_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class BacktestCashLedgerEntry:
    """Immutable accounting entry recording cash movement in the backtest portfolio."""

    entry_id: str
    timestamp_utc: datetime
    entry_type: str  # "DEPOSIT", "COMMISSION", "REALIZED_PNL"
    amount: Decimal
    balance_after: Decimal
    reference_id: str  # position_id or order_id
    description: str


@dataclass(frozen=True, slots=True)
class BacktestPortfolioSnapshot:
    """Point-in-Time snapshot of the simulated portfolio state."""

    snapshot_time_utc: datetime
    cash_balance: Decimal
    equity: Decimal
    reserved_risk: Decimal
    free_risk_capacity: Decimal
    open_position_count: int
    active_order_count: int


# ── Summary & Run Models ────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class BacktestRunSummary:
    """Exhaustive quantitative summary of a completed backtest run."""

    total_historical_setups: int
    total_historical_trade_plans: int
    total_opportunities: int
    total_selected_opportunities: int
    total_orders_created: int
    total_orders_rejected: int
    total_orders_expired: int
    total_simulated_fills: int
    total_simulated_open_positions: int
    total_simulated_closed_positions: int
    total_stop_exits: int
    total_target_exits: int
    total_intrabar_ambiguous_events: int
    total_open_at_end_positions: int
    total_realized_simulated_pnl: Decimal
    total_estimated_transaction_costs: Decimal
    total_portfolio_risk_rejections: int
    total_economic_opportunity_duplicates_prevented: int
    initial_equity: Decimal
    final_equity: Decimal
    simulation_status: SimulationStatus


@dataclass(frozen=True, slots=True)
class BacktestRun:
    """Complete, immutable result container for a deterministic backtest execution."""

    run_id: str
    config: BacktestConfig
    orders: tuple[BacktestOrder, ...]
    fills: tuple[BacktestFill, ...]
    positions: tuple[BacktestPosition, ...]
    cash_ledger: tuple[BacktestCashLedgerEntry, ...]
    portfolio_snapshots: tuple[BacktestPortfolioSnapshot, ...]
    summary: BacktestRunSummary
