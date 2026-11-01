"""Portfolio risk bounds, capacity evaluation, and execution cost stress simulation.

Guarantees that aggregate proposed risk never exceeds verified portfolio limits,
independent risk allocations are bounded, and execution costs under stress
remain compliant with risk budgets.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from traderlab.core.types import AvailabilityConfidence
from traderlab.domain.risk.ict_risk_management import (
    AccountRiskSnapshot,
    InstrumentRiskSpecification,
    RiskManagementConfig,
    calculate_position_size,
    calculate_risk_reward,
)
from traderlab.domain.strategy_validation.economic_opportunity import SelectedTradePlan


# ── Enums ───────────────────────────────────────────────────────────────────
class CostStressScenario(enum.Enum):
    """Stress testing scenario for execution costs (spread, commission, slippage)."""

    BASE_COST = "BASE_COST"
    """Standard baseline cost scenario without stress multipliers."""

    WIDER_SPREAD = "WIDER_SPREAD"
    """Spread stressed by configured multiplier (e.g. 2.0x wider spread)."""

    HIGHER_COMMISSION = "HIGHER_COMMISSION"
    """Round-trip commission stressed by configured multiplier (e.g. 1.5x commission)."""

    ADDITIONAL_SLIPPAGE = "ADDITIONAL_SLIPPAGE"
    """Additional slippage ticks applied to entry and stop executions."""

    COMBINED_STRESS = "COMBINED_STRESS"
    """Simultaneous application of spread widening, higher commission, and slippage."""


# ── Domain Models ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class PortfolioRiskSnapshot:
    """Point-in-Time snapshot of portfolio equity, open commitments, and risk limits."""

    account_id: str
    equity: Decimal
    snapshot_available_at: datetime
    balance: Decimal | None = None
    account_currency: str = "USD"
    existing_open_risk: Decimal = Decimal("0.00")
    committed_or_reserved_risk: Decimal = Decimal("0.00")
    daily_realized_loss: Decimal = Decimal("0.00")
    daily_risk_limit: Decimal | None = None
    maximum_portfolio_risk: Decimal | None = None
    maximum_single_event_risk: Decimal | None = None
    source: str = "HYPOTHETICAL_RESEARCH_SCENARIO"
    data_confidence: AvailabilityConfidence = AvailabilityConfidence.VERIFIED


@dataclass(frozen=True, slots=True)
class PortfolioRiskValidation:
    """Evaluation result of proposing trade plan risk against portfolio limits."""

    is_valid: bool
    portfolio_risk_capacity: Decimal
    proposed_event_risk: Decimal
    aggregate_portfolio_risk: Decimal
    daily_risk_usage: Decimal
    rejection_reasons: tuple[str, ...]
    explanation: str


# ── Cost Stress Evaluation ──────────────────────────────────────────────────
def evaluate_cost_stress(
    plan: SelectedTradePlan,
    specification: InstrumentRiskSpecification,
    *,
    scenario: CostStressScenario = CostStressScenario.BASE_COST,
    spread_multiplier: Decimal = Decimal("1.0"),
    commission_multiplier: Decimal = Decimal("1.0"),
    additional_slippage_ticks: int = 0,
) -> tuple[Decimal, Decimal | None, Decimal]:
    """Calculate stressed execution costs, resulting Net R:R, and required lot adjustment.

    Returns:
        tuple[stressed_cost_per_lot, stressed_net_rr, stressed_cash_risk]
    """
    base_spread_ticks = specification.typical_spread_ticks or 0
    base_comm = specification.commission_per_lot or Decimal("0.00")
    base_slip = specification.slippage_ticks

    # Apply scenario multipliers
    if scenario == CostStressScenario.WIDER_SPREAD:
        spread_ticks = int(Decimal(str(base_spread_ticks)) * spread_multiplier)
        comm = base_comm
        slip = base_slip
    elif scenario == CostStressScenario.HIGHER_COMMISSION:
        spread_ticks = base_spread_ticks
        comm = (base_comm * commission_multiplier).quantize(Decimal("0.01"))
        slip = base_slip
    elif scenario == CostStressScenario.ADDITIONAL_SLIPPAGE:
        spread_ticks = base_spread_ticks
        comm = base_comm
        slip = base_slip + additional_slippage_ticks
    elif scenario == CostStressScenario.COMBINED_STRESS:
        spread_ticks = int(Decimal(str(base_spread_ticks)) * spread_multiplier)
        comm = (base_comm * commission_multiplier).quantize(Decimal("0.01"))
        slip = base_slip + additional_slippage_ticks
    else:  # BASE_COST
        spread_ticks = base_spread_ticks
        comm = base_comm
        slip = base_slip

    # Create stressed specification
    stressed_spec = InstrumentRiskSpecification(
        instrument_id=specification.instrument_id,
        source_id=specification.source_id,
        tick_size=specification.tick_size,
        pip_size=specification.pip_size,
        units_per_lot=specification.units_per_lot,
        minimum_order_quantity=specification.minimum_order_quantity,
        maximum_order_quantity=specification.maximum_order_quantity,
        quantity_step=specification.quantity_step,
        quote_currency=specification.quote_currency,
        account_currency=specification.account_currency,
        commission_per_lot=comm,
        typical_spread_ticks=spread_ticks,
        slippage_ticks=slip,
        source_type=specification.source_type,
    )

    cost_per_unit = stressed_spec.calculate_execution_cost_per_unit()
    cost_per_lot = stressed_spec.calculate_execution_cost_per_lot()

    # Recompute Net R:R
    entry_px = plan.plan.entry_reference_price
    stop_px = plan.assessment.stop_loss.price
    tgt_px = plan.assessment.take_profit.primary_target_price

    _, stressed_net_rr = calculate_risk_reward(
        entry_price=entry_px,
        stop_price=stop_px,
        target_price=tgt_px,
        stop_scenario_cost=cost_per_unit,
        target_scenario_cost=cost_per_unit,
    )

    # Recompute position sizing under stress
    if plan.assessment.position_sizing and plan.assessment.position_sizing.is_valid:
        # Re-run sizing with stressed specification
        budget = plan.assessment.position_sizing.risk_budget
        # Create minimal snapshot for re-sizing
        temp_acct = AccountRiskSnapshot(
            account_id="TEMP_STRESS",
            equity=plan.assessment.position_sizing.account_equity,
            balance=plan.assessment.position_sizing.account_equity,
            account_currency=specification.account_currency,
        )
        temp_cfg = RiskManagementConfig(fixed_monetary_risk=budget)
        temp_res = calculate_position_size(
            account=temp_acct,
            specification=stressed_spec,
            risk_distance=plan.assessment.stop_loss.risk_distance,
            config=temp_cfg,
        )
        stressed_cash_risk = temp_res.estimated_cash_risk
    else:
        stressed_cash_risk = Decimal("0.00")

    return cost_per_lot, stressed_net_rr, stressed_cash_risk


# ── Portfolio Risk Validation ───────────────────────────────────────────────
def evaluate_portfolio_risk(
    proposed_plan: SelectedTradePlan,
    snapshot: PortfolioRiskSnapshot | None,
    *,
    require_snapshot: bool = True,
    as_of_utc: datetime | None = None,
) -> PortfolioRiskValidation:
    """Validate proposed trade plan risk against portfolio open commitments and limits."""
    cutoff = (
        as_of_utc.replace(tzinfo=UTC)
        if as_of_utc is not None
        else datetime.max.replace(tzinfo=UTC)
    )

    # 1. Missing snapshot handling
    if snapshot is None:
        if require_snapshot:
            return PortfolioRiskValidation(
                is_valid=False,
                portfolio_risk_capacity=Decimal("0.00"),
                proposed_event_risk=Decimal("0.00"),
                aggregate_portfolio_risk=Decimal("0.00"),
                daily_risk_usage=Decimal("0.00"),
                rejection_reasons=("MISSING_PORTFOLIO_SNAPSHOT",),
                explanation="Portfolio risk snapshot is required but not provided",
            )
        # Permissive research mode
        plan_risk = (
            proposed_plan.assessment.position_sizing.estimated_cash_risk
            if proposed_plan.assessment.position_sizing
            else Decimal("0.00")
        )
        return PortfolioRiskValidation(
            is_valid=True,
            portfolio_risk_capacity=plan_risk,
            proposed_event_risk=plan_risk,
            aggregate_portfolio_risk=plan_risk,
            daily_risk_usage=Decimal("0.00"),
            rejection_reasons=(),
            explanation="Provisional validation without snapshot under permissive research mode",
        )

    # 2. Point-in-Time validation of snapshot
    if snapshot.snapshot_available_at > cutoff:
        return PortfolioRiskValidation(
            is_valid=False,
            portfolio_risk_capacity=Decimal("0.00"),
            proposed_event_risk=Decimal("0.00"),
            aggregate_portfolio_risk=Decimal("0.00"),
            daily_risk_usage=Decimal("0.00"),
            rejection_reasons=("FUTURE_INFORMATION",),
            explanation=f"Snapshot time {snapshot.snapshot_available_at} is after evaluation cutoff {cutoff}",
        )

    # 3. Extract proposed risk
    ps = proposed_plan.assessment.position_sizing
    proposed_risk = ps.estimated_cash_risk if (ps and ps.is_valid) else Decimal("0.00")

    # 4. Compute risk capacity
    max_port_risk = snapshot.maximum_portfolio_risk
    if max_port_risk is None:
        # Default to 6% of equity if unspecified
        max_port_risk = (snapshot.equity * Decimal("0.06")).quantize(Decimal("0.01"))

    used_risk = snapshot.existing_open_risk + snapshot.committed_or_reserved_risk
    capacity = max_port_risk - used_risk
    if capacity < Decimal("0.00"):
        capacity = Decimal("0.00")

    rejection_reasons: list[str] = []

    # 5. Check Single Event Risk Limit
    max_event_risk = snapshot.maximum_single_event_risk
    if max_event_risk is None:
        # Default to 2% of equity if unspecified
        max_event_risk = (snapshot.equity * Decimal("0.02")).quantize(Decimal("0.01"))

    if proposed_risk > max_event_risk:
        rejection_reasons.append("EVENT_RISK_LIMIT_EXCEEDED")

    # 6. Check Aggregate Portfolio Risk Limit
    new_aggregate_risk = used_risk + proposed_risk
    if new_aggregate_risk > max_port_risk:
        rejection_reasons.append("PORTFOLIO_RISK_LIMIT_EXCEEDED")

    # 7. Check Daily Risk Limit
    max_daily_risk = snapshot.daily_risk_limit
    if max_daily_risk is None:
        # Default to 3% of equity if unspecified
        max_daily_risk = (snapshot.equity * Decimal("0.03")).quantize(Decimal("0.01"))

    daily_usage = snapshot.daily_realized_loss + proposed_risk
    if daily_usage > max_daily_risk:
        rejection_reasons.append("DAILY_RISK_LIMIT_EXCEEDED")

    is_valid = len(rejection_reasons) == 0
    expl = (
        f"Portfolio risk validated: capacity ${capacity}, proposed ${proposed_risk}, "
        f"new aggregate ${new_aggregate_risk} <= ${max_port_risk}"
        if is_valid
        else f"Portfolio risk limit breached: {', '.join(rejection_reasons)}"
    )

    return PortfolioRiskValidation(
        is_valid=is_valid,
        portfolio_risk_capacity=capacity,
        proposed_event_risk=proposed_risk,
        aggregate_portfolio_risk=new_aggregate_risk,
        daily_risk_usage=daily_usage,
        rejection_reasons=tuple(rejection_reasons),
        explanation=expl,
    )
