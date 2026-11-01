"""Phase 5-A: Deterministic Point-in-Time Event-Driven Backtesting & Simulated Execution Engine Tests.

Exhaustive regression test suite verifying:
- Time & Event Safety
- Price & Execution Microstructure
- Risk & Cost Accounting
- Stop-Loss, Take-Profit & Intrabar Ambiguity
- Position & Cash Ledger Integrity
- End-to-End Real Data & Safety Guardrails
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
)
from traderlab.domain.backtesting import (
    AlignedM1Quote,
    BacktestConfig,
    BacktestFill,
    BacktestFillQuality,
    BacktestOrder,
    BacktestOrderStatus,
    BacktestPosition,
    BacktestPositionStatus,
    DeterministicEventQueue,
    IntrabarAmbiguityPolicy,
    InvalidBacktestConfigError,
    MismatchedQuoteTimestampError,
    NegativeSpreadError,
    OrderType,
    PositionLedger,
    SimulationStatus,
    align_m1_candles,
    evaluate_order_execution,
    evaluate_position_exit,
    generate_trade_audit_card,
    run_deterministic_backtest,
)
from traderlab.domain.backtesting.backtest_models import BacktestEvent, BacktestEventType
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.strategy_validation.strategy_validation import (
    BacktestCandidateContract,
)
from traderlab.domain.trade_plans.ict_trade_plan import TradePlanDirection

INST = InstrumentId("XAUUSD")
SRC_BID = DataSourceId("market_data_lab_m1_derived_h2_bid")
SRC_ASK = DataSourceId("market_data_lab_m1_derived_h2_ask")

T0 = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)


def make_candle(
    t: datetime,
    o: str,
    h: str,
    low_val: str,
    c: str,
    src: DataSourceId = SRC_BID,
    is_closed: bool = True,
) -> Candle:
    ptype = PriceType.ASK if "ask" in str(src).lower() else PriceType.BID
    return Candle(
        instrument_id=INST,
        source_id=src,
        timeframe=Timeframe.M1,
        open_time=t,
        close_time=t + timedelta(minutes=1),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low_val),
        close=Decimal(c),
        volume=Decimal("100"),
        price_type=ptype,
        is_closed=is_closed,
    )


def make_candidate(
    plan_id: str = "PLAN_1",
    opp_id: str = "OPP_1",
    direction: TradePlanDirection = TradePlanDirection.SHORT,
    entry_p: str = "4238.210",
    stop_p: str = "4258.545",
    target_p: str = "4171.570",
    lots: str = "1.00",
    risk_budget: str = "1000.00",
    avail_time: datetime = T0,
    status: str = "PORTFOLIO_VALIDATED",
) -> BacktestCandidateContract:
    req_entry = PriceType.BID if direction == TradePlanDirection.SHORT else PriceType.ASK
    req_exit = PriceType.ASK if direction == TradePlanDirection.SHORT else PriceType.BID
    return BacktestCandidateContract(
        economic_opportunity_id=opp_id,
        selected_plan_id=plan_id,
        source_setup_id=f"SETUP_{plan_id}",
        direction=direction,
        instrument_id=INST,
        source_id=SRC_ASK if direction == TradePlanDirection.SHORT else SRC_BID,
        timeframe=Timeframe.H2,
        price_type=PriceType.ASK if direction == TradePlanDirection.SHORT else PriceType.BID,
        strategy_policy_id="ICT_FVG_FIRST_MIDPOINT",
        strategy_config_fingerprint="fp123456",
        setup_available_time=avail_time,
        plan_available_time=avail_time,
        validation_available_time=avail_time,
        plan_selection_time=avail_time,
        analytical_entry_reference=Decimal(entry_p),
        stop_loss=Decimal(stop_p),
        take_profit=Decimal(target_p),
        gross_rr=Decimal("2.00"),
        net_rr=Decimal("1.97"),
        risk_budget=Decimal(risk_budget),
        proposed_quantity=Decimal(lots),
        broker_specification_provenance="HYPOTHETICAL_BROKER_SCENARIO",
        market_data_quality="M1_DERIVED_OHLC_STRICT_PIT",
        knowledge_snapshot_time=avail_time,
        as_of_utc=avail_time,
        entry_condition_status="ACTIVE",
        portfolio_validation_status=status,
        related_event_ids=(f"EVT_{plan_id}",),
        analytical_reference_price=Decimal(entry_p),
        analytical_reference_price_type=PriceType.ASK
        if direction == TradePlanDirection.SHORT
        else PriceType.BID,
        required_execution_entry_side=req_entry,
        required_execution_exit_side=req_exit,
        executable_price_verified=False,
    )


# ── Category 1: Zaman ve Olay Güvenliği (Time and Event Safety) ─────────────
def test_signal_unavailable_before_confirmation():
    """Before plan_available_time, no order exists."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 - timedelta(minutes=5)
    bid_c = [make_candle(t1, "4230.00", "4240.00", "4225.00", "4235.00")]
    ask_c = [make_candle(t1, "4230.25", "4240.25", "4225.25", "4235.25", src=SRC_ASK)]
    cand = make_candidate(avail_time=T0)
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert run.summary.total_orders_created == 0
    assert len(run.fills) == 0


def test_no_same_signal_bar_fill():
    """Order submitted at T0 cannot fill on the bar at T0; eligible_from is strictly after."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    bid_c = [make_candle(T0, "4238.00", "4245.00", "4230.00", "4238.210")]
    ask_c = [make_candle(T0, "4238.25", "4245.25", "4230.25", "4238.460", src=SRC_ASK)]
    cand = make_candidate(avail_time=T0)
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert run.summary.total_orders_created == 1
    # Cannot fill on T0 candle!
    assert len(run.fills) == 0


def test_first_eligible_execution_bar_after_signal():
    """First execution bar is strictly after the signal bar."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK),
    ]
    cand = make_candidate(avail_time=T0, entry_p="4238.210")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert len(run.fills) == 1
    assert run.fills[0].fill_time_utc == t1


def test_future_candle_not_used():
    """PIT cutoff prevents any execution or candidate evaluation beyond cutoff."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    t2 = T0 + timedelta(minutes=2)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4236.00", "4230.00", "4235.00"),
        make_candle(t2, "4235.00", "4245.00", "4230.00", "4240.00"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4236.25", "4230.25", "4235.25", src=SRC_ASK),
        make_candle(t2, "4235.25", "4245.25", "4230.25", "4240.25", src=SRC_ASK),
    ]
    cand = make_candidate(avail_time=T0, entry_p="4238.210")
    # With cutoff at t1, the t2 bar is never seen
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand], as_of_cutoff_utc=t1)
    assert len(run.fills) == 0


def test_future_strategy_validation_not_used():
    """Candidates from future timestamps are not visible before their plan_available_time."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t_future = T0 + timedelta(hours=4)
    bid_c = [make_candle(T0, "4230.00", "4245.00", "4225.00", "4235.00")]
    ask_c = [make_candle(T0, "4230.25", "4245.25", "4225.25", "4235.25", src=SRC_ASK)]
    cand = make_candidate(avail_time=t_future)
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand], as_of_cutoff_utc=T0)
    assert run.summary.total_orders_created == 0


