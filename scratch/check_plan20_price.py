import sys
from decimal import Decimal
from datetime import datetime, UTC
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached
from traderlab.domain.trade_plans.ict_trade_plan import (
    IctTradePlan,
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

m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
m1_ask = load_month_m1_candles_cached(2026, 9, PriceType.ASK) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)

all_plans = []
for pt in (PriceType.BID, PriceType.ASK):
    m1_data = m1_bid if pt == PriceType.BID else m1_ask
    m1_filtered = [c for c in m1_data if START_UTC <= c.open_time <= END_UTC]
    for mode_name, tf, close_mode in MODES:
        resampled = resample_m1_candles(m1_filtered, tf, daily_close_mode=close_mode)
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
        plans = generate_ict_trade_plans(setups=setups, candles=raw_candles, fvg_zones=fvgs, order_blocks=obs, ote_zones=otes, fvg_respect_summaries=respects, config=TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.FVG_FIRST, entry_mode=TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE, max_waiting_bars=30))
        all_plans.extend(plans)

print(f"Total plans: {len(all_plans)}")

# Print all 26 plans index, TF, side, entry ref price, status
for i, p in enumerate(all_plans, 1):
    print(f"Plan {i:2d} | {p.timeframe.name:2s} {p.price_type.value:3s} | Entry: {p.entry_reference_price} | Zone: [{p.entry_zone_bottom}, {p.entry_zone_top}] | Status: {p.plan_status.value:22s} | ID: {p.deterministic_trade_plan_id}")

# Now risk assessments
spec = InstrumentRiskSpecification(
    instrument_id="XAUUSD",
    source_id="test",
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
acct = AccountRiskSnapshot(account_id="A1", account_currency="USD", equity=Decimal("100000"), balance=Decimal("100000"))
cfg = RiskManagementConfig()

assessments = [evaluate_trade_plan_risk(p, config=cfg, specification=spec, account_snapshot=acct) for p in all_plans]

# Validate
snap = PortfolioRiskSnapshot(account_id="A1", equity=Decimal("100000"), snapshot_available_at=END_UTC)
val_results = validate_ict_strategy_candidates(all_plans, assessments, portfolio_snapshot=snap)
contracts = export_backtest_candidate_contracts(val_results)

print("\n=== VALIDATION RESULTS FOR THE 4 RISK APPROVED PLANS ===")
for v in val_results:
    if "20260928T000000Z" in v.opportunity_id:
        p = v.plan
        print(f"Plan ID: {p.deterministic_trade_plan_id}")
        print(f"  TF: {p.timeframe.name} | Side: {p.price_type.value} | Status: {v.status.value} | Selected: {v.is_selected} | Dup: {v.is_duplicate_alternative}")
        print(f"  Plan Entry Reference Price: {p.entry_reference_price}")

print(f"\n=== EXPORTED BACKTEST CANDIDATE CONTRACTS: {len(contracts)} ===")
for c in contracts:
    print(f"Contract Selected Plan ID:               {c.selected_plan_id}")
    print(f"PriceType:                              {c.price_type.value}")
    print(f"Timeframe:                              {c.timeframe.name}")
    print(f"analytical_entry_reference:             {c.analytical_entry_reference}")
    print(f"analytical_reference_price:             {c.analytical_reference_price}")
    print(f"analytical_reference_price_type:        {c.analytical_reference_price_type.value}")
    print(f"required_execution_entry_side:          {c.required_execution_entry_side.value}")
    print(f"required_execution_exit_side:           {c.required_execution_exit_side.value}")
    print(f"executable_price_verified:              {c.executable_price_verified}")
