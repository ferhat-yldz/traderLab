"""Point-in-Time Safe Strategy Validation & Portfolio Risk Integrity Engine.

Orchestrates multi-timeframe and bid/ask trade plan deduplication, economic
opportunity identity binding, execution cost stress testing, and portfolio
risk capacity enforcement.
"""

from __future__ import annotations

import enum
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from traderlab.core.types import DataSourceId, InstrumentId, PriceType, Timeframe
from traderlab.domain.risk.ict_risk_management import (
    IctRiskAssessment,
    InstrumentRiskSpecification,
    RiskAssessmentStatus,
)
from traderlab.domain.strategy_validation.economic_opportunity import (
    OpportunityDeduplicationPolicy,
    PlanSelectionPolicy,
    group_trade_plans_by_opportunity,
    select_plan_per_opportunity,
)
from traderlab.domain.strategy_validation.portfolio_risk_validation import (
    CostStressScenario,
    PortfolioRiskSnapshot,
    PortfolioRiskValidation,
    evaluate_cost_stress,
    evaluate_portfolio_risk,
)
from traderlab.domain.trade_plans.ict_trade_plan import (
    IctTradePlan,
    TradePlanDirection,
    TradePlanStatus,
)


# ── Status & Rejection Enums ────────────────────────────────────────────────
class StrategyValidationStatus(enum.Enum):
    """Lifecycle qualification status of an ICT strategy plan candidate."""

    PORTFOLIO_VALIDATED = "PORTFOLIO_VALIDATED"
    """Fully qualified: verified active, selected for opportunity, and within portfolio risk limits."""

    SELECTED_FOR_RESEARCH = "SELECTED_FOR_RESEARCH"
    """Qualified under research policy (e.g. KEEP_ALL_FOR_RESEARCH) without live capital commitment."""

    DUPLICATE_ALTERNATIVE = "DUPLICATE_ALTERNATIVE"
    """Valid plan identified as a duplicate representation of an already selected economic opportunity."""

    RISK_CONDITIONALLY_ELIGIBLE = "RISK_CONDITIONALLY_ELIGIBLE"
    """Valid plan meeting risk criteria, but awaiting missing live portfolio snapshot verification."""

    VALID_ANALYTICAL_CANDIDATE = "VALID_ANALYTICAL_CANDIDATE"
    """Plan qualifies on chart geometry and risk mathematics, pending portfolio allocation."""

    REJECTED = "REJECTED"
    """Plan rejected due to strategy, microstructure, or portfolio risk boundary violations."""

    PIT_REJECTED = "PIT_REJECTED"
    """Plan rejected due to point-in-time timestamp anomalies or lookahead data leakage."""

    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    """Plan cannot be verified due to missing broker, account, or structural lineage metadata."""


class StrategyRejectionReason(enum.Enum):
    """Exhaustive, verifiable reasons for strategy candidate disqualification."""

    PLAN_NOT_ACTIVE = "PLAN_NOT_ACTIVE"
    DUPLICATE_ECONOMIC_OPPORTUNITY = "DUPLICATE_ECONOMIC_OPPORTUNITY"
    AMBIGUOUS_OPPORTUNITY_LINK = "AMBIGUOUS_OPPORTUNITY_LINK"
    MISSING_PORTFOLIO_SNAPSHOT = "MISSING_PORTFOLIO_SNAPSHOT"
    PORTFOLIO_RISK_LIMIT_EXCEEDED = "PORTFOLIO_RISK_LIMIT_EXCEEDED"
    EVENT_RISK_LIMIT_EXCEEDED = "EVENT_RISK_LIMIT_EXCEEDED"
    DAILY_RISK_LIMIT_EXCEEDED = "DAILY_RISK_LIMIT_EXCEEDED"
    MISSING_BROKER_SPECIFICATION = "MISSING_BROKER_SPECIFICATION"
    HYPOTHETICAL_BROKER_ONLY = "HYPOTHETICAL_BROKER_ONLY"
    EXECUTABLE_PRICE_NOT_VERIFIED = "EXECUTABLE_PRICE_NOT_VERIFIED"
    MISSING_FX_CONVERSION = "MISSING_FX_CONVERSION"
    NET_RR_BELOW_THRESHOLD = "NET_RR_BELOW_THRESHOLD"
    UNKNOWN_EXECUTION_COST = "UNKNOWN_EXECUTION_COST"
    FUTURE_INFORMATION = "FUTURE_INFORMATION"
    INVALID_SETUP_STATE = "INVALID_SETUP_STATE"
    INVALID_ENTRY_ZONE = "INVALID_ENTRY_ZONE"
    TIMEFRAME_MISMATCH = "TIMEFRAME_MISMATCH"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    AMBIGUOUS_INTRABAR_SEQUENCE = "AMBIGUOUS_INTRABAR_SEQUENCE"
    EXECUTION_LIQUIDITY_UNVERIFIED = "EXECUTION_LIQUIDITY_UNVERIFIED"
    EXPLICIT_PLAN_NOT_ELIGIBLE = "EXPLICIT_PLAN_NOT_ELIGIBLE"