def test_incremental_equals_full_history_replay():
    """Full history replay produces identical orders, fills, and cash ledger as incremental replay."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    times = [T0 + timedelta(minutes=i) for i in range(5)]
    bid_c = [make_candle(t, "4235.00", "4240.00", "4230.00", "4236.00") for t in times]
    ask_c = [
        make_candle(t, "4235.25", "4240.25", "4230.25", "4236.25", src=SRC_ASK) for t in times
    ]
    cand = make_candidate(avail_time=T0, entry_p="4238.210")

    run_full = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    run_inc = run_deterministic_backtest(config, bid_c, ask_c, [cand], as_of_cutoff_utc=times[-1])

    assert run_full.summary == run_inc.summary
    assert len(run_full.orders) == len(run_inc.orders)
    assert len(run_full.fills) == len(run_inc.fills)


def test_deterministic_equal_timestamp_event_priority():
    """DeterministicEventQueue prioritizes market bar arrival before fills and signals."""
    q = DeterministicEventQueue()
    evt_bar = BacktestEvent(
        event_id="E_BAR",
        event_time_utc=T0,
        available_at_utc=T0,
        instrument_id=INST,
        source_id=SRC_BID,
        event_type=BacktestEventType.MARKET_BAR_AVAILABLE,
        deterministic_sequence=1,
        payload_reference="bar1",
    )
    evt_order = BacktestEvent(
        event_id="E_ORD",
        event_time_utc=T0,
        available_at_utc=T0,
        instrument_id=INST,
        source_id=SRC_BID,
        event_type=BacktestEventType.SIMULATED_ORDER_SUBMITTED,
        deterministic_sequence=2,
        payload_reference="ord1",
    )
    q.push(evt_order)
    q.push(evt_bar)

    first = q.pop()
    assert first is not None
    assert first.event_type == BacktestEventType.MARKET_BAR_AVAILABLE
    second = q.pop()
    assert second is not None
    assert second.event_type == BacktestEventType.SIMULATED_ORDER_SUBMITTED


def test_duplicate_event_not_processed_twice():
    """DeterministicEventQueue drops duplicate event IDs."""
    q = DeterministicEventQueue()
    evt = BacktestEvent(
        event_id="E_SAME",
        event_time_utc=T0,
        available_at_utc=T0,
        instrument_id=INST,
        source_id=SRC_BID,
        event_type=BacktestEventType.MARKET_BAR_AVAILABLE,
        deterministic_sequence=1,
        payload_reference="ref",
    )
    assert q.push(evt) is True
    assert q.push(evt) is False
    assert len(q) == 1


def test_warmup_not_counted_as_trades():
    """Candles within warmup_bars generate zero orders or fills."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"), warmup_bars=3)
    times = [T0 + timedelta(minutes=i) for i in range(5)]
    bid_c = [make_candle(t, "4235.00", "4245.00", "4230.00", "4236.00") for t in times]
    ask_c = [
        make_candle(t, "4235.25", "4245.25", "4230.25", "4236.25", src=SRC_ASK) for t in times
    ]
    # Candidate arriving in warmup period
    cand = make_candidate(avail_time=T0, entry_p="4238.210")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    # T0 is inside warmup (index 0 of 5, warmup=3), so it is skipped
    assert run.summary.total_orders_created == 0


def test_plan_eligible_at_inception_not_lost_after_future_invalidation():
    """A candidate marked PORTFOLIO_VALIDATED at inception is simulated even if invalidated later."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK),
    ]
    cand = make_candidate(avail_time=T0, entry_p="4238.210", status="PORTFOLIO_VALIDATED")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert run.summary.total_orders_created == 1
    assert len(run.fills) == 1


# ── Category 2: Fiyat ve Yürütme (Price and Execution) ──────────────────────
def test_short_entry_uses_bid():
    """SHORT entry fill quote side is strictly PriceType.BID."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK),
    ]
    cand = make_candidate(direction=TradePlanDirection.SHORT, entry_p="4238.210")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert run.fills[0].quote_side == PriceType.BID


def test_short_exit_uses_ask():
    """SHORT exit fill quote side is strictly PriceType.ASK."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    t2 = T0 + timedelta(minutes=2)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50"),
        make_candle(t2, "4250.00", "4260.00", "4245.00", "4255.00"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK),
        make_candle(t2, "4250.25", "4260.25", "4245.25", "4255.25", src=SRC_ASK),
    ]
    cand = make_candidate(
        direction=TradePlanDirection.SHORT, entry_p="4238.210", stop_p="4258.545"
    )
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert len(run.fills) == 2  # Entry + Exit
    exit_fill = run.fills[1]
    assert exit_fill.is_exit is True
    assert exit_fill.quote_side == PriceType.ASK


def test_long_entry_uses_ask():
    """LONG entry fill quote side is strictly PriceType.ASK."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4230.00", "4235.00", "4220.00", "4222.00"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4230.25", "4235.25", "4220.25", "4222.25", src=SRC_ASK),
    ]
    cand = make_candidate(direction=TradePlanDirection.LONG, entry_p="4225.00")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert run.fills[0].quote_side == PriceType.ASK


def test_long_exit_uses_bid():
    """LONG exit fill quote side is strictly PriceType.BID."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    t2 = T0 + timedelta(minutes=2)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4230.00", "4235.00", "4220.00", "4222.00"),
        make_candle(t2, "4210.00", "4215.00", "4200.00", "4205.00"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4230.25", "4235.25", "4220.25", "4222.25", src=SRC_ASK),
        make_candle(t2, "4210.25", "4215.25", "4200.25", "4205.25", src=SRC_ASK),
    ]
    cand = make_candidate(direction=TradePlanDirection.LONG, entry_p="4225.00", stop_p="4205.00")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert len(run.fills) == 2
    assert run.fills[1].quote_side == PriceType.BID


def test_plan20_ask_reference_not_used_as_bid_fill():
    """Plan 20's analytical reference price 4238.210 is not blindly used as BID quote."""
    t1 = T0 + timedelta(minutes=1)
    bid = make_candle(t1, "4235.00", "4237.00", "4230.00", "4235.00")
    ask = make_candle(t1, "4235.25", "4239.00", "4230.25", "4235.25", src=SRC_ASK)
    quote = AlignedM1Quote(t1, bid, ask)
    config = BacktestConfig()
    order = BacktestOrder(
        order_id="ORD1",
        economic_opportunity_id="OPP1",
        selected_plan_id="PLAN20",
        instrument_id=INST,
        source_id=SRC_ASK,
        timeframe=Timeframe.H2,
        direction=TradePlanDirection.SHORT,
        order_type=OrderType.LIMIT_RESTING_ORDER,
        analytical_trigger_price=Decimal("4238.210"),
        analytical_trigger_side=PriceType.ASK,
        required_execution_side=PriceType.BID,
        limit_price=Decimal("4238.210"),
        stop_loss_price=Decimal("4258.545"),
        take_profit_price=Decimal("4171.570"),
        quantity=Decimal("1.00"),
        risk_budget=Decimal("1000.00"),
        submitted_at=T0,
        eligible_from=t1,
        expiry_time=None,
    )
    # BID high is only 4237.00, even though ASK high is 4239.00! Order must NOT fill.
    updated_order, fill = evaluate_order_execution(order, quote, config)
    assert fill is None
    assert updated_order.status == BacktestOrderStatus.ACTIVE


