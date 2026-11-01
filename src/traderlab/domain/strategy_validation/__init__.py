"""Strategy Validation & Portfolio Risk Integrity bounded context.

Provides Point-in-Time safe deduplication of multi-timeframe and bid/ask trade plans,
economic opportunity identity binding, execution cost stress testing, and portfolio
risk capacity enforcement.
"""

from traderlab.domain.strategy_validation.economic_opportunity import (
    EconomicOpportunity,
    EconomicOpportunityEvidence,
    EconomicOpportunityGroup,
    OpportunityDeduplicationPolicy,
    PlanSelectionPolicy,
    RejectedTradePlan,
    SelectedTradePlan,
    generate_economic_opportunity_id,
    group_trade_plans_by_opportunity,
    identify_economic_opportunity,
    select_plan_per_opportunity,
)
from traderlab.domain.strategy_validation.portfolio_risk_validation import (
    CostStressScenario,
    PortfolioRiskSnapshot,
    PortfolioRiskValidation,
    evaluate_cost_stress,
    evaluate_portfolio_risk,
)
from traderlab.domain.strategy_validation.strategy_validation import (
    BacktestCandidateContract,
    StrategyRejectionReason,
    StrategyValidationConfig,
    StrategyValidationResult,
    StrategyValidationStatus,
    StrategyValidationSummary,
    explain_strategy_validation,
    export_backtest_candidate_contracts,
    filter_strategy_validations_as_of,
    generate_validation_id,
    summarize_strategy_validations,
    validate_ict_strategy_candidates,
)

__all__ = [
    "BacktestCandidateContract",
    "CostStressScenario",
    "EconomicOpportunity",
    "EconomicOpportunityEvidence",
    "EconomicOpportunityGroup",
    "OpportunityDeduplicationPolicy",
    "PlanSelectionPolicy",
    "PortfolioRiskSnapshot",
    "PortfolioRiskValidation",
    "RejectedTradePlan",
    "SelectedTradePlan",
    "StrategyRejectionReason",
    "StrategyValidationConfig",
    "StrategyValidationResult",
    "StrategyValidationStatus",
    "StrategyValidationSummary",
    "evaluate_cost_stress",
    "evaluate_portfolio_risk",
    "explain_strategy_validation",
    "export_backtest_candidate_contracts",
    "filter_strategy_validations_as_of",
    "generate_economic_opportunity_id",
    "generate_validation_id",
    "group_trade_plans_by_opportunity",
    "identify_economic_opportunity",
    "select_plan_per_opportunity",
    "summarize_strategy_validations",
    "validate_ict_strategy_candidates",
]
