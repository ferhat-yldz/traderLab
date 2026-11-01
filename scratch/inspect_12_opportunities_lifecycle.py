import time
from decimal import Decimal
from datetime import datetime, UTC
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached
from traderlab.domain.trade_plans.ict_trade_plan import (
    TradePlanConfig,
    generate_ict_trade_plans,
    EntryZoneSelectionPolicy,
    TradePlanEntryMode,
    TradePlanStatus,
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
from traderlab.domain.strategy_validation.economic_opportunity import (
    group_trade_plans_by_opportunity,
    select_plan_per_opportunity,
    PlanSelectionPolicy,
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

m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
m1_ask = load_month_m1_candles_cached(2026, 9, PriceType.ASK) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)
m1_bid_period = [c for c in m1_bid if START_UTC <= c.open_time <= END_UTC]
m1_ask_period = [c for c in m1_ask if START_UTC <= c.open_time <= END_UTC]

RESPECT_CONFIG = FvgRespectConfig(
    min_departure_ticks=Decimal("1"),
    min_bars_outside_zone=1,
    min_bars_between_retests=1,
    reaction_confirmation_bars=3,
    min_reaction_ticks=Decimal("1"),
    max_reported_retests=4,
    tick_size=Decimal("0.01"),
)
MODES = [
    ("D1 NY", Timeframe.D1, DailyCloseMode.NEW_YORK_1700),
    ("D1 UTC", Timeframe.D1, DailyCloseMode.UTC_MIDNIGHT),
    ("H4", Timeframe.H4, DailyCloseMode.NEW_YORK_1700),
    ("H2", Timeframe.H2, DailyCloseMode.NEW_YORK_1700),
]

all_plans = []
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
        setups = detect_ict_setup_candidates(raw_candles, sweeps=sweeps, structure_breaks=breaks, mss_records=msss, displacements=conf_disps, fvg_zones=fvgs, order_blocks=obs, ote_zones=otes, fvg_respect_summaries=respects, dealing_ranges=ranges, d1_session_mode=close_mode.value if tf == Timeframe.D1 else None)
        plans = generate_ict_trade_plans(setups=setups, candles=raw_candles, fvg_zones=fvgs, order_blocks=obs, ote_zones=otes, fvg_respect_summaries=respects, config=TradePlanConfig(entry_zone_policy=EntryZoneSelectionPolicy.FVG_FIRST, entry_mode=TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE, max_waiting_bars=30))
        all_plans.extend(plans)

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

# Evaluate risk at each plan's inception time
assessments_at_inception = {
    p.deterministic_trade_plan_id: evaluate_trade_plan_risk(
        p, config=cfg, specification=spec, account_snapshot=acct, as_of_utc=p.plan_available_time
    )
    for p in all_plans
}

groups = group_trade_plans_by_opportunity(all_plans, list(assessments_at_inception.values()))
print(f"Total Groups: {len(groups)}")

# Map quotes by minute
bid_by_min = {c.open_time: c for c in m1_bid_period}
ask_by_min = {c.open_time: c for c in m1_ask_period}
common_minutes = sorted(set(bid_by_min.keys()) & set(ask_by_min.keys()))

for idx, g in enumerate(sorted(groups, key=lambda x: x.opportunity.available_at_utc), 1):
    opp = g.opportunity
    # Select plan for this opportunity at its inception
    sel, rej = select_plan_per_opportunity(
        g,
        policy=PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE,
        as_of_utc=opp.available_at_utc,
    )
    if not sel:
        # If none selected at earliest, try with each plan's time
        sel, rej = select_plan_per_opportunity(
            g,
            policy=PlanSelectionPolicy.EARLIEST_PIT_ELIGIBLE,
            as_of_utc=max(p.plan_available_time for p in g.plans),
        )
    
    p = sel.plan if sel else g.plans[0]
    a = sel.assessment if sel else assessments_at_inception[p.deterministic_trade_plan_id]
    
    # Check execution against M1 quotes
    # For SHORT: entry on BID >= entry_reference_price
    # For LONG: entry on ASK <= entry_reference_price
    entry_price = p.entry_reference_price
    stop_price = a.stop_loss.price
    tp_price = a.take_profit.primary_target_price
    
    # Trace through M1 bars starting after plan_available_time
    # eligible_from = p.plan_available_time + 1 min
    m1_after = [t for t in common_minutes if t > p.plan_available_time]
    
    fill_time = None
    fill_price = None
    exit_time = None
    exit_price = None
    exit_reason = None
    
    # Expiry: 30 bars of plan timeframe
    import datetime as dt_mod
    tf_secs = 7200 if p.timeframe == Timeframe.H2 else 14400
    expiry_time = p.plan_available_time + dt_mod.timedelta(seconds=30 * tf_secs)
    
    for t in m1_after:
        if t > expiry_time and fill_time is None:
            exit_time = t
            exit_reason = "EXPIRED"
            break
            
        b_c = bid_by_min[t]
        a_c = ask_by_min[t]
        
        if fill_time is None:
            # Check if limit order fills
            if p.direction.value == "SHORT":
                # SHORT limit fills if BID High >= entry_price
                if b_c.high >= entry_price:
                    fill_time = t
                    fill_price = entry_price
            else:
                # LONG limit fills if ASK Low <= entry_price
                if a_c.low <= entry_price:
                    fill_time = t
                    fill_price = entry_price
        else:
            # Position open! Check stop / TP
            if p.direction.value == "SHORT":
                # Exit SHORT: on ASK
                # Stop loss hit if ASK High >= stop_price
                # TP hit if ASK Low <= tp_price
                stop_hit = a_c.high >= stop_price
                tp_hit = a_c.low <= tp_price
                if stop_hit and tp_hit:
                    exit_time = t
                    exit_price = stop_price
                    exit_reason = "AMBIGUOUS_INTRABAR_STOP_LOSS"
                    break
                elif stop_hit:
                    exit_time = t
                    exit_price = stop_price
                    exit_reason = "STOP_LOSS"
                    break
                elif tp_hit:
                    exit_time = t
                    exit_price = tp_price
                    exit_reason = "TAKE_PROFIT"
                    break
            else:
                # Exit LONG: on BID
                stop_hit = b_c.low <= stop_price
                tp_hit = b_c.high >= tp_price
                if stop_hit and tp_hit:
                    exit_time = t
                    exit_price = stop_price
                    exit_reason = "AMBIGUOUS_INTRABAR_STOP_LOSS"
                    break
                elif stop_hit:
                    exit_time = t
                    exit_price = stop_price
                    exit_reason = "STOP_LOSS"
                    break
                elif tp_hit:
                    exit_time = t
                    exit_price = tp_price
                    exit_reason = "TAKE_PROFIT"
                    break

    print(f"[{idx:02d}] {opp.economic_opportunity_id[:35]} | {p.direction.value} {p.timeframe.name} {p.price_type.value}")
    print(f"     Avail: {p.plan_available_time.strftime('%Y-%m-%d %H:%M')} | Net RR: {a.net_risk_reward} | Final Plan Status: {p.plan_status.value}")
    print(f"     Entry: {entry_price} | SL: {stop_price} | TP: {tp_price}")
    print(f"     Sim Fill: {fill_time} @ {fill_price} | Sim Exit: {exit_time} @ {exit_price} ({exit_reason})")
    print()