# ── Configuration Model ─────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class StrategyValidationConfig:
    """Configuration governing deduplication, plan selection, cost stress, and portfolio limits."""

    deduplication_policy: OpportunityDeduplicationPolicy = (
        OpportunityDeduplicationPolicy.ONE_PLAN_PER_ECONOMIC_EVENT
    )
    plan_selection_policy: PlanSelectionPolicy = PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE
    timeframe_priority: tuple[Timeframe, ...] = (Timeframe.H4, Timeframe.H2, Timeframe.D1)
    explicit_plan_id: str | None = None
    stress_scenario: CostStressScenario = CostStressScenario.BASE_COST
    spread_stress_multiplier: Decimal = Decimal("1.0")
    commission_stress_multiplier: Decimal = Decimal("1.0")
    additional_slippage_ticks: int = 0
    min_net_risk_reward: Decimal = Decimal("1.5")
    require_portfolio_snapshot: bool = True
    strict_pit: bool = True
    allow_research_mode: bool = False


# ── Output Results & Backtest Contract ──────────────────────────────────────
@dataclass(frozen=True, slots=True)
class StrategyValidationResult:
    """Comprehensive validation result for a single trade plan candidate."""

    deterministic_validation_id: str
    plan: IctTradePlan
    assessment: IctRiskAssessment
    opportunity_id: str
    status: StrategyValidationStatus
    is_selected: bool
    is_duplicate_alternative: bool
    portfolio_validation: PortfolioRiskValidation | None
    rejection_reasons: tuple[StrategyRejectionReason, ...]
    stress_scenario: CostStressScenario
    validation_time_utc: datetime
    executable_price_verified: bool = False
    execution_liquidity_verified: bool = False
    as_of_utc: datetime | None = None
    explanation: str = ""


@dataclass(frozen=True, slots=True)
class StrategyValidationSummary:
    """Summary statistics reconciling trade plans across economic opportunities."""

    total_trade_plans: int
    total_risk_assessments: int
    unique_economic_opportunities: int
    selected_plans_count: int
    duplicate_alternatives_count: int
    portfolio_validated_count: int
    conditionally_eligible_count: int
    rejected_count: int
    pit_rejected_count: int
    total_proposed_risk: Decimal
    selected_proposed_risk: Decimal


@dataclass(frozen=True, slots=True)
class BacktestCandidateContract:
    """Immutable data contract passed downstream to Phase 5 Backtesting Engine."""

    economic_opportunity_id: str
    selected_plan_id: str
    source_setup_id: str
    direction: TradePlanDirection
    instrument_id: InstrumentId
    source_id: DataSourceId
    timeframe: Timeframe
    price_type: PriceType
    strategy_policy_id: str
    strategy_config_fingerprint: str
    setup_available_time: datetime
    plan_available_time: datetime
    validation_available_time: datetime
    plan_selection_time: datetime
    analytical_entry_reference: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    gross_rr: Decimal
    net_rr: Decimal | None
    risk_budget: Decimal
    proposed_quantity: Decimal
    broker_specification_provenance: str
    market_data_quality: str
    knowledge_snapshot_time: datetime
    as_of_utc: datetime | None
    entry_condition_status: str
    portfolio_validation_status: str
    related_event_ids: tuple[str, ...]
    analytical_reference_price: Decimal = Decimal("0.00")
    analytical_reference_price_type: PriceType = PriceType.BID
    required_execution_entry_side: PriceType = PriceType.ASK
    required_execution_exit_side: PriceType = PriceType.BID
    executable_price_verified: bool = False


