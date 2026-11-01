"""Position ledger and cash accounting for deterministic backtesting.

Maintains immutable cash transactions, running equity, portfolio risk reservations,
and position lifecycle transitions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from traderlab.domain.backtesting.backtest_models import (
    BacktestCashLedgerEntry,
    BacktestConfig,
    BacktestFill,
    BacktestOrder,
    BacktestOrderStatus,
    BacktestPortfolioSnapshot,
    BacktestPosition,
    BacktestPositionStatus,
)


class PositionLedger:
    """Stateful simulation ledger maintaining positions, cash transactions, and risk reservations."""

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.cash_balance = config.initial_equity
        self.reserved_risk = Decimal("0.00")
        self.ledger_entries: list[BacktestCashLedgerEntry] = []
        self.positions: list[BacktestPosition] = []
        self.orders: list[BacktestOrder] = []
        self.active_orders: dict[str, BacktestOrder] = {}
        self.open_positions: dict[str, BacktestPosition] = {}
        self.opportunity_active_map: dict[str, str] = {}  # opp_id -> active order/pos id
        self._entry_counter: int = 0

        # Initial deposit entry
        self._record_cash_entry(
            timestamp_utc=config.start_time_utc or datetime(2026, 1, 1, tzinfo=UTC),
            entry_type="DEPOSIT",
            amount=config.initial_equity,
            reference_id="INIT_CAPITAL",
            description=f"Initial capital deposit of {config.initial_equity} {config.account_currency}",
        )

    def _record_cash_entry(
        self,
        timestamp_utc: datetime,
        entry_type: str,
        amount: Decimal,
        reference_id: str,
        description: str,
    ) -> BacktestCashLedgerEntry:
        self._entry_counter += 1
        entry_id = f"TX_{self._entry_counter:06d}"
        balance_after = self.cash_balance
        entry = BacktestCashLedgerEntry(
            entry_id=entry_id,
            timestamp_utc=timestamp_utc,
            entry_type=entry_type,
            amount=amount,
            balance_after=balance_after,
            reference_id=reference_id,
            description=description,
        )
        self.ledger_entries.append(entry)
        return entry

    def can_reserve_risk(self, risk_budget: Decimal, opportunity_id: str) -> tuple[bool, str]:
        """Check if risk budget can be allocated under portfolio and opportunity limits."""
        # Check opportunity deduplication:
        if opportunity_id in self.opportunity_active_map:
            return (
                False,
                f"OPPORTUNITY_ALREADY_ACTIVE:{self.opportunity_active_map[opportunity_id]}",
            )

        max_portfolio_risk = self.config.initial_equity * self.config.max_portfolio_risk_fraction
        if self.reserved_risk + risk_budget > max_portfolio_risk:
            return False, "PORTFOLIO_RISK_LIMIT_EXCEEDED"

        max_event_risk = self.config.initial_equity * self.config.max_single_event_risk_fraction
        if risk_budget > max_event_risk:
            return False, "SINGLE_EVENT_RISK_LIMIT_EXCEEDED"

        return True, "APPROVED"

    def register_order(self, order: BacktestOrder) -> None:
        """Register a new active order and reserve its risk budget."""
        self.orders.append(order)
        if order.status == BacktestOrderStatus.ACTIVE:
            self.active_orders[order.order_id] = order
            self.opportunity_active_map[order.economic_opportunity_id] = order.order_id
            self.reserved_risk += order.risk_budget

    def release_order_risk(self, order_id: str) -> None:
        """Release risk reserved for an order when cancelled, expired, or rejected."""
        order = self.active_orders.pop(order_id, None)
        if order:
            self.reserved_risk = max(Decimal("0.00"), self.reserved_risk - order.risk_budget)
            if self.opportunity_active_map.get(order.economic_opportunity_id) == order_id:
                del self.opportunity_active_map[order.economic_opportunity_id]

    def open_position_from_fill(
        self, order: BacktestOrder, fill: BacktestFill
    ) -> BacktestPosition:
        """Create a new open position upon order fill, deduct commission, and transition risk."""
        # Deduct commission from cash balance
        self.cash_balance -= fill.commission_paid
        self._record_cash_entry(
            timestamp_utc=fill.fill_time_utc,
            entry_type="COMMISSION",
            amount=-fill.commission_paid,
            reference_id=fill.fill_id,
            description=f"Round-trip commission paid for {order.quantity} lots",
        )

        pos_id = f"POS_{order.order_id}"
        pos = BacktestPosition(
            position_id=pos_id,
            economic_opportunity_id=order.economic_opportunity_id,
            selected_plan_id=order.selected_plan_id,
            direction=order.direction,
            quantity=order.quantity,
            entry_fill=fill,
            entry_price=fill.fill_price,
            entry_time=fill.fill_time_utc,
            stop_level=order.stop_loss_price,
            target_level=order.take_profit_price,
            commission_paid=fill.commission_paid,
            slippage_cost=fill.slippage_cost,
            account_currency=self.config.account_currency,
            status=BacktestPositionStatus.OPEN,
            source_event_ids=order.source_event_ids,
        )

        # Transition order to filled
        self.active_orders.pop(order.order_id, None)
        self.open_positions[pos_id] = pos
        self.opportunity_active_map[order.economic_opportunity_id] = pos_id
        self.positions.append(pos)
        return pos

    def close_position_from_exit(
        self,
        closed_position: BacktestPosition,
        exit_fill: BacktestFill,
    ) -> None:
        """Close an open position, realize PnL into cash balance, and release risk."""
        pos_id = closed_position.position_id
        self.open_positions.pop(pos_id, None)

        # Update position in list
        for idx, p in enumerate(self.positions):
            if p.position_id == pos_id:
                self.positions[idx] = closed_position
                break

        # Realize gross PnL minus exit slippage into cash balance
        # (Entry commission was already deducted at entry fill)
        cash_credit = closed_position.gross_pnl - exit_fill.slippage_cost
        self.cash_balance += cash_credit

        self._record_cash_entry(
            timestamp_utc=exit_fill.fill_time_utc,
            entry_type="REALIZED_PNL",
            amount=cash_credit,
            reference_id=closed_position.position_id,
            description=f"Realized PnL from position {pos_id} (net: {closed_position.net_pnl})",
        )

        # Release risk reservation
        # Find original order risk
        order_risk = Decimal("0.00")
        for o in self.orders:
            if o.order_id == closed_position.entry_fill.order_id:
                order_risk = o.risk_budget
                break
        self.reserved_risk = max(Decimal("0.00"), self.reserved_risk - order_risk)

        if self.opportunity_active_map.get(closed_position.economic_opportunity_id) == pos_id:
            del self.opportunity_active_map[closed_position.economic_opportunity_id]

    def finalize_period(self, end_time_utc: datetime) -> None:
        """Mark remaining open positions as OPEN_AT_END and cancel unfulfilled resting orders."""
        for pos_id, pos in list(self.open_positions.items()):
            open_at_end_pos = BacktestPosition(
                position_id=pos.position_id,
                economic_opportunity_id=pos.economic_opportunity_id,
                selected_plan_id=pos.selected_plan_id,
                direction=pos.direction,
                quantity=pos.quantity,
                entry_fill=pos.entry_fill,
                entry_price=pos.entry_price,
                entry_time=pos.entry_time,
                stop_level=pos.stop_level,
                target_level=pos.target_level,
                commission_paid=pos.commission_paid,
                slippage_cost=pos.slippage_cost,
                gross_pnl=Decimal("0.00"),
                net_pnl=Decimal("0.00"),
                account_currency=pos.account_currency,
                status=BacktestPositionStatus.OPEN_AT_END,
                source_event_ids=pos.source_event_ids,
            )
            for idx, p in enumerate(self.positions):
                if p.position_id == pos_id:
                    self.positions[idx] = open_at_end_pos
                    break

        for o_id, order in list(self.active_orders.items()):
            cancelled_order = BacktestOrder(
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
                status=BacktestOrderStatus.CANCELLED,
                rejection_reason="SIMULATION_END_REACHED",
                source_event_ids=order.source_event_ids,
            )
            for idx, o in enumerate(self.orders):
                if o.order_id == o_id:
                    self.orders[idx] = cancelled_order
                    break
            self.release_order_risk(o_id)

    def create_snapshot(self, as_of_utc: datetime) -> BacktestPortfolioSnapshot:
        """Capture an immutable point-in-time snapshot of the portfolio."""
        max_portfolio_risk = self.config.initial_equity * self.config.max_portfolio_risk_fraction
        free_capacity = max(Decimal("0.00"), max_portfolio_risk - self.reserved_risk)
        return BacktestPortfolioSnapshot(
            snapshot_time_utc=as_of_utc,
            cash_balance=self.cash_balance,
            equity=self.cash_balance,  # Closed equity (unrealized mark-to-market separate)
            reserved_risk=self.reserved_risk,
            free_risk_capacity=free_capacity,
            open_position_count=len(self.open_positions),
            active_order_count=len(self.active_orders),
        )
