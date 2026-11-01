import sys
from decimal import Decimal
from datetime import datetime, UTC
from collections import defaultdict

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
    RiskAssessmentStatus,
)
from traderlab.domain.strategy_validation.strategy_validation import (
    validate_ict_strategy_candidates,
    export_backtest_candidate_contracts,
    StrategyValidationStatus,
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

print("Loading M1 candles...")
m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
m1_ask = load_month_m1_candles_cached(2026, 9, PriceType.ASK) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)

all_plans = []
all_setups = []
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
        all_setups.extend(setups)
        plans = generate_ict_trade_plans(setups=setups, candles=raw_candles, fvg_zones=fvgs, order_blocks=obs, ote_zones=otes, fvg_respect_summaries=respects, config=TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.FVG_FIRST, entry_mode=TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE, max_waiting_bars=30))
        all_plans.extend(plans)

print(f"Total historical setups: {len(all_setups)}")
print(f"Total historical trade plans: {len(all_plans)}")

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
approved = [a for a in assessments if a.status == RiskAssessmentStatus.RISK_APPROVED]
print(f"Total Risk Approved assessments: {len(approved)}")

# Group plans by plan_available_time
by_avail = defaultdict(list)
for p in all_plans:
    by_avail[p.plan_available_time].append(p)

print("\nPlans by Available Time:")
for t in sorted(by_avail.keys()):
    plist = by_avail[t]
    print(f"  {t.strftime('%Y-%m-%d %H:%M UTC')}: {len(plist)} plans")
    for p in plist:
        print(f"    - ID: {p.deterministic_trade_plan_id[:16]}... | TF: {p.timeframe.name} | PT: {p.price_type.name} | Dir: {p.direction.name} | Status: {p.plan_status.name}")

# Now check validations at each unique decision time
candidate_contracts_all = []
for t in sorted(by_avail.keys()):
    snap = PortfolioRiskSnapshot(account_id="SIM_ACCT", equity=Decimal("100000"), snapshot_available_at=t)
    # Plans available up to time t
    curr_plans = [p for p in all_plans if p.plan_available_time <= t]
    curr_ass = [a for p, a in zip(all_plans, assessments) if p.plan_available_time <= t]
    val_res = validate_ict_strategy_candidates(curr_plans, curr_ass, portfolio_snapshot=snap, as_of_utc=t)
    contracts = export_backtest_candidate_contracts(val_res)
    print(f"\nAt {t.strftime('%Y-%m-%d %H:%M UTC')}: Validated candidates={len(contracts)}")
    for c in contracts:
        print(f"    Contract: Plan={c.selected_plan_id[:16]} Opp={c.economic_opportunity_id[:25]} RefPrice={c.analytical_reference_price} Avail={c.plan_available_time}")
