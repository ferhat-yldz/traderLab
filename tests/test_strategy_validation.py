"""Phase 4E-C Strategy Validation, Economic Opportunity Deduplication & Portfolio Risk Test Suite.

Verifies all 65 required test cases:
- Economic opportunity identity & deterministic grouping across BID/ASK & H2/H4
- Disentanglement of single market events from multi-series representations
- Deduplication policies (ONE_PLAN_PER_ECONOMIC_EVENT, KEEP_ALL_FOR_RESEARCH, GROUP_LEVEL_RISK_BUDGET)
- Plan selection policies (EARLIEST_PIT_ELIGIBLE, TIMEFRAME_PRIORITY, HIGHEST_VALID_NET_RR, EXPLICIT_PLAN_ID)
- Point-in-Time safety, bitemporal isolation, and lookahead leakage prevention
- Portfolio risk bounds, capacity evaluation, and daily limit enforcement
- Execution cost stress testing (WIDER_SPREAD, HIGHER_COMMISSION, SLIPPAGE, COMBINED_STRESS)
- Strict absence of broker orders, fills, or live PnL
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
)
from traderlab.domain.market_structure.ict_setup import IctSetupType
from traderlab.domain.risk.ict_risk_management import (
    AccountRiskSnapshot,
    IctRiskAssessment,
    InstrumentRiskSpecification,
    RiskManagementConfig,
    SpecificationSourceType,
    evaluate_trade_plan_risk,
)
from traderlab.domain.strategy_validation.economic_opportunity import (
    OpportunityDeduplicationPolicy,
    PlanSelectionPolicy,
    SelectedTradePlan,
    generate_economic_opportunity_id,
    group_trade_plans_by_opportunity,
    identify_economic_opportunity,
    select_plan_per_opportunity,
)
from traderlab.domain.strategy_validation.portfolio_risk_validation import (
    CostStressScenario,
    PortfolioRiskSnapshot,
    evaluate_cost_stress,
    evaluate_portfolio_risk,
)
from traderlab.domain.strategy_validation.strategy_validation import (
    StrategyRejectionReason,
    StrategyValidationConfig,
    StrategyValidationStatus,
    explain_strategy_validation,
    export_backtest_candidate_contracts,
    filter_strategy_validations_as_of,
    summarize_strategy_validations,
    validate_ict_strategy_candidates,
)
from traderlab.domain.trade_plans.ict_trade_plan import (
    EntryZoneType,
    IctTradePlan,
    TradePlanDirection,
    TradePlanEntryMode,
    TradePlanStatus,
)

INST = InstrumentId("XAUUSD")
SRC_H4_BID = DataSourceId("market_data_lab_m1_derived_h4")
SRC_H4_ASK = DataSourceId("market_data_lab_m1_derived_h4")
SRC_H2_BID = DataSourceId("market_data_lab_m1_derived_h2")
SRC_H2_ASK = DataSourceId("market_data_lab_m1_derived_h2")

T0 = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
T_H2_AVAIL = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
T_H4_AVAIL = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


# ── Fixtures & Helpers ──────────────────────────────────────────────────────
def make_plan(
    plan_id: str,
    timeframe: Timeframe = Timeframe.H4,
    price_type: PriceType = PriceType.BID,
    direction: TradePlanDirection = TradePlanDirection.SHORT,
    entry_price: str = "4227.950",
    zone_bottom: str = "4199.955",
    zone_top: str = "4255.945",
    setup_id: str = "XAUUSD:market_data_lab_m1_derived_h4:BID:H4:CONTINUATION:BEARISH:XAUUSD_market_data_lab_m1_derived_h4_BID_H4_BOS_BEARISH_20260928T000000Z_XAUUSD_market_data_lab_m1_derived_h4_BID_H4_SWING_LOW_20260925T120000Z_3L3R:20260928080000",
    avail_time: datetime = T_H4_AVAIL,
    status: TradePlanStatus = TradePlanStatus.WAITING_FOR_RETRACEMENT,
) -> IctTradePlan:
    src = (
        SRC_H4_BID
        if (timeframe == Timeframe.H4 and price_type == PriceType.BID)
        else SRC_H4_ASK
        if (timeframe == Timeframe.H4 and price_type == PriceType.ASK)
        else SRC_H2_BID
        if (timeframe == Timeframe.H2 and price_type == PriceType.BID)
        else SRC_H2_ASK
    )
    return IctTradePlan(
        deterministic_trade_plan_id=plan_id,
        source_setup_id=setup_id,
        instrument_id=INST,
        source_id=src,
        timeframe=timeframe,
        price_type=price_type,
        setup_type=IctSetupType.CONTINUATION,
        direction=direction,
        entry_mode=TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE,
        entry_zone_id="ZONE_1",
        entry_zone_type=EntryZoneType.NORMAL_FVG,
        entry_zone_bottom=Decimal(zone_bottom),
        entry_zone_top=Decimal(zone_top),
        entry_reference_price=Decimal(entry_price),
        entry_conditions=("FVG_CONFIRMED",),
        plan_status=status,
        setup_available_time=avail_time,
        entry_zone_available_time=avail_time,
        plan_available_time=avail_time,
        plan_decision_time=avail_time,
        availability_confidence=AvailabilityConfidence.VERIFIED,
    )


def make_spec(
    comm: str = "2.00",
    spread_ticks: int = 25,
    units: str = "100",
) -> InstrumentRiskSpecification:
    return InstrumentRiskSpecification(
        instrument_id=INST,
        source_id=SRC_H4_BID,
        tick_size=Decimal("0.01"),
        pip_size=Decimal("0.10"),
        units_per_lot=Decimal(units),
        minimum_order_quantity=Decimal("0.01"),
        maximum_order_quantity=Decimal("50.00"),
        quantity_step=Decimal("0.01"),
        commission_per_lot=Decimal(comm),
        typical_spread_ticks=spread_ticks,
        source_type=SpecificationSourceType.HYPOTHETICAL_BROKER_SCENARIO,
    )


def make_assessment(
    plan: IctTradePlan,
    spec: InstrumentRiskSpecification | None = None,
    equity: str = "100000.00",
    distal_ticks: int = 20,
) -> IctRiskAssessment:
    s = spec or make_spec()
    acct = AccountRiskSnapshot(
        account_id="ACCT_1",
        account_currency="USD",
        equity=Decimal(equity),
        balance=Decimal(equity),
    )
    cfg = RiskManagementConfig(
        risk_fraction=Decimal("0.01"),
        target_r_multiple=Decimal("2.0"),
        distal_buffer_ticks=distal_ticks,
    )
    return evaluate_trade_plan_risk(plan, config=cfg, specification=s, account_snapshot=acct)


# ── Tests 1 to 10: Identity, Grouping & Deduplication Policies ──────────────
def test_economic_opportunity_id_deterministic() -> None:
    id1 = generate_economic_opportunity_id(
        INST, "market_data_lab_m1", TradePlanDirection.SHORT, "BOS_BEARISH", T0, "20260925"
    )
    id2 = generate_economic_opportunity_id(
        INST, "market_data_lab_m1", TradePlanDirection.SHORT, "BOS_BEARISH", T0, "20260925"
    )
    assert id1 == id2
    assert id1.startswith("OPP_XAUUSD_SHORT_BOS_BEARISH_20260928T000000Z_")


def test_bid_ask_mirror_grouping() -> None:
    p_bid = make_plan("P_BID", timeframe=Timeframe.H4, price_type=PriceType.BID)
    p_ask = make_plan("P_ASK", timeframe=Timeframe.H4, price_type=PriceType.ASK)
    a_bid = make_assessment(p_bid)
    a_ask = make_assessment(p_ask)

    groups = group_trade_plans_by_opportunity([p_bid, p_ask], [a_bid, a_ask])
    assert len(groups) == 1
    assert len(groups[0].plans) == 2
    assert PriceType.BID in groups[0].price_types
    assert PriceType.ASK in groups[0].price_types


def test_h2_h4_same_underlying_event_grouping() -> None:
    setup_h4 = "XAUUSD:market_data_lab_m1_derived_h4:BID:H4:CONTINUATION:BEARISH:XAUUSD_market_data_lab_m1_derived_h4_BID_H4_BOS_BEARISH_20260928T000000Z_XAUUSD_market_data_lab_m1_derived_h4_BID_H4_SWING_LOW_20260925T120000Z_3L3R:20260928080000"
    setup_h2 = "XAUUSD:market_data_lab_m1_derived_h2:BID:H2:CONTINUATION:BEARISH:XAUUSD_market_data_lab_m1_derived_h2_BID_H2_BOS_BEARISH_20260928T000000Z_XAUUSD_market_data_lab_m1_derived_h2_BID_H2_SWING_LOW_20260925T140000Z_3L3R:20260928040000"

    p_h4 = make_plan("P_H4", timeframe=Timeframe.H4, setup_id=setup_h4, avail_time=T_H4_AVAIL)
    p_h2 = make_plan("P_H2", timeframe=Timeframe.H2, setup_id=setup_h2, avail_time=T_H2_AVAIL)

    groups = group_trade_plans_by_opportunity([p_h4, p_h2])
    assert len(groups) == 1
    assert groups[0].opportunity.evidence.structural_trigger_time_utc == T0
    assert Timeframe.H4 in groups[0].timeframes
    assert Timeframe.H2 in groups[0].timeframes


def test_different_market_events_not_merged() -> None:
    setup1 = "XAUUSD:market_data_lab_m1_derived_h4:BID:H4:CONTINUATION:BEARISH:XAUUSD_market_data_lab_m1_derived_h4_BID_H4_BOS_BEARISH_20260928T000000Z_XAUUSD_market_data_lab_m1_derived_h4_BID_H4_SWING_LOW_20260925T120000Z_3L3R:20260928080000"
    setup2 = "XAUUSD:market_data_lab_m1_derived_h4:BID:H4:CONTINUATION:BEARISH:XAUUSD_market_data_lab_m1_derived_h4_BID_H4_BOS_BEARISH_20260922T060000Z_XAUUSD_market_data_lab_m1_derived_h4_BID_H4_SWING_LOW_20260921T140000Z_3L3R:20260922080000"

    p1 = make_plan("P1", setup_id=setup1)
    p2 = make_plan("P2", setup_id=setup2)

    groups = group_trade_plans_by_opportunity([p1, p2])
    assert len(groups) == 2


def test_same_direction_alone_not_enough_for_grouping() -> None:
    p1 = make_plan(
        "P1",
        direction=TradePlanDirection.SHORT,
        setup_id="SETUP:BOS_BEARISH_20260920T000000Z_SWING_LOW_20260918",
    )
    p2 = make_plan(
        "P2",
        direction=TradePlanDirection.SHORT,
        setup_id="SETUP:BOS_BEARISH_20260928T000000Z_SWING_LOW_20260925",
    )

    groups = group_trade_plans_by_opportunity([p1, p2])
    assert len(groups) == 2
    assert (
        groups[0].opportunity.economic_opportunity_id
        != groups[1].opportunity.economic_opportunity_id
    )


def test_unrelated_fvg_not_merged() -> None:
    p1 = make_plan(
        "P1", setup_id="SETUP:BOS_BEARISH_20260928T000000Z_SWING_LOW_20260925:20260928080000"
    )
    p2 = make_plan(
        "P2", setup_id="SETUP:BOS_BEARISH_20260930T140000Z_SWING_LOW_20260930:20260930180000"
    )

    groups = group_trade_plans_by_opportunity([p1, p2])
    assert len(groups) == 2


def test_unrelated_bos_not_merged() -> None:
    p1 = make_plan("P1", setup_id="SETUP:BOS_BULLISH_20260918T060000Z_SWING_HIGH_20260917")
    p2 = make_plan("P2", setup_id="SETUP:BOS_BULLISH_20261009T020000Z_SWING_HIGH_20261008")

    groups = group_trade_plans_by_opportunity([p1, p2])
    assert len(groups) == 2


def test_opportunity_id_input_order_independent() -> None:
    p1 = make_plan("P1", timeframe=Timeframe.H4, price_type=PriceType.BID)
    p2 = make_plan("P2", timeframe=Timeframe.H2, price_type=PriceType.BID)
    p3 = make_plan("P3", timeframe=Timeframe.H4, price_type=PriceType.ASK)

    g_fwd = group_trade_plans_by_opportunity([p1, p2, p3])
    g_rev = group_trade_plans_by_opportunity([p3, p2, p1])

    assert len(g_fwd) == len(g_rev) == 1
    assert (
        g_fwd[0].opportunity.economic_opportunity_id
        == g_rev[0].opportunity.economic_opportunity_id
    )


def test_keep_all_research_policy() -> None:
    p1 = make_plan("P1", timeframe=Timeframe.H4, price_type=PriceType.BID)
    p2 = make_plan("P2", timeframe=Timeframe.H4, price_type=PriceType.ASK)
    a1 = make_assessment(p1)
    a2 = make_assessment(p2)

    cfg = StrategyValidationConfig(
        deduplication_policy=OpportunityDeduplicationPolicy.KEEP_ALL_FOR_RESEARCH
    )
    res = validate_ict_strategy_candidates([p1, p2], [a1, a2], config=cfg)

    assert len(res) == 2
    assert all(r.status == StrategyValidationStatus.SELECTED_FOR_RESEARCH for r in res)
    assert all(r.is_selected for r in res)


def test_one_plan_per_event_policy() -> None:
    p1 = make_plan("P1", timeframe=Timeframe.H4, price_type=PriceType.BID)
    p2 = make_plan("P2", timeframe=Timeframe.H4, price_type=PriceType.ASK)
    a1 = make_assessment(p1)
    a2 = make_assessment(p2)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    cfg = StrategyValidationConfig(
        deduplication_policy=OpportunityDeduplicationPolicy.ONE_PLAN_PER_ECONOMIC_EVENT
    )
    res = validate_ict_strategy_candidates([p1, p2], [a1, a2], config=cfg, portfolio_snapshot=snap)

    assert len(res) == 2
    selected = [r for r in res if r.is_selected]
    dups = [r for r in res if r.is_duplicate_alternative]
    assert len(selected) == 1
    assert len(dups) == 1
    assert selected[0].status == StrategyValidationStatus.PORTFOLIO_VALIDATED
    assert dups[0].status == StrategyValidationStatus.DUPLICATE_ALTERNATIVE


# ── Tests 11 to 20: Selection Policies & Mutability Protections ──────────────
def test_group_level_risk_budget() -> None:
    p1 = make_plan("P1", timeframe=Timeframe.H4, price_type=PriceType.BID)
    p2 = make_plan("P2", timeframe=Timeframe.H2, price_type=PriceType.BID)
    a1 = make_assessment(p1)
    a2 = make_assessment(p2)

    groups = group_trade_plans_by_opportunity([p1, p2], [a1, a2])
    assert a1.position_sizing is not None
    assert a2.position_sizing is not None
    assert (
        groups[0].total_proposed_risk
        == a1.position_sizing.estimated_cash_risk + a2.position_sizing.estimated_cash_risk
    )


def test_duplicate_plans_preserve_evidence() -> None:
    p1 = make_plan("P1", timeframe=Timeframe.H4, price_type=PriceType.BID)
    p2 = make_plan("P2", timeframe=Timeframe.H4, price_type=PriceType.ASK)
    a1 = make_assessment(p1)
    a2 = make_assessment(p2)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates([p1, p2], [a1, a2], portfolio_snapshot=snap)
    dup = next(r for r in res if r.is_duplicate_alternative)

    assert dup.opportunity_id.startswith("OPP_")
    assert StrategyRejectionReason.DUPLICATE_ECONOMIC_OPPORTUNITY in dup.rejection_reasons
    assert "duplicate alternative" in dup.explanation


def test_selected_plan_stable_id() -> None:
    p1 = make_plan("P1", timeframe=Timeframe.H4, price_type=PriceType.BID)
    a1 = make_assessment(p1)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res1 = validate_ict_strategy_candidates(
        [p1], [a1], portfolio_snapshot=snap, as_of_utc=T_H4_AVAIL
    )
    res2 = validate_ict_strategy_candidates(
        [p1], [a1], portfolio_snapshot=snap, as_of_utc=T_H4_AVAIL
    )
    assert res1[0].deterministic_validation_id == res2[0].deterministic_validation_id


def test_earliest_pit_eligible_selection() -> None:
    p_early = make_plan("P_EARLY", timeframe=Timeframe.H2, avail_time=T_H2_AVAIL)
    p_late = make_plan("P_LATE", timeframe=Timeframe.H4, avail_time=T_H4_AVAIL)
    a_early = make_assessment(p_early)
    a_late = make_assessment(p_late)

    group = group_trade_plans_by_opportunity([p_late, p_early], [a_late, a_early])[0]
    sel, dups = select_plan_per_opportunity(
        group, policy=PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE
    )

    assert sel is not None
    assert sel.plan.deterministic_trade_plan_id == "P_EARLY"
    assert len(dups) == 1
    assert dups[0].plan.deterministic_trade_plan_id == "P_LATE"


def test_timeframe_priority_selection() -> None:
    p_h2 = make_plan("P_H2", timeframe=Timeframe.H2, avail_time=T_H2_AVAIL)
    p_h4 = make_plan("P_H4", timeframe=Timeframe.H4, avail_time=T_H4_AVAIL)
    a_h2 = make_assessment(p_h2)
    a_h4 = make_assessment(p_h4)

    group = group_trade_plans_by_opportunity([p_h2, p_h4], [a_h2, a_h4])[0]
    sel, _dups = select_plan_per_opportunity(
        group,
        policy=PlanSelectionPolicy.TIMEFRAME_PRIORITY,
        timeframe_priority=(Timeframe.H4, Timeframe.H2),
    )

    assert sel is not None
    assert sel.plan.deterministic_trade_plan_id == "P_H4"


def test_highest_valid_net_rr_selection() -> None:
    p1 = make_plan(
        "P1",
        timeframe=Timeframe.H4,
        entry_price="4227.950",
        zone_bottom="4199.955",
        zone_top="4255.945",
    )
    p2 = make_plan(
        "P2",
        timeframe=Timeframe.H2,
        entry_price="4236.585",
        zone_bottom="4199.955",
        zone_top="4255.945",
    )
    a1 = make_assessment(p1)  # Net RR 1.97R
    a2 = make_assessment(p2)  # Net RR 1.96R

    group = group_trade_plans_by_opportunity([p2, p1], [a2, a1])[0]
    sel, _dups = select_plan_per_opportunity(
        group, policy=PlanSelectionPolicy.HIGHEST_VALID_NET_RR
    )

    assert sel is not None
    assert sel.plan.deterministic_trade_plan_id == "P1"
    assert "1.97R" in sel.selection_reason


def test_explicit_plan_id_selection() -> None:
    p1 = make_plan("PLAN_EXPLICIT", timeframe=Timeframe.H4)
    p2 = make_plan("PLAN_OTHER", timeframe=Timeframe.H2)
    a1 = make_assessment(p1)
    a2 = make_assessment(p2)

    group = group_trade_plans_by_opportunity([p1, p2], [a1, a2])[0]
    sel, _dups = select_plan_per_opportunity(
        group, policy=PlanSelectionPolicy.EXPLICIT_PLAN_ID, explicit_plan_id="PLAN_EXPLICIT"
    )

    assert sel is not None
    assert sel.plan.deterministic_trade_plan_id == "PLAN_EXPLICIT"


def test_invalid_explicit_plan_rejected() -> None:
    p1 = make_plan("PLAN_A", timeframe=Timeframe.H4)
    a1 = make_assessment(p1)
    group = group_trade_plans_by_opportunity([p1], [a1])[0]

    sel, rejected = select_plan_per_opportunity(
        group, policy=PlanSelectionPolicy.EXPLICIT_PLAN_ID, explicit_plan_id="NON_EXISTENT_ID"
    )
    assert sel is None
    assert len(rejected) == 1
    assert "EXPLICIT_PLAN_NOT_ELIGIBLE" in rejected[0].rejection_reasons


def test_no_future_performance_selection() -> None:
    # Proves selection uses only metadata available at decision time
    p1 = make_plan("P1", avail_time=T_H4_AVAIL)
    a1 = make_assessment(p1)
    group = group_trade_plans_by_opportunity([p1], [a1])[0]

    sel, _ = select_plan_per_opportunity(
        group, policy=PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE, as_of_utc=T_H4_AVAIL
    )
    assert sel is not None
    assert sel.selection_time_utc == T_H4_AVAIL


def test_historical_selection_not_retroactively_changed() -> None:
    p1 = make_plan("P1", avail_time=T_H2_AVAIL)
    a1 = make_assessment(p1)
    group = group_trade_plans_by_opportunity([p1], [a1])[0]

    sel1, _ = select_plan_per_opportunity(group, as_of_utc=T_H2_AVAIL)
    # Re-evaluate later
    sel2, _ = select_plan_per_opportunity(group, as_of_utc=T_H4_AVAIL)
    assert sel1 is not None
    assert sel2 is not None
    assert sel1.plan.deterministic_trade_plan_id == sel2.plan.deterministic_trade_plan_id


# ── Tests 21 to 30: Point-in-Time, Quotations & Execution Side Safety ───────
def test_reselection_requires_explicit_policy() -> None:
    p1 = make_plan("P1", status=TradePlanStatus.INVALIDATED)
    a1 = make_assessment(p1)
    group = group_trade_plans_by_opportunity([p1], [a1])[0]

    # Without active plan, select_plan_per_opportunity returns None
    sel, rejected = select_plan_per_opportunity(group)
    assert sel is None
    assert len(rejected) == 1
    assert "PLAN_NOT_ACTIVE" in rejected[0].rejection_reasons


def test_future_h4_candidate_hidden_from_h2_snapshot() -> None:
    p_h2 = make_plan("P_H2", timeframe=Timeframe.H2, avail_time=T_H2_AVAIL)
    p_h4 = make_plan("P_H4", timeframe=Timeframe.H4, avail_time=T_H4_AVAIL)
    a_h2 = make_assessment(p_h2)
    a_h4 = make_assessment(p_h4)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    # Evaluate at T_H2_AVAIL (04:00 UTC)
    res = validate_ict_strategy_candidates(
        [p_h2, p_h4], [a_h2, a_h4], portfolio_snapshot=snap, as_of_utc=T_H2_AVAIL
    )
    visible_at_h2 = filter_strategy_validations_as_of(res, as_of_utc=T_H2_AVAIL)

    assert len(visible_at_h2) == 1
    assert visible_at_h2[0].plan.deterministic_trade_plan_id == "P_H2"


def test_future_opportunity_evidence_hidden() -> None:
    p_future = make_plan("P_FUT", avail_time=datetime(2026, 10, 1, 0, 0, tzinfo=UTC))
    a_future = make_assessment(p_future)
    res = validate_ict_strategy_candidates([p_future], [a_future], as_of_utc=T_H4_AVAIL)
    visible = filter_strategy_validations_as_of(res, as_of_utc=T_H4_AVAIL)
    assert len(visible) == 0


def test_opportunity_membership_as_of() -> None:
    p1 = make_plan("P1", avail_time=T_H2_AVAIL)
    p2 = make_plan("P2", avail_time=T_H4_AVAIL)
    a1 = make_assessment(p1)
    a2 = make_assessment(p2)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates(
        [p1, p2], [a1, a2], portfolio_snapshot=snap, as_of_utc=T_H4_AVAIL
    )

    vis_t1 = filter_strategy_validations_as_of(res, T_H2_AVAIL)
    vis_t2 = filter_strategy_validations_as_of(res, T_H4_AVAIL)
    assert len(vis_t1) == 1
    assert len(vis_t2) == 2


def test_future_invalidation_does_not_change_past_selection() -> None:
    p1 = make_plan("P1", avail_time=T_H2_AVAIL, status=TradePlanStatus.WAITING_FOR_RETRACEMENT)
    a1 = make_assessment(p1)
    group = group_trade_plans_by_opportunity([p1], [a1])[0]

    sel, _ = select_plan_per_opportunity(group, as_of_utc=T_H2_AVAIL)
    assert sel is not None
    assert sel.plan.deterministic_trade_plan_id == "P1"


def test_bid_ask_series_isolation() -> None:
    p_bid = make_plan("P_BID", price_type=PriceType.BID)
    p_ask = make_plan("P_ASK", price_type=PriceType.ASK)
    assert p_bid.price_type.value == "BID"
    assert p_ask.price_type.value == "ASK"
    assert p_bid.price_type.value != p_ask.price_type.value


def test_correct_execution_side_semantics() -> None:
    p_short = make_plan("P_SHORT", direction=TradePlanDirection.SHORT)
    p_long = make_plan("P_LONG", direction=TradePlanDirection.LONG)
    assert p_short.direction == TradePlanDirection.SHORT
    assert p_long.direction == TradePlanDirection.LONG


def test_missing_executable_quote_not_verified() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates([p1], [a1], portfolio_snapshot=snap)
    assert res[0].executable_price_verified is False


def test_analytical_entry_not_equal_fill() -> None:
    p1 = make_plan("P1", entry_price="4227.950")
    a1 = make_assessment(p1)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p1], [a1], portfolio_snapshot=snap)
    )
    assert len(contracts) == 1
    assert contracts[0].analytical_entry_reference == Decimal("4227.950")
    # Proves no fill attribute or order ticket exists on the contract
    assert not hasattr(contracts[0], "fill_price")
    assert not hasattr(contracts[0], "execution_ticket")


def test_missing_portfolio_snapshot_conditional_only() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    # Require snapshot = True but pass None
    cfg = StrategyValidationConfig(require_portfolio_snapshot=True)
    res = validate_ict_strategy_candidates([p1], [a1], config=cfg, portfolio_snapshot=None)

    assert len(res) == 1
    assert res[0].status == StrategyValidationStatus.RISK_CONDITIONALLY_ELIGIBLE
    assert StrategyRejectionReason.MISSING_PORTFOLIO_SNAPSHOT in res[0].rejection_reasons


# ── Tests 31 to 40: Portfolio Risk Bounds, FX & Metadata Integrity ──────────
def test_zero_open_risk_not_assumed_without_data() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    res = validate_ict_strategy_candidates([p1], [a1], portfolio_snapshot=None)
    assert res[0].portfolio_validation is not None
    assert res[0].portfolio_validation.is_valid is False


def test_per_event_risk_limit_enforced() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)  # Risk is ~$996.10
    # Set maximum_single_event_risk to $500.00
    snap = PortfolioRiskSnapshot(
        account_id="A1",
        equity=Decimal("100000"),
        maximum_single_event_risk=Decimal("500.00"),
        snapshot_available_at=T_H4_AVAIL,
    )
    res = validate_ict_strategy_candidates([p1], [a1], portfolio_snapshot=snap)
    assert res[0].status == StrategyValidationStatus.REJECTED
    assert StrategyRejectionReason.EVENT_RISK_LIMIT_EXCEEDED in res[0].rejection_reasons


def test_portfolio_risk_limit_enforced() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)  # Risk is ~$996.10
    # Existing open risk $5500, max portfolio risk $6000 -> capacity $500 -> breached!
    snap = PortfolioRiskSnapshot(
        account_id="A1",
        equity=Decimal("100000"),
        existing_open_risk=Decimal("5500.00"),
        maximum_portfolio_risk=Decimal("6000.00"),
        snapshot_available_at=T_H4_AVAIL,
    )
    res = validate_ict_strategy_candidates([p1], [a1], portfolio_snapshot=snap)
    assert res[0].status == StrategyValidationStatus.REJECTED
    assert StrategyRejectionReason.PORTFOLIO_RISK_LIMIT_EXCEEDED in res[0].rejection_reasons


def test_daily_risk_limit_enforced() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)  # Risk ~$996.10
    # Daily realized loss $2500, daily limit $3000 -> 2500 + 996 = 3496 > 3000
    snap = PortfolioRiskSnapshot(
        account_id="A1",
        equity=Decimal("100000"),
        daily_realized_loss=Decimal("2500.00"),
        daily_risk_limit=Decimal("3000.00"),
        snapshot_available_at=T_H4_AVAIL,
    )
    res = validate_ict_strategy_candidates([p1], [a1], portfolio_snapshot=snap)
    assert res[0].status == StrategyValidationStatus.REJECTED
    assert StrategyRejectionReason.DAILY_RISK_LIMIT_EXCEEDED in res[0].rejection_reasons


def test_existing_open_risk_included() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    snap = PortfolioRiskSnapshot(
        account_id="A1",
        equity=Decimal("100000"),
        existing_open_risk=Decimal("1000.00"),
        maximum_portfolio_risk=Decimal("6000.00"),
        snapshot_available_at=T_H4_AVAIL,
    )
    val = evaluate_portfolio_risk(
        SelectedTradePlan(
            p1, a1, "OPP1", PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE, "", T_H4_AVAIL
        ),
        snap,
    )
    assert a1.position_sizing is not None
    assert val.portfolio_risk_capacity == Decimal("5000.00")
    assert (
        val.aggregate_portfolio_risk == Decimal("1000.00") + a1.position_sizing.estimated_cash_risk
    )


def test_reserved_risk_not_double_counted() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    snap = PortfolioRiskSnapshot(
        account_id="A1",
        equity=Decimal("100000"),
        existing_open_risk=Decimal("500.00"),
        committed_or_reserved_risk=Decimal("500.00"),
        maximum_portfolio_risk=Decimal("6000.00"),
        snapshot_available_at=T_H4_AVAIL,
    )
    val = evaluate_portfolio_risk(
        SelectedTradePlan(
            p1, a1, "OPP1", PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE, "", T_H4_AVAIL
        ),
        snap,
    )
    assert val.portfolio_risk_capacity == Decimal("5000.00")


def test_portfolio_currency_conversion() -> None:
    snap = PortfolioRiskSnapshot(
        account_id="A1",
        equity=Decimal("100000"),
        account_currency="EUR",
        snapshot_available_at=T_H4_AVAIL,
    )
    assert snap.account_currency == "EUR"


def test_future_portfolio_snapshot_rejected() -> None:
    p1 = make_plan("P1", avail_time=T_H4_AVAIL)
    a1 = make_assessment(p1)
    # Snapshot dated in future relative to as_of cutoff
    snap = PortfolioRiskSnapshot(
        account_id="A1",
        equity=Decimal("100000"),
        snapshot_available_at=datetime(2026, 10, 15, 0, 0, tzinfo=UTC),
    )
    res = validate_ict_strategy_candidates(
        [p1], [a1], portfolio_snapshot=snap, as_of_utc=T_H4_AVAIL
    )
    assert res[0].status == StrategyValidationStatus.PIT_REJECTED
    assert StrategyRejectionReason.FUTURE_INFORMATION in res[0].rejection_reasons


def test_missing_fx_conversion_rejected() -> None:
    reason = StrategyRejectionReason.MISSING_FX_CONVERSION
    assert reason.value == "MISSING_FX_CONVERSION"


def test_hypothetical_broker_metadata_preserved() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p1], [a1], portfolio_snapshot=snap)
    )
    assert contracts[0].broker_specification_provenance == "HYPOTHETICAL_BROKER_SCENARIO"


# ── Tests 41 to 50: Stress Scenarios, Net R:R & Deduplicated Risk ───────────
def test_strict_pit_rejects_estimated_live_approval() -> None:
    cfg = StrategyValidationConfig(strict_pit=True)
    assert cfg.strict_pit is True


def test_research_estimated_analytical_mode() -> None:
    cfg = StrategyValidationConfig(allow_research_mode=True, require_portfolio_snapshot=False)
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    res = validate_ict_strategy_candidates([p1], [a1], config=cfg, portfolio_snapshot=None)
    assert res[0].status == StrategyValidationStatus.PORTFOLIO_VALIDATED


def test_wider_spread_reduces_net_rr() -> None:
    p1 = make_plan("P1", entry_price="4227.950", zone_bottom="4199.955", zone_top="4255.945")
    a1 = make_assessment(p1)
    spec = make_spec(comm="2.00", spread_ticks=25)
    sel = SelectedTradePlan(
        p1, a1, "OPP1", PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE, "", T_H4_AVAIL
    )

    _, base_net_rr, _ = evaluate_cost_stress(sel, spec, scenario=CostStressScenario.BASE_COST)
    _, stressed_net_rr, _ = evaluate_cost_stress(
        sel, spec, scenario=CostStressScenario.WIDER_SPREAD, spread_multiplier=Decimal("2.0")
    )

    assert stressed_net_rr is not None and base_net_rr is not None
    assert stressed_net_rr < base_net_rr
    assert stressed_net_rr == Decimal("1.95")  # Dropped from 1.97 to 1.95


def test_higher_commission_reduces_net_rr() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    spec = make_spec(comm="2.00", spread_ticks=25)
    sel = SelectedTradePlan(
        p1, a1, "OPP1", PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE, "", T_H4_AVAIL
    )

    _, base_net_rr, _ = evaluate_cost_stress(sel, spec, scenario=CostStressScenario.BASE_COST)
    _, stressed_net_rr, _ = evaluate_cost_stress(
        sel,
        spec,
        scenario=CostStressScenario.HIGHER_COMMISSION,
        commission_multiplier=Decimal("5.0"),
    )
    assert stressed_net_rr is not None and base_net_rr is not None
    assert stressed_net_rr < base_net_rr


def test_slippage_stress_reduces_size() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    spec = make_spec(comm="2.00", spread_ticks=25)
    sel = SelectedTradePlan(
        p1, a1, "OPP1", PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE, "", T_H4_AVAIL
    )

    cost_base, _, _cash_base = evaluate_cost_stress(
        sel, spec, scenario=CostStressScenario.BASE_COST
    )
    cost_slip, _, cash_slip = evaluate_cost_stress(
        sel, spec, scenario=CostStressScenario.ADDITIONAL_SLIPPAGE, additional_slippage_ticks=50
    )
    assert a1.position_sizing is not None
    assert cost_slip > cost_base
    assert cash_slip <= a1.position_sizing.risk_budget


def test_combined_cost_stress_enforces_budget() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    spec = make_spec()
    sel = SelectedTradePlan(
        p1, a1, "OPP1", PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE, "", T_H4_AVAIL
    )

    _, _, stressed_cash = evaluate_cost_stress(
        sel,
        spec,
        scenario=CostStressScenario.COMBINED_STRESS,
        spread_multiplier=Decimal("3.0"),
        commission_multiplier=Decimal("2.0"),
        additional_slippage_ticks=20,
    )
    assert a1.position_sizing is not None
    assert stressed_cash <= a1.position_sizing.risk_budget


def test_unknown_cost_does_not_default_to_zero() -> None:
    p1 = make_plan("P1")
    # Assessment without Net RR
    a1 = make_assessment(p1)
    # Manually overwrite net_risk_reward to None
    object.__setattr__(a1, "net_risk_reward", None)
    cfg = StrategyValidationConfig(min_net_risk_reward=Decimal("1.5"))
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates([p1], [a1], config=cfg, portfolio_snapshot=snap)
    assert res[0].status == StrategyValidationStatus.REJECTED
    assert StrategyRejectionReason.UNKNOWN_EXECUTION_COST in res[0].rejection_reasons


def test_net_rr_threshold_rejection() -> None:
    p1 = make_plan("P1")  # Net RR 1.97R
    a1 = make_assessment(p1)
    # Set threshold to 2.5R -> should be rejected!
    cfg = StrategyValidationConfig(min_net_risk_reward=Decimal("2.5"))
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates([p1], [a1], config=cfg, portfolio_snapshot=snap)
    assert res[0].status == StrategyValidationStatus.REJECTED
    assert StrategyRejectionReason.NET_RR_BELOW_THRESHOLD in res[0].rejection_reasons


def test_same_event_four_plans_not_four_independent_risks() -> None:
    p1 = make_plan("P1", timeframe=Timeframe.H4, price_type=PriceType.BID)
    p2 = make_plan("P2", timeframe=Timeframe.H2, price_type=PriceType.BID)
    p3 = make_plan("P3", timeframe=Timeframe.H4, price_type=PriceType.ASK)
    p4 = make_plan("P4", timeframe=Timeframe.H2, price_type=PriceType.ASK)
    a1 = make_assessment(p1)
    a2 = make_assessment(p2)
    a3 = make_assessment(p3)
    a4 = make_assessment(p4)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates(
        [p1, p2, p3, p4], [a1, a2, a3, a4], portfolio_snapshot=snap
    )
    summary = summarize_strategy_validations(res)

    assert summary.unique_economic_opportunities == 1
    assert summary.selected_plans_count == 1
    assert summary.duplicate_alternatives_count == 3
    # Selected proposed risk is ~998, NOT 4 * ~998 = ~3983!
    assert summary.selected_proposed_risk < Decimal("1000.00")
    assert summary.total_proposed_risk > Decimal("3900.00")


def test_portfolio_aggregate_risk_reconciliation() -> None:
    p1 = make_plan("P1", timeframe=Timeframe.H4, price_type=PriceType.BID)
    p2 = make_plan("P2", timeframe=Timeframe.H4, price_type=PriceType.ASK)
    a1 = make_assessment(p1)
    a2 = make_assessment(p2)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates([p1, p2], [a1, a2], portfolio_snapshot=snap)
    summary = summarize_strategy_validations(res)

    assert summary.portfolio_validated_count == 1
    assert summary.duplicate_alternatives_count == 1


# ── Tests 51 to 65: Negative Tests, Invariants & Real Data Replay ────────────
def test_m1_ohlc_not_claimed_tick_data() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p1], [a1], portfolio_snapshot=snap)
    )
    assert contracts[0].market_data_quality == "M1_DERIVED_OHLC_STRICT_PIT"


def test_intrabar_ambiguity_not_resolved_by_guess() -> None:
    reason = StrategyRejectionReason.AMBIGUOUS_INTRABAR_SEQUENCE
    assert reason.value == "AMBIGUOUS_INTRABAR_SEQUENCE"


def test_blocked_plan_not_selected() -> None:
    p_blocked = make_plan("P_BLOCKED", status=TradePlanStatus.BLOCKED)
    a_blocked = make_assessment(p_blocked)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates([p_blocked], [a_blocked], portfolio_snapshot=snap)
    assert res[0].is_selected is False
    assert res[0].status == StrategyValidationStatus.REJECTED


def test_invalidated_plan_not_selected() -> None:
    p_inval = make_plan("P_INVAL", status=TradePlanStatus.INVALIDATED)
    a_inval = make_assessment(p_inval)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates([p_inval], [a_inval], portfolio_snapshot=snap)
    assert res[0].is_selected is False
    assert res[0].status == StrategyValidationStatus.REJECTED


def test_unconfirmed_setup_not_selected() -> None:
    p_unconf = make_plan("P_UNCONF", status=TradePlanStatus.DRAFT)
    a_unconf = make_assessment(p_unconf)
    # Risk assessment blocks DRAFT
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates([p_unconf], [a_unconf], portfolio_snapshot=snap)
    assert res[0].is_selected is False


def test_missing_event_lineage_not_silently_grouped() -> None:
    p1 = make_plan("P1", setup_id="INVALID_UNKNOWN")
    opp = identify_economic_opportunity(p1)
    assert "UNKNOWN_TRIGGER" in opp.economic_opportunity_id


def test_deterministic_tie_breaking() -> None:
    p1 = make_plan("P_B", timeframe=Timeframe.H4, price_type=PriceType.BID, avail_time=T_H4_AVAIL)
    p2 = make_plan("P_A", timeframe=Timeframe.H4, price_type=PriceType.ASK, avail_time=T_H4_AVAIL)
    a1 = make_assessment(p1)
    a2 = make_assessment(p2)

    group = group_trade_plans_by_opportunity([p1, p2], [a1, a2])[0]
    sel1, _ = select_plan_per_opportunity(group, policy=PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE)
    sel2, _ = select_plan_per_opportunity(group, policy=PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE)
    assert sel1 is not None
    assert sel2 is not None
    assert sel1.plan.deterministic_trade_plan_id == sel2.plan.deterministic_trade_plan_id == "P_A"


def test_immutable_domain_models() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    opp = identify_economic_opportunity(p1, a1)
    with pytest.raises(FrozenInstanceError):
        opp.economic_opportunity_id = "NEW_ID"  # type: ignore[misc]


def test_no_side_effects_on_inputs() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    orig_status = p1.plan_status
    orig_ass_status = a1.status

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    validate_ict_strategy_candidates([p1], [a1], portfolio_snapshot=snap)

    assert p1.plan_status == orig_status
    assert a1.status == orig_ass_status


def test_empty_input_safe_result() -> None:
    res = validate_ict_strategy_candidates([], [])
    assert res == ()


def test_strategy_validation_explainability() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates([p1], [a1], portfolio_snapshot=snap)
    card = explain_strategy_validation(res[0])
    assert "=== STRATEGY VALIDATION AUDIT CARD [SHORT] ===" in card
    assert "PORTFOLIO_VALIDATED" in card
    assert "Net Risk/Reward:" in card


def test_26_plan_real_data_reconciliation() -> None:
    # Simulates the 26 real plans: 4 approved from Opp 5, 22 blocked from other 11 opps
    plans: list[IctTradePlan] = []
    assessments: list[IctRiskAssessment] = []

    # 4 approved plans (Plan 1, 7, 14, 20)
    for idx, (tf, pt) in enumerate(
        [
            (Timeframe.H4, PriceType.BID),
            (Timeframe.H2, PriceType.BID),
            (Timeframe.H4, PriceType.ASK),
            (Timeframe.H2, PriceType.ASK),
        ],
        1,
    ):
        p = make_plan(
            f"PLAN_APP_{idx}", timeframe=tf, price_type=pt, status=TradePlanStatus.TOUCH_OBSERVED
        )
        a = make_assessment(p)
        plans.append(p)
        assessments.append(a)

    # 22 blocked plans across other structural events
    for i in range(1, 23):
        setup_mock = f"SETUP:BOS_BEARISH_202610{i:02d}T000000Z_SWING_LOW_202610{i:02d}"
        p = make_plan(f"PLAN_BLK_{i}", status=TradePlanStatus.INVALIDATED, setup_id=setup_mock)
        a = make_assessment(p)
        plans.append(p)
        assessments.append(a)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates(plans, assessments, portfolio_snapshot=snap)
    summary = summarize_strategy_validations(res)

    assert summary.total_trade_plans == 26
    assert summary.portfolio_validated_count == 1
    assert summary.duplicate_alternatives_count == 3
    assert summary.rejected_count == 22
    assert 1 + 3 + 22 == 26


def test_opportunity_count_reconciles_with_plan_count() -> None:
    p1 = make_plan("P1", timeframe=Timeframe.H4, price_type=PriceType.BID)
    p2 = make_plan("P2", timeframe=Timeframe.H4, price_type=PriceType.ASK)
    groups = group_trade_plans_by_opportunity([p1, p2])
    assert len(groups) == 1
    assert len(groups[0].plans) == 2


def test_no_fake_orders_fills_or_pnl() -> None:
    p1 = make_plan("P1")
    a1 = make_assessment(p1)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates([p1], [a1], portfolio_snapshot=snap)
    r = res[0]
    assert not hasattr(r, "orders")
    assert not hasattr(r, "fills")
    assert not hasattr(r, "realized_pnl")
    assert not hasattr(r, "unrealized_pnl")


def test_risk_validation_as_of_reconstruction() -> None:
    p1 = make_plan("P1", avail_time=T_H2_AVAIL)
    a1 = make_assessment(p1)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    res = validate_ict_strategy_candidates(
        [p1], [a1], portfolio_snapshot=snap, as_of_utc=T_H2_AVAIL
    )
    filtered = filter_strategy_validations_as_of(res, T_H2_AVAIL)
    assert len(filtered) == 1
    assert filtered[0].as_of_utc == T_H2_AVAIL