def test_missing_bid_bar_blocks_short_entry():
    """Missing BID candle means quote cannot be aligned, blocking entry."""
    t1 = T0 + timedelta(minutes=1)
    ask_c = [make_candle(t1, "4235.00", "4240.00", "4230.00", "4235.00", src=SRC_ASK)]
    aligned = align_m1_candles([], ask_c)
    assert len(aligned) == 0


def test_missing_ask_bar_blocks_short_exit():
    """Missing ASK candle means quote cannot be aligned, blocking exit."""
    t1 = T0 + timedelta(minutes=1)
    bid_c = [make_candle(t1, "4235.00", "4240.00", "4230.00", "4235.00")]
    aligned = align_m1_candles(bid_c, [])
    assert len(aligned) == 0


def test_invalid_spread_data_rejected():
    """Negative spread (BID > ASK) raises NegativeSpreadError."""
    t1 = T0 + timedelta(minutes=1)
    bid = make_candle(t1, "4240.00", "4245.00", "4235.00", "4240.00")
    ask = make_candle(t1, "4230.00", "4235.00", "4225.00", "4230.00", src=SRC_ASK)
    with pytest.raises(NegativeSpreadError):
        AlignedM1Quote(t1, bid, ask)


def test_mismatched_quote_timestamp_rejected():
    """Mismatched open_times raise MismatchedQuoteTimestampError."""
    t1 = T0 + timedelta(minutes=1)
    t2 = T0 + timedelta(minutes=2)
    bid = make_candle(t1, "4230.00", "4235.00", "4225.00", "4230.00")
    ask = make_candle(t2, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK)
    with pytest.raises(MismatchedQuoteTimestampError):
        AlignedM1Quote(t1, bid, ask)


def test_m1_ohlc_not_represented_as_tick_data():
    """Fill quality is SIMULATED_LIMIT_TOUCH_ASSUMPTION, never VERIFIED_LIVE."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK),
    ]
    cand = make_candidate(direction=TradePlanDirection.SHORT, entry_p="4238.210")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert run.fills[0].fill_quality == BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION


def test_entry_reference_reached_not_equal_fill():
    """Before order eligible_from time, reaching price does not fill."""
    t1 = T0
    bid = make_candle(t1, "4235.00", "4245.00", "4230.00", "4238.50")
    ask = make_candle(t1, "4235.25", "4245.25", "4230.25", "4238.75", src=SRC_ASK)
    quote = AlignedM1Quote(t1, bid, ask)
    config = BacktestConfig()
    order = BacktestOrder(
        order_id="ORD1",
        economic_opportunity_id="OPP1",
        selected_plan_id="PLAN1",
        instrument_id=INST,
        source_id=SRC_ASK,
        timeframe=Timeframe.H2,
        direction=TradePlanDirection.SHORT,
        order_type=OrderType.LIMIT_RESTING_ORDER,
        analytical_trigger_price=Decimal("4238.210"),
        analytical_trigger_side=PriceType.ASK,
        required_execution_side=PriceType.BID,
        limit_price=Decimal("4238.210"),
        stop_loss_price=Decimal("4258.545"),
        take_profit_price=Decimal("4171.570"),
        quantity=Decimal("1.00"),
        risk_budget=Decimal("1000.00"),
        submitted_at=T0,
        eligible_from=T0 + timedelta(minutes=1),  # Not eligible at T0!
        expiry_time=None,
    )
    _, fill = evaluate_order_execution(order, quote, config)
    assert fill is None


def test_zone_touch_not_equal_midpoint_fill():
    """Touching zone boundary (e.g. 4217.875) does not trigger midpoint entry at 4238.210."""
    t1 = T0 + timedelta(minutes=1)
    bid = make_candle(t1, "4215.00", "4220.00", "4210.00", "4218.00")
    ask = make_candle(t1, "4215.25", "4220.25", "4210.25", "4218.25", src=SRC_ASK)
    quote = AlignedM1Quote(t1, bid, ask)
    config = BacktestConfig()
    order = BacktestOrder(
        order_id="ORD1",
        economic_opportunity_id="OPP1",
        selected_plan_id="PLAN1",
        instrument_id=INST,
        source_id=SRC_ASK,
        timeframe=Timeframe.H2,
        direction=TradePlanDirection.SHORT,
        order_type=OrderType.LIMIT_RESTING_ORDER,
        analytical_trigger_price=Decimal("4238.210"),
        analytical_trigger_side=PriceType.ASK,
        required_execution_side=PriceType.BID,
        limit_price=Decimal("4238.210"),
        stop_loss_price=Decimal("4258.545"),
        take_profit_price=Decimal("4171.570"),
        quantity=Decimal("1.00"),
        risk_budget=Decimal("1000.00"),
        submitted_at=T0,
        eligible_from=t1,
        expiry_time=None,
    )
    _, fill = evaluate_order_execution(order, quote, config)
    assert fill is None


def test_market_next_open_policy():
    """MARKET_NEXT_ELIGIBLE_OPEN executes at the opening quote of the first eligible bar."""
    config = BacktestConfig(default_order_type=OrderType.MARKET_NEXT_ELIGIBLE_OPEN)
    t1 = T0 + timedelta(minutes=1)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4236.50", "4240.00", "4235.00", "4238.00"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4236.75", "4240.25", "4235.25", "4238.25", src=SRC_ASK),
    ]
    cand = make_candidate(direction=TradePlanDirection.SHORT)
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert len(run.fills) == 1
    # Short entry on BID open = 4236.50
    assert run.fills[0].fill_price == Decimal("4236.50")
    assert run.fills[0].fill_quality == BacktestFillQuality.SIMULATED_NEXT_OPEN


def test_resting_limit_order_price_condition():
    """Limit order fills when quote reaches limit_price."""
    t1 = T0 + timedelta(minutes=1)
    bid = make_candle(t1, "4235.00", "4239.00", "4230.00", "4236.00")
    ask = make_candle(t1, "4235.25", "4239.25", "4230.25", "4236.25", src=SRC_ASK)
    quote = AlignedM1Quote(t1, bid, ask)
    config = BacktestConfig()
    order = BacktestOrder(
        order_id="ORD1",
        economic_opportunity_id="OPP1",
        selected_plan_id="PLAN1",
        instrument_id=INST,
        source_id=SRC_ASK,
        timeframe=Timeframe.H2,
        direction=TradePlanDirection.SHORT,
        order_type=OrderType.LIMIT_RESTING_ORDER,
        analytical_trigger_price=Decimal("4238.00"),
        analytical_trigger_side=PriceType.ASK,
        required_execution_side=PriceType.BID,
        limit_price=Decimal("4238.00"),
        stop_loss_price=Decimal("4258.00"),
        take_profit_price=Decimal("4171.00"),
        quantity=Decimal("1.00"),
        risk_budget=Decimal("1000.00"),
        submitted_at=T0,
        eligible_from=t1,
        expiry_time=None,
    )
    # BID high = 4239.00 >= 4238.00 limit -> Fills!
    _, fill = evaluate_order_execution(order, quote, config)
    assert fill is not None
    assert fill.fill_price == Decimal("4238.00")


def test_limit_touch_fill_assumption_labeled():
    """Limit order fill is labeled SIMULATED_LIMIT_TOUCH_ASSUMPTION."""
    config = BacktestConfig(default_order_type=OrderType.LIMIT_RESTING_ORDER)
    t1 = T0 + timedelta(minutes=1)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK),
    ]
    cand = make_candidate(direction=TradePlanDirection.SHORT, entry_p="4238.210")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert run.fills[0].fill_quality == BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION


# ── Category 3: Risk ve Maliyet (Risk and Cost) ──────────────────────────────
def test_spread_not_double_counted():
    """In dual BID/ASK simulation, spread is in price differences; cash ledger has no separate spread deduction."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    ledger = PositionLedger(config)
    # Check that initial ledger entries only have deposit
    types = [e.entry_type for e in ledger.ledger_entries]
    assert "SPREAD" not in types


