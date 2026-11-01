"""Deterministic Point-in-Time Event-Driven Backtesting Core Engine.

Coordinates chronological progression of M1 BID/ASK market quotes, candidate contracts,
simulated orders, fill matching, stop/target execution, and ledger accounting.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from traderlab.domain.backtesting.backtest_models import (
    BacktestConfig,
    BacktestFill,
    BacktestOrder,
    BacktestOrderStatus,
    BacktestPositionStatus,
    BacktestRun,
    BacktestRunSummary,
)
from traderlab.domain.backtesting.execution_simulator import (
    align_m1_candles,
    evaluate_order_execution,
    evaluate_position_exit,
)
from traderlab.domain.backtesting.position_ledger import PositionLedger
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.strategy_validation.strategy_validation import (
    BacktestCandidateContract,
)


def run_deterministic_backtest(
    config: BacktestConfig,
    bid_candles: list[Candle],
    ask_candles: list[Candle],
    candidate_contracts: Sequence[BacktestCandidateContract],
    *,
    as_of_cutoff_utc: datetime | None = None,
) -> BacktestRun:
    """Execute a fully deterministic event-driven backtest on M1 BID/ASK data.

    Enforces:
    - Point-in-Time causal sequencing.
    - Dual BID/ASK quote side execution (SHORT entry BID, exit ASK; LONG entry ASK, exit BID).
    - No fill on signal bar (eligible_from >= plan_available_time + 1 minute).
    - Conservative intrabar handling.
    - Zero double-counting of spread.
    - Ledger conservation.
    """
    aligned_quotes = align_m1_candles(bid_candles, ask_candles)
    all_times = sorted(aligned_quotes.keys())

    # Filter quotes by configured time window and optional PIT cutoff
    start_t = config.start_time_utc or (
        all_times[0] if all_times else datetime(2026, 1, 1, tzinfo=UTC)
    )
    end_t = (
        as_of_cutoff_utc
        or config.end_time_utc
        or (all_times[-1] if all_times else datetime(2026, 12, 31, tzinfo=UTC))
    )

    filtered_times = [t for t in all_times if start_t <= t <= end_t]
    if config.warmup_bars > 0 and len(filtered_times) > config.warmup_bars:
        eval_times = filtered_times[config.warmup_bars :]
    else:
        eval_times = filtered_times

    # Index candidates by their historical plan_available_time (decision time)
    contracts_by_time: dict[datetime, list[BacktestCandidateContract]] = {}
    for c in candidate_contracts:
        # Ignore candidates outside the PIT knowledge window
        if as_of_cutoff_utc and c.plan_available_time > as_of_cutoff_utc:
            continue
        contracts_by_time.setdefault(c.plan_available_time, []).append(c)

    ledger = PositionLedger(config)
    fills: list[BacktestFill] = []
    snapshots = []

    total_opps = len({c.economic_opportunity_id for c in candidate_contracts})
    selected_opps: set[str] = set()
    orders_created = 0
    orders_rejected = 0
    portfolio_risk_rejections = 0
    duplicate_opps_prevented = 0

    # Chronological simulation loop
    for current_t in eval_times:
        quote = aligned_quotes[current_t]

        # 1. First, evaluate exits on existing open positions (Stop / Take-Profit)
        open_pos_ids = list(ledger.open_positions.keys())
        for pos_id in open_pos_ids:
            pos = ledger.open_positions.get(pos_id)
            if pos is None:
                continue
            updated_pos, exit_fill = evaluate_position_exit(pos, quote, config)
            if exit_fill:
                ledger.close_position_from_exit(updated_pos, exit_fill)
                fills.append(exit_fill)

        # 2. Second, evaluate fills on active resting/market orders
        active_order_ids = list(ledger.active_orders.keys())
        for order_id in active_order_ids:
            order = ledger.active_orders.get(order_id)
            if order is None:
                continue
            updated_order, entry_fill = evaluate_order_execution(order, quote, config)
            if updated_order.status == BacktestOrderStatus.EXPIRED:
                ledger.release_order_risk(order.order_id)
                # Update order in ledger
                for idx, o in enumerate(ledger.orders):
                    if o.order_id == order.order_id:
                        ledger.orders[idx] = updated_order
                        break
            elif entry_fill:
                # Update order in ledger
                for idx, o in enumerate(ledger.orders):
                    if o.order_id == order.order_id:
                        ledger.orders[idx] = updated_order
                        break
                pos = ledger.open_position_from_fill(updated_order, entry_fill)
                fills.append(entry_fill)

        # 3. Third, process any newly arriving trade plan contracts at this exact minute
        new_contracts = contracts_by_time.get(current_t, [])
        for contract in new_contracts:
            # Check portfolio validation status
            if contract.portfolio_validation_status != "PORTFOLIO_VALIDATED":
                orders_rejected += 1
                continue

            # Check risk allocation & deduplication
            can_reserve, reason = ledger.can_reserve_risk(
                contract.risk_budget, contract.economic_opportunity_id
            )
            if not can_reserve:
                orders_rejected += 1
                if "PORTFOLIO_RISK_LIMIT" in reason:
                    portfolio_risk_rejections += 1
                elif "OPPORTUNITY_ALREADY_ACTIVE" in reason:
                    duplicate_opps_prevented += 1
                continue

            # Candidate contract is valid and risk is approved -> Create simulated order
            orders_created += 1
            selected_opps.add(contract.economic_opportunity_id)
            order_id = (
                f"ORD_{contract.selected_plan_id[:12]}_{current_t.strftime('%Y%m%dT%H%M%SZ')}"
            )

            # Strict execution rule: eligible_from must be strictly after the signal bar
            eligible_from = current_t + timedelta(minutes=1)
            tf_seconds = (
                contract.timeframe.value
                if hasattr(contract.timeframe, "value")
                and isinstance(contract.timeframe.value, int)
                else 60
            )
            expiry_time = current_t + timedelta(seconds=config.max_waiting_bars * tf_seconds)

            order = BacktestOrder(
                order_id=order_id,
                economic_opportunity_id=contract.economic_opportunity_id,
                selected_plan_id=contract.selected_plan_id,
                instrument_id=contract.instrument_id,
                source_id=contract.source_id,
                timeframe=contract.timeframe,
                direction=contract.direction,
                order_type=config.default_order_type,
                analytical_trigger_price=contract.analytical_reference_price,
                analytical_trigger_side=contract.analytical_reference_price_type,
                required_execution_side=contract.required_execution_entry_side,
                limit_price=contract.analytical_reference_price,
                stop_loss_price=contract.stop_loss,
                take_profit_price=contract.take_profit,
                quantity=contract.proposed_quantity,
                risk_budget=contract.risk_budget,
                submitted_at=current_t,
                eligible_from=eligible_from,
                expiry_time=expiry_time,
                status=BacktestOrderStatus.ACTIVE,
                source_event_ids=contract.related_event_ids,
            )
            ledger.register_order(order)

        # Capture hourly or daily portfolio snapshot (every 60 minutes)
        if current_t.minute == 0:
            snapshots.append(ledger.create_snapshot(current_t))

    # Finalize backtest run at period end
    final_time = eval_times[-1] if eval_times else end_t
    ledger.finalize_period(final_time)
    snapshots.append(ledger.create_snapshot(final_time))

    # Aggregate quantitative summary
    closed_positions = [
        p
        for p in ledger.positions
        if p.status
        in (
            BacktestPositionStatus.CLOSED_BY_STOP,
            BacktestPositionStatus.CLOSED_BY_TARGET,
            BacktestPositionStatus.CLOSED_BY_POLICY,
        )
    ]
    open_at_end_positions = [
        p for p in ledger.positions if p.status == BacktestPositionStatus.OPEN_AT_END
    ]
    stop_exits = sum(
        1 for p in closed_positions if p.status == BacktestPositionStatus.CLOSED_BY_STOP
    )
    target_exits = sum(
        1 for p in closed_positions if p.status == BacktestPositionStatus.CLOSED_BY_TARGET
    )
    ambiguous_events = sum(1 for p in ledger.positions if p.is_ambiguous_intrabar)
    realized_pnl = sum((p.net_pnl for p in closed_positions), Decimal("0.00"))
    total_costs = sum(
        (p.commission_paid + p.slippage_cost for p in ledger.positions), Decimal("0.00")
    )
    orders_expired = sum(1 for o in ledger.orders if o.status == BacktestOrderStatus.EXPIRED)

    run_id = (
        f"RUN_{hashlib.sha256(f'{final_time}_{config.initial_equity}'.encode()).hexdigest()[:12]}"
    )

    summary = BacktestRunSummary(
        total_historical_setups=len({c.source_setup_id for c in candidate_contracts}),
        total_historical_trade_plans=len(candidate_contracts),
        total_opportunities=total_opps,
        total_selected_opportunities=len(selected_opps),
        total_orders_created=orders_created,
        total_orders_rejected=orders_rejected,
        total_orders_expired=orders_expired,
        total_simulated_fills=len(fills),
        total_simulated_open_positions=len(ledger.positions),
        total_simulated_closed_positions=len(closed_positions),
        total_stop_exits=stop_exits,
        total_target_exits=target_exits,
        total_intrabar_ambiguous_events=ambiguous_events,
        total_open_at_end_positions=len(open_at_end_positions),
        total_realized_simulated_pnl=realized_pnl,
        total_estimated_transaction_costs=total_costs,
        total_portfolio_risk_rejections=portfolio_risk_rejections,
        total_economic_opportunity_duplicates_prevented=duplicate_opps_prevented,
        initial_equity=config.initial_equity,
        final_equity=ledger.cash_balance,
        simulation_status=config.simulation_status,
    )

    return BacktestRun(
        run_id=run_id,
        config=config,
        orders=tuple(ledger.orders),
        fills=tuple(fills),
        positions=tuple(ledger.positions),
        cash_ledger=tuple(ledger.ledger_entries),
        portfolio_snapshots=tuple(snapshots),
        summary=summary,
    )
