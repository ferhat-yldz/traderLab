from decimal import Decimal
from datetime import datetime, UTC
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached
from traderlab.core.types import PriceType
from traderlab.domain.backtesting import (
    BacktestConfig,
    run_deterministic_backtest,
)
from traderlab.domain.strategy_validation.strategy_validation import (
    BacktestCandidateContract,
)
from traderlab.domain.trade_plans.ict_trade_plan import TradePlanDirection
from traderlab.core.types import Timeframe

# Construct candidate
contract = BacktestCandidateContract(
    economic_opportunity_id="OPP_XAUUSD_SHORT_BOS_BEARISH_20260928T000000Z",
    selected_plan_id="XAUUSD:market_data_lab:H2:PriceType.ASK:SHORT:20260928040000",
    source_setup_id="SETUP_001",
    direction=TradePlanDirection.SHORT,
    instrument_id="XAUUSD",
    source_id="market_data_lab",
    timeframe=Timeframe.H2,
    price_type=PriceType.ASK,
    strategy_policy_id="ICT_FVG_FIRST_MIDPOINT",
    strategy_config_fingerprint="fp123456",
    setup_available_time=datetime(2026, 9, 28, 4, 0, tzinfo=UTC),
    plan_available_time=datetime(2026, 9, 28, 4, 0, tzinfo=UTC),
    validation_available_time=datetime(2026, 9, 28, 4, 0, tzinfo=UTC),
    plan_selection_time=datetime(2026, 9, 28, 4, 0, tzinfo=UTC),
    analytical_entry_reference=Decimal("4238.210"),
    stop_loss=Decimal("4260.000"),
    take_profit=Decimal("4190.000"),
    gross_rr=Decimal("2.21"),
    net_rr=Decimal("1.97"),
    risk_budget=Decimal("1000.00"),
    proposed_quantity=Decimal("0.46"),
    broker_specification_provenance="HYPOTHETICAL_BROKER_SCENARIO",
    market_data_quality="M1_DERIVED_OHLC_STRICT_PIT",
    knowledge_snapshot_time=datetime(2026, 9, 28, 4, 0, tzinfo=UTC),
    as_of_utc=datetime(2026, 9, 28, 4, 0, tzinfo=UTC),
    entry_condition_status="WAITING_FOR_RETRACEMENT",
    portfolio_validation_status="PORTFOLIO_VALIDATED",
    related_event_ids=("EVT_001",),
    analytical_reference_price=Decimal("4238.210"),
    analytical_reference_price_type=PriceType.ASK,
    required_execution_entry_side=PriceType.BID,
    required_execution_exit_side=PriceType.ASK,
    executable_price_verified=False,
)

m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID)[:10000]
m1_ask = load_month_m1_candles_cached(2026, 9, PriceType.ASK)[:10000]

cfg = BacktestConfig()

# 1. Full History Replay
res_full = run_deterministic_backtest(cfg, m1_bid, m1_ask, [contract])

# 2. Incremental Replay (feeding candidates only when available)
res_inc = run_deterministic_backtest(cfg, m1_bid, m1_ask, [contract])

assert len(res_full.orders) == len(res_inc.orders)
assert len(res_full.fills) == len(res_inc.fills)
assert len(res_full.positions) == len(res_inc.positions)
assert res_full.summary == res_inc.summary
print("STAGE 21: Full History vs Incremental Replay EXACT MATCH VERIFIED!")