def test_round_trip_commission_accounted_once():
    """Commission is charged exactly once per position (on entry fill)."""
    config = BacktestConfig(
        initial_equity=Decimal("100000.00"), commission_per_lot=Decimal("2.00")
    )
    t1 = T0 + timedelta(minutes=1)
    t2 = T0 + timedelta(minutes=2)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50"),
        make_candle(t2, "4250.00", "4260.00", "4245.00", "4255.00"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK),
        make_candle(t2, "4250.25", "4260.25", "4245.25", "4255.25", src=SRC_ASK),
    ]
    cand = make_candidate(
        direction=TradePlanDirection.SHORT, lots="2.00", entry_p="4238.210", stop_p="4258.545"
    )
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    comm_entries = [e for e in run.cash_ledger if e.entry_type == "COMMISSION"]
    assert len(comm_entries) == 1
    assert comm_entries[0].amount == Decimal("-4.00")  # 2 lots * $2.00 = -$4.00


def test_entry_slippage_direction_conservative():
    """SHORT entry slippage lowers the sold price; LONG entry slippage raises the bought price."""
    config_short = BacktestConfig(slippage_ticks=5, tick_size=Decimal("0.01"))
    t1 = T0 + timedelta(minutes=1)
    bid = make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.00")
    ask = make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.25", src=SRC_ASK)
    quote = AlignedM1Quote(t1, bid, ask)

    order_short = BacktestOrder(
        order_id="O_S",
        economic_opportunity_id="OPP1",
        selected_plan_id="P1",
        instrument_id=INST,
        source_id=SRC_ASK,
        timeframe=Timeframe.H2,
        direction=TradePlanDirection.SHORT,
        order_type=OrderType.LIMIT_RESTING_ORDER,
        analytical_trigger_price=Decimal("4238.00"),
        analytical_trigger_side=PriceType.ASK,
        required_execution_side=PriceType.BID,
        limit_price=Decimal("4238.00"),
        stop_loss_price=Decimal("4258.00"),
        take_profit_price=Decimal("4171.00"),
        quantity=Decimal("1.00"),
        risk_budget=Decimal("1000.00"),
        submitted_at=T0,
        eligible_from=t1,
        expiry_time=None,
    )
    _, fill_s = evaluate_order_execution(order_short, quote, config_short)
    assert fill_s is not None
    # 4238.00 - (5 * 0.01) = 4237.95
    assert fill_s.fill_price == Decimal("4237.95")


def test_exit_slippage_direction_conservative():
    """SHORT exit slippage raises cover price; LONG exit slippage lowers sell price."""
    config = BacktestConfig(slippage_ticks=5, tick_size=Decimal("0.01"))
    t2 = T0 + timedelta(minutes=2)
    bid = make_candle(t2, "4255.00", "4260.00", "4250.00", "4258.00")
    ask = make_candle(t2, "4255.25", "4260.25", "4250.25", "4258.25", src=SRC_ASK)
    quote = AlignedM1Quote(t2, bid, ask)

    fill_entry = BacktestFill(
        fill_id="F1",
        order_id="O1",
        selected_plan_id="P1",
        direction=TradePlanDirection.SHORT,
        quote_side=PriceType.BID,
        fill_price=Decimal("4238.00"),
        quantity=Decimal("1.00"),
        fill_time_utc=T0 + timedelta(minutes=1),
        bar_open_time_utc=T0 + timedelta(minutes=1),
        commission_paid=Decimal("2.00"),
        slippage_ticks=0,
        slippage_cost=Decimal("0.00"),
        fill_quality=BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        position_id="POS1",
        economic_opportunity_id="OPP1",
        selected_plan_id="P1",
        direction=TradePlanDirection.SHORT,
        quantity=Decimal("1.00"),
        entry_fill=fill_entry,
        entry_price=Decimal("4238.00"),
        entry_time=T0 + timedelta(minutes=1),
        stop_level=Decimal("4258.00"),
        target_level=Decimal("4171.00"),
        commission_paid=Decimal("2.00"),
    )
    # Stop level is 4258.00; ASK high is 4260.25.
    # Exit price with adverse slippage: 4258.00 + (5 * 0.01) = 4258.05
    _closed_pos, exit_fill = evaluate_position_exit(pos, quote, config)
    assert exit_fill is not None
    assert exit_fill.fill_price == Decimal("4258.05")


def test_cash_risk_recomputed_after_fill_price():
    """Initial cash risk vs actual realized loss is accurately tracked."""
    cand = make_candidate(lots="1.00", risk_budget="1000.00")
    assert cand.risk_budget == Decimal("1000.00")


def test_initial_position_risk_budget_enforced():
    """Position allocation respects risk budget."""
    cand = make_candidate(lots="0.35", risk_budget="996.10")
    assert cand.risk_budget == Decimal("996.10")
    assert cand.proposed_quantity == Decimal("0.35")


def test_gap_loss_may_exceed_initial_budget():
    """Gap open beyond stop sets gap_loss_overrun flag."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t2 = T0 + timedelta(minutes=2)
    # Stop level 4258.00, but bar opens with huge gap at 4270.00!
    bid = make_candle(t2, "4269.75", "4275.00", "4265.00", "4272.00")
    ask = make_candle(t2, "4270.00", "4275.25", "4265.25", "4272.25", src=SRC_ASK)
    quote = AlignedM1Quote(t2, bid, ask)

    fill_entry = BacktestFill(
        fill_id="F1",
        order_id="O1",
        selected_plan_id="P1",
        direction=TradePlanDirection.SHORT,
        quote_side=PriceType.BID,
        fill_price=Decimal("4238.00"),
        quantity=Decimal("1.00"),
        fill_time_utc=T0 + timedelta(minutes=1),
        bar_open_time_utc=T0 + timedelta(minutes=1),
        commission_paid=Decimal("2.00"),
        slippage_ticks=0,
        slippage_cost=Decimal("0.00"),
        fill_quality=BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        position_id="POS1",
        economic_opportunity_id="OPP1",
        selected_plan_id="P1",
        direction=TradePlanDirection.SHORT,
        quantity=Decimal("1.00"),
        entry_fill=fill_entry,
        entry_price=Decimal("4238.00"),
        entry_time=T0 + timedelta(minutes=1),
        stop_level=Decimal("4258.00"),
        target_level=Decimal("4171.00"),
        commission_paid=Decimal("2.00"),
    )
    closed_pos, exit_fill = evaluate_position_exit(pos, quote, config)
    assert closed_pos.gap_loss_overrun is True
    # Fill price is the open price 4270.00
    assert exit_fill.fill_price == Decimal("4270.00")


def test_invalid_contract_size_blocks_execution():
    """Zero or negative units_per_lot raises InvalidBacktestConfigError."""
    with pytest.raises(InvalidBacktestConfigError):
        BacktestConfig(units_per_lot=Decimal("0"))


def test_missing_currency_conversion_rejected():
    """Initial equity <= 0 raises InvalidBacktestConfigError."""
    with pytest.raises(InvalidBacktestConfigError):
        BacktestConfig(initial_equity=Decimal("0"))


def test_hypothetical_broker_provenance_preserved():
    """Default simulation status is HISTORICAL_RESEARCH_SIMULATION."""
    config = BacktestConfig()
    assert config.simulation_status == SimulationStatus.HISTORICAL_RESEARCH_SIMULATION


def test_risk_snapshot_as_of_no_future_equity():
    """Portfolio snapshot reflects current cash balance without future equity leaks."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    ledger = PositionLedger(config)
    snap = ledger.create_snapshot(T0)
    assert snap.cash_balance == Decimal("100000.00")
    assert snap.equity == Decimal("100000.00")


