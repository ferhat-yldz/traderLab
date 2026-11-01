"""Point-in-Time Safe ICT Risk Management, Stop-Loss, Take-Profit & Position Sizing Engine.

This module provides deterministic, explainable, and fully auditable risk assessments
for analytical ICT Trade Plans.

Guarantees:
- **Zero Execution Leakage**: Does NOT execute orders, connect to brokers, create fills,
  or compute real PnL. All calculations are strictly analytical risk proposals.
- **Strict Point-in-Time Safety**: Stop loss, take profit, ATR, swings, and conversion rates
  are strictly evaluated as of `as_of_utc`. No future market data or specification revisions leak.
- **Exact Decimal Arithmetic**: All price levels, distances, currency budgets, and lot
  allocations use `Decimal`. Floating-point arithmetic is strictly forbidden.
- **Microstructure Accountability**: Respects broker-specific contract units (`units_per_lot`),
  quantity steps (`quantity_step`), tick sizes, commissions, and spreads.
- **Conservative Lot Rounding**: Sizing strictly rounds down (`// quantity_step * quantity_step`),
  guaranteeing that final estimated risk never exceeds the allocated risk budget.
"""

from __future__ import annotations

import enum
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal

from traderlab.core.errors import TraderLabError
from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    PitPolicy,
    PriceType,
    Timeframe,
    ensure_utc,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.displacement import compute_historical_atr
from traderlab.domain.market_structure.swing import SwingPoint
from traderlab.domain.trade_plans.ict_trade_plan import (
    IctTradePlan,
    TradePlanDirection,
    TradePlanStatus,
)


# ── Exceptions ───────────────────────────────────────────────────────────────
class RiskManagementError(TraderLabError):
    """Base exception for Risk Management domain errors."""

    def __init__(self, message: str, *, code: str = "RISK_MANAGEMENT_ERROR") -> None:
        super().__init__(message, code=code)


class InvalidRiskConfigError(RiskManagementError):
    """Raised when risk configuration parameters are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_RISK_CONFIG")


class InvalidRiskCalculationError(RiskManagementError):
    """Raised when mathematical risk calculations encounter illegal divisions or boundaries."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_RISK_CALCULATION")


# ── Enumerations ────────────────────────────────────────────────────────────
class RiskAssessmentStatus(enum.Enum):
    """Status lifecycle of an ICT Trade Plan Risk Assessment."""

    ANALYTICAL_ONLY = "ANALYTICAL_ONLY"
    """Theoretical risk parameters derived without full account or contract metadata."""

    RISK_EVALUATED = "RISK_EVALUATED"
    """Risk parameters (SL, TP, R:R) computed; awaiting account sizing verification."""

    RISK_APPROVED = "RISK_APPROVED"
    """Plan meets all risk constraints and lot sizing limits (Analytical qualification, NOT a broker order)."""

    RISK_BLOCKED = "RISK_BLOCKED"
    """Plan rejected due to risk rule violations (e.g. min R:R, invalid distances, budget exceeded)."""

    MISSING_METADATA = "MISSING_METADATA"
    """Required instrument or broker specification metadata is unverified or absent."""

    INSUFFICIENT_ACCOUNT_DATA = "INSUFFICIENT_ACCOUNT_DATA"
    """Account equity or risk capital data unavailable."""

    UNSUPPORTED_INSTRUMENT = "UNSUPPORTED_INSTRUMENT"
    """Non-linear or inverse contracts where linear position sizing is invalid."""

    PIT_REJECTED = "PIT_REJECTED"
    """Strict PIT rule violation or historical data confidence mismatch."""


class SpecificationSourceType(enum.Enum):
    """Provenance and verification category of broker contract specification."""

    HYPOTHETICAL_BROKER_SCENARIO = "HYPOTHETICAL_BROKER_SCENARIO"
    """Hypothetical or analytical scenario parameters (NOT verified against a live broker)."""

    RESEARCH_FIXTURE = "RESEARCH_FIXTURE"
    """Deterministic fixture created for testing or research simulation."""

    VERIFIED_LIVE_BROKER = "VERIFIED_LIVE_BROKER"
    """Confirmed against an authenticated live broker API contract definition."""

    UNKNOWN_PROVENANCE = "UNKNOWN_PROVENANCE"
    """Unverified origin; cannot be used for production execution qualification."""


class RiskRewardMetric(enum.Enum):
    """Metric used for minimum risk/reward constraint validation."""

    GROSS = "GROSS"
    """Evaluate threshold against pure gross price distance ratio."""

    NET = "NET"
    """Evaluate threshold against execution cost-adjusted net ratio."""


class StopLossMethod(enum.Enum):
    """Mathematical approach used to derive the protective Stop-Loss price level."""

    STRUCTURAL_SWING = "STRUCTURAL_SWING"
    """Stop placed beyond the confirmed structural swing extreme linked to the setup."""

    ZONE_DISTAL_BOUNDARY = "ZONE_DISTAL_BOUNDARY"
    """Stop placed beyond the distal boundary of the entry zone with tick buffer."""

    ATR_ADJUSTED = "ATR_ADJUSTED"
    """Stop placed at entry +/- (historical ATR * atr_multiplier)."""


class TakeProfitMethod(enum.Enum):
    """Approach used to derive the analytical Take-Profit target(s)."""

    FIXED_R_MULTIPLE = "FIXED_R_MULTIPLE"
    """Target placed at entry +/- (risk_distance * target_r_multiple)."""

    OPPOSING_LIQUIDITY_TARGET = "OPPOSING_LIQUIDITY_TARGET"
    """Target placed at an opposing confirmed liquidity pool (Swing High/Low)."""

    MULTI_TARGET = "MULTI_TARGET"
    """Multiple staged targets (TP1, TP2, TP3) with hypothetical exit percentage weights."""


class PositionSizingMethod(enum.Enum):
    """Mathematical method used to compute the allocated lot size."""

    FIXED_FRACTIONAL = "FIXED_FRACTIONAL"
    """Risk budget is a fixed fraction (percentage) of account equity."""

    FIXED_MONETARY = "FIXED_MONETARY"
    """Risk budget is a fixed monetary cash amount."""


class RiskRejectionReason(enum.Enum):
    """Explicit explanation for why a trade plan failed risk qualification."""

    INVALID_ENTRY_PLAN = "INVALID_ENTRY_PLAN"
    INVALID_STOP_DIRECTION = "INVALID_STOP_DIRECTION"
    INVALID_TARGET_DIRECTION = "INVALID_TARGET_DIRECTION"
    ZERO_RISK_DISTANCE = "ZERO_RISK_DISTANCE"
    MISSING_CONTRACT_SIZE = "MISSING_CONTRACT_SIZE"
    MISSING_QUANTITY_STEP = "MISSING_QUANTITY_STEP"
    MISSING_CONVERSION_RATE = "MISSING_CONVERSION_RATE"
    UNKNOWN_TRADING_COST = "UNKNOWN_TRADING_COST"
    INVALID_TICK_ALIGNMENT = "INVALID_TICK_ALIGNMENT"
    RISK_BUDGET_EXCEEDED = "RISK_BUDGET_EXCEEDED"
    MINIMUM_LOT_EXCEEDS_RISK = "MINIMUM_LOT_EXCEEDS_RISK"
    SPREAD_LIMIT_EXCEEDED = "SPREAD_LIMIT_EXCEEDED"
    MINIMUM_RR_NOT_MET = "MINIMUM_RR_NOT_MET"
    STOP_DISTANCE_OUT_OF_RANGE = "STOP_DISTANCE_OUT_OF_RANGE"
    MISSING_PORTFOLIO_CONTEXT = "MISSING_PORTFOLIO_CONTEXT"
    FUTURE_INFORMATION = "FUTURE_INFORMATION"
    UNSUPPORTED_PRICE_MODEL = "UNSUPPORTED_PRICE_MODEL"
    DATA_QUALITY_FAILURE = "DATA_QUALITY_FAILURE"
    PLAN_NOT_ACTIVE = "PLAN_NOT_ACTIVE"
    AMBIGUOUS_INTRABAR_SEQUENCE = "AMBIGUOUS_INTRABAR_SEQUENCE"
    MISSING_METADATA = "MISSING_METADATA"
    MISSING_ACCOUNT_DATA = "MISSING_ACCOUNT_DATA"
    PIT_REJECTED = "PIT_REJECTED"


