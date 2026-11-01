import time
from decimal import Decimal
from datetime import datetime, UTC
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached
from traderlab.domain.trade_plans.ict_trade_plan import (
    TradePlanConfig,
    generate_ict_trade_plans,
    EntryZoneSelectionPolicy,
    TradePlanEntryMode,
)
from traderlab.domain.risk.ict_risk_management import (
    evaluate_trade_plan_risk,
    InstrumentRiskSpecification,
    AccountRiskSnapshot,
    RiskManagementConfig,
    SpecificationSourceType,
)
from traderlab.domain.strategy_validation.strategy_validation import (
    validate_ict_strategy_candidates,
    export_backtest_candidate_contracts,
)
from traderlab.domain.strategy_validation.portfolio_risk_validation import PortfolioRiskSnapshot
from traderlab.core.types import PriceType, Timeframe
from traderlab.application.analysis.resampling import resample_m1_candles, DailyCloseMode
from traderlab.domain.market_structure.swing import detect_swings, filter_confirmed_swings, TiePolicy
from traderlab.domain.market_structure.liquidity_sweep import (
    detect_liquidity_sweeps,
    filter_confirmed_sweeps,
    LiquiditySweepConfig,
)
from traderlab.domain.market_structure.market_structure import (
    detect_market_structure,
    filter_confirmed_structure_breaks,
    MarketStructureConfig,
)
from traderlab.domain.market_structure.displacement import detect_displacements, DisplacementConfig
from traderlab.domain.market_structure.fvg import detect_fair_value_gaps
from traderlab.domain.market_structure.mss_confirmation import (
    evaluate_mss_confirmations,
    filter_confirmed_mss,
    MssConfirmationConfig,
)
from traderlab.domain.market_structure.order_block import detect_order_blocks, OrderBlockConfig
from traderlab.domain.market_structure.premium_discount import identify_dealing_ranges
from traderlab.domain.market_structure.ote import generate_ote_zones
from traderlab.domain.market_structure.fvg_respect import evaluate_all_fvgs_respect, FvgRespectConfig
from traderlab.domain.market_structure.ict_setup import detect_ict_setup_candidates, IctSetupConfig
from traderlab.domain.backtesting import (
    BacktestConfig,
    run_deterministic_backtest,
    generate_trade_audit_card,
    format_backtest_run_markdown_report,
)

t0_total = time.perf_counter()

# 1. Benchmark: Data Loading
t0 = time.perf_counter()
m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
m1_ask = load_month_m1_candles_cached(2026, 9, PriceType.ASK) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)
t_data_loading = time.perf_counter() - t0

START_UTC = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
END_UTC = datetime(2026, 10, 15, 23, 59, 59, tzinfo=UTC)

# Filter M1 to the period
m1_bid_period = [c for c in m1_bid if START_UTC <= c.open_time <= END_UTC]
m1_ask_period = [c for c in m1_ask if START_UTC <= c.open_time <= END_UTC]

# 2. Resampling & Structure
t0 = time.perf_counter()
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

all_plans = []
all_setups = []
for pt in (PriceType.BID, PriceType.ASK):
    m1_data = m1_bid_period if pt == PriceType.BID else m1_ask_period
    for mode_name, tf, close_mode in MODES:
        resampled = resample_m1_candles(m1_data, tf, daily_close_mode=close_mode)
        raw_candles = [rc.candle for rc in resampled if START_UTC <= rc.candle.open_time <= END_UTC and rc.candle.is_closed]
        swings = filter_confirmed_swings(detect_swings(raw_candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT))
        sweeps = filter_confirmed_sweeps(detect_liquidity_sweeps(raw_candles, swings, config=LiquiditySweepConfig(tick_size=Decimal('0.01'), min_penetration_ticks=1)))
        struct_breaks, _ = detect_market_structure(raw_candles, swings, config=MarketStructureConfig(tick_size=Decimal('0.01'), break_threshold_ticks=1))
        breaks = filter_confirmed_structure_breaks(struct_breaks)
        chochs = [b for b in breaks if b.is_choch]
        disps = detect_displacements(raw_candles, config=DisplacementConfig(tick_size=Decimal('0.01'), min_body_ratio=Decimal('0.60'), min_bullish_close_location=Decimal('0.75'), max_bearish_close_location=Decimal('0.25'), atr_window=14, min_range_atr_multiple=Decimal('1.50')))
        conf_disps = [d for d in disps if getattr(d, 'confirmation_status', 'CONFIRMED') == 'CONFIRMED']
        fvgs = detect_fair_value_gaps(raw_candles)
        mss_records = evaluate_mss_confirmations(raw_candles, chochs, displacements=conf_disps, fvgs=fvgs, sweeps=sweeps, config=MssConfirmationConfig(max_confirmation_bars=3, require_displacement=True, require_fvg_confirmation=True))
        msss = filter_confirmed_mss(mss_records)
        obs = detect_order_blocks(raw_candles, structure_breaks=breaks, displacements=conf_disps, mss_confirmations=msss, fvg_zones=fvgs, config=OrderBlockConfig(max_origin_lookback_bars=6))
        ranges = identify_dealing_ranges(swings)
        otes = generate_ote_zones(ranges)
        respects = evaluate_all_fvgs_respect(fvgs, raw_candles, config=RESPECT_CONFIG)
        setups = detect_ict_setup_candidates(raw_candles, sweeps=sweeps, structure_breaks=breaks, mss_records=msss, displacements=conf_disps, fvg_zones=fvgs, order_blocks=obs, ote_zones=otes, fvg_respect_summaries=respects, dealing_ranges=ranges, config=SETUP_CONFIG_DEFAULT, d1_session_mode=close_mode.value if tf == Timeframe.D1 else None)
        all_setups.extend(setups)
        plans = generate_ict_trade_plans(setups=setups, candles=raw_candles, fvg_zones=fvgs, order_blocks=obs, ote_zones=otes, fvg_respect_summaries=respects, config=TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.FVG_FIRST, entry_mode=TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE, max_waiting_bars=30))
        all_plans.extend(plans)