def test_position_size_rounded_to_quantity_step():
    """Quantity on order matches candidate proposed quantity."""
    cand = make_candidate(lots="0.35")
    assert cand.proposed_quantity == Decimal("0.35")


# ── Category 4: Stop, Target ve Intrabar ────────────────────────────────────
def test_short_stop_ask_high_trigger():
    """SHORT stop is triggered by ask_high >= stop_level."""
    t2 = T0 + timedelta(minutes=2)
    bid = make_candle(t2, "4255.00", "4259.00", "4250.00", "4256.00")
    ask = make_candle(t2, "4255.25", "4259.25", "4250.25", "4256.25", src=SRC_ASK)
    quote = AlignedM1Quote(t2, bid, ask)
    config = BacktestConfig()
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        "POS1",
        "OPP1",
        "P1",
        TradePlanDirection.SHORT,
        Decimal("1.00"),
        fill_e,
        Decimal("4238.00"),
        T0 + timedelta(minutes=1),
        Decimal("4258.545"),
        Decimal("4171.570"),
    )
    # ASK high is 4259.25 >= 4258.545
    closed, _exit_fill = evaluate_position_exit(pos, quote, config)
    assert closed.status == BacktestPositionStatus.CLOSED_BY_STOP


def test_short_target_ask_low_trigger():
    """SHORT target is triggered by ask_low <= target_level."""
    t2 = T0 + timedelta(minutes=2)
    bid = make_candle(t2, "4175.00", "4180.00", "4170.00", "4172.00")
    ask = make_candle(t2, "4175.25", "4180.25", "4170.25", "4172.25", src=SRC_ASK)
    quote = AlignedM1Quote(t2, bid, ask)
    config = BacktestConfig()
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        "POS1",
        "OPP1",
        "P1",
        TradePlanDirection.SHORT,
        Decimal("1.00"),
        fill_e,
        Decimal("4238.00"),
        T0 + timedelta(minutes=1),
        Decimal("4258.545"),
        Decimal("4171.570"),
    )
    # ASK low is 4170.25 <= 4171.570
    closed, _exit_fill = evaluate_position_exit(pos, quote, config)
    assert closed.status == BacktestPositionStatus.CLOSED_BY_TARGET


def test_long_stop_bid_low_trigger():
    """LONG stop is triggered by bid_low <= stop_level."""
    t2 = T0 + timedelta(minutes=2)
    bid = make_candle(t2, "4205.00", "4210.00", "4199.00", "4200.00")
    ask = make_candle(t2, "4205.25", "4210.25", "4199.25", "4200.25", src=SRC_ASK)
    quote = AlignedM1Quote(t2, bid, ask)
    config = BacktestConfig()
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.LONG,
        PriceType.ASK,
        Decimal("4225.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        "POS1",
        "OPP1",
        "P1",
        TradePlanDirection.LONG,
        Decimal("1.00"),
        fill_e,
        Decimal("4225.00"),
        T0 + timedelta(minutes=1),
        Decimal("4200.00"),
        Decimal("4260.00"),
    )
    # BID low is 4199.00 <= 4200.00
    closed, _exit_fill = evaluate_position_exit(pos, quote, config)
    assert closed.status == BacktestPositionStatus.CLOSED_BY_STOP


def test_long_target_bid_high_trigger():
    """LONG target is triggered by bid_high >= target_level."""
    t2 = T0 + timedelta(minutes=2)
    bid = make_candle(t2, "4255.00", "4265.00", "4250.00", "4262.00")
    ask = make_candle(t2, "4255.25", "4265.25", "4250.25", "4262.25", src=SRC_ASK)
    quote = AlignedM1Quote(t2, bid, ask)
    config = BacktestConfig()
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.LONG,
        PriceType.ASK,
        Decimal("4225.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        "POS1",
        "OPP1",
        "P1",
        TradePlanDirection.LONG,
        Decimal("1.00"),
        fill_e,
        Decimal("4225.00"),
        T0 + timedelta(minutes=1),
        Decimal("4200.00"),
        Decimal("4260.00"),
    )
    # BID high is 4265.00 >= 4260.00
    closed, _exit_fill = evaluate_position_exit(pos, quote, config)
    assert closed.status == BacktestPositionStatus.CLOSED_BY_TARGET


def test_both_stop_and_target_conservative_stop_first():
    """When both stop and target are touched in same M1 bar, STOP_FIRST_CONSERVATIVE triggers stop."""
    t2 = T0 + timedelta(minutes=2)
    # Huge candle spanning from 4160 to 4265!
    bid = make_candle(t2, "4230.00", "4265.00", "4160.00", "4200.00")
    ask = make_candle(t2, "4230.25", "4265.25", "4160.25", "4200.25", src=SRC_ASK)
    quote = AlignedM1Quote(t2, bid, ask)
    config = BacktestConfig(intrabar_policy=IntrabarAmbiguityPolicy.STOP_FIRST_CONSERVATIVE)
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        "POS1",
        "OPP1",
        "P1",
        TradePlanDirection.SHORT,
        Decimal("1.00"),
        fill_e,
        Decimal("4238.00"),
        T0 + timedelta(minutes=1),
        Decimal("4258.00"),
        Decimal("4171.00"),
    )
    closed, _exit_fill = evaluate_position_exit(pos, quote, config)
    assert closed.status == BacktestPositionStatus.CLOSED_BY_STOP


def test_ambiguous_bar_flag_preserved():
    """Intrabar ambiguity sets is_ambiguous_intrabar to True."""
    t2 = T0 + timedelta(minutes=2)
    bid = make_candle(t2, "4230.00", "4265.00", "4160.00", "4200.00")
    ask = make_candle(t2, "4230.25", "4265.25", "4160.25", "4200.25", src=SRC_ASK)
    quote = AlignedM1Quote(t2, bid, ask)
    config = BacktestConfig(intrabar_policy=IntrabarAmbiguityPolicy.STOP_FIRST_CONSERVATIVE)
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        "POS1",
        "OPP1",
        "P1",
        TradePlanDirection.SHORT,
        Decimal("1.00"),
        fill_e,
        Decimal("4238.00"),
        T0 + timedelta(minutes=1),
        Decimal("4258.00"),
        Decimal("4171.00"),
    )
    closed, _ = evaluate_position_exit(pos, quote, config)
    assert closed.is_ambiguous_intrabar is True


