"""Generate exact 26-plan mathematical reconciliation table for Stage 3 audit."""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal

sys.path.insert(0, ".")

from traderlab.domain.risk.ict_risk_management import (
    AccountRiskSnapshot,
    InstrumentRiskSpecification,
    RiskManagementConfig,
    RiskAssessmentStatus,
    StopLossMethod,
    TakeProfitMethod,
    evaluate_trade_plan_risk,
)
from traderlab.core.types import Timeframe, PriceType
from scratch.audit_phase_4e_b_real_data import run_audit

async def main() -> None:
    trade_plans = await run_audit()
    print(f"Loaded {len(trade_plans)} trade plans.")

    # Contract Specification
    spec = InstrumentRiskSpecification(
        instrument_id=trade_plans[0].instrument_id,
        source_id=trade_plans[0].source_id,
        tick_size=Decimal("0.01"),
        pip_size=Decimal("0.10"),
        units_per_lot=Decimal("100"),
        minimum_order_quantity=Decimal("0.01"),
        maximum_order_quantity=Decimal("50.00"),
        quantity_step=Decimal("0.01"),
        quote_currency="USD",
        account_currency="USD",
        commission_per_lot=Decimal("2.00"),
        typical_spread_ticks=25,
    )

    account = AccountRiskSnapshot(
        account_id="HYPOTHETICAL_AUDIT_ACCT_100K",
        equity=Decimal("100000.00"),
        balance=Decimal("100000.00"),
        account_currency="USD",
        open_risk_ratio=Decimal("0.00"),
        max_open_risk_ratio=Decimal("0.06"),
        daily_realized_loss=Decimal("0.00"),
        daily_loss_limit_ratio=Decimal("0.03"),
    )

    cfg = RiskManagementConfig(
        stop_loss_method=StopLossMethod.ZONE_DISTAL_BOUNDARY,
        take_profit_method=TakeProfitMethod.FIXED_R_MULTIPLE,
        risk_fraction=Decimal("0.01"),
        target_r_multiple=Decimal("2.0"),
        min_risk_reward_ratio=Decimal("1.5"),
        distal_buffer_ticks=20,
    )

    rows = []
    for idx, plan in enumerate(trade_plans, 1):
        assessment = evaluate_trade_plan_risk(
            plan=plan,
            config=cfg,
            specification=spec,
            account_snapshot=account,
        )

        stop_dist = assessment.stop_loss.risk_distance
        reward_dist = assessment.take_profit.reward_distance
        gross_rr = assessment.gross_risk_reward
        net_rr = assessment.net_risk_reward

        ps = assessment.position_sizing
        lots = ps.allocated_lots if ps else Decimal("0")
        gross_loss = ps.gross_cash_loss if ps else Decimal("0")
        gross_reward = ps.gross_cash_reward if ps else Decimal("0")
        spread_cost = ps.estimated_spread_cost if ps else Decimal("0")
        comm_cost = ps.estimated_commission if ps else Decimal("0")
        slip_cost = ps.estimated_slippage_cost if ps else Decimal("0")
        stop_cost = ps.stop_scenario_total_cost if ps else Decimal("0")
        target_cost = ps.target_scenario_total_cost if ps else Decimal("0")
        cash_risk = ps.estimated_cash_risk if ps else Decimal("0")
        net_reward = ps.net_cash_reward if ps else Decimal("0")
        budget = ps.risk_budget if ps else Decimal("0")
        ps_status = "VALID" if (ps and ps.is_valid) else "INVALID"
        risk_status = assessment.status.value
        rej_reasons = ",".join(r.value for r in assessment.rejection_reasons) if assessment.rejection_reasons else "NONE"

        short_plan_id = plan.deterministic_trade_plan_id.split(":")[-3] + "..."

        rows.append({
            "idx": idx,
            "plan_id": plan.deterministic_trade_plan_id,
            "short_id": f"P{idx}_{plan.timeframe.name}_{plan.price_type.value}_{plan.direction.value}",
            "price_type": plan.price_type.value,
            "tf": plan.timeframe.name,
            "direction": plan.direction.value,
            "entry": plan.entry_reference_price,
            "stop": assessment.stop_loss.price,
            "target": assessment.take_profit.primary_target_price,
            "stop_dist": stop_dist,
            "reward_dist": reward_dist,
            "gross_rr": f"{gross_rr}R",
            "contract_units": spec.units_per_lot,
            "lot": lots,
            "gross_loss": f"${gross_loss}",
            "gross_reward": f"${gross_reward}",
            "spread_cost": f"${spread_cost}",
            "comm_cost": f"${comm_cost}",
            "slip_assump": "0 ticks",
            "stop_cost": f"${stop_cost}",
            "target_cost": f"${target_cost}",
            "cash_risk": f"${cash_risk}",
            "net_reward": f"${net_reward}",
            "net_rr": f"{net_rr}R" if net_rr is not None else "NONE",
            "budget": f"${budget}",
            "ps_status": ps_status,
            "risk_status": risk_status,
            "rejections": rej_reasons,
        })

    # Print markdown table
    cols = [
        "Plan", "Series", "Dir", "Entry", "Stop", "Target", "Stop Dist", "Tgt Dist",
        "Gross R:R", "Lot", "Gross Loss", "Gross Reward", "Spread Cost", "Comm Cost",
        "Stop Cost", "Target Cost", "Cash Risk", "Net Reward", "Net R:R", "Budget", "Status", "Rejection Reason"
    ]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    print(header)
    print(sep)

    md_lines = [header, sep]
    for r in rows:
        line = (
            f"| {r['short_id']} | {r['tf']} {r['price_type']} | {r['direction']} | {r['entry']} | {r['stop']} | "
            f"{r['target']} | {r['stop_dist']} | {r['reward_dist']} | {r['gross_rr']} | {r['lot']} | "
            f"{r['gross_loss']} | {r['gross_reward']} | {r['spread_cost']} | {r['comm_cost']} | {r['stop_cost']} | "
            f"{r['target_cost']} | {r['cash_risk']} | {r['net_reward']} | {r['net_rr']} | {r['budget']} | "
            f"{r['risk_status']} | {r['rejections']} |"
        )
        print(line)
        md_lines.append(line)

    with open("scratch/stage3_26_plan_reconciliation_table.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

if __name__ == "__main__":
    asyncio.run(main())