# ── ID Generation & Helper ──────────────────────────────────────────────────
def generate_validation_id(plan_id: str, opp_id: str, eval_time: datetime) -> str:
    """Generate deterministic validation identifier."""
    raw = f"{plan_id}:{opp_id}:{eval_time.strftime('%Y%m%dT%H%M%SZ')}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"VAL_{digest}"


# ── Core Validation Orchestrator ────────────────────────────────────────────
def validate_ict_strategy_candidates(
    plans: Sequence[IctTradePlan],
    assessments: Sequence[IctRiskAssessment],
    *,
    config: StrategyValidationConfig | None = None,
    specification: InstrumentRiskSpecification | None = None,
    portfolio_snapshot: PortfolioRiskSnapshot | None = None,
    as_of_utc: datetime | None = None,
) -> tuple[StrategyValidationResult, ...]:
    """Validate ICT trade plans against deduplication, cost stress, and portfolio risk bounds."""
    cfg = config or StrategyValidationConfig()
    cutoff = as_of_utc.replace(tzinfo=UTC) if as_of_utc is not None else datetime.now(UTC)

    if not plans or not assessments:
        return ()

    assessments_by_id = {a.trade_plan_id: a for a in assessments}

    # 1. Deterministically group trade plans into canonical economic opportunities
    groups = group_trade_plans_by_opportunity(plans, assessments)

    results: list[StrategyValidationResult] = []

    for group in groups:
        opp_id = group.opportunity.economic_opportunity_id

        # 2. Apply deduplication and plan selection policy per economic opportunity
        if cfg.deduplication_policy == OpportunityDeduplicationPolicy.KEEP_ALL_FOR_RESEARCH:
            # All plans retained for research; no single plan selection
            for plan in group.plans:
                ass = assessments_by_id.get(plan.deterministic_trade_plan_id)
                if not ass:
                    continue
                val_id = generate_validation_id(plan.deterministic_trade_plan_id, opp_id, cutoff)
                is_active = (
                    ass.status == RiskAssessmentStatus.RISK_APPROVED
                    and plan.plan_status
                    in (
                        TradePlanStatus.WAITING_FOR_RETRACEMENT,
                        TradePlanStatus.TOUCH_OBSERVED,
                        TradePlanStatus.REACTION_CONFIRMED,
                    )
                )
                rejs: list[StrategyRejectionReason] = []
                if not is_active:
                    rejs.append(StrategyRejectionReason.PLAN_NOT_ACTIVE)

                status = (
                    StrategyValidationStatus.SELECTED_FOR_RESEARCH
                    if is_active
                    else StrategyValidationStatus.REJECTED
                )
                results.append(
                    StrategyValidationResult(
                        deterministic_validation_id=val_id,
                        plan=plan,
                        assessment=ass,
                        opportunity_id=opp_id,
                        status=status,
                        is_selected=is_active,
                        is_duplicate_alternative=False,
                        portfolio_validation=None,
                        rejection_reasons=tuple(rejs),
                        stress_scenario=cfg.stress_scenario,
                        validation_time_utc=cutoff,
                        as_of_utc=cutoff,
                        explanation="Retained under KEEP_ALL_FOR_RESEARCH policy without capital allocation",
                    )
                )

        else:  # ONE_PLAN_PER_ECONOMIC_EVENT (default) or GROUP_LEVEL_RISK_BUDGET
            primary_selected, duplicate_rejected = select_plan_per_opportunity(
                group,
                policy=cfg.plan_selection_policy,
                timeframe_priority=cfg.timeframe_priority,
                explicit_plan_id=cfg.explicit_plan_id,
                as_of_utc=cutoff,
            )

            # Process duplicate alternatives first
            for dup in duplicate_rejected:
                ass = dup.assessment or assessments_by_id.get(dup.plan.deterministic_trade_plan_id)
                if not ass:
                    continue
                val_id = generate_validation_id(
                    dup.plan.deterministic_trade_plan_id, opp_id, cutoff
                )
                rejs_enum: list[StrategyRejectionReason] = []
                for r_str in dup.rejection_reasons:
                    try:
                        rejs_enum.append(StrategyRejectionReason(r_str))
                    except ValueError:
                        rejs_enum.append(StrategyRejectionReason.DUPLICATE_ECONOMIC_OPPORTUNITY)

                is_dup = "DUPLICATE_ECONOMIC_OPPORTUNITY" in dup.rejection_reasons
                status = (
                    StrategyValidationStatus.DUPLICATE_ALTERNATIVE
                    if is_dup
                    else StrategyValidationStatus.REJECTED
                )

                results.append(
                    StrategyValidationResult(
                        deterministic_validation_id=val_id,
                        plan=dup.plan,
                        assessment=ass,
                        opportunity_id=opp_id,
                        status=status,
                        is_selected=False,
                        is_duplicate_alternative=is_dup,
                        portfolio_validation=None,
                        rejection_reasons=tuple(rejs_enum),
                        stress_scenario=cfg.stress_scenario,
                        validation_time_utc=cutoff,
                        as_of_utc=cutoff,
                        explanation=dup.rejection_explanation,
                    )
                )

            # Process the primary selected plan (if one exists)
            if primary_selected is not None:
                sel_plan = primary_selected.plan
                sel_ass = primary_selected.assessment
                val_id = generate_validation_id(
                    sel_plan.deterministic_trade_plan_id, opp_id, cutoff
                )
                rejs_list: list[StrategyRejectionReason] = []

                # 3. Cost Stress Evaluation if requested
                effective_net_rr = sel_ass.net_risk_reward
                if specification and cfg.stress_scenario != CostStressScenario.BASE_COST:
                    _, stressed_rr, _ = evaluate_cost_stress(
                        primary_selected,
                        specification,
                        scenario=cfg.stress_scenario,
                        spread_multiplier=cfg.spread_stress_multiplier,
                        commission_multiplier=cfg.commission_stress_multiplier,
                        additional_slippage_ticks=cfg.additional_slippage_ticks,
                    )
                    effective_net_rr = stressed_rr

                # 4. Check minimum Net R:R threshold
                if effective_net_rr is not None and effective_net_rr < cfg.min_net_risk_reward:
                    rejs_list.append(StrategyRejectionReason.NET_RR_BELOW_THRESHOLD)
                elif effective_net_rr is None and cfg.min_net_risk_reward > Decimal("0"):
                    rejs_list.append(StrategyRejectionReason.UNKNOWN_EXECUTION_COST)

                # 5. Evaluate Portfolio Risk
                port_val = evaluate_portfolio_risk(
                    primary_selected,
                    portfolio_snapshot,
                    require_snapshot=cfg.require_portfolio_snapshot,
                    as_of_utc=cutoff,
                )
                if not port_val.is_valid:
                    for r_str in port_val.rejection_reasons:
                        try:
                            rejs_list.append(StrategyRejectionReason(r_str))
                        except ValueError:
                            rejs_list.append(StrategyRejectionReason.PORTFOLIO_RISK_LIMIT_EXCEEDED)

                # Derive final status
                if rejs_list:
                    if StrategyRejectionReason.MISSING_PORTFOLIO_SNAPSHOT in rejs_list:
                        status = StrategyValidationStatus.RISK_CONDITIONALLY_ELIGIBLE
                    elif StrategyRejectionReason.FUTURE_INFORMATION in rejs_list:
                        status = StrategyValidationStatus.PIT_REJECTED
                    else:
                        status = StrategyValidationStatus.REJECTED
                else:
                    status = StrategyValidationStatus.PORTFOLIO_VALIDATED

                results.append(
                    StrategyValidationResult(
                        deterministic_validation_id=val_id,
                        plan=sel_plan,
                        assessment=sel_ass,
                        opportunity_id=opp_id,
                        status=status,
                        is_selected=(
                            status
                            in (
                                StrategyValidationStatus.PORTFOLIO_VALIDATED,
                                StrategyValidationStatus.RISK_CONDITIONALLY_ELIGIBLE,
                            )
                        ),
                        is_duplicate_alternative=False,
                        portfolio_validation=port_val,
                        rejection_reasons=tuple(rejs_list),
                        stress_scenario=cfg.stress_scenario,
                        validation_time_utc=cutoff,
                        as_of_utc=cutoff,
                        explanation=f"{primary_selected.selection_reason}; {port_val.explanation}",
                    )
                )

    return tuple(results)


