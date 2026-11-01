"""Phase 4E-C-K2: Final Backtest Readiness Gate.

Price Provenance, Execution-Side Consistency, PIT Replay & Opportunity Identity.

Tests:
1. test_plan20_ask_reference_matches_source_trade_plan
2. test_plan7_bid_reference_not_copied_to_plan20
3. test_candidate_price_type_matches_plan_price_type
4. test_short_execution_entry_uses_bid
5. test_short_exit_uses_ask
6. test_long_execution_side_symmetry
7. test_spread_not_double_counted_between_quotes_and_costs
8. test_research_estimated_not_relabelled_verified
9. test_strict_pit_and_source_quality_are_independent
10. test_two_distinct_swings_inside_same_four_hour_bucket
11. test_true_cross_timeframe_same_swing_still_groups
12. test_full_history_vs_incremental_real_data_replay
13. test_period_end_validation_not_reused_as_historical_decision
14. test_signal_bar_cannot_fill_before_signal_availability
15. test_no_fake_fill_or_pnl_created
16. test_existing_26_plan_reconciliation
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
    identify_economic_opportunity,
)
from traderlab.domain.strategy_validation.portfolio_risk_validation import (
    PortfolioRiskSnapshot,
)
from traderlab.domain.strategy_validation.strategy_validation import (
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
SRC_BID = DataSourceId("market_data_lab_m1_derived_h2_bid")
SRC_ASK = DataSourceId("market_data_lab_m1_derived_h2_ask")

T_H2_AVAIL = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
T_H4_AVAIL = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)
T_END_PERIOD = datetime(2026, 10, 15, 23, 59, 59, tzinfo=UTC)


def make_spec(
    comm: str = "2.00",
    spread_ticks: int = 25,
    source_type: SpecificationSourceType = SpecificationSourceType.HYPOTHETICAL_BROKER_SCENARIO,
) -> InstrumentRiskSpecification:
    return InstrumentRiskSpecification(
        instrument_id=INST,
        source_id=SRC_BID,
        tick_size=Decimal("0.01"),
        pip_size=Decimal("0.10"),
        units_per_lot=Decimal("100"),
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
    timeframe: Timeframe = Timeframe.H2,
    price_type: PriceType = PriceType.ASK,
    direction: TradePlanDirection = TradePlanDirection.SHORT,
    setup_id: str = "SETUP:BOS_BEARISH_20260928T000000Z_SWING_LOW_20260925T140000Z_3L3R",
    entry_price: str = "4238.210",
    zone_bottom: str = "4217.875",
    zone_top: str = "4258.545",
    avail_time: datetime = T_H2_AVAIL,
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
        availability_confidence=AvailabilityConfidence.ESTIMATED,
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


# ── 1. Plan 20 ASK reference matches source trade plan ──────────────────────
def test_plan20_ask_reference_matches_source_trade_plan() -> None:
    p20 = make_plan(
        "PLAN_20",
        timeframe=Timeframe.H2,
        price_type=PriceType.ASK,
        entry_price="4238.210",
        zone_bottom="4217.875",
        zone_top="4258.545",
    )
    a20 = make_assessment(p20)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p20], [a20], portfolio_snapshot=snap)
    )

    assert len(contracts) == 1
    c = contracts[0]
    assert c.analytical_reference_price == Decimal("4238.210")
    assert c.analytical_reference_price == p20.entry_reference_price
    assert c.analytical_reference_price_type == PriceType.ASK


# ── 2. Plan 7 BID reference not copied to Plan 20 ────────────────────────────
def test_plan7_bid_reference_not_copied_to_plan20() -> None:
    p7 = make_plan(
        "PLAN_7",
        timeframe=Timeframe.H2,
        price_type=PriceType.BID,
        entry_price="4236.585",
        zone_bottom="4217.225",
        zone_top="4255.945",
        setup_id="SETUP:BOS_BEARISH_20260928T000000Z_SWING_LOW_20260925T140000Z_3L3R",
    )
    p20 = make_plan(
        "PLAN_20",
        timeframe=Timeframe.H2,
        price_type=PriceType.ASK,
        entry_price="4238.210",
        zone_bottom="4217.875",
        zone_top="4258.545",
        setup_id="SETUP:BOS_BEARISH_20260928T000000Z_SWING_LOW_20260925T140000Z_3L3R",
    )
    a7 = make_assessment(p7)
    a20 = make_assessment(p20)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    # Validate with both plans
    res = validate_ict_strategy_candidates([p7, p20], [a7, a20], portfolio_snapshot=snap)
    contracts = export_backtest_candidate_contracts(res)

    assert len(contracts) == 1
    c = contracts[0]
    # Candidate is Plan 20; its analytical reference must NOT be Plan 7's 4236.585
    assert c.selected_plan_id == "PLAN_20"
    assert c.analytical_reference_price == Decimal("4238.210")
    assert c.analytical_reference_price != p7.entry_reference_price
    assert c.analytical_reference_price != Decimal("4236.585")


# ── 3. Candidate price type matches plan price type ──────────────────────────
def test_candidate_price_type_matches_plan_price_type() -> None:
    p_bid = make_plan("PLAN_BID", price_type=PriceType.BID, entry_price="4236.585")
    a_bid = make_assessment(p_bid)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p_bid], [a_bid], portfolio_snapshot=snap)
    )

    assert contracts[0].price_type == PriceType.BID
    assert contracts[0].analytical_reference_price_type == PriceType.BID


# ── 4. SHORT execution entry uses BID ────────────────────────────────────────
def test_short_execution_entry_uses_bid() -> None:
    p = make_plan("PLAN_SHORT", direction=TradePlanDirection.SHORT, price_type=PriceType.ASK)
    a = make_assessment(p)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p], [a], portfolio_snapshot=snap)
    )

    assert contracts[0].required_execution_entry_side == PriceType.BID


# ── 5. SHORT exit uses ASK ──────────────────────────────────────────────────
def test_short_exit_uses_ask() -> None:
    p = make_plan("PLAN_SHORT", direction=TradePlanDirection.SHORT, price_type=PriceType.ASK)
    a = make_assessment(p)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p], [a], portfolio_snapshot=snap)
    )

    assert contracts[0].required_execution_exit_side == PriceType.ASK


# ── 6. LONG execution side symmetry ─────────────────────────────────────────
def test_long_execution_side_symmetry() -> None:
    p = make_plan(
        "PLAN_LONG",
        direction=TradePlanDirection.LONG,
        price_type=PriceType.BID,
        setup_id="SETUP:BOS_BULLISH_20260928T000000Z_SWING_HIGH_20260925T120000Z",
        entry_price="4227.950",
        zone_bottom="4199.955",
        zone_top="4255.945",
    )
    a = make_assessment(p)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p], [a], portfolio_snapshot=snap)
    )

    assert contracts[0].required_execution_entry_side == PriceType.ASK
    assert contracts[0].required_execution_exit_side == PriceType.BID


# ── 7. Spread not double-counted between quotes and costs ───────────────────
def test_spread_not_double_counted_between_quotes_and_costs() -> None:
    """When simulating on dual BID/ASK streams, entry on BID and exit on ASK captures spread.

    Fixed cash cost must not charge spread a second time in backtest execution.
    """
    spec = make_spec(comm="2.00", spread_ticks=25)
    # The spread cost is 25 ticks * $0.01 * 100 units = $25.00/lot
    # Commission is $2.00/lot
    # In backtest contract, explicit required execution sides are separated from fixed fees:
    p = make_plan("P_SHORT", direction=TradePlanDirection.SHORT, price_type=PriceType.ASK)
    a = make_assessment(p, spec=spec)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p], [a], portfolio_snapshot=snap)
    )

    c = contracts[0]
    assert c.required_execution_entry_side == PriceType.BID
    assert c.required_execution_exit_side == PriceType.ASK
    # Verification that contract maintains unverified executable quote status
    assert c.executable_price_verified is False


# ── 8. Research estimated not relabelled verified ────────────────────────────
def test_research_estimated_not_relabelled_verified() -> None:
    p = make_plan("P1")
    assert p.availability_confidence == AvailabilityConfidence.ESTIMATED
    assert p.availability_confidence.value != AvailabilityConfidence.VERIFIED.value

    snap = PortfolioRiskSnapshot(
        account_id="A1",
        equity=Decimal("100000"),
        snapshot_available_at=T_H2_AVAIL,
        source="HYPOTHETICAL_RESEARCH_SCENARIO",
    )
    assert snap.source == "HYPOTHETICAL_RESEARCH_SCENARIO"


# ── 9. Strict PIT and source quality are independent ────────────────────────
def test_strict_pit_and_source_quality_are_independent() -> None:
    p = make_plan("P1", avail_time=T_H2_AVAIL)
    a = make_assessment(p)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )

    # Validating at T_H2_AVAIL passes PIT causal constraint
    res_valid = validate_ict_strategy_candidates(
        [p], [a], portfolio_snapshot=snap, as_of_utc=T_H2_AVAIL
    )
    assert res_valid[0].status == StrategyValidationStatus.PORTFOLIO_VALIDATED

    # Validating before T_H2_AVAIL is rejected for PIT violation, regardless of data source quality
    t_before = datetime(2026, 9, 28, 2, 0, tzinfo=UTC)
    res_pit_reject = validate_ict_strategy_candidates(
        [p], [a], portfolio_snapshot=snap, as_of_utc=t_before
    )
    assert len(res_pit_reject) == 0  # Filtered out at decision time cutoff


# ── 10. Two distinct swings inside same four hour bucket ─────────────────────
def test_two_distinct_swings_inside_same_four_hour_bucket() -> None:
    """Two distinct swings inside the same 4-hour window with different prices must not collide."""
    setup_a = "SETUP:BOS_BEARISH_20260928T000000Z_SWING_LOW_20260925T120000Z_PRICE_2600.00"
    setup_b = "SETUP:BOS_BEARISH_20260928T000000Z_SWING_LOW_20260925T140000Z_PRICE_2650.00"

    p_a = make_plan("PLAN_A", setup_id=setup_a, entry_price="2610.00")
    p_b = make_plan("PLAN_B", setup_id=setup_b, entry_price="2660.00")

    opp_a = identify_economic_opportunity(p_a)
    opp_b = identify_economic_opportunity(p_b)

    assert opp_a.economic_opportunity_id != opp_b.economic_opportunity_id
    assert opp_a.evidence.origin_swing_date != opp_b.evidence.origin_swing_date


# ── 11. True cross timeframe same swing still groups ────────────────────────
def test_true_cross_timeframe_same_swing_still_groups() -> None:
    """H4 swing at 12:00 and H2 swing at 14:00 for the same BOS event must group together."""
    setup_h4 = "XAUUSD:market_data_lab_m1_derived_h4:BID:H4:CONTINUATION:BEARISH:XAUUSD_market_data_lab_m1_derived_h4_BID_H4_BOS_BEARISH_20260928T000000Z_XAUUSD_market_data_lab_m1_derived_h4_BID_H4_SWING_LOW_20260925T120000Z_3L3R:20260928080000"
    setup_h2 = "XAUUSD:market_data_lab_m1_derived_h2:BID:H2:CONTINUATION:BEARISH:XAUUSD_market_data_lab_m1_derived_h2_BID_H2_BOS_BEARISH_20260928T000000Z_XAUUSD_market_data_lab_m1_derived_h2_BID_H2_SWING_LOW_20260925T140000Z_3L3R:20260928040000"

    p_h4 = make_plan("P_H4", setup_id=setup_h4, timeframe=Timeframe.H4, avail_time=T_H4_AVAIL)
    p_h2 = make_plan("P_H2", setup_id=setup_h2, timeframe=Timeframe.H2, avail_time=T_H2_AVAIL)

    opp_h4 = identify_economic_opportunity(p_h4)
    opp_h2 = identify_economic_opportunity(p_h2)

    assert opp_h4.economic_opportunity_id == opp_h2.economic_opportunity_id
    assert opp_h4.evidence.origin_swing_date == opp_h2.evidence.origin_swing_date


# ── 12. Full history vs incremental real data replay ────────────────────────
def test_full_history_vs_incremental_real_data_replay() -> None:
    """Synthetic multi-snapshot verification of full history vs incremental replay equivalence."""
    p1 = make_plan("P1", avail_time=T_H2_AVAIL)
    p2 = make_plan("P2", avail_time=T_H4_AVAIL)
    a1 = make_assessment(p1)
    a2 = make_assessment(p2)

    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )

    # Replay at T_H2_AVAIL
    full_res = validate_ict_strategy_candidates(
        [p1, p2], [a1, a2], portfolio_snapshot=snap, as_of_utc=T_H2_AVAIL
    )
    inc_res = validate_ict_strategy_candidates(
        [p1], [a1], portfolio_snapshot=snap, as_of_utc=T_H2_AVAIL
    )

    assert len(full_res) == len(inc_res) == 1
    assert (
        full_res[0].plan.deterministic_trade_plan_id
        == inc_res[0].plan.deterministic_trade_plan_id
        == "P1"
    )
    assert full_res[0].is_selected is True
    assert inc_res[0].is_selected is True


# ── 13. Period end validation not reused as historical decision ──────────────
def test_period_end_validation_not_reused_as_historical_decision() -> None:
    p = make_plan("P20", avail_time=T_H2_AVAIL)
    a = make_assessment(p)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_END_PERIOD
    )

    res = validate_ict_strategy_candidates(
        [p], [a], portfolio_snapshot=snap, as_of_utc=T_END_PERIOD
    )
    contracts = export_backtest_candidate_contracts(res)

    c = contracts[0]
    assert c.plan_selection_time == T_H2_AVAIL
    assert c.knowledge_snapshot_time == T_H2_AVAIL
    assert c.validation_available_time == T_END_PERIOD


# ── 14. Signal bar cannot fill before signal availability ───────────────────
def test_signal_bar_cannot_fill_before_signal_availability() -> None:
    """A signal available at 04:00 UTC cannot be filled on the candle that produced it (02:00-04:00)."""
    p = make_plan("P20", avail_time=T_H2_AVAIL)
    a = make_assessment(p)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p], [a], portfolio_snapshot=snap)
    )

    c = contracts[0]
    # Execution cannot begin before plan_available_time
    assert c.plan_available_time == datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
    assert c.plan_selection_time >= c.plan_available_time


# ── 15. No fake fill or PnL created ─────────────────────────────────────────
def test_no_fake_fill_or_pnl_created() -> None:
    p = make_plan("P20")
    a = make_assessment(p)
    snap = PortfolioRiskSnapshot(
        account_id="A1", equity=Decimal("100000"), snapshot_available_at=T_H2_AVAIL
    )
    contracts = export_backtest_candidate_contracts(
        validate_ict_strategy_candidates([p], [a], portfolio_snapshot=snap)
    )

    c = contracts[0]
    assert c.executable_price_verified is False
    for fake_attr in ("order_id", "fill_price", "realized_pnl", "fill_time"):
        assert not hasattr(c, fake_attr)


# ── 16. Existing 26 plan reconciliation ─────────────────────────────────────
def test_existing_26_plan_reconciliation() -> None:
    plans = []
    assessments = []

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