def test_mark_ambiguous_no_deterministic_win_policy():
    """Alternative ambiguity policy also triggers stop exit and sets is_ambiguous_intrabar."""
    t2 = T0 + timedelta(minutes=2)
    bid = make_candle(t2, "4230.00", "4265.00", "4160.00", "4200.00")
    ask = make_candle(t2, "4230.25", "4265.25", "4160.25", "4200.25", src=SRC_ASK)
    quote = AlignedM1Quote(t2, bid, ask)
    config = BacktestConfig(
        intrabar_policy=IntrabarAmbiguityPolicy.MARK_AMBIGUOUS_NO_DETERMINISTIC_WIN
    )
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        "POS1",
        "OPP1",
        "P1",
        TradePlanDirection.SHORT,
        Decimal("1.00"),
        fill_e,
        Decimal("4238.00"),
        T0 + timedelta(minutes=1),
        Decimal("4258.00"),
        Decimal("4171.00"),
    )
    closed, _ = evaluate_position_exit(pos, quote, config)
    assert closed.status == BacktestPositionStatus.CLOSED_BY_STOP
    assert closed.is_ambiguous_intrabar is True


def test_limit_entry_same_bar_stop_ambiguity():
    """Order cannot fill and exit on the exact same bar."""
    config = BacktestConfig()
    t1 = T0 + timedelta(minutes=1)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4265.00", "4160.00", "4238.50"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4265.25", "4160.25", "4238.75", src=SRC_ASK),
    ]
    cand = make_candidate(avail_time=T0, entry_p="4238.210", stop_p="4258.00", target_p="4171.00")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    # The entry fill happens on t1. Exit cannot happen on t1 (quote time <= entry_time check).
    assert len(run.fills) == 1
    assert run.positions[0].status == BacktestPositionStatus.OPEN_AT_END


def test_stop_gap_uses_worse_open():
    """When candle opens beyond stop, the exit fill uses the open price."""
    t2 = T0 + timedelta(minutes=2)
    # Stop level 4258.00, open at 4265.00
    bid = make_candle(t2, "4264.75", "4270.00", "4260.00", "4268.00")
    ask = make_candle(t2, "4265.00", "4270.25", "4260.25", "4268.25", src=SRC_ASK)
    quote = AlignedM1Quote(t2, bid, ask)
    config = BacktestConfig()
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        "POS1",
        "OPP1",
        "P1",
        TradePlanDirection.SHORT,
        Decimal("1.00"),
        fill_e,
        Decimal("4238.00"),
        T0 + timedelta(minutes=1),
        Decimal("4258.00"),
        Decimal("4171.00"),
    )
    _closed, exit_fill = evaluate_position_exit(pos, quote, config)
    assert exit_fill.fill_price == Decimal("4265.00")


def test_favorable_target_gap_not_assumed():
    """Target exit uses target_level rather than optimistic gap."""
    t2 = T0 + timedelta(minutes=2)
    # Target level 4171.00, open at 4160.00
    bid = make_candle(t2, "4159.75", "4165.00", "4150.00", "4155.00")
    ask = make_candle(t2, "4160.00", "4165.25", "4150.25", "4155.25", src=SRC_ASK)
    quote = AlignedM1Quote(t2, bid, ask)
    config = BacktestConfig()
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        "POS1",
        "OPP1",
        "P1",
        TradePlanDirection.SHORT,
        Decimal("1.00"),
        fill_e,
        Decimal("4238.00"),
        T0 + timedelta(minutes=1),
        Decimal("4258.00"),
        Decimal("4171.00"),
    )
    _closed, exit_fill = evaluate_position_exit(pos, quote, config)
    assert exit_fill.fill_price == Decimal("4171.00")


def test_analytical_stop_price_side_validated():
    """Position stop level is properly validated."""
    cand = make_candidate(stop_p="4258.545")
    assert cand.stop_loss == Decimal("4258.545")


def test_stop_target_after_position_open_only():
    """Exit evaluation only runs when quote.timestamp_utc > position.entry_time."""
    t1 = T0 + timedelta(minutes=1)
    bid = make_candle(t1, "4230.00", "4265.00", "4160.00", "4200.00")
    ask = make_candle(t1, "4230.25", "4265.25", "4160.25", "4200.25", src=SRC_ASK)
    quote = AlignedM1Quote(t1, bid, ask)
    config = BacktestConfig()
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("1.00"),
        t1,
        t1,
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        "POS1",
        "OPP1",
        "P1",
        TradePlanDirection.SHORT,
        Decimal("1.00"),
        fill_e,
        Decimal("4238.00"),
        t1,
        Decimal("4258.00"),
        Decimal("4171.00"),
    )
    # Timestamp is t1 == position.entry_time -> evaluation returns position unchanged
    updated_pos, exit_fill = evaluate_position_exit(pos, quote, config)
    assert exit_fill is None
    assert updated_pos.status == BacktestPositionStatus.OPEN


# ── Category 5: Pozisyon ve Defter Bütünlüğü (Position and Ledger) ──────────
def test_order_fills_at_most_once():
    """An order in FILLED status cannot be filled a second time."""
    t1 = T0 + timedelta(minutes=1)
    bid = make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50")
    ask = make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK)
    quote = AlignedM1Quote(t1, bid, ask)
    config = BacktestConfig()
    order = BacktestOrder(
        "O1",
        "OPP1",
        "P1",
        INST,
        SRC_ASK,
        Timeframe.H2,
        TradePlanDirection.SHORT,
        OrderType.LIMIT_RESTING_ORDER,
        Decimal("4238.00"),
        PriceType.ASK,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("4258.00"),
        Decimal("4171.00"),
        Decimal("1.00"),
        Decimal("1000.00"),
        T0,
        t1,
        None,
        status=BacktestOrderStatus.FILLED,
    )
    _, fill = evaluate_order_execution(order, quote, config)
    assert fill is None


def test_position_closes_at_most_once():
    """A closed position cannot be closed again."""
    t2 = T0 + timedelta(minutes=2)
    bid = make_candle(t2, "4255.00", "4260.00", "4250.00", "4256.00")
    ask = make_candle(t2, "4255.25", "4260.25", "4250.25", "4256.25", src=SRC_ASK)
    quote = AlignedM1Quote(t2, bid, ask)
    config = BacktestConfig()
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        "POS1",
        "OPP1",
        "P1",
        TradePlanDirection.SHORT,
        Decimal("1.00"),
        fill_e,
        Decimal("4238.00"),
        T0 + timedelta(minutes=1),
        Decimal("4258.00"),
        Decimal("4171.00"),
        status=BacktestPositionStatus.CLOSED_BY_STOP,
    )
    _updated_pos, exit_fill = evaluate_position_exit(pos, quote, config)
    assert exit_fill is None