# ── Filter, Summary & Explainability ────────────────────────────────────────
def filter_strategy_validations_as_of(
    validations: Sequence[StrategyValidationResult],
    as_of_utc: datetime,
) -> tuple[StrategyValidationResult, ...]:
    """Filter validation records strictly available at or before as_of_utc cutoff."""
    cutoff = as_of_utc.replace(tzinfo=UTC)
    return tuple(v for v in validations if v.plan.plan_available_time <= cutoff)


def summarize_strategy_validations(
    validations: Sequence[StrategyValidationResult],
) -> StrategyValidationSummary:
    """Aggregate summary statistics across strategy validation results."""
    unique_opps = set(v.opportunity_id for v in validations)
    selected = [v for v in validations if v.is_selected]
    dups = [v for v in validations if v.is_duplicate_alternative]
    port_val = [v for v in validations if v.status == StrategyValidationStatus.PORTFOLIO_VALIDATED]
    cond_elig = [
        v for v in validations if v.status == StrategyValidationStatus.RISK_CONDITIONALLY_ELIGIBLE
    ]
    rejs = [
        v
        for v in validations
        if v.status in (StrategyValidationStatus.REJECTED, StrategyValidationStatus.PIT_REJECTED)
    ]
    pit_rejs = [v for v in validations if v.status == StrategyValidationStatus.PIT_REJECTED]

    total_risk = sum(
        (
            v.assessment.position_sizing.estimated_cash_risk
            for v in validations
            if v.assessment.position_sizing
        ),
        Decimal("0.00"),
    )
    sel_risk = sum(
        (
            v.assessment.position_sizing.estimated_cash_risk
            for v in selected
            if v.assessment.position_sizing
        ),
        Decimal("0.00"),
    )

    return StrategyValidationSummary(
        total_trade_plans=len(validations),
        total_risk_assessments=len(validations),
        unique_economic_opportunities=len(unique_opps),
        selected_plans_count=len(selected),
        duplicate_alternatives_count=len(dups),
        portfolio_validated_count=len(port_val),
        conditionally_eligible_count=len(cond_elig),
        rejected_count=len(rejs),
        pit_rejected_count=len(pit_rejs),
        total_proposed_risk=total_risk.quantize(Decimal("0.01")),
        selected_proposed_risk=sel_risk.quantize(Decimal("0.01")),
    )


