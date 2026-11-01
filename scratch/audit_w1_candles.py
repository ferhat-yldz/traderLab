import csv
import sys
from datetime import datetime, UTC
from pathlib import Path
from decimal import Decimal

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from traderlab.interfaces.dashboard.app import load_candles_for_range, get_cached_inventory_summary
from traderlab.core.types import PriceType, Timeframe
from traderlab.application.analysis.resampling import DailyCloseMode

inv = get_cached_inventory_summary()
s = datetime(2021, 10, 19, 0, 0, tzinfo=UTC)
e = datetime(2026, 10, 19, 23, 59, 59, tzinfo=UTC)

candles, rc_list, warn, err = load_candles_for_range(
    "XAUUSD",
    PriceType.BID,
    Timeframe.W1,
    s,
    e,
    daily_close_mode=DailyCloseMode.NEW_YORK_1700,
    available_months=inv.available_months,
)

print(f"Total W1 candles: {len(rc_list)}")

out_csv = Path("docs/reports/w1_candle_audit_table.csv")
out_csv.parent.mkdir(parents=True, exist_ok=True)

rows = []
quality_counts = {"PURE_REAL": 0, "MIXED": 0, "PURE_SYNTHETIC": 0, "PARTIAL": 0}
synth_composition_counts = {"PURE_REAL": 0, "MIXED": 0, "PURE_SYNTHETIC": 0}
window_completeness_counts = {
    "COMPLETE": 0,
    "PARTIAL_BOUNDARY_START": 0,
    "PARTIAL_BOUNDARY_END": 0,
}

for idx, rc in enumerate(rc_list, 1):
    w_start = rc.candle.open_time.strftime("%Y-%m-%d %H:%M UTC")
    act = rc.active_m1_count
    syn = rc.synthetic_m1_count
    tot = rc.present_m1_count
    exp = rc.expected_m1_count
    missing = max(0, exp - tot)
    w1_open = f"{rc.candle.open:.3f}"

    # 1. Synthetic Composition Dimension (Content purity)
    if act == 0 and syn > 0:
        synth_comp = "PURE_SYNTHETIC"
    elif syn == 0 and act > 0:
        synth_comp = "PURE_REAL"
    else:
        synth_comp = "MIXED"
    synth_composition_counts[synth_comp] += 1

    # 2. Window Completeness Dimension (Coverage against archive boundaries)
    if idx == 1:
        # Week 1 starts 2023-12-31 22:00 UTC, but archive starts 2024-01-01 00:00 UTC (120 mins clipped)
        window_comp = "PARTIAL_BOUNDARY_START"
    elif idx == len(rc_list):
        # Last week cutoff on 2026-10-19 13:59 UTC (9,120 mins unelapsed)
        window_comp = "PARTIAL_BOUNDARY_END"
    else:
        window_comp = "COMPLETE"
    window_completeness_counts[window_comp] += 1

    # Legacy / consolidated quality class
    if act == 0 and syn > 0:
        q_class = "PURE_SYNTHETIC"
    elif syn == 0 and act >= 6000:
        q_class = "PURE_REAL"
    elif syn == 0 and 0 < act < 6000:
        q_class = "PARTIAL"
    elif syn > 0 and act > 0:
        q_class = "MIXED"
    else:
        q_class = "PARTIAL"
    quality_counts[q_class] += 1

    rows.append(
        {
            "week_index": idx,
            "week_start_utc": w_start,
            "active_m1_count": act,
            "synthetic_m1_count": syn,
            "missing_m1_count": missing,
            "synthetic_composition": synth_comp,
            "window_completeness": window_comp,
            "quality_class": q_class,
            "w1_open": w1_open,
            "w1_high": f"{rc.candle.high:.3f}",
            "w1_low": f"{rc.candle.low:.3f}",
            "w1_close": f"{rc.candle.close:.3f}",
        }
    )

fieldnames = [
    "week_index",
    "week_start_utc",
    "active_m1_count",
    "synthetic_m1_count",
    "missing_m1_count",
    "synthetic_composition",
    "window_completeness",
    "quality_class",
    "w1_open",
    "w1_high",
    "w1_low",
    "w1_close",
]

with open(out_csv, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print(f"Saved {len(rows)} rows to {out_csv}")
print("Synthetic Composition summary:", synth_composition_counts)
print("Window Completeness summary:", window_completeness_counts)
print("Quality class summary:", quality_counts)