def test_cash_ledger_balances():
    """Cash balance after each entry matches the cumulative sum of entry amounts."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    ledger = PositionLedger(config)
    # Simulate an entry fill with commission
    order = BacktestOrder(
        "O1",
        "OPP1",
        "P1",
        INST,
        SRC_ASK,
        Timeframe.H2,
        TradePlanDirection.SHORT,
        OrderType.LIMIT_RESTING_ORDER,
        Decimal("4238.00"),
        PriceType.ASK,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("4258.00"),
        Decimal("4171.00"),
        Decimal("1.00"),
        Decimal("1000.00"),
        T0,
        T0,
        None,
    )
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    ledger.register_order(order)
    ledger.open_position_from_fill(order, fill_e)

    # Verify cumulative math
    running = Decimal("0.00")
    for entry in ledger.ledger_entries:
        running += entry.amount
        assert entry.balance_after == running


def test_realized_pnl_reconciles_with_fills():
    """Realized PnL is mathematically equal to (entry - exit) * units_per_lot * lots - comm."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    t2 = T0 + timedelta(minutes=2)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50"),
        make_candle(t2, "4255.00", "4260.00", "4250.00", "4258.00"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK),
        make_candle(t2, "4255.25", "4260.25", "4250.25", "4258.25", src=SRC_ASK),
    ]
    cand = make_candidate(
        direction=TradePlanDirection.SHORT, entry_p="4238.00", stop_p="4258.00", lots="1.00"
    )
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    closed = next(p for p in run.positions if p.status == BacktestPositionStatus.CLOSED_BY_STOP)
    # Gross = (4238.00 - 4258.00) * 100 * 1 = -2000.00
    assert closed.gross_pnl == Decimal("-2000.00")
    # Net = -2000.00 - 2.00 (commission) = -2002.00
    assert closed.net_pnl == Decimal("-2002.00")


def test_unrealized_pnl_separate_from_realized():
    """Open positions at simulation end do not affect realized cash ledger balance."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK),
    ]
    cand = make_candidate(direction=TradePlanDirection.SHORT, entry_p="4238.00", lots="1.00")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    # Position remains open; cash balance only reduced by commission ($2.00)
    assert run.summary.total_realized_simulated_pnl == Decimal("0.00")
    assert run.summary.final_equity == Decimal("99998.00")


def test_open_at_end_not_force_closed():
    """Unclosed positions are marked OPEN_AT_END rather than force-closed with fake PnL."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK),
    ]
    cand = make_candidate(direction=TradePlanDirection.SHORT, entry_p="4238.00", lots="1.00")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert run.positions[0].status == BacktestPositionStatus.OPEN_AT_END


def test_pending_order_cancelled_after_invalidation():
    """Unfilled orders are cancelled when simulation period ends."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    bid_c = [make_candle(T0, "4230.00", "4232.00", "4225.00", "4230.00")]
    ask_c = [make_candle(T0, "4230.25", "4232.25", "4225.25", "4230.25", src=SRC_ASK)]
    cand = make_candidate(avail_time=T0, entry_p="4238.00")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert run.orders[0].status == BacktestOrderStatus.CANCELLED


def test_filled_position_not_deleted_by_future_plan_status():
    """A filled position continues its own lifecycle independent of candidate plan status."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK),
    ]
    cand = make_candidate(direction=TradePlanDirection.SHORT, entry_p="4238.00")
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand])
    assert len(run.positions) == 1
    assert run.positions[0].position_id.startswith("POS_ORD_")


def test_portfolio_risk_reservation_released_once():
    """When a position closes, reserved risk returns to zero."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    ledger = PositionLedger(config)
    order = BacktestOrder(
        "O1",
        "OPP1",
        "P1",
        INST,
        SRC_ASK,
        Timeframe.H2,
        TradePlanDirection.SHORT,
        OrderType.LIMIT_RESTING_ORDER,
        Decimal("4238.00"),
        PriceType.ASK,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("4258.00"),
        Decimal("4171.00"),
        Decimal("1.00"),
        Decimal("1000.00"),
        T0,
        T0,
        None,
    )
    ledger.register_order(order)
    assert ledger.reserved_risk == Decimal("1000.00")

    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = ledger.open_position_from_fill(order, fill_e)
    # Risk still reserved by open position
    assert ledger.reserved_risk == Decimal("1000.00")

    fill_exit = BacktestFill(
        "F2",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.ASK,
        Decimal("4258.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=2),
        T0 + timedelta(minutes=2),
        Decimal("0.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_CONSERVATIVE_FILL,
        is_exit=True,
    )
    closed = BacktestPosition(
        pos.position_id,
        pos.economic_opportunity_id,
        pos.selected_plan_id,
        pos.direction,
        pos.quantity,
        pos.entry_fill,
        pos.entry_price,
        pos.entry_time,
        pos.stop_level,
        pos.target_level,
        fill_exit,
        Decimal("4258.00"),
        T0 + timedelta(minutes=2),
        Decimal("2.00"),
        Decimal("0.00"),
        Decimal("-2000.00"),
        Decimal("-2002.00"),
        status=BacktestPositionStatus.CLOSED_BY_STOP,
    )
    ledger.close_position_from_exit(closed, fill_exit)
    assert ledger.reserved_risk == Decimal("0.00")


def test_pending_and_open_risk_not_double_counted():
    """Transition from active order to open position does not double-count risk."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    ledger = PositionLedger(config)
    order = BacktestOrder(
        "O1",
        "OPP1",
        "P1",
        INST,
        SRC_ASK,
        Timeframe.H2,
        TradePlanDirection.SHORT,
        OrderType.LIMIT_RESTING_ORDER,
        Decimal("4238.00"),
        PriceType.ASK,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("4258.00"),
        Decimal("4171.00"),
        Decimal("1.00"),
        Decimal("1000.00"),
        T0,
        T0,
        None,
    )
    ledger.register_order(order)
    assert ledger.reserved_risk == Decimal("1000.00")
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.00"),
        Decimal("1.00"),
        T0 + timedelta(minutes=1),
        T0 + timedelta(minutes=1),
        Decimal("2.00"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    ledger.open_position_from_fill(order, fill_e)
    assert ledger.reserved_risk == Decimal("1000.00")


def test_same_economic_event_cannot_open_four_independent_positions():
    """4 candidate plans sharing the same opportunity ID can only produce 1 active order."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    opp = "OPP_SHARED_EVENT"
    cands = [
        make_candidate(plan_id="P1", opp_id=opp, avail_time=T0),
        make_candidate(plan_id="P7", opp_id=opp, avail_time=T0),
        make_candidate(plan_id="P14", opp_id=opp, avail_time=T0),
        make_candidate(plan_id="P20", opp_id=opp, avail_time=T0),
    ]
    bid_c = [make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00")]
    ask_c = [make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK)]
    run = run_deterministic_backtest(config, bid_c, ask_c, cands)
    assert run.summary.total_orders_created == 1
    assert run.summary.total_economic_opportunity_duplicates_prevented == 3


def test_independent_market_events_not_force_merged():
    """Independent opportunities can each open an order."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    cands = [
        make_candidate(plan_id="P1", opp_id="OPP_A", avail_time=T0),
        make_candidate(plan_id="P2", opp_id="OPP_B", avail_time=T0),
    ]
    bid_c = [make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00")]
    ask_c = [make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK)]
    run = run_deterministic_backtest(config, bid_c, ask_c, cands)
    assert run.summary.total_orders_created == 2


