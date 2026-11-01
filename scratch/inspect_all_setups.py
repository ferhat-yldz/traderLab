import sys
sys.path.insert(0, ".")
import scratch.check_plan_details as cpd

for i, p in enumerate(cpd.all_plans, 1):
    parts = p.source_setup_id.split(":")
    trigger = parts[-2] if len(parts) >= 2 else "UNKNOWN"
    fvg_t = parts[-1] if len(parts) >= 1 else "UNKNOWN"
    print(f"P{i:02d} | {p.timeframe.name:<2} | {p.price_type.value:<3} | {p.direction.value:<5} | FVG_T: {fvg_t} | {trigger}")
