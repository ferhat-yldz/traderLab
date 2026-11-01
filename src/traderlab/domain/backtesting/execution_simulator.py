"""Execution simulator for discrete M1 quote matching, orders, fills, and stop/target evaluation.

Applies strict execution quote sides (SHORT: Entry on BID, Exits on ASK; LONG: Entry on ASK, Exits on BID),
conservative slippage models, intrabar ambiguity handling, and gap detection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from traderlab.core.types import PriceType
from traderlab.domain.backtesting.backtest_models import (
    BacktestConfig,
    BacktestFill,
    BacktestFillQuality,
    BacktestOrder,
    BacktestOrderStatus,
    BacktestPosition,
    BacktestPositionStatus,
    IntrabarAmbiguityPolicy,
    MismatchedQuoteTimestampError,
    NegativeSpreadError,
    OrderType,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.trade_plans.ict_trade_plan import TradePlanDirection


@dataclass(frozen=True, slots=True)
class AlignedM1Quote:
    """Pair of synchronously aligned closed M1 BID and ASK bars."""

    timestamp_utc: datetime
    bid: Candle
    ask: Candle

    def __post_init__(self) -> None:
        if self.bid.open_time != self.ask.open_time:
            raise MismatchedQuoteTimestampError(
                f"M1 BID time {self.bid.open_time} != ASK time {self.ask.open_time}"
            )
        if self.bid.close > self.ask.close or self.bid.open > self.ask.open:
            raise NegativeSpreadError(
                f"Negative spread detected at {self.timestamp_utc}: BID > ASK"
            )


def align_m1_candles(
    bid_candles: list[Candle],
    ask_candles: list[Candle],
) -> dict[datetime, AlignedM1Quote]:
    """Index and align M1 BID and ASK candle streams strictly by UTC open timestamp."""
    bid_by_time = {c.open_time: c for c in bid_candles if c.is_closed}
    ask_by_time = {c.open_time: c for c in ask_candles if c.is_closed}

    common_times = sorted(set(bid_by_time.keys()) & set(ask_by_time.keys()))
    aligned: dict[datetime, AlignedM1Quote] = {}
    for t in common_times:
        aligned[t] = AlignedM1Quote(
            timestamp_utc=t,
            bid=bid_by_time[t],
            ask=ask_by_time[t],
        )
    return aligned


def evaluate_order_execution(
    order: BacktestOrder,
    quote: AlignedM1Quote,
    config: BacktestConfig,
) -> tuple[BacktestOrder, BacktestFill | None]:
    """Evaluate whether an active resting or market order is filled on the current M1 bar.

    Enforces:
    - No fill on signal bar (quote.timestamp_utc must be >= order.eligible_from).
    - Correct quote side (SHORT: BID, LONG: ASK).
    - Conservative slippage.
    """
    if order.status != BacktestOrderStatus.ACTIVE:
        return order, None

    # Order eligibility check: cannot fill before eligible_from
    if quote.timestamp_utc < order.eligible_from:
        return order, None

    # Expiry check
    if order.expiry_time and quote.timestamp_utc > order.expiry_time:
        expired_order = BacktestOrder(
            order_id=order.order_id,
            economic_opportunity_id=order.economic_opportunity_id,
            selected_plan_id=order.selected_plan_id,
            instrument_id=order.instrument_id,
            source_id=order.source_id,
            timeframe=order.timeframe,
            direction=order.direction,
            order_type=order.order_type,
            analytical_trigger_price=order.analytical_trigger_price,
            analytical_trigger_side=order.analytical_trigger_side,
            required_execution_side=order.required_execution_side,
            limit_price=order.limit_price,
            stop_loss_price=order.stop_loss_price,
            take_profit_price=order.take_profit_price,
            quantity=order.quantity,
            risk_budget=order.risk_budget,
            submitted_at=order.submitted_at,
            eligible_from=order.eligible_from,
            expiry_time=order.expiry_time,
            status=BacktestOrderStatus.EXPIRED,
            rejection_reason="ORDER_EXPIRED_MAX_WAITING_BARS",
            source_event_ids=order.source_event_ids,
        )
        return expired_order, None

    slippage_amount = Decimal(config.slippage_ticks) * config.tick_size
    comm_amount = config.commission_per_lot * order.quantity

    if order.order_type == OrderType.MARKET_NEXT_ELIGIBLE_OPEN:
        if order.direction == TradePlanDirection.SHORT:
            # Short entry on BID
            raw_price = quote.bid.open
            fill_price = raw_price - slippage_amount  # adverse slippage for sell
            quote_side = PriceType.BID
        else:
            # Long entry on ASK
            raw_price = quote.ask.open
            fill_price = raw_price + slippage_amount  # adverse slippage for buy
            quote_side = PriceType.ASK

        fill = BacktestFill(
            fill_id=f"FILL_ENTRY_{order.order_id}",
            order_id=order.order_id,
            selected_plan_id=order.selected_plan_id,
            direction=order.direction,
            quote_side=quote_side,
            fill_price=fill_price,
            quantity=order.quantity,
            fill_time_utc=quote.timestamp_utc,
            bar_open_time_utc=quote.timestamp_utc,
            commission_paid=comm_amount,
            slippage_ticks=config.slippage_ticks,
            slippage_cost=slippage_amount * config.units_per_lot * order.quantity,
            fill_quality=BacktestFillQuality.SIMULATED_NEXT_OPEN,
            is_exit=False,
            source_event_ids=order.source_event_ids,
        )
        filled_order = BacktestOrder(
            order_id=order.order_id,
            economic_opportunity_id=order.economic_opportunity_id,
            selected_plan_id=order.selected_plan_id,
            instrument_id=order.instrument_id,
            source_id=order.source_id,
            timeframe=order.timeframe,
            direction=order.direction,
            order_type=order.order_type,
            analytical_trigger_price=order.analytical_trigger_price,
            analytical_trigger_side=order.analytical_trigger_side,
            required_execution_side=order.required_execution_side,
            limit_price=order.limit_price,
            stop_loss_price=order.stop_loss_price,
            take_profit_price=order.take_profit_price,
            quantity=order.quantity,
            risk_budget=order.risk_budget,
            submitted_at=order.submitted_at,
            eligible_from=order.eligible_from,
            expiry_time=order.expiry_time,
            status=BacktestOrderStatus.FILLED,
            source_event_ids=order.source_event_ids,
        )
        return filled_order, fill

    elif order.order_type == OrderType.LIMIT_RESTING_ORDER:
        limit_p = order.limit_price or order.analytical_trigger_price
        is_touched = False

        if order.direction == TradePlanDirection.SHORT:
            # Selling on BID: High of BID candle must reach or exceed limit
            if quote.bid.high >= limit_p:
                is_touched = True
                quote_side = PriceType.BID
                fill_price = limit_p - slippage_amount
        else:
            # Buying on ASK: Low of ASK candle must reach or be below limit
            if quote.ask.low <= limit_p:
                is_touched = True
                quote_side = PriceType.ASK
                fill_price = limit_p + slippage_amount

        if is_touched:
            fill = BacktestFill(
                fill_id=f"FILL_ENTRY_{order.order_id}",
                order_id=order.order_id,
                selected_plan_id=order.selected_plan_id,
                direction=order.direction,
                quote_side=quote_side,
                fill_price=fill_price,
                quantity=order.quantity,
                fill_time_utc=quote.timestamp_utc,
                bar_open_time_utc=quote.timestamp_utc,
                commission_paid=comm_amount,
                slippage_ticks=config.slippage_ticks,
                slippage_cost=slippage_amount * config.units_per_lot * order.quantity,
                fill_quality=BacktestFillQuality.SIMULATED_LIMIT_TOUCH_ASSUMPTION,
                is_exit=False,
                source_event_ids=order.source_event_ids,
            )
            filled_order = BacktestOrder(
                order_id=order.order_id,
                economic_opportunity_id=order.economic_opportunity_id,
                selected_plan_id=order.selected_plan_id,
                instrument_id=order.instrument_id,
                source_id=order.source_id,
                timeframe=order.timeframe,
                direction=order.direction,
                order_type=order.order_type,
                analytical_trigger_price=order.analytical_trigger_price,
                analytical_trigger_side=order.analytical_trigger_side,
                required_execution_side=order.required_execution_side,
                limit_price=order.limit_price,
                stop_loss_price=order.stop_loss_price,
                take_profit_price=order.take_profit_price,
                quantity=order.quantity,
                risk_budget=order.risk_budget,
                submitted_at=order.submitted_at,
                eligible_from=order.eligible_from,
                expiry_time=order.expiry_time,
                status=BacktestOrderStatus.FILLED,
                source_event_ids=order.source_event_ids,
            )
            return filled_order, fill

    return order, None


def evaluate_position_exit(
    position: BacktestPosition,
    quote: AlignedM1Quote,
    config: BacktestConfig,
) -> tuple[BacktestPosition, BacktestFill | None]:
    """Evaluate whether an open position hits stop-loss or take-profit on the current M1 bar.

    Enforces:
    - Position must be open and quote time must be strictly after entry fill time.
    - SHORT exits evaluated on ASK quotes; LONG exits evaluated on BID quotes.
    - Intrabar ambiguity policy when both stop and target are touched.
    - Gap loss detection (worse open price than stop level).
    """
    if position.status != BacktestPositionStatus.OPEN:
        return position, None

    # Exits cannot trigger on the entry bar itself if entered at bar close/open
    if quote.timestamp_utc <= position.entry_time:
        return position, None

    slippage_amount = Decimal(config.slippage_ticks) * config.tick_size
    stop_hit = False
    target_hit = False
    stop_gap = False
    raw_exit_price = Decimal("0.00")

    if position.direction == TradePlanDirection.SHORT:
        # SHORT exit: buy to cover on ASK
        quote_side = PriceType.ASK
        ask_bar = quote.ask

        if ask_bar.high >= position.stop_level:
            stop_hit = True
            if ask_bar.open > position.stop_level:
                # Gap open above stop loss
                stop_gap = True
                raw_exit_price = ask_bar.open
            else:
                raw_exit_price = position.stop_level

        if ask_bar.low <= position.target_level:
            target_hit = True

    else:
        # LONG exit: sell to close on BID
        quote_side = PriceType.BID
        bid_bar = quote.bid

        if bid_bar.low <= position.stop_level:
            stop_hit = True
            if bid_bar.open < position.stop_level:
                # Gap open below stop loss
                stop_gap = True
                raw_exit_price = bid_bar.open
            else:
                raw_exit_price = position.stop_level

        if bid_bar.high >= position.target_level:
            target_hit = True

    # Intrabar ambiguity resolution: both stop and target triggered
    is_ambiguous = stop_hit and target_hit
    final_exit_status: BacktestPositionStatus = position.status

    if is_ambiguous and config.intrabar_policy in (
        IntrabarAmbiguityPolicy.STOP_FIRST_CONSERVATIVE,
        IntrabarAmbiguityPolicy.MARK_AMBIGUOUS_NO_DETERMINISTIC_WIN,
    ):
        stop_hit = True
        target_hit = False
        final_exit_status = BacktestPositionStatus.CLOSED_BY_STOP

    if stop_hit:
        final_exit_status = BacktestPositionStatus.CLOSED_BY_STOP
        # Adverse slippage on stop exit:
        # SHORT cover: price increases (+ slippage)
        # LONG sell: price decreases (- slippage)
        if position.direction == TradePlanDirection.SHORT:
            final_price = raw_exit_price + slippage_amount
        else:
            final_price = raw_exit_price - slippage_amount

        exit_fill = BacktestFill(
            fill_id=f"FILL_EXIT_STOP_{position.position_id}",
            order_id=position.entry_fill.order_id,
            selected_plan_id=position.selected_plan_id,
            direction=position.direction,
            quote_side=quote_side,
            fill_price=final_price,
            quantity=position.quantity,
            fill_time_utc=quote.timestamp_utc,
            bar_open_time_utc=quote.timestamp_utc,
            commission_paid=Decimal("0.00"),  # Full round-trip already accounted at entry
            slippage_ticks=config.slippage_ticks,
            slippage_cost=slippage_amount * config.units_per_lot * position.quantity,
            fill_quality=BacktestFillQuality.SIMULATED_CONSERVATIVE_FILL,
            is_exit=True,
            source_event_ids=position.source_event_ids,
        )

        # Calculate PnL
        if position.direction == TradePlanDirection.SHORT:
            gross = (position.entry_price - final_price) * config.units_per_lot * position.quantity
        else:
            gross = (final_price - position.entry_price) * config.units_per_lot * position.quantity

        total_slippage = (
            position.slippage_cost + slippage_amount * config.units_per_lot * position.quantity
        )
        net = gross - position.commission_paid - total_slippage

        closed_position = BacktestPosition(
            position_id=position.position_id,
            economic_opportunity_id=position.economic_opportunity_id,
            selected_plan_id=position.selected_plan_id,
            direction=position.direction,
            quantity=position.quantity,
            entry_fill=position.entry_fill,
            entry_price=position.entry_price,
            entry_time=position.entry_time,
            stop_level=position.stop_level,
            target_level=position.target_level,
            exit_fill=exit_fill,
            exit_price=final_price,
            exit_time=quote.timestamp_utc,
            commission_paid=position.commission_paid,
            slippage_cost=total_slippage,
            gross_pnl=gross,
            net_pnl=net,
            account_currency=position.account_currency,
            status=final_exit_status,
            is_ambiguous_intrabar=is_ambiguous,
            gap_loss_overrun=stop_gap,
            source_event_ids=position.source_event_ids,
        )
        return closed_position, exit_fill

    elif target_hit:
        final_exit_status = BacktestPositionStatus.CLOSED_BY_TARGET
        final_price = position.target_level

        exit_fill = BacktestFill(
            fill_id=f"FILL_EXIT_TARGET_{position.position_id}",
            order_id=position.entry_fill.order_id,
            selected_plan_id=position.selected_plan_id,
            direction=position.direction,
            quote_side=quote_side,
            fill_price=final_price,
            quantity=position.quantity,
            fill_time_utc=quote.timestamp_utc,
            bar_open_time_utc=quote.timestamp_utc,
            commission_paid=Decimal("0.00"),
            slippage_ticks=0,
            slippage_cost=Decimal("0.00"),
            fill_quality=BacktestFillQuality.SIMULATED_CONSERVATIVE_FILL,
            is_exit=True,
            source_event_ids=position.source_event_ids,
        )

        if position.direction == TradePlanDirection.SHORT:
            gross = (position.entry_price - final_price) * config.units_per_lot * position.quantity
        else:
            gross = (final_price - position.entry_price) * config.units_per_lot * position.quantity

        net = gross - position.commission_paid - position.slippage_cost

        closed_position = BacktestPosition(
            position_id=position.position_id,
            economic_opportunity_id=position.economic_opportunity_id,
            selected_plan_id=position.selected_plan_id,
            direction=position.direction,
            quantity=position.quantity,
            entry_fill=position.entry_fill,
            entry_price=position.entry_price,
            entry_time=position.entry_time,
            stop_level=position.stop_level,
            target_level=position.target_level,
            exit_fill=exit_fill,
            exit_price=final_price,
            exit_time=quote.timestamp_utc,
            commission_paid=position.commission_paid,
            slippage_cost=position.slippage_cost,
            gross_pnl=gross,
            net_pnl=net,
            account_currency=position.account_currency,
            status=final_exit_status,
            is_ambiguous_intrabar=False,
            gap_loss_overrun=False,
            source_event_ids=position.source_event_ids,
        )
        return closed_position, exit_fill

    return position, None