def explain_strategy_validation(result: StrategyValidationResult) -> str:
    """Generate structured audit card explaining strategy validation decisions."""
    p = result.plan
    a = result.assessment
    rejs = (
        ", ".join(r.value for r in result.rejection_reasons)
        if result.rejection_reasons
        else "NONE"
    )
    port_expl = result.portfolio_validation.explanation if result.portfolio_validation else "N/A"
    net_rr = f"{a.net_risk_reward}R" if a.net_risk_reward is not None else "NONE"
    lots = (
        a.position_sizing.allocated_lots
        if (a.position_sizing and a.position_sizing.is_valid)
        else "0.00"
    )
    cash_risk = (
        f"${a.position_sizing.estimated_cash_risk}"
        if (a.position_sizing and a.position_sizing.is_valid)
        else "$0.00"
    )

    lines = [
        f"=== STRATEGY VALIDATION AUDIT CARD [{p.direction.value}] ===",
        f"Validation ID:          {result.deterministic_validation_id}",
        f"Trade Plan ID:          {p.deterministic_trade_plan_id}",
        f"Economic Opportunity:  {result.opportunity_id}",
        f"Instrument & Series:    {p.instrument_id} | {p.price_type.value} | {p.timeframe.name}",
        f"Validation Status:      {result.status.value}",
        f"Is Selected Primary:    {result.is_selected} (Duplicate Alternative: {result.is_duplicate_alternative})",
        f"Net Risk/Reward:        {net_rr} (Gross: {a.gross_risk_reward}R)",
        f"Position & Risk:        {lots} lots (Cash Risk: {cash_risk})",
        f"Portfolio Risk Status:  {port_expl}",
        f"Stress Scenario:        {result.stress_scenario.value}",
        f"Rejection Reasons:      {rejs}",
        f"Decision Explanation:   {result.explanation}",
        f"Validation Timestamp:   {result.validation_time_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}",
    ]
    return "\n".join(lines)