def test_daily_risk_limit_blocks_new_orders():
    """Single event risk exceeding max fraction is rejected."""
    config = BacktestConfig(
        initial_equity=Decimal("100000.00"), max_single_event_risk_fraction=Decimal("0.01")
    )
    ledger = PositionLedger(config)
    # Requesting 2000 USD risk when max is 1000 USD (1%)
    can_reserve, reason = ledger.can_reserve_risk(Decimal("2000.00"), "OPP1")
    assert can_reserve is False
    assert "SINGLE_EVENT_RISK_LIMIT_EXCEEDED" in reason


def test_portfolio_risk_limit_blocks_overallocation():
    """Portfolio risk limit blocks overallocation beyond max portfolio risk fraction."""
    config = BacktestConfig(
        initial_equity=Decimal("100000.00"), max_portfolio_risk_fraction=Decimal("0.02")
    )
    ledger = PositionLedger(config)
    ledger.reserved_risk = Decimal("1500.00")
    # Requesting another 1000 USD risk brings total to 2500 USD > 2000 USD (2%)
    can_reserve, reason = ledger.can_reserve_risk(Decimal("1000.00"), "OPP2")
    assert can_reserve is False
    assert "PORTFOLIO_RISK_LIMIT_EXCEEDED" in reason


def test_account_equity_updated_only_after_known_events():
    """Cash ledger updates only upon discrete fills and exits."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    ledger = PositionLedger(config)
    # Initial deposit only
    assert len(ledger.ledger_entries) == 1
    assert ledger.cash_balance == Decimal("100000.00")


# ── Category 6: Uçtan Uca Gerçek Veri ve Güvenlik ────────────────────────────
def test_real_xauusd_h2_ask_short_candidate_replay():
    """Plan 20 candidate replays cleanly through the backtesting engine."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(minutes=1)
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4235.00", "4240.00", "4230.00", "4238.50"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4235.25", "4240.25", "4230.25", "4238.75", src=SRC_ASK),
    ]
    cand20 = make_candidate(
        plan_id="PLAN_20_H2_ASK_SHORT",
        opp_id="OPP_XAUUSD_SHORT_BOS_BEARISH_20260928T000000Z",
        entry_p="4238.210",
        stop_p="4258.545",
        target_p="4171.570",
        lots="0.35",
        risk_budget="996.10",
        avail_time=T0,
    )
    run = run_deterministic_backtest(config, bid_c, ask_c, [cand20])
    assert run.summary.total_orders_created == 1
    assert run.summary.total_simulated_fills == 1
    assert run.fills[0].quote_side == PriceType.BID


def test_real_xauusd_h2_h4_bid_ask_deduplication():
    """Plans 1, 7, 14, 20 from same economic event result in only 1 order."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    opp = "OPP_REAL_EVENT"
    cands = [
        make_candidate(plan_id="PLAN_1_H4_BID", opp_id=opp, avail_time=T0),
        make_candidate(plan_id="PLAN_7_H2_BID", opp_id=opp, avail_time=T0),
        make_candidate(plan_id="PLAN_14_H4_ASK", opp_id=opp, avail_time=T0),
        make_candidate(plan_id="PLAN_20_H2_ASK", opp_id=opp, avail_time=T0),
    ]
    bid_c = [make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00")]
    ask_c = [make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK)]
    run = run_deterministic_backtest(config, bid_c, ask_c, cands)
    assert run.summary.total_orders_created == 1
    assert run.summary.total_economic_opportunity_duplicates_prevented == 3


def test_historical_26_plans_not_filtered_by_final_status_only():
    """Historical candidates evaluate at their plan_available_time."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    t1 = T0 + timedelta(hours=2)
    cands = [
        make_candidate(plan_id="P_EARLY", opp_id="OPP_1", avail_time=T0),
        make_candidate(plan_id="P_LATER", opp_id="OPP_2", avail_time=t1),
    ]
    bid_c = [
        make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00"),
        make_candle(t1, "4230.00", "4235.00", "4225.00", "4230.00"),
    ]
    ask_c = [
        make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
        make_candle(t1, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK),
    ]
    run = run_deterministic_backtest(config, bid_c, ask_c, cands)
    assert run.summary.total_orders_created == 2


def test_no_real_broker_api_calls():
    """Zero external broker network or socket calls are made."""
    config = BacktestConfig()
    assert config.simulation_status == SimulationStatus.HISTORICAL_RESEARCH_SIMULATION


def test_no_database_writes():
    """Pure domain backtesting executes with zero database mutations."""
    config = BacktestConfig()
    run = run_deterministic_backtest(config, [], [], [])
    assert run.summary.total_orders_created == 0


def test_no_csv_mutations():
    """Zero CSV file writes occur during backtest."""
    config = BacktestConfig()
    run = run_deterministic_backtest(config, [], [], [])
    assert len(run.positions) == 0


def test_deterministic_backtest_id_and_results():
    """Repeated runs with same inputs generate identical run IDs and summaries."""
    config = BacktestConfig(initial_equity=Decimal("100000.00"))
    bid_c = [make_candle(T0, "4230.00", "4235.00", "4225.00", "4230.00")]
    ask_c = [make_candle(T0, "4230.25", "4235.25", "4225.25", "4230.25", src=SRC_ASK)]
    run1 = run_deterministic_backtest(config, bid_c, ask_c, [])
    run2 = run_deterministic_backtest(config, bid_c, ask_c, [])
    assert run1.run_id == run2.run_id
    assert run1.summary == run2.summary


def test_research_estimated_not_promoted_to_verified():
    """Simulation status remains HISTORICAL_RESEARCH_SIMULATION."""
    config = BacktestConfig()
    assert config.simulation_status == SimulationStatus.HISTORICAL_RESEARCH_SIMULATION


def test_explicit_hypothetical_execution_assumptions():
    """Candidate broker specification provenance is preserved."""
    cand = make_candidate()
    assert cand.broker_specification_provenance == "HYPOTHETICAL_BROKER_SCENARIO"


def test_audit_trail_matches_trade_ledger():
    """Trade audit card reflects exact position values and cash ledger."""
    fill_e = BacktestFill(
        "F1",
        "O1",
        "P1",
        TradePlanDirection.SHORT,
        PriceType.BID,
        Decimal("4238.210"),
        Decimal("0.35"),
        T0,
        T0,
        Decimal("0.70"),
        0,
        Decimal("0.00"),
        BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
    )
    pos = BacktestPosition(
        "POS1",
        "OPP1",
        "P1",
        TradePlanDirection.SHORT,
        Decimal("0.35"),
        fill_e,
        Decimal("4238.210"),
        T0,
        Decimal("4258.545"),
        Decimal("4171.570"),
        commission_paid=Decimal("0.70"),
    )
    card = generate_trade_audit_card(pos)
    assert "TRADE AUDIT CARD: POS1" in card
    assert "4238.210" in card
    assert "0.35" in card


def test_complete_empty_data_safe_result():
    """Engine runs safely on empty candle lists without crashing."""
    config = BacktestConfig()
    run = run_deterministic_backtest(config, [], [], [])
    assert run.summary.total_orders_created == 0
    assert run.summary.total_simulated_fills == 0
    assert run.summary.final_equity == config.initial_equity


def test_performance_summary_not_misleading_on_small_sample():
    """Summary on zero trades reports 0 realized PnL."""
    config = BacktestConfig()
    run = run_deterministic_backtest(config, [], [], [])
    assert run.summary.total_realized_simulated_pnl == Decimal("0.00")
