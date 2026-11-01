"""Phase 4E-C Real XAUUSD Data Audit & Chronological Strategy Validation Replay Script.

Runs end-to-end pipeline:
Market Data -> Resampling -> Features -> ICT Setups -> 26 Trade Plans ->
Risk Management Engine -> Strategy Validation & Deduplication Engine ->
Portfolio Risk Validation -> Point-in-Time Chronological Replay
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, ".")

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    resample_m1_candles,
)
from traderlab.core.types import PriceType, Timeframe
from traderlab.domain.market_structure.displacement import (
    DisplacementConfig,
    detect_displacements,
)
from traderlab.domain.market_structure.fvg import (
    detect_fair_value_gaps,
)
from traderlab.domain.market_structure.fvg_respect import (
    FvgRespectConfig,
    evaluate_all_fvgs_respect,
)
from traderlab.domain.market_structure.ict_setup import (
    IctSetupConfig,
    detect_ict_setup_candidates,
)
from traderlab.domain.market_structure.liquidity_sweep import (
    LiquiditySweepConfig,
    detect_liquidity_sweeps,
    filter_confirmed_sweeps,
)
from traderlab.domain.market_structure.market_structure import (
    MarketStructureConfig,
    detect_market_structure,
    filter_confirmed_structure_breaks,
)
from traderlab.domain.market_structure.mss_confirmation import (
    MssConfirmationConfig,
    evaluate_mss_confirmations,
    filter_confirmed_mss,
)
from traderlab.domain.market_structure.order_block import (
    OrderBlockConfig,
    detect_order_blocks,
)
from traderlab.domain.market_structure.ote import (
    generate_ote_zones,
)
from traderlab.domain.market_structure.premium_discount import (
    identify_dealing_ranges,
)
from traderlab.domain.market_structure.swing import (
    TiePolicy,
    detect_swings,
    filter_confirmed_swings,
)
from traderlab.domain.risk.ict_risk_management import (
    AccountRiskSnapshot,
    InstrumentRiskSpecification,
    RiskAssessmentStatus,
    RiskManagementConfig,
    SpecificationSourceType,
    StopLossMethod,
    TakeProfitMethod,
    evaluate_trade_plan_risk,
)
from traderlab.domain.strategy_validation.economic_opportunity import (
    OpportunityDeduplicationPolicy,
    PlanSelectionPolicy,
    group_trade_plans_by_opportunity,
)
from traderlab.domain.strategy_validation.portfolio_risk_validation import (
    PortfolioRiskSnapshot,
)
from traderlab.domain.strategy_validation.strategy_validation import (
    StrategyValidationConfig,
    StrategyValidationStatus,
    explain_strategy_validation,
    export_backtest_candidate_contracts,
    filter_strategy_validations_as_of,
    summarize_strategy_validations,
    validate_ict_strategy_candidates,
)
from traderlab.domain.trade_plans.ict_trade_plan import (
    EntryZoneSelectionPolicy,
    TradePlanConfig,
    TradePlanEntryMode,
    generate_ict_trade_plans,
)
from traderlab.interfaces.dashboard.app import (
    load_month_m1_candles_cached,
)

START_UTC = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
END_UTC = datetime(2026, 10, 15, 23, 59, 59, tzinfo=UTC)

RESPECT_CONFIG = FvgRespectConfig(
    min_departure_ticks=Decimal("1"),
    min_bars_outside_zone=1,
    min_bars_between_retests=1,
    reaction_confirmation_bars=3,
    min_reaction_ticks=Decimal("1"),
    max_reported_retests=4,
    tick_size=Decimal("0.01"),
)

SETUP_CONFIG_DEFAULT = IctSetupConfig()

MODES = [
    ("D1 NY", Timeframe.D1, DailyCloseMode.NEW_YORK_1700),
    ("D1 UTC", Timeframe.D1, DailyCloseMode.UTC_MIDNIGHT),
    ("H4", Timeframe.H4, DailyCloseMode.NEW_YORK_1700),
    ("H2", Timeframe.H2, DailyCloseMode.NEW_YORK_1700),
]


def run_phase_4e_c_audit() -> None:
    t_start_pipeline = time.perf_counter()

    print("=" * 115)
    print("PHASE 4E-C STRATEGY VALIDATION & PORTFOLIO RISK AUDIT (REAL XAUUSD DATA)")
    print("=" * 115)
    print(f"Data Window: {START_UTC.strftime('%Y-%m-%d')} to {END_UTC.strftime('%Y-%m-%d')} UTC")

    # 1. Load Data
    t0_load = time.perf_counter()
    m1_bid = load_month_m1_candles_cached(
        2026, 9, PriceType.BID
    ) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
    m1_ask = load_month_m1_candles_cached(
        2026, 9, PriceType.ASK
    ) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)
    t_load = time.perf_counter() - t0_load

    t_resample_total = 0.0
    t_features_total = 0.0
    t_setup_total = 0.0
    t_plan_gen_total = 0.0

    all_trade_plans = []
    all_setups = []

    for pt in (PriceType.BID, PriceType.ASK):
        m1_data = m1_bid if pt == PriceType.BID else m1_ask
        m1_filtered = [c for c in m1_data if START_UTC <= c.open_time <= END_UTC]

        for mode_name, tf, close_mode in MODES:
            t0_resample = time.perf_counter()
            resampled = resample_m1_candles(m1_filtered, tf, daily_close_mode=close_mode)
            raw_candles = [
                rc.candle
                for rc in resampled
                if START_UTC <= rc.candle.open_time <= END_UTC and rc.candle.is_closed
            ]
            t_resample_total += time.perf_counter() - t0_resample

            t0_feat = time.perf_counter()
            swings = filter_confirmed_swings(
                detect_swings(
                    raw_candles,
                    left_bars=3,
                    right_bars=3,
                    tie_policy=TiePolicy.STRICT,
                )
            )
            sweeps = filter_confirmed_sweeps(
                detect_liquidity_sweeps(
                    raw_candles,
                    swings,
                    config=LiquiditySweepConfig(
                        tick_size=Decimal("0.01"),
                        min_penetration_ticks=1,
                    ),
                )
            )
            struct_breaks, _ = detect_market_structure(
                raw_candles,
                swings,
                config=MarketStructureConfig(
                    tick_size=Decimal("0.01"),
                    break_threshold_ticks=1,
                ),
            )
            breaks = filter_confirmed_structure_breaks(struct_breaks)
            chochs = [b for b in breaks if b.is_choch]
            disps = detect_displacements(
                raw_candles,
                config=DisplacementConfig(
                    tick_size=Decimal("0.01"),
                    min_body_ratio=Decimal("0.60"),
                    min_bullish_close_location=Decimal("0.75"),
                    max_bearish_close_location=Decimal("0.25"),
                    atr_window=14,
                    min_range_atr_multiple=Decimal("1.50"),
                ),
            )
            conf_disps = [
                d
                for d in disps
                if getattr(d, "confirmation_status", "CONFIRMED") == "CONFIRMED"
            ]
            fvgs = detect_fair_value_gaps(raw_candles)
            msss = filter_confirmed_mss(
                evaluate_mss_confirmations(
                    raw_candles,
                    chochs,
                    displacements=conf_disps,
                    fvgs=fvgs,
                    sweeps=sweeps,
                    config=MssConfirmationConfig(
                        max_confirmation_bars=3,
                        require_displacement=True,
                        require_fvg_confirmation=True,
                    ),
                )
            )
            obs = detect_order_blocks(
                raw_candles,
                structure_breaks=breaks,
                displacements=conf_disps,
                mss_confirmations=msss,
                fvg_zones=fvgs,
                config=OrderBlockConfig(max_origin_lookback_bars=6),
            )
            ranges = identify_dealing_ranges(swings)
            otes = generate_ote_zones(ranges)
            respects = evaluate_all_fvgs_respect(
                fvgs,
                raw_candles,
                config=RESPECT_CONFIG,
            )
            t_features_total += time.perf_counter() - t0_feat

            t0_setup = time.perf_counter()
            setups = detect_ict_setup_candidates(
                raw_candles,
                sweeps=sweeps,
                structure_breaks=breaks,
                mss_records=msss,
                displacements=conf_disps,
                fvg_zones=fvgs,
                order_blocks=obs,
                ote_zones=otes,
                fvg_respect_summaries=respects,
                dealing_ranges=ranges,
                config=SETUP_CONFIG_DEFAULT,
                d1_session_mode=close_mode.value if tf == Timeframe.D1 else None,
            )
            t_setup_total += time.perf_counter() - t0_setup
            all_setups.extend(setups)

            t0_plans = time.perf_counter()
            plan_cfg = TradePlanConfig(
                entry_zone_policy=EntryZoneSelectionPolicy.FVG_FIRST,
                entry_mode=TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE,
                max_waiting_bars=30,
            )
            plans = generate_ict_trade_plans(
                setups=setups,
                candles=raw_candles,
                fvg_zones=fvgs,
                order_blocks=obs,
                ote_zones=otes,
                fvg_respect_summaries=respects,
                config=plan_cfg,
            )
            t_plan_gen_total += time.perf_counter() - t0_plans
            all_trade_plans.extend(plans)

    print(f"Total ICT Setups:        {len(all_setups)}")
    print(f"Total Trade Plans:       {len(all_trade_plans)}")

    # 2. Risk Evaluation (4E-B-K1 Standard)
    t0_risk = time.perf_counter()
    spec = InstrumentRiskSpecification(
        instrument_id=all_trade_plans[0].instrument_id,
        source_id=all_trade_plans[0].source_id,
        tick_size=Decimal("0.01"),
        pip_size=Decimal("0.10"),
        units_per_lot=Decimal("100"),
        minimum_order_quantity=Decimal("0.01"),
        maximum_order_quantity=Decimal("50.00"),
        quantity_step=Decimal("0.01"),
        commission_per_lot=Decimal("2.00"),
        typical_spread_ticks=25,
        source_type=SpecificationSourceType.HYPOTHETICAL_BROKER_SCENARIO,
    )
    account = AccountRiskSnapshot(
        account_id="AUDIT_ACCT_100K",
        account_currency="USD",
        equity=Decimal("100000.00"),
        balance=Decimal("100000.00"),
        snapshot_time=END_UTC,
    )
    risk_cfg = RiskManagementConfig(
        stop_loss_method=StopLossMethod.ZONE_DISTAL_BOUNDARY,
        take_profit_method=TakeProfitMethod.FIXED_R_MULTIPLE,
        risk_fraction=Decimal("0.01"),
        target_r_multiple=Decimal("2.0"),
        min_risk_reward_ratio=Decimal("1.5"),
        distal_buffer_ticks=20,
    )
    all_assessments = [
        evaluate_trade_plan_risk(p, config=risk_cfg, specification=spec, account_snapshot=account, as_of_utc=END_UTC)
        for p in all_trade_plans
    ]
    t_risk_total = time.perf_counter() - t0_risk

    # 3. Strategy Validation & Opportunity Deduplication (Stage 16)
    t0_val = time.perf_counter()
    portfolio_snap = PortfolioRiskSnapshot(
        account_id="AUDIT_PORTFOLIO_100K",
        equity=Decimal("100000.00"),
        existing_open_risk=Decimal("0.00"),
        committed_or_reserved_risk=Decimal("0.00"),
        daily_realized_loss=Decimal("0.00"),
        daily_risk_limit=Decimal("3000.00"),
        maximum_portfolio_risk=Decimal("6000.00"),
        maximum_single_event_risk=Decimal("1500.00"),
        snapshot_available_at=END_UTC,
    )
    val_cfg = StrategyValidationConfig(
        deduplication_policy=OpportunityDeduplicationPolicy.ONE_PLAN_PER_ECONOMIC_EVENT,
        plan_selection_policy=PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE,
        timeframe_priority=(Timeframe.H4, Timeframe.H2, Timeframe.D1),
        require_portfolio_snapshot=True,
    )

    t0_group = time.perf_counter()
    opp_groups = group_trade_plans_by_opportunity(all_trade_plans, all_assessments)
    t_group_total = time.perf_counter() - t0_group

    validation_results = validate_ict_strategy_candidates(
        all_trade_plans,
        all_assessments,
        config=val_cfg,
        specification=spec,
        portfolio_snapshot=portfolio_snap,
        as_of_utc=END_UTC,
    )
    t_val_total = time.perf_counter() - t0_val

    # 4. Results & Metrics Output
    summary = summarize_strategy_validations(validation_results)
    print("\n" + "=" * 115)
    print("PHASE 4E-C RECONCILIATION & METRIC AUDIT")
    print("=" * 115)
    print(f"Total Trade Plans Assessed:               {summary.total_trade_plans}")
    print(f"Total Risk Assessments:                   {summary.total_risk_assessments}")
    print(f"Unique Economic Opportunities Identified: {summary.unique_economic_opportunities}")
    print(f"Selected Primary Plans:                   {summary.selected_plans_count}")
    print(f"Duplicate Alternative Plans:              {summary.duplicate_alternatives_count}")
    print(f"Portfolio Validated Plans:                {summary.portfolio_validated_count}")
    print(f"Conditionally Eligible Plans:             {summary.conditionally_eligible_count}")
    print(f"Rejected Plans (Inactive/Limits):         {summary.rejected_count}")
    print(f"Total Proposed Risk (Raw All Plans):      ${summary.total_proposed_risk}")
    print(f"Selected Proposed Risk (Deduplicated):    ${summary.selected_proposed_risk}")

    # Deduplication Proof
    proof_sum = (
        summary.portfolio_validated_count
        + summary.conditionally_eligible_count
        + summary.duplicate_alternatives_count
        + summary.rejected_count
    )
    print(
        f"\nPartitions Proof: {summary.portfolio_validated_count} (Validated) + "
        f"{summary.conditionally_eligible_count} (Cond) + {summary.duplicate_alternatives_count} (Dup) + "
        f"{summary.rejected_count} (Rej) = {proof_sum} == {len(all_trade_plans)}"
    )

    # Print Opportunities breakdown
    print("\n" + "=" * 115)
    print("ECONOMIC OPPORTUNITIES BREAKDOWN")
    print("=" * 115)
    header = f"{'Opp ID':<42} | {'Dir':<5} | {'Plans':<5} | {'TFs':<10} | {'Sides':<8} | {'Total Risk':<12} | {'Max Plan Risk':<12}"
    print(header)
    print("-" * 115)
    for g in opp_groups:
        tf_str = ",".join(t.name for t in g.timeframes)
        pt_str = ",".join(p.value for p in g.price_types)
        print(
            f"{g.opportunity.economic_opportunity_id:<42} | {g.opportunity.evidence.direction.value:<5} | "
            f"{len(g.plans):<5} | {tf_str:<10} | {pt_str:<8} | ${g.total_proposed_risk:<11} | ${g.max_single_plan_risk}"
        )

    # Opportunity #5 Deep Dive (The 4 Approved Plans)
    print("\n" + "=" * 115)
    print("OPPORTUNITY #5 DEEP DIVE (THE 4 RISK-APPROVED PLANS)")
    print("=" * 115)
    opp5_results = [
        v for v in validation_results
        if "20260928T000000Z" in v.opportunity_id
    ]
    for v in opp5_results:
        p = v.plan
        a = v.assessment
        lots = a.position_sizing.allocated_lots if a.position_sizing else Decimal("0")
        cash_risk = a.position_sizing.estimated_cash_risk if a.position_sizing else Decimal("0")
        print(
            f"Plan {p.deterministic_trade_plan_id[:28]}... | {p.timeframe.name} {p.price_type.value} | "
            f"Lots: {lots:<5} | Risk: ${cash_risk:<8} | Status: {v.status.value:<22} | "
            f"Selected: {v.is_selected} | Dup: {v.is_duplicate_alternative}"
        )

    # Stage 18: Chronological Point-in-Time Replay across 8 snapshots
    print("\n" + "=" * 115)
    print("STAGE 18: CHRONOLOGICAL POINT-IN-TIME REPLAY (8 DISCRETE SNAPSHOTS)")
    print("=" * 115)
    snapshots = [
        ("Snapshot 1: T-1h Before Setup (2026-09-27 23:00 UTC)", datetime(2026, 9, 27, 23, 0, tzinfo=UTC)),
        ("Snapshot 2: At BOS Confirmation (2026-09-28 00:00 UTC)", datetime(2026, 9, 28, 0, 0, tzinfo=UTC)),
        ("Snapshot 3: At H2 FVG Confirmation (2026-09-28 04:00 UTC)", datetime(2026, 9, 28, 4, 0, tzinfo=UTC)),
        ("Snapshot 4: At H4 FVG Confirmation (2026-09-28 08:00 UTC)", datetime(2026, 9, 28, 8, 0, tzinfo=UTC)),
        ("Snapshot 5: First Zone Touch Window (2026-09-29 00:00 UTC)", datetime(2026, 9, 29, 0, 0, tzinfo=UTC)),
        ("Snapshot 6: Mid-Retracement (2026-10-05 12:00 UTC)", datetime(2026, 10, 5, 12, 0, tzinfo=UTC)),
        ("Snapshot 7: Invalidation of Secondary Plans (2026-10-14 20:00 UTC)", datetime(2026, 10, 14, 20, 0, tzinfo=UTC)),
        ("Snapshot 8: Analysis Period End (2026-10-15 23:59 UTC)", datetime(2026, 10, 15, 23, 59, 59, tzinfo=UTC)),
    ]

    for label, snap_t in snapshots:
        val_as_of = filter_strategy_validations_as_of(validation_results, snap_t)
        active_in_snap = [v for v in val_as_of if v.status == StrategyValidationStatus.PORTFOLIO_VALIDATED]
        dups_in_snap = [v for v in val_as_of if v.status == StrategyValidationStatus.DUPLICATE_ALTERNATIVE]
        print(f"\n--- {label} ---")
        print(f"  Visible Plans: {len(val_as_of)} | Portfolio Validated: {len(active_in_snap)} | Duplicate Alternatives: {len(dups_in_snap)}")
        for v in active_in_snap:
            print(f"    -> VALIDATED PRIMARY: {v.plan.timeframe.name} {v.plan.price_type.value} | {v.plan.deterministic_trade_plan_id[:25]}... (Avail: {v.plan.plan_available_time})")

    # Sample Explainability Card
    print("\n" + "=" * 115)
    print("SAMPLE STRATEGY VALIDATION AUDIT CARD (FIRST PORTFOLIO VALIDATED PLAN)")
    print("=" * 115)
    validated_plans = [v for v in validation_results if v.status == StrategyValidationStatus.PORTFOLIO_VALIDATED]
    if validated_plans:
        print(explain_strategy_validation(validated_plans[0]))

    # Phase 5 Backtest Contract Export
    contracts = export_backtest_candidate_contracts(validation_results)
    print("\n" + "=" * 115)
    print(f"PHASE 5 BACKTEST CONTRACTS EXPORTED: {len(contracts)} CANDIDATES")
    print("=" * 115)
    for c in contracts:
        print(f"Candidate: {c.selected_plan_id[:30]}... | TF: {c.timeframe.name} | Net RR: {c.net_rr}R | Risk Budget: ${c.risk_budget}")

    # Benchmarks & Latency
    t_total_pipeline = time.perf_counter() - t_start_pipeline
    substages_sum = (
        t_load
        + t_resample_total
        + t_features_total
        + t_setup_total
        + t_plan_gen_total
        + t_risk_total
        + t_val_total
    )

    print("\n" + "=" * 115)
    print("STAGE 20: PERFORMANCE & BENCHMARK RECONCILIATION")
    print("=" * 115)
    print(f"Data Loading (M1 CSV):           {t_load:8.4f}s  ({t_load / t_total_pipeline * 100:5.1f}%)")
    print(f"Resampling (M1->TF):            {t_resample_total:8.4f}s  ({t_resample_total / t_total_pipeline * 100:5.1f}%)")
    print(f"Structural Features:            {t_features_total:8.4f}s  ({t_features_total / t_total_pipeline * 100:5.1f}%)")
    print(f"ICT Setup Assembly:             {t_setup_total:8.4f}s  ({t_setup_total / t_total_pipeline * 100:5.1f}%)")
    print(f"Trade Plan Generation:          {t_plan_gen_total:8.4f}s  ({t_plan_gen_total / t_total_pipeline * 100:5.1f}%)")
    print(f"Risk Management Engine:         {t_risk_total:8.4f}s  ({t_risk_total / t_total_pipeline * 100:5.1f}%)")
    print(f"Strategy Validation & Deduplication: {t_val_total:8.4f}s  ({t_val_total / t_total_pipeline * 100:5.1f}%)")
    print(f"  -> Opp Grouping Sub-stage:      {t_group_total:8.4f}s")
    print("-" * 65)
    print(f"Sub-stages Sum:                 {substages_sum:8.4f}s")
    print(f"Total Pipeline Wall Time:       {t_total_pipeline:8.4f}s")
    overhead = t_total_pipeline - substages_sum
    print(f"Pipeline & Audit Overhead:      {overhead:8.4f}s  ({overhead / t_total_pipeline * 100:5.1f}%)")
    print("=" * 115)


if __name__ == "__main__":
    run_phase_4e_c_audit()