# ── Domain Models ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class InstrumentRiskSpecification:
    """Explicit contract specifications for an instrument on a specific data source / broker.

    Guarantees:
    - No assumed default lot size (units_per_lot must be explicitly verified).
    - Conservative quantity stepping and order size constraints.
    - Full traceability of specification source and validity timestamp.
    """

    instrument_id: InstrumentId
    source_id: DataSourceId
    tick_size: Decimal
    pip_size: Decimal
    units_per_lot: Decimal
    minimum_order_quantity: Decimal
    maximum_order_quantity: Decimal
    quantity_step: Decimal
    quote_currency: str = "USD"
    account_currency: str = "USD"
    commission_per_lot: Decimal | None = None
    typical_spread_ticks: int | None = None
    slippage_ticks: int = 0
    specification_source: str = "VERIFIED_BROKER_SPEC"
    specification_valid_at: datetime | None = None
    source_type: SpecificationSourceType = SpecificationSourceType.HYPOTHETICAL_BROKER_SCENARIO
    is_round_trip_commission: bool = True

    def __post_init__(self) -> None:
        if self.tick_size <= Decimal("0"):
            raise InvalidRiskConfigError("tick_size must be strictly positive Decimal")
        if self.pip_size <= Decimal("0"):
            raise InvalidRiskConfigError("pip_size must be strictly positive Decimal")
        if self.units_per_lot <= Decimal("0"):
            raise InvalidRiskConfigError("units_per_lot must be strictly positive Decimal")
        if self.minimum_order_quantity <= Decimal("0"):
            raise InvalidRiskConfigError(
                "minimum_order_quantity must be strictly positive Decimal"
            )
        if self.maximum_order_quantity < self.minimum_order_quantity:
            raise InvalidRiskConfigError(
                "maximum_order_quantity must be >= minimum_order_quantity"
            )
        if self.quantity_step <= Decimal("0"):
            raise InvalidRiskConfigError("quantity_step must be strictly positive Decimal")

    def calculate_execution_cost_per_lot(self) -> Decimal:
        """Calculate total execution cost per 1.0 standard lot in quote currency."""
        comm = self.commission_per_lot or Decimal("0")
        spread_cost = Decimal("0")
        if self.typical_spread_ticks is not None:
            spread_px = Decimal(self.typical_spread_ticks) * self.tick_size
            spread_cost = spread_px * self.units_per_lot
        slip_cost = Decimal("0")
        if self.slippage_ticks > 0:
            slip_px = Decimal(self.slippage_ticks) * self.tick_size
            slip_cost = slip_px * self.units_per_lot
        return comm + spread_cost + slip_cost

    def calculate_execution_cost_per_unit(self) -> Decimal:
        """Calculate total execution cost per 1 contract unit (e.g. per oz) in quote currency."""
        return self.calculate_execution_cost_per_lot() / self.units_per_lot


@dataclass(frozen=True, slots=True)
class AccountRiskSnapshot:
    """Point-in-time state of an account's equity, balance, and open risk limits."""

    account_id: str
    account_currency: str
    equity: Decimal
    balance: Decimal
    open_risk_ratio: Decimal = Decimal("0")
    daily_realized_loss: Decimal = Decimal("0")
    daily_loss_limit_ratio: Decimal | None = None
    max_open_risk_ratio: Decimal | None = None
    snapshot_time: datetime | None = None

    def __post_init__(self) -> None:
        if self.equity <= Decimal("0"):
            raise InvalidRiskConfigError("account equity must be strictly positive Decimal")


@dataclass(frozen=True, slots=True)
class FxConversionRate:
    """Point-in-time currency conversion rate between quote and account currencies."""

    from_currency: str
    to_currency: str
    rate: Decimal
    rate_time: datetime
    source: str = "DIRECT"

    def __post_init__(self) -> None:
        if self.rate <= Decimal("0"):
            raise InvalidRiskConfigError("conversion rate must be strictly positive Decimal")


@dataclass(frozen=True, slots=True)
class TakeProfitTarget:
    """Individual take-profit target in a single or multi-target scaling structure."""

    target_id: str
    target_price: Decimal
    target_r_multiple: Decimal
    allocation_fraction: Decimal = Decimal("1.0")
    description: str = ""

    def __post_init__(self) -> None:
        if self.allocation_fraction <= Decimal("0") or self.allocation_fraction > Decimal("1.0"):
            raise InvalidRiskConfigError("allocation_fraction must be in (0.0, 1.0]")


@dataclass(frozen=True, slots=True)
class StopLossProposal:
    """Proposed stop-loss level, method, and point-in-time structural reference."""

    method: StopLossMethod
    price: Decimal
    risk_distance: Decimal
    reference_event_id: str | None = None
    reference_available_time: datetime | None = None
    buffer_ticks: int = 0
    explanation: str = ""

    @property
    def is_valid(self) -> bool:
        return self.risk_distance > Decimal("0")


@dataclass(frozen=True, slots=True)
class TakeProfitProposal:
    """Proposed take-profit target structure, primary target, and reward distance."""

    method: TakeProfitMethod
    primary_target_price: Decimal
    reward_distance: Decimal
    targets: tuple[TakeProfitTarget, ...] = field(default_factory=tuple)
    reference_event_id: str | None = None
    reference_available_time: datetime | None = None
    explanation: str = ""

    @property
    def is_valid(self) -> bool:
        return self.reward_distance > Decimal("0")


@dataclass(frozen=True, slots=True)
class PositionSizingResult:
    """Deterministic result of position sizing calculation and constraints validation."""

    method: PositionSizingMethod
    account_equity: Decimal
    risk_fraction: Decimal
    risk_budget: Decimal
    raw_lots: Decimal
    allocated_lots: Decimal
    estimated_cash_risk: Decimal
    units: Decimal
    estimated_commission: Decimal = Decimal("0")
    estimated_spread_cost: Decimal = Decimal("0")
    estimated_slippage_cost: Decimal = Decimal("0")
    gross_cash_loss: Decimal = Decimal("0")
    gross_cash_reward: Decimal = Decimal("0")
    net_cash_reward: Decimal = Decimal("0")
    stop_scenario_total_cost: Decimal = Decimal("0")
    target_scenario_total_cost: Decimal = Decimal("0")
    cost_status: str = "FULL_COSTS_INCLUDED"
    is_valid: bool = True
    rejection_reason: RiskRejectionReason | None = None


