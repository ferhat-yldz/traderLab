import sys
from pathlib import Path
from decimal import Decimal
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from traderlab.infrastructure.providers.market_data_lab.csv_reader import read_m1_csv
from traderlab.infrastructure.providers.market_data_lab.bar_classifier import (
    classify_m1_series,
    BarQualityClassification,
)
from traderlab.core.types import PriceType

bid_dir = Path("../Market-Data-Lab/xauusd/bid/m1")
ask_dir = Path("../Market-Data-Lab/xauusd/ask/m1")

bid_files = sorted(bid_dir.glob("xauusd_bid_m1_*.csv"))

monthly_summary = []
bid_only_active_all = []
ask_only_active_all = []
raw_bid_only_all = []
raw_ask_only_all = []
inverted_spreads_all = []

total_raw_bid = 0
total_raw_ask = 0
total_raw_matched = 0
total_raw_duplicates_bid = 0
total_raw_duplicates_ask = 0

total_active_bid = 0
total_active_ask = 0
total_active_matched = 0

for b_file in bid_files:
    month_str = b_file.stem.replace("xauusd_bid_m1_", "")
    a_file = ask_dir / f"xauusd_ask_m1_{month_str}.csv"

    bars_b = read_m1_csv(b_file, PriceType.BID)
    bars_a = read_m1_csv(a_file, PriceType.ASK)

    # Check raw duplicates
    ts_b_list = [b.timestamp_ms for b in bars_b]
    ts_a_list = [a.timestamp_ms for a in bars_a]

    dup_b = len(ts_b_list) - len(set(ts_b_list))
    dup_a = len(ts_a_list) - len(set(ts_a_list))

    total_raw_duplicates_bid += dup_b
    total_raw_duplicates_ask += dup_a

    map_b_raw = {b.timestamp_ms: b for b in bars_b}
    map_a_raw = {a.timestamp_ms: a for a in bars_a}

    set_b_raw = set(map_b_raw.keys())
    set_a_raw = set(map_a_raw.keys())

    raw_matched = set_b_raw & set_a_raw
    raw_b_only = set_b_raw - set_a_raw
    raw_a_only = set_a_raw - set_b_raw

    for ts in raw_b_only:
        raw_bid_only_all.append((month_str, pd.to_datetime(ts, unit="ms", utc=True)))
    for ts in raw_a_only:
        raw_ask_only_all.append((month_str, pd.to_datetime(ts, unit="ms", utc=True)))

    # Check spread on matched raw bars
    month_inverted_raw = 0
    spreads_active = []

    # Classification
    cb_b = classify_m1_series(bars_b)
    cb_a = classify_m1_series(bars_a)

    active_b_map = {
        b.bar.timestamp_ms: b.bar
        for b in cb_b
        if b.classification != BarQualityClassification.SYNTHETIC_CARRY_FORWARD
    }
    active_a_map = {
        a.bar.timestamp_ms: a.bar
        for a in cb_a
        if a.classification != BarQualityClassification.SYNTHETIC_CARRY_FORWARD
    }

    set_b_active = set(active_b_map.keys())
    set_a_active = set(active_a_map.keys())

    active_matched = set_b_active & set_a_active
    active_b_only = set_b_active - set_a_active
    active_a_only = set_a_active - set_b_active

    for ts in active_b_only:
        bid_only_active_all.append(
            {
                "month": month_str,
                "timestamp_utc": str(pd.to_datetime(ts, unit="ms", utc=True)),
                "bid_bar": active_b_map[ts],
                "ask_raw_status": "SYNTHETIC" if ts in map_a_raw else "MISSING_IN_RAW",
            }
        )
    for ts in active_a_only:
        ask_only_active_all.append(
            {
                "month": month_str,
                "timestamp_utc": str(pd.to_datetime(ts, unit="ms", utc=True)),
                "ask_bar": active_a_map[ts],
                "bid_raw_status": "SYNTHETIC" if ts in map_b_raw else "MISSING_IN_RAW",
            }
        )

    # Spread checks on active matched bars
    inverted_active_count = 0
    for ts in active_matched:
        bb = active_b_map[ts]
        ba = active_a_map[ts]
        spread_close = ba.close - bb.close
        if spread_close < 0:
            inverted_active_count += 1
            inverted_spreads_all.append(
                {
                    "month": month_str,
                    "timestamp_utc": str(pd.to_datetime(ts, unit="ms", utc=True)),
                    "bid_close": str(bb.close),
                    "ask_close": str(ba.close),
                    "spread": str(spread_close),
                }
            )
        spreads_active.append(float(spread_close))

    avg_spread = sum(spreads_active) / len(spreads_active) if spreads_active else 0.0
    min_spread = min(spreads_active) if spreads_active else 0.0
    max_spread = max(spreads_active) if spreads_active else 0.0

    total_raw_bid += len(bars_b)
    total_raw_ask += len(bars_a)
    total_raw_matched += len(raw_matched)

    total_active_bid += len(active_b_map)
    total_active_ask += len(active_a_map)
    total_active_matched += len(active_matched)

    monthly_summary.append(
        {
            "month": month_str,
            "raw_bid": len(bars_b),
            "raw_ask": len(bars_a),
            "raw_matched": len(raw_matched),
            "raw_b_only": len(raw_b_only),
            "raw_a_only": len(raw_a_only),
            "dup_bid": dup_b,
            "dup_ask": dup_a,
            "active_bid": len(active_b_map),
            "active_ask": len(active_a_map),
            "active_matched": len(active_matched),
            "active_b_only": len(active_b_only),
            "active_a_only": len(active_a_only),
            "inverted_spread_count": inverted_active_count,
            "min_spread": round(min_spread, 3),
            "avg_spread": round(avg_spread, 3),
            "max_spread": round(max_spread, 3),
        }
    )
    print(
        f"[{month_str}] Raw: B={len(bars_b)}, A={len(bars_a)}, Match={len(raw_matched)} | Active: B={len(active_b_map)}, A={len(active_a_map)}, Match={len(active_matched)} | Inverted={inverted_active_count}"
    )