t_structure_and_plans = time.perf_counter() - t0

# 3. Risk Assessment
t0 = time.perf_counter()
spec = InstrumentRiskSpecification(
    instrument_id="XAUUSD",
    source_id="historical_m1",
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
acct = AccountRiskSnapshot(account_id="SIM_ACCT", account_currency="USD", equity=Decimal("100000"), balance=Decimal("100000"))
cfg = RiskManagementConfig()
assessments = [evaluate_trade_plan_risk(p, config=cfg, specification=spec, account_snapshot=acct) for p in all_plans]
t_risk_assessment = time.perf_counter() - t0

# 4. Strategy Validation
t0 = time.perf_counter()
snap = PortfolioRiskSnapshot(account_id="SIM_ACCT", equity=Decimal("100000"), snapshot_available_at=END_UTC)
val_res = validate_ict_strategy_candidates(all_plans, assessments, portfolio_snapshot=snap, as_of_utc=END_UTC)
candidate_contracts = export_backtest_candidate_contracts(val_res)
t_strategy_validation = time.perf_counter() - t0

# 5. Deterministic Backtest Run
t0 = time.perf_counter()
backtest_config = BacktestConfig(
    initial_equity=Decimal("100000.00"),
    max_portfolio_risk_fraction=Decimal("0.05"),
    max_single_event_risk_fraction=Decimal("0.01"),
    daily_risk_limit_fraction=Decimal("0.03"),
)
run = run_deterministic_backtest(
    config=backtest_config,
    bid_candles=m1_bid_period,
    ask_candles=m1_ask_period,
    candidate_contracts=candidate_contracts,
)
t_backtest_engine = time.perf_counter() - t0
t_total = time.perf_counter() - t0_total

print("=" * 80)
print("PHASE 5-A REAL XAUUSD BACKTEST EXECUTION SUMMARY")
print("=" * 80)
print(f"Total Historical Setups:              {len(all_setups)}")
print(f"Total Historical Trade Plans:          {len(all_plans)}")
print(f"Total Strategy Validated Candidates:   {len(candidate_contracts)}")
print(f"Total Orders Created:                  {run.summary.total_orders_created}")
print(f"Total Orders Rejected:                 {run.summary.total_orders_rejected}")
print(f"Total Orders Expired:                  {run.summary.total_orders_expired}")
print(f"Total Simulated Fills:                 {run.summary.total_simulated_fills}")
print(f"Total Simulated Open Positions:        {run.summary.total_simulated_open_positions}")
print(f"Total Simulated Closed Positions:      {run.summary.total_simulated_closed_positions}")
print(f"Total Stop Exits:                      {run.summary.total_stop_exits}")
print(f"Total Target Exits:                    {run.summary.total_target_exits}")
print(f"Total Intrabar Ambiguous Events:       {run.summary.total_intrabar_ambiguous_events}")
print(f"Total Open-at-End Positions:           {run.summary.total_open_at_end_positions}")
print(f"Total Realized Simulated PnL:          ${run.summary.total_realized_simulated_pnl:,.2f}")
print(f"Total Estimated Transaction Costs:     ${run.summary.total_estimated_transaction_costs:,.2f}")
print(f"Total Portfolio Risk Rejections:       {run.summary.total_portfolio_risk_rejections}")
print(f"Total Economic Opp Duplicates Prev:    {run.summary.total_economic_opportunity_duplicates_prevented}")
print(f"Final Account Equity:                  ${run.summary.final_equity:,.2f}")

print("\n" + "=" * 80)
print("BENCHMARK TIMINGS")
print("=" * 80)
print(f"Data Loading (M1 BID/ASK Cached):      {t_data_loading:6.3f}s")
print(f"Structure & Trade Plan Generation:     {t_structure_and_plans:6.3f}s")
print(f"Risk Assessment (26 plans):            {t_risk_assessment:6.3f}s")
print(f"Strategy Validation & Deduplication:   {t_strategy_validation:6.3f}s")
print(f"Deterministic Backtest Engine:         {t_backtest_engine:6.3f}s")
print(f"Total Pipeline Runtime:                {t_total:6.3f}s")

if run.positions:
    print("\n" + "=" * 80)
    print("POSITION DETAILS")
    print("=" * 80)
    for pos in run.positions:
        print(generate_trade_audit_card(pos))