@dataclass(frozen=True, slots=True)
class RiskManagementConfig:
    """Configuration parameters for deterministic risk evaluation and position sizing."""

    stop_loss_method: StopLossMethod = StopLossMethod.ZONE_DISTAL_BOUNDARY
    take_profit_method: TakeProfitMethod = TakeProfitMethod.FIXED_R_MULTIPLE
    position_sizing_method: PositionSizingMethod = PositionSizingMethod.FIXED_FRACTIONAL
    risk_fraction: Decimal = Decimal("0.01")
    fixed_monetary_risk: Decimal | None = None
    target_r_multiple: Decimal = Decimal("2.0")
    multi_targets: tuple[tuple[Decimal, Decimal], ...] | None = None
    min_risk_reward_ratio: Decimal = Decimal("1.5")
    rr_evaluation_metric: RiskRewardMetric = RiskRewardMetric.GROSS
    distal_buffer_ticks: int = 2
    atr_window: int = 14
    atr_multiplier: Decimal = Decimal("1.5")
    max_spread_ticks: int | None = None
    min_stop_distance_ticks: int = 5
    max_stop_distance_ticks: int = 10000
    strict_pit: bool = False
    policy: PitPolicy = PitPolicy.RESEARCH_ESTIMATED

    def __post_init__(self) -> None:
        if self.risk_fraction <= Decimal("0") or self.risk_fraction > Decimal("1.0"):
            raise InvalidRiskConfigError("risk_fraction must be in (0, 1.0]")
        if self.target_r_multiple <= Decimal("0"):
            raise InvalidRiskConfigError("target_r_multiple must be strictly positive Decimal")
        if self.min_risk_reward_ratio <= Decimal("0"):
            raise InvalidRiskConfigError("min_risk_reward_ratio must be strictly positive Decimal")
        if self.distal_buffer_ticks < 0:
            raise InvalidRiskConfigError("distal_buffer_ticks must be non-negative integer")
        if self.atr_window < 1:
            raise InvalidRiskConfigError("atr_window must be >= 1")
        if self.atr_multiplier <= Decimal("0"):
            raise InvalidRiskConfigError("atr_multiplier must be strictly positive Decimal")
        if self.min_stop_distance_ticks < 1:
            raise InvalidRiskConfigError("min_stop_distance_ticks must be >= 1")
        if self.max_stop_distance_ticks < self.min_stop_distance_ticks:
            raise InvalidRiskConfigError(
                "max_stop_distance_ticks must be >= min_stop_distance_ticks"
            )
        if self.multi_targets is not None:
            total_weight = sum(w for _, w in self.multi_targets)
            if abs(total_weight - Decimal("1.0")) > Decimal("0.0001"):
                raise InvalidRiskConfigError(
                    f"multi_targets weights must sum to 1.0, got {total_weight}"
                )


@dataclass(frozen=True, slots=True)
class IctRiskAssessment:
    """Immutable domain representation of an analytical Risk Assessment for an ICT Trade Plan.

    Guarantees:
    - Pure domain calculation: zero broker orders, zero fills, zero PnL.
    - Strict Point-in-Time safety: risk parameters evaluated strictly at as_of_utc.
    - Explicit Decimal precision across all prices, ratios, and lot calculations.
    """

    deterministic_assessment_id: str
    trade_plan_id: str
    setup_id: str
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    price_type: PriceType
    direction: TradePlanDirection
    entry_reference_price: Decimal
    stop_loss: StopLossProposal
    take_profit: TakeProfitProposal
    gross_risk_reward: Decimal
    status: RiskAssessmentStatus
    risk_assessment_time: datetime
    net_risk_reward: Decimal | None = None
    position_sizing: PositionSizingResult | None = None
    rejection_reasons: tuple[RiskRejectionReason, ...] = field(default_factory=tuple)
    as_of_utc: datetime | None = None
    specification: InstrumentRiskSpecification | None = None
    account_snapshot: AccountRiskSnapshot | None = None
    related_event_ids: tuple[str, ...] = field(default_factory=tuple)
    explanation: str = ""

    def __post_init__(self) -> None:
        if self.status != RiskAssessmentStatus.RISK_BLOCKED:
            if self.direction == TradePlanDirection.LONG:
                if self.stop_loss.price >= self.entry_reference_price:
                    raise InvalidRiskCalculationError(
                        f"Long stop-loss ({self.stop_loss.price}) must be strictly below entry ({self.entry_reference_price})"
                    )
                if self.take_profit.primary_target_price <= self.entry_reference_price:
                    raise InvalidRiskCalculationError(
                        f"Long take-profit ({self.take_profit.primary_target_price}) must be strictly above entry ({self.entry_reference_price})"
                    )
            else:
                if self.stop_loss.price <= self.entry_reference_price:
                    raise InvalidRiskCalculationError(
                        f"Short stop-loss ({self.stop_loss.price}) must be strictly above entry ({self.entry_reference_price})"
                    )
                if self.take_profit.primary_target_price >= self.entry_reference_price:
                    raise InvalidRiskCalculationError(
                        f"Short take-profit ({self.take_profit.primary_target_price}) must be strictly below entry ({self.entry_reference_price})"
                    )


@dataclass(frozen=True, slots=True)
class RiskAssessmentSummary:
    """Aggregate statistics for a collection of risk assessments."""

    total_assessed: int
    approved_count: int
    evaluated_count: int
    blocked_count: int
    analytical_only_count: int
    avg_gross_rr: Decimal
    avg_risk_distance: Decimal


# ── Deterministic Identifier Generator ───────────────────────────────────────
def generate_risk_assessment_id(
    trade_plan_id: str,
    stop_method: StopLossMethod,
    target_method: TakeProfitMethod,
    config_fingerprint: str = "",
    as_of_utc: datetime | None = None,
) -> str:
    """Generate a collision-resistant deterministic ID for an ICT Risk Assessment."""
    t_str = ensure_utc(as_of_utc).strftime("%Y%m%d%H%M%S") if as_of_utc is not None else "LIVE"
    raw = f"{trade_plan_id}:{stop_method.value}:{target_method.value}:{config_fingerprint}:{t_str}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"RISK_{trade_plan_id[:35]}_{digest}"


