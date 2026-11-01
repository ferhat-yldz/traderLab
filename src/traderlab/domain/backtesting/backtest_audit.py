"""Audit trail generation and markdown reporting for deterministic backtest runs.

Produces comprehensive trace cards linking every simulated trade to its
source economic opportunity, setup, orders, fills, and ledger events.
"""

from __future__ import annotations

from traderlab.domain.backtesting.backtest_models import BacktestPosition, BacktestRun


def generate_trade_audit_card(position: BacktestPosition) -> str:
    """Generate a structured Markdown audit card for a single simulated trade position."""
    lines = [
        f"### TRADE AUDIT CARD: {position.position_id}",
        f"- **Economic Opportunity ID:** `{position.economic_opportunity_id}`",
        f"- **Selected Trade Plan ID:** `{position.selected_plan_id}`",
        f"- **Direction:** `{position.direction.value}`",
        f"- **Quantity (Lots):** `{position.quantity}`",
        f"- **Status:** `{position.status.value}`",
        f"- **Entry Time (UTC):** `{position.entry_time.isoformat()}`",
        f"- **Entry Fill Price:** `{position.entry_price}` (Quote: `{position.entry_fill.quote_side.value}`)",
        f"- **Entry Quality:** `{position.entry_fill.fill_quality.value}`",
        f"- **Stop Loss Level:** `{position.stop_level}`",
        f"- **Take Profit Level:** `{position.target_level}`",
    ]

    if position.exit_fill and position.exit_price:
        lines.extend(
            [
                f"- **Exit Time (UTC):** `{position.exit_time.isoformat() if position.exit_time else 'N/A'}`",
                f"- **Exit Fill Price:** `{position.exit_price}` (Quote: `{position.exit_fill.quote_side.value}`)",
                f"- **Exit Quality:** `{position.exit_fill.fill_quality.value}`",
                f"- **Gross PnL ({position.account_currency}):** `{position.gross_pnl:+}`",
                f"- **Commission Paid ({position.account_currency}):** `{position.commission_paid}`",
                f"- **Slippage Cost ({position.account_currency}):** `{position.slippage_cost}`",
                f"- **Net PnL ({position.account_currency}):** `{position.net_pnl:+}`",
                f"- **Intrabar Ambiguity Flag:** `{position.is_ambiguous_intrabar}`",
                f"- **Gap Loss Overrun Flag:** `{position.gap_loss_overrun}`",
            ]
        )
    else:
        lines.append("- **Exit:** Open at simulation end (unrealized mark-to-market separate)")

    return "\n".join(lines)


def format_backtest_run_markdown_report(run: BacktestRun) -> str:
    """Generate a full Markdown audit summary table for a completed BacktestRun."""
    s = run.summary
    lines = [
        f"# BACKTEST RUN AUDIT REPORT: {run.run_id}",
        f"- **Status:** `{s.simulation_status.value}`",
        f"- **Initial Equity:** `{s.initial_equity} {run.config.account_currency}`",
        f"- **Final Equity:** `{s.final_equity} {run.config.account_currency}`",
        f"- **Realized Net PnL:** `{s.total_realized_simulated_pnl:+} {run.config.account_currency}`",
        f"- **Total Transaction Costs:** `{s.total_estimated_transaction_costs} {run.config.account_currency}`",
        "",
        "## Quantitative Performance & Execution Counts",
        "| Metric | Count / Value |",
        "| :--- | :--- |",
        f"| Historical Setups Evaluated | `{s.total_historical_setups}` |",
        f"| Historical Trade Plans Evaluated | `{s.total_historical_trade_plans}` |",
        f"| Total Economic Opportunities | `{s.total_opportunities}` |",
        f"| Selected Opportunities | `{s.total_selected_opportunities}` |",
        f"| Orders Created | `{s.total_orders_created}` |",
        f"| Orders Rejected | `{s.total_orders_rejected}` |",
        f"| Orders Expired | `{s.total_orders_expired}` |",
        f"| Total Simulated Fills | `{s.total_simulated_fills}` |",
        f"| Total Simulated Positions | `{s.total_simulated_open_positions}` |",
        f"| Positions Closed | `{s.total_simulated_closed_positions}` |",
        f"| Stop-Loss Exits | `{s.total_stop_exits}` |",
        f"| Take-Profit Exits | `{s.total_target_exits}` |",
        f"| Intrabar Ambiguous Events | `{s.total_intrabar_ambiguous_events}` |",
        f"| Open Positions at End | `{s.total_open_at_end_positions}` |",
        f"| Portfolio Risk Rejections | `{s.total_portfolio_risk_rejections}` |",
        f"| Duplicate Opportunities Blocked | `{s.total_economic_opportunity_duplicates_prevented}` |",
    ]
    return "\n".join(lines)