df_summary = pd.DataFrame(monthly_summary)
out_csv = Path("docs/reports/phase_1c_bid_ask_timestamp_reconciliation.csv")
df_summary.to_csv(out_csv, index=False)

print("\n================== FULL 34-MONTH AUDIT RESULTS ==================")
print(f"Total Raw BID Rows:            {total_raw_bid:,}")
print(f"Total Raw ASK Rows:            {total_raw_ask:,}")
print(f"Total Raw Matching Timestamps: {total_raw_matched:,}")
print(f"Total Raw BID-Only Timestamps: {len(raw_bid_only_all)}")
print(f"Total Raw ASK-Only Timestamps: {len(raw_ask_only_all)}")
if raw_ask_only_all:
    print(f"  --> Details: {raw_ask_only_all}")
print(f"Duplicate BID Timestamps:      {total_raw_duplicates_bid}")
print(f"Duplicate ASK Timestamps:      {total_raw_duplicates_ask}")

print(f"\nTotal Active BID Rows:           {total_active_bid:,}")
print(f"Total Active ASK Rows:           {total_active_ask:,}")
print(f"Total Active Matching Timestamps:{total_active_matched:,}")
print(f"Active BID-Only Timestamps:      {len(bid_only_active_all)}")
for item in bid_only_active_all:
    print(f"  --> BID-Only: {item['timestamp_utc']} (ASK status in raw: {item['ask_raw_status']})")
print(f"Active ASK-Only Timestamps:      {len(ask_only_active_all)}")
for item in ask_only_active_all:
    print(f"  --> ASK-Only: {item['timestamp_utc']} (BID status in raw: {item['bid_raw_status']})")

print(f"\nTotal Negative Spread Anomalies: {len(inverted_spreads_all)}")
if inverted_spreads_all:
    print(f"  --> Details: {inverted_spreads_all}")
else:
    print("  --> ZERO NEGATIVE SPREADS DETECTED! (100% Valid Market Arbitrage Integrity)")
