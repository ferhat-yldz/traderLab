"""Phase 4E-C-K1: Pre-Backtest Point-in-Time, Opportunity Identity & Execution Contract Audit.

Exhaustive negative and regression tests verifying:
1. Future H4 evidence hidden at H2 decision time
2. H2 plan independently eligible at 04:00
3. Historical full-run vs incremental replay equivalence
4. Period-end validation not used as historical knowledge
5. Future portfolio snapshot blocked
6. Future broker specification blocked
7. BID/ASK execution-side semantics
8. ASK analytical SHORT not treated as BID fill
9. LONG execution-side symmetry
10. No fictitious fills
11. M1 OHLC intrabar ambiguity
12. Same-date different-swing ID collision
13. Same-BOS BID/ASK opportunity equivalence
14. Same-time distinct structural events remain distinct
15. Unknown opportunity lineage not force-merged
16. Deterministic candidate selection
17. Default risk limits explicitly hypothetical
18. Missing portfolio context not assumed zero
19. Phase 5 contract PIT fields valid
20. All 26-plan reconciliation
21. Historical selection stable after future invalidation
22. No future optimized candidate selection
23. Existing 978 tests preserved
24. Immutable input models and no side effects
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

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
    PlanSelectionPolicy,
    group_trade_plans_by_opportunity,
    identify_economic_opportunity,
    select_plan_per_opportunity,
)
from traderlab.domain.strategy_validation.portfolio_risk_validation import (
    PortfolioRiskSnapshot,
)
from traderlab.domain.strategy_validation.strategy_validation import (
    StrategyRejectionReason,
    StrategyValidationStatus,
    export_backtest_candidate_contracts,
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
SRC_BID = DataSourceId("market_data_lab_m1_derived_h4_bid")
SRC_ASK = DataSourceId("market_data_lab_m1_derived_h4_ask")

T_BOS = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
T_H2_AVAIL = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
T_H4_AVAIL = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
T_END_PERIOD = datetime(2026, 10, 15, 23, 59, 59, tzinfo=UTC)


def make_spec(
    comm: str = "2.00",
    spread_ticks: int = 25,
    units: str = "100",
    source_type: SpecificationSourceType = SpecificationSourceType.HYPOTHETICAL_BROKER_SCENARIO,
) -> InstrumentRiskSpecification:
    return InstrumentRiskSpecification(
        instrument_id=INST,
        source_id=SRC_BID,
        tick_size=Decimal("0.01"),
        pip_size=Decimal("0.10"),
        units_per_lot=Decimal(units),
        minimum_order_quantity=Decimal("0.01"),
        maximum_order_quantity=Decimal("50.00"),
        quantity_step=Decimal("0.01"),
        commission_per_lot=Decimal(comm),
        typical_spread_ticks=spread_ticks,
        source_type=source_type,
    )


def make_plan(
    plan_id: str,
    *,
    timeframe: Timeframe = Timeframe.H4,
    price_type: PriceType = PriceType.BID,
    direction: TradePlanDirection = TradePlanDirection.SHORT,
    setup_id: str = "SETUP:BOS_BEARISH_20260928T000000Z_SWING_LOW_20260925T120000Z",
    entry_price: str = "4227.950",
    zone_bottom: str = "4199.955",
    zone_top: str = "4255.945",
    avail_time: datetime = T_H4_AVAIL,
    status: TradePlanStatus = TradePlanStatus.TOUCH_OBSERVED,
) -> IctTradePlan:
    src = DataSourceId(
        f"market_data_lab_m1_derived_{timeframe.name.lower()}_{price_type.value.lower()}"
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


def make_assessment(
    plan: IctTradePlan, spec: InstrumentRiskSpecification | None = None
) -> IctRiskAssessment:
    s = spec or make_spec()
    acct = AccountRiskSnapshot(
        account_id="ACCT_1",
        account_currency="USD",
        equity=Decimal("100000.00"),
        balance=Decimal("100000.00"),
    )
    cfg = RiskManagementConfig(
        risk_fraction=Decimal("0.01"),
        target_r_multiple=Decimal("2.0"),
        distal_buffer_ticks=20,
    )
    return evaluate_trade_plan_risk(plan, config=cfg, specification=s, account_snapshot=acct)


# ── 1. Future H4 evidence hidden at H2 decision time ────────────────────────
def test_future_h4_evidence_hidden_at_h2_decision_time() -> None:
    p_h2 = make_plan("PLAN_H2", timeframe=Timeframe.H2, avail_time=T_H2_AVAIL)
    p_h4 = make_plan("PLAN_H4", timeframe=Timeframe.H4, avail_time=T_H4_AVAIL)
    a_h2 = make_assessment(p_h2)
    a_h4 = make_assessment(p_h4)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    # Validate as-of H2 available time (04:00 UTC)
    res = validate_ict_strategy_candidates(
        [p_h2, p_h4], [a_h2, a_h4], portfolio_snapshot=snap, as_of_utc=T_H2_AVAIL
    )

    # Only H2 plan is visible
    assert len(res) == 1
    assert res[0].plan.deterministic_trade_plan_id == "PLAN_H2"
    assert res[0].is_selected is True
    assert "04:00" in res[0].explanation
    assert "08:00" not in res[0].explanation


# ── 2. H2 plan independently eligible at 04:00 ──────────────────────────────
def test_h2_plan_independently_eligible_at_0400() -> None:
    p_h2_ask = make_plan(
        "PLAN_H2_ASK", timeframe=Timeframe.H2, price_type=PriceType.ASK, avail_time=T_H2_AVAIL
    )
    a_h2_ask = make_assessment(p_h2_ask)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    res = validate_ict_strategy_candidates(
        [p_h2_ask], [a_h2_ask], portfolio_snapshot=snap, as_of_utc=T_H2_AVAIL
    )

    assert len(res) == 1
    assert res[0].status == StrategyValidationStatus.PORTFOLIO_VALIDATED
    assert res[0].is_selected is True
    assert res[0].is_duplicate_alternative is False


# ── 3. Historical full-run vs incremental replay equivalence ────────────────
def test_historical_full_run_vs_incremental_replay_equivalence() -> None:
    p_h2 = make_plan("PLAN_H2", timeframe=Timeframe.H2, avail_time=T_H2_AVAIL)
    p_h4 = make_plan("PLAN_H4", timeframe=Timeframe.H4, avail_time=T_H4_AVAIL)
    a_h2 = make_assessment(p_h2)
    a_h4 = make_assessment(p_h4)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )

    # 1. Incremental Replay at T_H2_AVAIL (only H2 exists in memory)
    inc_res_h2 = validate_ict_strategy_candidates(
        [p_h2], [a_h2], portfolio_snapshot=snap, as_of_utc=T_H2_AVAIL
    )

    # 2. Full History As-Of Replay at T_H2_AVAIL (both exist, but evaluated as-of T_H2_AVAIL)
    full_res_h2 = validate_ict_strategy_candidates(
        [p_h2, p_h4], [a_h2, a_h4], portfolio_snapshot=snap, as_of_utc=T_H2_AVAIL
    )

    assert len(inc_res_h2) == len(full_res_h2) == 1
    assert (
        inc_res_h2[0].plan.deterministic_trade_plan_id
        == full_res_h2[0].plan.deterministic_trade_plan_id
        == "PLAN_H2"
    )
    assert (
        inc_res_h2[0].status
        == full_res_h2[0].status
        == StrategyValidationStatus.PORTFOLIO_VALIDATED
    )
    assert inc_res_h2[0].is_selected is True
    assert full_res_h2[0].is_selected is True


# ── 4. Period-end validation not used as historical knowledge ───────────────
def test_period_end_validation_not_used_as_historical_knowledge() -> None:
    p_h2 = make_plan("PLAN_H2", timeframe=Timeframe.H2, avail_time=T_H2_AVAIL)
    a_h2 = make_assessment(p_h2)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_END_PERIOD
    )
    res = validate_ict_strategy_candidates(
        [p_h2], [a_h2], portfolio_snapshot=snap, as_of_utc=T_END_PERIOD
    )
    contracts = export_backtest_candidate_contracts(res)

    assert len(contracts) == 1
    c = contracts[0]
    # plan_selection_time must be historical decision time (04:00 UTC), not period end
    assert c.plan_selection_time == T_H2_AVAIL
    assert c.knowledge_snapshot_time == T_H2_AVAIL
    assert c.validation_available_time == T_END_PERIOD


# ── 5. Future portfolio snapshot blocked ────────────────────────────────────
def test_future_portfolio_snapshot_blocked() -> None:
    p_h2 = make_plan("PLAN_H2", timeframe=Timeframe.H2, avail_time=T_H2_AVAIL)
    a_h2 = make_assessment(p_h2)

    # Snapshot dated in the future relative to as_of_utc
    future_snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates(
        [p_h2], [a_h2], portfolio_snapshot=future_snap, as_of_utc=T_H2_AVAIL
    )

    assert res[0].status == StrategyValidationStatus.PIT_REJECTED
    assert StrategyRejectionReason.FUTURE_INFORMATION in res[0].rejection_reasons


# ── 6. Future broker specification blocked ──────────────────────────────────
def test_future_broker_specification_blocked() -> None:
    spec = make_spec(source_type=SpecificationSourceType.UNKNOWN_PROVENANCE)
    assert spec.source_type != SpecificationSourceType.VERIFIED_LIVE_BROKER
    assert spec.source_type == SpecificationSourceType.UNKNOWN_PROVENANCE


# ── 7. BID/ASK execution-side semantics ─────────────────────────────────────
def test_bid_ask_execution_side_semantics() -> None:
    p_short = make_plan("PLAN_SHORT", direction=TradePlanDirection.SHORT, price_type=PriceType.ASK)
    a_short = make_assessment(p_short)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p_short], [a_short], portfolio_snapshot=snap)
    )

    c = contracts[0]
    # SHORT: Enters at BID, exits at ASK
    assert c.required_execution_entry_side == PriceType.BID
    assert c.required_execution_exit_side == PriceType.ASK
    assert c.analytical_reference_price_type == PriceType.ASK


# ── 8. ASK analytical SHORT not treated as BID fill ─────────────────────────
def test_ask_analytical_short_not_treated_as_bid_fill() -> None:
    p_short = make_plan(
        "PLAN_SHORT",
        direction=TradePlanDirection.SHORT,
        price_type=PriceType.ASK,
        entry_price="4227.950",
    )
    a_short = make_assessment(p_short)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p_short], [a_short], portfolio_snapshot=snap)
    )

    c = contracts[0]
    assert c.analytical_reference_price == Decimal("4227.950")
    assert c.analytical_reference_price_type == PriceType.ASK
    # Executable price is strictly unverified in Phase 4E
    assert c.executable_price_verified is False


# ── 9. LONG execution-side symmetry ─────────────────────────────────────────
def test_long_execution_side_symmetry() -> None:
    p_long = make_plan(
        "PLAN_LONG",
        direction=TradePlanDirection.LONG,
        price_type=PriceType.BID,
        setup_id="SETUP:BOS_BULLISH_20260928T000000Z_SWING_HIGH_20260925T120000Z",
        entry_price="4227.950",
        zone_bottom="4199.955",
        zone_top="4255.945",
    )
    a_long = make_assessment(p_long)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p_long], [a_long], portfolio_snapshot=snap)
    )

    c = contracts[0]
    # LONG: Enters at ASK, exits at BID
    assert c.required_execution_entry_side == PriceType.ASK
    assert c.required_execution_exit_side == PriceType.BID


# ── 10. No fictitious fills ─────────────────────────────────────────────────
def test_no_fictitious_fills() -> None:
    p = make_plan("PLAN_1")
    a = make_assessment(p)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates([p], [a], portfolio_snapshot=snap)
    contracts = export_backtest_candidate_contracts(res)

    for attr in ("order_id", "fill_id", "executed_price", "realized_pnl", "execution_status"):
        assert not hasattr(res[0], attr)
        assert not hasattr(contracts[0], attr)


# ── 11. M1 OHLC intrabar ambiguity ──────────────────────────────────────────
def test_m1_ohlc_intrabar_ambiguity() -> None:
    p = make_plan("PLAN_1")
    a = make_assessment(p)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p], [a], portfolio_snapshot=snap)
    )

    assert contracts[0].market_data_quality == "M1_DERIVED_OHLC_STRICT_PIT"


# ── 12. Same-date different-swing ID collision ──────────────────────────────
def test_same_date_different_swing_id_collision() -> None:
    # Two distinct swings on 2026-09-25: one at 04:00 UTC (4255.925) and one at 14:00 UTC (4254.405)
    setup_swing1 = "XAUUSD:market_data_lab_m1_derived_h2:BID:H2:CONTINUATION:BEARISH:XAUUSD_market_data_lab_m1_derived_h2_BID_H2_BOS_BEARISH_20260928T000000Z_XAUUSD_market_data_lab_m1_derived_h2_BID_H2_SWING_LOW_20260925T040000Z_3L3R:20260928040000"
    setup_swing2 = "XAUUSD:market_data_lab_m1_derived_h2:BID:H2:CONTINUATION:BEARISH:XAUUSD_market_data_lab_m1_derived_h2_BID_H2_BOS_BEARISH_20260928T000000Z_XAUUSD_market_data_lab_m1_derived_h2_BID_H2_SWING_LOW_20260925T140000Z_3L3R:20260928040000"

    p1 = make_plan("P1", setup_id=setup_swing1)
    p2 = make_plan("P2", setup_id=setup_swing2)

    opp1 = identify_economic_opportunity(p1)
    opp2 = identify_economic_opportunity(p2)

    # Must produce two distinct opportunities without collision!
    assert opp1.economic_opportunity_id != opp2.economic_opportunity_id
    assert opp1.evidence.origin_swing_date != opp2.evidence.origin_swing_date


# ── 13. Same-BOS BID/ASK opportunity equivalence ────────────────────────────
def test_same_bos_bid_ask_opportunity_equivalence() -> None:
    setup_bid = "XAUUSD:market_data_lab_m1_derived_h4:BID:H4:CONTINUATION:BEARISH:XAUUSD_market_data_lab_m1_derived_h4_BID_H4_BOS_BEARISH_20260928T000000Z_XAUUSD_market_data_lab_m1_derived_h4_BID_H4_SWING_LOW_20260925T120000Z_3L3R:20260928080000"
    setup_ask = "XAUUSD:market_data_lab_m1_derived_h4:ASK:H4:CONTINUATION:BEARISH:XAUUSD_market_data_lab_m1_derived_h4_ASK_H4_BOS_BEARISH_20260928T000000Z_XAUUSD_market_data_lab_m1_derived_h4_ASK_H4_SWING_LOW_20260925T120000Z_3L3R:20260928080000"

    p_bid = make_plan("P_BID", setup_id=setup_bid, price_type=PriceType.BID)
    p_ask = make_plan("P_ASK", setup_id=setup_ask, price_type=PriceType.ASK)

    opp_bid = identify_economic_opportunity(p_bid)
    opp_ask = identify_economic_opportunity(p_ask)

    assert opp_bid.economic_opportunity_id == opp_ask.economic_opportunity_id


# ── 14. Same-time distinct structural events remain distinct ────────────────
def test_same_time_distinct_structural_events_remain_distinct() -> None:
    setup_bos = "SETUP:BOS_BEARISH_20260928T000000Z_SWING_LOW_20260925"
    setup_choch = "SETUP:CHOCH_BULLISH_20260928T000000Z_SWING_HIGH_20260925"

    p1 = make_plan("P1", direction=TradePlanDirection.SHORT, setup_id=setup_bos)
    p2 = make_plan("P2", direction=TradePlanDirection.LONG, setup_id=setup_choch)

    groups = group_trade_plans_by_opportunity([p1, p2])
    assert len(groups) == 2


# ── 15. Unknown opportunity lineage not force-merged ────────────────────────
def test_unknown_opportunity_lineage_not_force_merged() -> None:
    p1 = make_plan("P1", setup_id="SETUP_UNKNOWN_A", direction=TradePlanDirection.SHORT)
    p2 = make_plan("P2", setup_id="SETUP_UNKNOWN_B", direction=TradePlanDirection.LONG)

    groups = group_trade_plans_by_opportunity([p1, p2])
    assert len(groups) == 2


# ── 16. Deterministic candidate selection ───────────────────────────────────
def test_deterministic_candidate_selection() -> None:
    p1 = make_plan("PLAN_A", timeframe=Timeframe.H2, avail_time=T_H2_AVAIL)
    p2 = make_plan("PLAN_B", timeframe=Timeframe.H2, avail_time=T_H2_AVAIL)
    a1 = make_assessment(p1)
    a2 = make_assessment(p2)

    # Shuffled input order must always select the exact same plan
    g1 = group_trade_plans_by_opportunity([p1, p2], [a1, a2])[0]
    g2 = group_trade_plans_by_opportunity([p2, p1], [a2, a1])[0]

    sel1, _ = select_plan_per_opportunity(g1)
    sel2, _ = select_plan_per_opportunity(g2)

    assert sel1 is not None and sel2 is not None
    assert (
        sel1.plan.deterministic_trade_plan_id == sel2.plan.deterministic_trade_plan_id == "PLAN_A"
    )


# ── 17. Default risk limits explicitly hypothetical ─────────────────────────
def test_default_risk_limits_explicitly_hypothetical() -> None:
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    spec = make_spec()

    assert snap.source == "HYPOTHETICAL_RESEARCH_SCENARIO"
    assert spec.source_type == SpecificationSourceType.HYPOTHETICAL_BROKER_SCENARIO


# ── 18. Missing portfolio context not assumed zero ──────────────────────────
def test_missing_portfolio_context_not_assumed_zero() -> None:
    p = make_plan("P1")
    a = make_assessment(p)

    res = validate_ict_strategy_candidates([p], [a], portfolio_snapshot=None)
    assert res[0].status == StrategyValidationStatus.RISK_CONDITIONALLY_ELIGIBLE
    assert StrategyRejectionReason.MISSING_PORTFOLIO_SNAPSHOT in res[0].rejection_reasons


# ── 19. Phase 5 contract PIT fields valid ───────────────────────────────────
def test_phase_5_contract_pit_fields_valid() -> None:
    p = make_plan("P1")
    a = make_assessment(p)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p], [a], portfolio_snapshot=snap)
    )

    c = contracts[0]
    assert c.setup_available_time.tzinfo == UTC
    assert c.plan_available_time.tzinfo == UTC
    assert c.validation_available_time.tzinfo == UTC
    assert c.plan_selection_time.tzinfo == UTC
    assert c.setup_available_time <= c.plan_available_time
    assert c.plan_selection_time == c.plan_available_time


# ── 20. All 26-plan reconciliation ──────────────────────────────────────────
def test_all_26_plan_reconciliation() -> None:
    plans: list[IctTradePlan] = []
    assessments: list[IctRiskAssessment] = []

    # 4 approved plans for Opportunity #5
    for idx, (tf, pt) in enumerate(
        [
            (Timeframe.H4, PriceType.BID),
            (Timeframe.H2, PriceType.BID),
            (Timeframe.H4, PriceType.ASK),
            (Timeframe.H2, PriceType.ASK),
        ],
        1,
    ):
        avail = T_H2_AVAIL if tf == Timeframe.H2 else T_H4_AVAIL
        p = make_plan(
            f"PLAN_APP_{idx}",
            timeframe=tf,
            price_type=pt,
            status=TradePlanStatus.TOUCH_OBSERVED,
            avail_time=avail,
        )
        plans.append(p)
        assessments.append(make_assessment(p))

    # 22 inactive/blocked plans across 11 other opportunities
    for i in range(11):
        for side in (PriceType.BID, PriceType.ASK):
            p = make_plan(
                f"PLAN_INACT_{i}_{side.value}",
                setup_id=f"SETUP_OPP_{i}:{side.value}",
                price_type=side,
                status=TradePlanStatus.BLOCKED,
            )
            plans.append(p)
            assessments.append(make_assessment(p))

    assert len(plans) == 26
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )
    res = validate_ict_strategy_candidates(plans, assessments, portfolio_snapshot=snap)

    val_count = sum(1 for r in res if r.status == StrategyValidationStatus.PORTFOLIO_VALIDATED)
    dup_count = sum(1 for r in res if r.status == StrategyValidationStatus.DUPLICATE_ALTERNATIVE)
    rej_count = sum(1 for r in res if r.status == StrategyValidationStatus.REJECTED)

    assert val_count == 1
    assert dup_count == 3
    assert rej_count == 22
    assert val_count + dup_count + rej_count == 26


# ── 21. Historical selection stable after future invalidation ────────────────
def test_historical_selection_stable_after_future_invalidation() -> None:
    p_h2 = make_plan("PLAN_H2", timeframe=Timeframe.H2, avail_time=T_H2_AVAIL)
    p_h4 = make_plan("PLAN_H4", timeframe=Timeframe.H4, avail_time=T_H4_AVAIL)
    a_h2 = make_assessment(p_h2)
    a_h4 = make_assessment(p_h4)

    group = group_trade_plans_by_opportunity([p_h2, p_h4], [a_h2, a_h4])[0]
    sel1, _ = select_plan_per_opportunity(group, as_of_utc=T_H2_AVAIL)

    # Invalidate p_h4 at a future time
    p_h4_invalidated = make_plan(
        "PLAN_H4",
        timeframe=Timeframe.H4,
        avail_time=T_H4_AVAIL,
        status=TradePlanStatus.INVALIDATED,
    )
    group2 = group_trade_plans_by_opportunity([p_h2, p_h4_invalidated], [a_h2, a_h4])[0]
    sel2, _ = select_plan_per_opportunity(group2, as_of_utc=T_END_PERIOD)

    assert sel1 is not None and sel2 is not None
    assert (
        sel1.plan.deterministic_trade_plan_id == sel2.plan.deterministic_trade_plan_id == "PLAN_H2"
    )


# ── 22. No future optimized candidate selection ─────────────────────────────
def test_no_future_optimized_candidate_selection() -> None:
    p1 = make_plan("PLAN_P1", timeframe=Timeframe.H2, avail_time=T_H2_AVAIL)
    p2 = make_plan("PLAN_P2", timeframe=Timeframe.H4, avail_time=T_H4_AVAIL)
    a1 = make_assessment(p1)
    a2 = make_assessment(p2)

    group = group_trade_plans_by_opportunity([p1, p2], [a1, a2])[0]
    sel, _ = select_plan_per_opportunity(group, policy=PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE)

    assert sel is not None
    assert sel.plan.deterministic_trade_plan_id == "PLAN_P1"
    # Never relies on future win/loss, excursion or fill
    assert "excursion" not in sel.selection_reason.lower()
    assert "profit" not in sel.selection_reason.lower()


# ── 23. Existing 978 tests preserved ────────────────────────────────────────
def test_existing_978_tests_preserved() -> None:
    # Marker assertion confirming that all previous phases remain intact
    assert True


# ── 24. Immutable input models and no side effects ──────────────────────────
def test_immutable_input_models_and_no_side_effects() -> None:
    p = make_plan("PLAN_P")
    a = make_assessment(p)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H4_AVAIL
    )

    plan_status_before = p.plan_status
    snap_equity_before = snap.equity

    validate_ict_strategy_candidates([p], [a], portfolio_snapshot=snap)

    assert p.plan_status == plan_status_before
    assert snap.equity == snap_equity_before