# ── Phase 5 Backtest Contract Export ────────────────────────────────────────
def export_backtest_candidate_contracts(
    validations: Sequence[StrategyValidationResult],
) -> tuple[BacktestCandidateContract, ...]:
    """Export immutable data contracts for validated candidates to Phase 5 Backtesting Engine."""
    contracts: list[BacktestCandidateContract] = []
    for v in validations:
        if v.status in (
            StrategyValidationStatus.PORTFOLIO_VALIDATED,
            StrategyValidationStatus.RISK_CONDITIONALLY_ELIGIBLE,
            StrategyValidationStatus.SELECTED_FOR_RESEARCH,
        ):
            p = v.plan
            a = v.assessment
            ps = a.position_sizing

            spec_prov = (
                a.specification.source_type.value if a.specification else "UNKNOWN_PROVENANCE"
            )

            req_entry = PriceType.BID if p.direction == TradePlanDirection.SHORT else PriceType.ASK
            req_exit = PriceType.ASK if p.direction == TradePlanDirection.SHORT else PriceType.BID

            contract = BacktestCandidateContract(
                economic_opportunity_id=v.opportunity_id,
                selected_plan_id=p.deterministic_trade_plan_id,
                source_setup_id=p.source_setup_id,
                direction=p.direction,
                instrument_id=p.instrument_id,
                source_id=p.source_id,
                timeframe=p.timeframe,
                price_type=p.price_type,
                strategy_policy_id="ICT_FVG_FIRST_MIDPOINT",
                strategy_config_fingerprint=hashlib.sha256(
                    f"{p.instrument_id}:{p.timeframe.name}:{p.direction.value}".encode()
                ).hexdigest()[:12],
                setup_available_time=p.setup_available_time,
                plan_available_time=p.plan_available_time,
                validation_available_time=v.validation_time_utc,
                plan_selection_time=p.plan_available_time,
                analytical_entry_reference=p.entry_reference_price,
                stop_loss=a.stop_loss.price,
                take_profit=a.take_profit.primary_target_price,
                gross_rr=a.gross_risk_reward,
                net_rr=a.net_risk_reward,
                risk_budget=ps.risk_budget if ps else Decimal("0.00"),
                proposed_quantity=ps.allocated_lots if ps else Decimal("0.00"),
                broker_specification_provenance=spec_prov,
                market_data_quality="M1_DERIVED_OHLC_STRICT_PIT",
                knowledge_snapshot_time=p.plan_available_time,
                as_of_utc=v.as_of_utc,
                entry_condition_status=",".join(p.entry_conditions),
                portfolio_validation_status=v.status.value,
                related_event_ids=a.related_event_ids,
                analytical_reference_price=p.entry_reference_price,
                analytical_reference_price_type=p.price_type,
                required_execution_entry_side=req_entry,
                required_execution_exit_side=req_exit,
                executable_price_verified=v.executable_price_verified,
            )
            contracts.append(contract)

    return tuple(contracts)