# ── Calculation Functions ────────────────────────────────────────────────────
def calculate_stop_loss(
    plan: IctTradePlan,
    method: StopLossMethod,
    *,
    tick_size: Decimal = Decimal("0.01"),
    buffer_ticks: int = 2,
    swings: Sequence[SwingPoint] | None = None,
    candles: Sequence[Candle] | None = None,
    atr_window: int = 14,
    atr_multiplier: Decimal = Decimal("1.5"),
    as_of_utc: datetime | None = None,
) -> tuple[StopLossProposal, RiskRejectionReason | None]:
    """Derive deterministic Stop-Loss price level according to selected method and constraints."""
    cutoff = ensure_utc(as_of_utc) if as_of_utc is not None else datetime.max.replace(tzinfo=UTC)
    ref_event_id: str | None = None
    ref_avail_time: datetime | None = None
    explanation = ""

    if method == StopLossMethod.STRUCTURAL_SWING:
        # Require confirmed swing opposite to trend direction forming at or before cutoff
        eligible_swings: list[SwingPoint] = []
        for s in swings or ():
            if s.instrument_id != plan.instrument_id or s.price_type != plan.price_type:
                continue
            if s.confirmation_time is None or s.confirmation_time > cutoff:
                continue
            is_valid_long_stop = (
                plan.direction == TradePlanDirection.LONG
                and s.is_swing_low
                and s.pivot_price < plan.entry_reference_price
            )
            is_valid_short_stop = (
                plan.direction == TradePlanDirection.SHORT
                and s.is_swing_high
                and s.pivot_price > plan.entry_reference_price
            )
            if is_valid_long_stop or is_valid_short_stop:
                eligible_swings.append(s)

        if eligible_swings:
            # Sort by confirmation time descending (most recent confirmed structural swing)
            eligible_swings.sort(
                key=lambda s: (
                    s.confirmation_time or datetime.min.replace(tzinfo=UTC),
                    s.pivot_time,
                ),
                reverse=True,
            )
            chosen_swing = eligible_swings[0]
            ref_event_id = chosen_swing.id
            ref_avail_time = chosen_swing.confirmation_time
            if plan.direction == TradePlanDirection.LONG:
                raw_stop = chosen_swing.pivot_price - (tick_size * Decimal(buffer_ticks))
            else:
                raw_stop = chosen_swing.pivot_price + (tick_size * Decimal(buffer_ticks))
            explanation = (
                f"Structural swing {chosen_swing.swing_type.value} at {chosen_swing.pivot_price}"
            )
        else:
            # Safe Fallback to distal boundary if structural swing not confirmed at cutoff
            ref_event_id = plan.entry_zone_id
            ref_avail_time = plan.entry_zone_available_time
            if plan.direction == TradePlanDirection.LONG:
                raw_stop = plan.entry_zone_bottom - (tick_size * Decimal(buffer_ticks))
            else:
                raw_stop = plan.entry_zone_top + (tick_size * Decimal(buffer_ticks))
            explanation = (
                "Structural swing unconfirmed at as_of_utc; fell back to ZONE_DISTAL_BOUNDARY"
            )

    elif method == StopLossMethod.ATR_ADJUSTED:
        eligible_candles = [
            c
            for c in (candles or ())
            if c.is_closed
            and c.close_time <= cutoff
            and c.instrument_id == plan.instrument_id
            and c.price_type == plan.price_type
        ]
        if len(eligible_candles) < atr_window:
            return (
                StopLossProposal(method=method, price=Decimal("0"), risk_distance=Decimal("0")),
                RiskRejectionReason.DATA_QUALITY_FAILURE,
            )
        atr_val = compute_historical_atr(eligible_candles, window=atr_window)
        if atr_val is None or atr_val <= Decimal("0"):
            return (
                StopLossProposal(method=method, price=Decimal("0"), risk_distance=Decimal("0")),
                RiskRejectionReason.DATA_QUALITY_FAILURE,
            )
        atr_buf = (atr_val * atr_multiplier).quantize(tick_size)
        if plan.direction == TradePlanDirection.LONG:
            raw_stop = plan.entry_reference_price - atr_buf
        else:
            raw_stop = plan.entry_reference_price + atr_buf
        explanation = f"ATR({atr_window})={atr_val} * {atr_multiplier}"
        ref_avail_time = eligible_candles[-1].close_time

    else:
        # Default: ZONE_DISTAL_BOUNDARY
        ref_event_id = plan.entry_zone_id
        ref_avail_time = plan.entry_zone_available_time
        if plan.direction == TradePlanDirection.LONG:
            raw_stop = plan.entry_zone_bottom - (tick_size * Decimal(buffer_ticks))
        else:
            raw_stop = plan.entry_zone_top + (tick_size * Decimal(buffer_ticks))
        explanation = f"Zone distal boundary with {buffer_ticks} tick buffer"

    # Conservative tick quantization: floor for Long (wider distance), ceil for Short (wider distance)
    if plan.direction == TradePlanDirection.LONG:
        stop_price = (raw_stop // tick_size) * tick_size
        if stop_price == plan.entry_reference_price:
            return (
                StopLossProposal(method=method, price=stop_price, risk_distance=Decimal("0")),
                RiskRejectionReason.ZERO_RISK_DISTANCE,
            )
        if stop_price > plan.entry_reference_price:
            return (
                StopLossProposal(method=method, price=stop_price, risk_distance=Decimal("0")),
                RiskRejectionReason.INVALID_STOP_DIRECTION,
            )
    else:
        # Ceiling division for Decimal: -(-raw_stop // tick_size) * tick_size
        stop_price = -(-raw_stop // tick_size) * tick_size
        if stop_price == plan.entry_reference_price:
            return (
                StopLossProposal(method=method, price=stop_price, risk_distance=Decimal("0")),
                RiskRejectionReason.ZERO_RISK_DISTANCE,
            )
        if stop_price < plan.entry_reference_price:
            return (
                StopLossProposal(method=method, price=stop_price, risk_distance=Decimal("0")),
                RiskRejectionReason.INVALID_STOP_DIRECTION,
            )

    risk_dist = abs(plan.entry_reference_price - stop_price)
    if risk_dist <= Decimal("0"):
        return (
            StopLossProposal(method=method, price=stop_price, risk_distance=Decimal("0")),
            RiskRejectionReason.ZERO_RISK_DISTANCE,
        )

    proposal = StopLossProposal(
        method=method,
        price=stop_price,
        risk_distance=risk_dist,
        reference_event_id=ref_event_id,
        reference_available_time=ref_avail_time,
        buffer_ticks=buffer_ticks,
        explanation=explanation,
    )
    return proposal, None


def calculate_take_profit(
    plan: IctTradePlan,
    method: TakeProfitMethod,
    risk_distance: Decimal,
    *,
    tick_size: Decimal = Decimal("0.01"),
    target_r_multiple: Decimal = Decimal("2.0"),
    multi_targets: tuple[tuple[Decimal, Decimal], ...] | None = None,
    swings: Sequence[SwingPoint] | None = None,
    as_of_utc: datetime | None = None,
) -> tuple[TakeProfitProposal, RiskRejectionReason | None]:
    """Derive deterministic Take-Profit target level(s) according to selected method."""
    cutoff = ensure_utc(as_of_utc) if as_of_utc is not None else datetime.max.replace(tzinfo=UTC)

    if risk_distance <= Decimal("0"):
        return (
            TakeProfitProposal(
                method=method, primary_target_price=Decimal("0"), reward_distance=Decimal("0")
            ),
            RiskRejectionReason.ZERO_RISK_DISTANCE,
        )

    ref_event_id: str | None = None
    ref_avail_time: datetime | None = None
    explanation = ""
    targets: list[TakeProfitTarget] = []

    if method == TakeProfitMethod.OPPOSING_LIQUIDITY_TARGET:
        eligible_swings: list[SwingPoint] = []
        for s in swings or ():
            if s.instrument_id != plan.instrument_id or s.price_type != plan.price_type:
                continue
            if s.confirmation_time is None or s.confirmation_time > cutoff:
                continue
            is_valid_long_target = (
                plan.direction == TradePlanDirection.LONG
                and s.is_swing_high
                and s.pivot_price > plan.entry_reference_price
            )
            is_valid_short_target = (
                plan.direction == TradePlanDirection.SHORT
                and s.is_swing_low
                and s.pivot_price < plan.entry_reference_price
            )
            if is_valid_long_target or is_valid_short_target:
                eligible_swings.append(s)

        if eligible_swings:
            # Sort by confirmation time descending
            eligible_swings.sort(
                key=lambda s: (
                    s.confirmation_time or datetime.min.replace(tzinfo=UTC),
                    s.pivot_time,
                ),
                reverse=True,
            )
            chosen_swing = eligible_swings[0]
            ref_event_id = chosen_swing.id
            ref_avail_time = chosen_swing.confirmation_time
            primary_target = (chosen_swing.pivot_price // tick_size) * tick_size
            reward_dist = abs(primary_target - plan.entry_reference_price)
            r_mult = (reward_dist / risk_distance).quantize(Decimal("0.01"))
            targets.append(
                TakeProfitTarget(
                    target_id="TP_OPPOSING_LIQUIDITY",
                    target_price=primary_target,
                    target_r_multiple=r_mult,
                    allocation_fraction=Decimal("1.0"),
                    description=f"Opposing {chosen_swing.swing_type.value} liquidity pool",
                )
            )
            explanation = f"Opposing swing {chosen_swing.swing_type.value} target at {chosen_swing.pivot_price}"
        else:
            # Fallback to Fixed R multiple
            if plan.direction == TradePlanDirection.LONG:
                primary_target = (
                    plan.entry_reference_price + (risk_distance * target_r_multiple)
                ).quantize(tick_size)
            else:
                primary_target = (
                    plan.entry_reference_price - (risk_distance * target_r_multiple)
                ).quantize(tick_size)
            reward_dist = abs(primary_target - plan.entry_reference_price)
            targets.append(
                TakeProfitTarget(
                    target_id="TP1",
                    target_price=primary_target,
                    target_r_multiple=target_r_multiple,
                    allocation_fraction=Decimal("1.0"),
                    description="Fallback Fixed R Multiple",
                )
            )
            explanation = "Opposing liquidity unconfirmed at cutoff; fell back to FIXED_R_MULTIPLE"

    elif method == TakeProfitMethod.MULTI_TARGET:
        target_specs = multi_targets or (
            (Decimal("1.0"), Decimal("0.33")),
            (Decimal("2.0"), Decimal("0.33")),
            (Decimal("3.0"), Decimal("0.34")),
        )
        total_weight = sum(w for _, w in target_specs)
        if abs(total_weight - Decimal("1.0")) > Decimal("0.0001"):
            raise InvalidRiskConfigError(
                f"multi_targets weights must sum to 1.0, got {total_weight}"
            )
        for idx, (r_mult, weight) in enumerate(target_specs, 1):
            if plan.direction == TradePlanDirection.LONG:
                tgt_px = (plan.entry_reference_price + (risk_distance * r_mult)).quantize(
                    tick_size
                )
            else:
                tgt_px = (plan.entry_reference_price - (risk_distance * r_mult)).quantize(
                    tick_size
                )
            targets.append(
                TakeProfitTarget(
                    target_id=f"TP{idx}",
                    target_price=tgt_px,
                    target_r_multiple=r_mult,
                    allocation_fraction=weight,
                    description=f"Multi-target scale {r_mult}R ({weight * Decimal('100')}%)",
                )
            )
        primary_target = targets[0].target_price
        reward_dist = abs(primary_target - plan.entry_reference_price)
        explanation = f"Multi-target structure ({len(targets)} stages)"

    else:
        # Default: FIXED_R_MULTIPLE
        if plan.direction == TradePlanDirection.LONG:
            primary_target = (
                plan.entry_reference_price + (risk_distance * target_r_multiple)
            ).quantize(tick_size)
        else:
            primary_target = (
                plan.entry_reference_price - (risk_distance * target_r_multiple)
            ).quantize(tick_size)
        reward_dist = abs(primary_target - plan.entry_reference_price)
        targets.append(
            TakeProfitTarget(
                target_id="TP1",
                target_price=primary_target,
                target_r_multiple=target_r_multiple,
                allocation_fraction=Decimal("1.0"),
                description=f"Fixed {target_r_multiple}R Multiple Target",
            )
        )
        explanation = f"Fixed {target_r_multiple}R Multiple Target"

    # Validation
    if plan.direction == TradePlanDirection.LONG:
        if primary_target <= plan.entry_reference_price:
            return (
                TakeProfitProposal(
                    method=method,
                    primary_target_price=primary_target,
                    reward_distance=Decimal("0"),
                ),
                RiskRejectionReason.INVALID_TARGET_DIRECTION,
            )
    else:
        if primary_target >= plan.entry_reference_price:
            return (
                TakeProfitProposal(
                    method=method,
                    primary_target_price=primary_target,
                    reward_distance=Decimal("0"),
                ),
                RiskRejectionReason.INVALID_TARGET_DIRECTION,
            )

    proposal = TakeProfitProposal(
        method=method,
        primary_target_price=primary_target,
        reward_distance=reward_dist,
        targets=tuple(targets),
        reference_event_id=ref_event_id,
        reference_available_time=ref_avail_time,
        explanation=explanation,
    )
    return proposal, None


def calculate_risk_reward(
    entry_price: Decimal,
    stop_price: Decimal,
    target_price: Decimal,
    *,
    trading_cost: Decimal | None = None,
    stop_scenario_cost: Decimal | None = None,
    target_scenario_cost: Decimal | None = None,
) -> tuple[Decimal, Decimal | None]:
    """Calculate exact Decimal Gross and Net (cost-adjusted) Risk-to-Reward ratios.

    Net R:R formula:
        net_rr = (gross_reward - target_cost) / (gross_loss + stop_cost)
    where gross_loss = risk_dist, gross_reward = reward_dist.
    If costs are unknown, net_rr is None.
    """
    risk_dist = abs(entry_price - stop_price)
    reward_dist = abs(target_price - entry_price)

    if risk_dist <= Decimal("0"):
        raise InvalidRiskCalculationError("risk_distance must be strictly positive Decimal")

    gross_rr = (reward_dist / risk_dist).quantize(Decimal("0.01"))
    net_rr: Decimal | None = None

    stop_cost: Decimal | None = None
    target_cost: Decimal | None = None
    if stop_scenario_cost is not None or target_scenario_cost is not None:
        stop_cost = stop_scenario_cost if stop_scenario_cost is not None else Decimal("0")
        target_cost = target_scenario_cost if target_scenario_cost is not None else Decimal("0")
    elif trading_cost is not None and trading_cost >= Decimal("0"):
        stop_cost = trading_cost
        target_cost = trading_cost

    if stop_cost is not None and target_cost is not None:
        total_risk = risk_dist + stop_cost
        net_reward = reward_dist - target_cost
        if total_risk > Decimal("0") and net_reward > Decimal("0"):
            net_rr = (net_reward / total_risk).quantize(Decimal("0.01"))
        elif total_risk > Decimal("0"):
            net_rr = Decimal("0.00")
        else:
            net_rr = None

    return gross_rr, net_rr


def calculate_position_size(
    account: AccountRiskSnapshot | None,
    specification: InstrumentRiskSpecification | None,
    risk_distance: Decimal,
    *,
    config: RiskManagementConfig,
    fx_rate: FxConversionRate | None = None,
    as_of_utc: datetime | None = None,
) -> PositionSizingResult:
    """Calculate allocated lot size and cash risk budget with strict conservative rounding."""
    cutoff = ensure_utc(as_of_utc) if as_of_utc is not None else datetime.max.replace(tzinfo=UTC)

    if account is None or specification is None:
        return PositionSizingResult(
            method=config.position_sizing_method,
            account_equity=Decimal("0"),
            risk_fraction=config.risk_fraction,
            risk_budget=Decimal("0"),
            raw_lots=Decimal("0"),
            allocated_lots=Decimal("0"),
            estimated_cash_risk=Decimal("0"),
            units=Decimal("0"),
            is_valid=False,
            rejection_reason=RiskRejectionReason.MISSING_METADATA,
        )

    # Determine risk budget
    if config.position_sizing_method == PositionSizingMethod.FIXED_FRACTIONAL:
        risk_budget = (account.equity * config.risk_fraction).quantize(Decimal("0.01"))
    else:
        if config.fixed_monetary_risk is None:
            return PositionSizingResult(
                method=config.position_sizing_method,
                account_equity=account.equity,
                risk_fraction=config.risk_fraction,
                risk_budget=Decimal("0"),
                raw_lots=Decimal("0"),
                allocated_lots=Decimal("0"),
                estimated_cash_risk=Decimal("0"),
                units=Decimal("0"),
                is_valid=False,
                rejection_reason=RiskRejectionReason.MISSING_METADATA,
            )
        risk_budget = config.fixed_monetary_risk

    # FX Conversion rate if quote currency != account currency
    if specification.quote_currency != account.account_currency:
        if fx_rate is None:
            return PositionSizingResult(
                method=config.position_sizing_method,
                account_equity=account.equity,
                risk_fraction=config.risk_fraction,
                risk_budget=risk_budget,
                raw_lots=Decimal("0"),
                allocated_lots=Decimal("0"),
                estimated_cash_risk=Decimal("0"),
                units=Decimal("0"),
                is_valid=False,
                rejection_reason=RiskRejectionReason.MISSING_CONVERSION_RATE,
            )
        if fx_rate.rate_time > cutoff:
            return PositionSizingResult(
                method=config.position_sizing_method,
                account_equity=account.equity,
                risk_fraction=config.risk_fraction,
                risk_budget=risk_budget,
                raw_lots=Decimal("0"),
                allocated_lots=Decimal("0"),
                estimated_cash_risk=Decimal("0"),
                units=Decimal("0"),
                is_valid=False,
                rejection_reason=RiskRejectionReason.FUTURE_INFORMATION,
            )
        risk_dist_acct = risk_distance * fx_rate.rate
    else:
        risk_dist_acct = risk_distance

    # Calculate risk per 1.0 standard lot
    price_risk_per_lot = risk_dist_acct * specification.units_per_lot
    commission_per_lot = specification.commission_per_lot or Decimal("0")
    spread_cost_per_lot = Decimal("0")
    if specification.typical_spread_ticks is not None:
        spread_px = Decimal(specification.typical_spread_ticks) * specification.tick_size
        spread_cost_per_lot = spread_px * specification.units_per_lot
    slippage_cost_per_lot = Decimal("0")
    if specification.slippage_ticks > 0:
        slip_px = Decimal(specification.slippage_ticks) * specification.tick_size
        slippage_cost_per_lot = slip_px * specification.units_per_lot

    total_cost_per_lot = commission_per_lot + spread_cost_per_lot + slippage_cost_per_lot
    total_risk_per_lot = price_risk_per_lot + total_cost_per_lot
    if total_risk_per_lot <= Decimal("0"):
        return PositionSizingResult(
            method=config.position_sizing_method,
            account_equity=account.equity,
            risk_fraction=config.risk_fraction,
            risk_budget=risk_budget,
            raw_lots=Decimal("0"),
            allocated_lots=Decimal("0"),
            estimated_cash_risk=Decimal("0"),
            units=Decimal("0"),
            is_valid=False,
            rejection_reason=RiskRejectionReason.ZERO_RISK_DISTANCE,
        )

    raw_lots = risk_budget / total_risk_per_lot

    # Round down to quantity_step to guarantee risk_budget is never exceeded
    step = specification.quantity_step
    allocated_lots = (raw_lots // step) * step

    # Minimum order quantity enforcement
    if allocated_lots < specification.minimum_order_quantity:
        min_lot_risk = specification.minimum_order_quantity * total_risk_per_lot
        if min_lot_risk > risk_budget:
            return PositionSizingResult(
                method=config.position_sizing_method,
                account_equity=account.equity,
                risk_fraction=config.risk_fraction,
                risk_budget=risk_budget,
                raw_lots=raw_lots,
                allocated_lots=Decimal("0"),
                estimated_cash_risk=Decimal("0"),
                units=Decimal("0"),
                is_valid=False,
                rejection_reason=RiskRejectionReason.MINIMUM_LOT_EXCEEDS_RISK,
            )
        allocated_lots = Decimal("0")

    # Maximum order quantity cap
    if allocated_lots > specification.maximum_order_quantity:
        allocated_lots = specification.maximum_order_quantity

    final_cash_risk = allocated_lots * total_risk_per_lot
    units = allocated_lots * specification.units_per_lot
    gross_loss = allocated_lots * price_risk_per_lot
    total_commission = allocated_lots * commission_per_lot
    total_spread_cost = allocated_lots * spread_cost_per_lot
    total_slippage_cost = allocated_lots * slippage_cost_per_lot
    total_stop_scenario_cost = total_commission + total_spread_cost + total_slippage_cost

    cost_status = (
        "PARTIAL_COSTS"
        if (specification.commission_per_lot is None or specification.typical_spread_ticks is None)
        else "FULL_COSTS_INCLUDED"
    )

    return PositionSizingResult(
        method=config.position_sizing_method,
        account_equity=account.equity,
        risk_fraction=config.risk_fraction,
        risk_budget=risk_budget,
        raw_lots=raw_lots.quantize(Decimal("0.0001")),
        allocated_lots=allocated_lots,
        estimated_cash_risk=final_cash_risk.quantize(Decimal("0.01")),
        units=units,
        estimated_commission=total_commission.quantize(Decimal("0.01")),
        estimated_spread_cost=total_spread_cost.quantize(Decimal("0.01")),
        estimated_slippage_cost=total_slippage_cost.quantize(Decimal("0.01")),
        gross_cash_loss=gross_loss.quantize(Decimal("0.01")),
        stop_scenario_total_cost=total_stop_scenario_cost.quantize(Decimal("0.01")),
        target_scenario_total_cost=total_stop_scenario_cost.quantize(Decimal("0.01")),
        cost_status=cost_status,
        is_valid=True,
    )


def validate_risk_constraints(
    plan: IctTradePlan,
    stop_loss: StopLossProposal,
    take_profit: TakeProfitProposal,
    gross_rr: Decimal,
    *,
    net_rr: Decimal | None = None,
    config: RiskManagementConfig,
    specification: InstrumentRiskSpecification | None = None,
    account: AccountRiskSnapshot | None = None,
    position_sizing: PositionSizingResult | None = None,
) -> list[RiskRejectionReason]:
    """Validate all strategy, microstructure, and portfolio risk bounds."""
    rejections: list[RiskRejectionReason] = []

    # 1. Plan Lifecycle Suitability
    if plan.plan_status in (
        TradePlanStatus.BLOCKED,
        TradePlanStatus.INVALIDATED,
        TradePlanStatus.EXPIRED,
    ):
        rejections.append(RiskRejectionReason.PLAN_NOT_ACTIVE)

    # 2. Minimum Risk/Reward Ratio (Configurable: GROSS or NET)
    rr_to_eval = (
        gross_rr
        if config.rr_evaluation_metric == RiskRewardMetric.GROSS
        else (net_rr if net_rr is not None else Decimal("0"))
    )
    if rr_to_eval < config.min_risk_reward_ratio:
        rejections.append(RiskRejectionReason.MINIMUM_RR_NOT_MET)

    # 3. Stop Distance Range
    tick_sz = specification.tick_size if specification else Decimal("0.01")
    stop_ticks = int(stop_loss.risk_distance / tick_sz)
    if stop_ticks < config.min_stop_distance_ticks or stop_ticks > config.max_stop_distance_ticks:
        rejections.append(RiskRejectionReason.STOP_DISTANCE_OUT_OF_RANGE)

    if (
        config.max_spread_ticks is not None
        and specification
        and specification.typical_spread_ticks is not None
        and specification.typical_spread_ticks > config.max_spread_ticks
    ):
        rejections.append(RiskRejectionReason.SPREAD_LIMIT_EXCEEDED)

    # 5. Position Sizing Internal Rejections
    if (
        position_sizing is not None
        and not position_sizing.is_valid
        and position_sizing.rejection_reason
    ):
        rejections.append(position_sizing.rejection_reason)

    # 6. Account & Portfolio Limits
    if account is not None:
        if account.daily_loss_limit_ratio is not None:
            max_daily_loss = account.equity * account.daily_loss_limit_ratio
            if account.daily_realized_loss >= max_daily_loss:
                rejections.append(RiskRejectionReason.RISK_BUDGET_EXCEEDED)
        if (
            account.max_open_risk_ratio is not None
            and position_sizing
            and position_sizing.is_valid
        ):
            new_open_risk = account.open_risk_ratio + (
                position_sizing.estimated_cash_risk / account.equity
            )
            if new_open_risk > account.max_open_risk_ratio:
                rejections.append(RiskRejectionReason.RISK_BUDGET_EXCEEDED)

    return rejections


# ── Public Risk Assessment Orchestrator ──────────────────────────────────────
def evaluate_trade_plan_risk(
    plan: IctTradePlan,
    *,
    config: RiskManagementConfig | None = None,
    specification: InstrumentRiskSpecification | None = None,
    account_snapshot: AccountRiskSnapshot | None = None,
    fx_rate: FxConversionRate | None = None,
    swings: Sequence[SwingPoint] | None = None,
    candles: Sequence[Candle] | None = None,
    as_of_utc: datetime | None = None,
) -> IctRiskAssessment:
    """Evaluate comprehensive, point-in-time safe risk parameters for an ICT Trade Plan."""
    cfg = config or RiskManagementConfig()
    norm_as_of = ensure_utc(as_of_utc) if as_of_utc is not None else None
    eval_time = norm_as_of or plan.plan_decision_time
    tick_sz = specification.tick_size if specification else Decimal("0.01")

    # 1. Calculate Stop Loss
    stop_prop, sl_rej = calculate_stop_loss(
        plan=plan,
        method=cfg.stop_loss_method,
        tick_size=tick_sz,
        buffer_ticks=cfg.distal_buffer_ticks,
        swings=swings,
        candles=candles,
        atr_window=cfg.atr_window,
        atr_multiplier=cfg.atr_multiplier,
        as_of_utc=norm_as_of,
    )

    # 2. Calculate Take Profit
    tp_prop, tp_rej = calculate_take_profit(
        plan=plan,
        method=cfg.take_profit_method,
        risk_distance=stop_prop.risk_distance,
        tick_size=tick_sz,
        target_r_multiple=cfg.target_r_multiple,
        multi_targets=cfg.multi_targets,
        swings=swings,
        as_of_utc=norm_as_of,
    )

    # 3. Calculate Risk/Reward with unified execution costs (commission + spread + slippage)
    stop_cost_unit: Decimal | None = None
    target_cost_unit: Decimal | None = None
    if specification is not None and (
        specification.commission_per_lot is not None
        or specification.typical_spread_ticks is not None
    ):
        cost_per_unit = specification.calculate_execution_cost_per_unit()
        stop_cost_unit = cost_per_unit
        target_cost_unit = cost_per_unit

    try:
        gross_rr, net_rr = calculate_risk_reward(
            entry_price=plan.entry_reference_price,
            stop_price=stop_prop.price,
            target_price=tp_prop.primary_target_price,
            stop_scenario_cost=stop_cost_unit,
            target_scenario_cost=target_cost_unit,
        )
    except InvalidRiskCalculationError:
        gross_rr = Decimal("0")
        net_rr = None

    # 4. Position Sizing Calculation
    sizing_res: PositionSizingResult | None = None
    if specification is not None and account_snapshot is not None:
        sizing_res = calculate_position_size(
            account=account_snapshot,
            specification=specification,
            risk_distance=stop_prop.risk_distance,
            config=cfg,
            fx_rate=fx_rate,
            as_of_utc=norm_as_of,
        )
        if sizing_res.is_valid and sizing_res.allocated_lots > Decimal("0"):
            # Compute reward cash flows
            if (
                specification.quote_currency != account_snapshot.account_currency
                and fx_rate is not None
            ):
                reward_dist_acct = tp_prop.reward_distance * fx_rate.rate
            else:
                reward_dist_acct = tp_prop.reward_distance
            gross_reward = (
                sizing_res.allocated_lots * specification.units_per_lot * reward_dist_acct
            ).quantize(Decimal("0.01"))
            net_reward = (gross_reward - sizing_res.target_scenario_total_cost).quantize(
                Decimal("0.01")
            )
            sizing_res = replace(
                sizing_res,
                gross_cash_reward=gross_reward,
                net_cash_reward=net_reward,
            )

    # 5. Validate Constraints & Derive Status
    rejections: list[RiskRejectionReason] = []
    if sl_rej:
        rejections.append(sl_rej)
    if tp_rej:
        rejections.append(tp_rej)

    constraint_rejections = validate_risk_constraints(
        plan=plan,
        stop_loss=stop_prop,
        take_profit=tp_prop,
        gross_rr=gross_rr,
        net_rr=net_rr,
        config=cfg,
        specification=specification,
        account=account_snapshot,
        position_sizing=sizing_res,
    )
    for r in constraint_rejections:
        if r not in rejections:
            rejections.append(r)

    # Strict PIT check
    if cfg.strict_pit and plan.availability_confidence.value != "VERIFIED":
        rejections.append(RiskRejectionReason.PIT_REJECTED)

    # Check intrabar ambiguity if closed candles are evaluated
    if candles:
        cutoff = norm_as_of or datetime.max.replace(tzinfo=UTC)
        for c in candles:
            if c.is_closed and c.open_time >= plan.plan_available_time and c.close_time <= cutoff:
                low_hit_sl = (
                    c.low <= stop_prop.price
                    if plan.direction == TradePlanDirection.LONG
                    else c.high >= stop_prop.price
                )
                high_hit_tp = (
                    c.high >= tp_prop.primary_target_price
                    if plan.direction == TradePlanDirection.LONG
                    else c.low <= tp_prop.primary_target_price
                )
                if low_hit_sl and high_hit_tp:
                    rejections.append(RiskRejectionReason.AMBIGUOUS_INTRABAR_SEQUENCE)
                    break

    # Determine Final Status
    if len(rejections) > 0:
        final_status = RiskAssessmentStatus.RISK_BLOCKED
    elif specification is None or account_snapshot is None:
        final_status = RiskAssessmentStatus.ANALYTICAL_ONLY
    elif sizing_res and sizing_res.is_valid:
        final_status = RiskAssessmentStatus.RISK_APPROVED
    else:
        final_status = RiskAssessmentStatus.RISK_EVALUATED

    # Related event IDs
    rel_events = list(plan.related_event_ids)
    if stop_prop.reference_event_id and stop_prop.reference_event_id not in rel_events:
        rel_events.append(stop_prop.reference_event_id)
    if tp_prop.reference_event_id and tp_prop.reference_event_id not in rel_events:
        rel_events.append(tp_prop.reference_event_id)

    cfg_fingerprint = f"{cfg.stop_loss_method.value}:{cfg.take_profit_method.value}:{cfg.risk_fraction}:{cfg.min_risk_reward_ratio}"
    assessment_id = generate_risk_assessment_id(
        trade_plan_id=plan.deterministic_trade_plan_id,
        stop_method=cfg.stop_loss_method,
        target_method=cfg.take_profit_method,
        config_fingerprint=cfg_fingerprint,
        as_of_utc=norm_as_of,
    )

    assessment = IctRiskAssessment(
        deterministic_assessment_id=assessment_id,
        trade_plan_id=plan.deterministic_trade_plan_id,
        setup_id=plan.source_setup_id,
        instrument_id=plan.instrument_id,
        source_id=plan.source_id,
        timeframe=plan.timeframe,
        price_type=plan.price_type,
        direction=plan.direction,
        entry_reference_price=plan.entry_reference_price,
        stop_loss=stop_prop,
        take_profit=tp_prop,
        gross_risk_reward=gross_rr,
        net_risk_reward=net_rr,
        position_sizing=sizing_res,
        status=final_status,
        rejection_reasons=tuple(rejections),
        risk_assessment_time=eval_time,
        as_of_utc=norm_as_of,
        specification=specification,
        account_snapshot=account_snapshot,
        related_event_ids=tuple(rel_events),
    )
    explanation = explain_risk_assessment(assessment)
    return replace(assessment, explanation=explanation)


def filter_risk_assessments_as_of(
    assessments: Sequence[IctRiskAssessment],
    as_of_utc: datetime,
) -> list[IctRiskAssessment]:
    """Reconstruct point-in-time state of risk assessments at cutoff timestamp."""
    cutoff = ensure_utc(as_of_utc)
    filtered: list[IctRiskAssessment] = []
    for a in assessments:
        if a.risk_assessment_time > cutoff:
            continue
        filtered.append(replace(a, as_of_utc=cutoff))
    return filtered


def summarize_risk_assessments(assessments: Sequence[IctRiskAssessment]) -> RiskAssessmentSummary:
    """Produce statistical summary across a collection of risk assessments."""
    total = len(assessments)
    if total == 0:
        return RiskAssessmentSummary(
            total_assessed=0,
            approved_count=0,
            evaluated_count=0,
            blocked_count=0,
            analytical_only_count=0,
            avg_gross_rr=Decimal("0"),
            avg_risk_distance=Decimal("0"),
        )

    approved = sum(1 for a in assessments if a.status == RiskAssessmentStatus.RISK_APPROVED)
    evaluated = sum(1 for a in assessments if a.status == RiskAssessmentStatus.RISK_EVALUATED)
    blocked = sum(1 for a in assessments if a.status == RiskAssessmentStatus.RISK_BLOCKED)
    analytical = sum(1 for a in assessments if a.status == RiskAssessmentStatus.ANALYTICAL_ONLY)

    avg_rr = Decimal(sum(a.gross_risk_reward for a in assessments)) / Decimal(total)
    avg_dist = Decimal(sum(a.stop_loss.risk_distance for a in assessments)) / Decimal(total)

    return RiskAssessmentSummary(
        total_assessed=total,
        approved_count=approved,
        evaluated_count=evaluated,
        blocked_count=blocked,
        analytical_only_count=analytical,
        avg_gross_rr=avg_rr.quantize(Decimal("0.01")),
        avg_risk_distance=avg_dist.quantize(Decimal("0.01")),
    )


def explain_risk_assessment(assessment: IctRiskAssessment) -> str:
    """Generate a structured, human-readable explanatory audit card for a risk assessment."""
    rej_str = (
        ", ".join(r.value for r in assessment.rejection_reasons)
        if assessment.rejection_reasons
        else "NONE"
    )
    lots_str = (
        f"{assessment.position_sizing.allocated_lots} lots"
        if assessment.position_sizing and assessment.position_sizing.is_valid
        else "UNALLOCATED"
    )
    budget_str = (
        f"${assessment.position_sizing.risk_budget}"
        if assessment.position_sizing
        else "UNSPECIFIED"
    )
    cash_risk_str = (
        f"${assessment.position_sizing.estimated_cash_risk}"
        if assessment.position_sizing and assessment.position_sizing.is_valid
        else "UNSPECIFIED"
    )
    net_rr_str = (
        f"{assessment.net_risk_reward}R"
        if assessment.net_risk_reward is not None
        else "UNSPECIFIED (Costs unknown)"
    )

    lines = [
        f"=== ICT RISK ASSESSMENT [{assessment.direction.value}] ===",
        f"Assessment ID:          {assessment.deterministic_assessment_id}",
        f"Trade Plan ID:          {assessment.trade_plan_id}",
        f"Source Setup ID:        {assessment.setup_id}",
        f"Instrument & Series:    {assessment.instrument_id} | {assessment.price_type.value} | {assessment.timeframe.name}",
        f"Status:                 {assessment.status.value}",
        f"Entry Reference Price:  {assessment.entry_reference_price}",
        f"Stop-Loss Level:        {assessment.stop_loss.price} ({assessment.stop_loss.method.value}, dist: {assessment.stop_loss.risk_distance})",
        f"Take-Profit Target:     {assessment.take_profit.primary_target_price} ({assessment.take_profit.method.value}, dist: {assessment.take_profit.reward_distance})",
        f"Gross Risk/Reward:      {assessment.gross_risk_reward}R",
        f"Net Risk/Reward:        {net_rr_str}",
        f"Risk Budget:            {budget_str}",
        f"Allocated Position:     {lots_str} (Estimated Cash Risk: {cash_risk_str})",
    ]
    if assessment.position_sizing and assessment.position_sizing.is_valid:
        ps = assessment.position_sizing
        lines.append(
            f"Cash Flows:             Gross Loss: ${ps.gross_cash_loss} | Gross Reward: ${ps.gross_cash_reward} | Net Reward: ${ps.net_cash_reward}"
        )
        lines.append(
            f"Execution Costs:        Comm: ${ps.estimated_commission} | Spread: ${ps.estimated_spread_cost} | Slip: ${ps.estimated_slippage_cost} (Total: ${ps.stop_scenario_total_cost})"
        )
    if assessment.specification:
        lines.append(
            f"Contract & Broker Spec: {assessment.specification.units_per_lot} units/lot | Step: {assessment.specification.quantity_step} | Source: {assessment.specification.source_type.value}"
        )
    lines.extend(
        [
            f"Rejection Reasons:      {rej_str}",
            f"Assessment Time:        {assessment.risk_assessment_time.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        ]
    )
    if assessment.as_of_utc:
        lines.append(
            f"As-Of Snapshot:         {assessment.as_of_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
    if assessment.related_event_ids:
        lines.append(f"Related Structural IDs: {', '.join(assessment.related_event_ids)}")
    return "\n".join(lines)
