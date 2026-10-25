import csv
import sys
from pathlib import Path
from decimal import Decimal
import pandas as pd

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from traderlab.infrastructure.providers.market_data_lab.csv_reader import read_m1_csv
from traderlab.infrastructure.providers.market_data_lab.bar_classifier import classify_m1_series, BarQualityClassification
from traderlab.core.types import PriceType

bid_dir = Path("../Market-Data-Lab/xauusd/bid/m1")
ask_dir = Path("../Market-Data-Lab/xauusd/ask/m1")

bid_files = sorted(bid_dir.glob("xauusd_bid_m1_*.csv"))
print(f"Found {len(bid_files)} monthly files in {bid_dir}")

results = []

for b_file in bid_files:
    month_str = b_file.stem.replace("xauusd_bid_m1_", "")
    a_file = ask_dir / f"xauusd_ask_m1_{month_str}.csv"
    
    # Read raw bars
    bars_bid = read_m1_csv(b_file, PriceType.BID)
    bars_ask = read_m1_csv(a_file, PriceType.ASK)
    
    cb_bid = classify_m1_series(bars_bid)
    cb_ask = classify_m1_series(bars_ask)
    
    raw_bid = len(cb_bid)
    raw_ask = len(cb_ask)
    
    syn_bid = sum(1 for b in cb_bid if b.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD)
    syn_ask = sum(1 for b in cb_ask if b.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD)
    
    clean_bid = raw_bid - syn_bid
    clean_ask = raw_ask - syn_ask
    
    # Legitimate flat / stagnant checks
    flat_open_bid = sum(1 for b in cb_bid if b.classification == BarQualityClassification.LEGITIMATE_FLAT_OPEN)
    flat_open_ask = sum(1 for b in cb_ask if b.classification == BarQualityClassification.LEGITIMATE_FLAT_OPEN)
    stagnant_bid = sum(1 for b in cb_bid if b.classification == BarQualityClassification.UNCERTAIN_STAGNANT)
    stagnant_ask = sum(1 for b in cb_ask if b.classification == BarQualityClassification.UNCERTAIN_STAGNANT)
    active_bid = sum(1 for b in cb_bid if b.classification == BarQualityClassification.ACTIVE_NORMAL)
    active_ask = sum(1 for b in cb_ask if b.classification == BarQualityClassification.ACTIVE_NORMAL)
    
    results.append({
        "month": month_str,
        "raw_bid": raw_bid,
        "raw_ask": raw_ask,
        "raw_delta": raw_bid - raw_ask,
        "syn_bid": syn_bid,
        "syn_ask": syn_ask,
        "syn_delta": syn_bid - syn_ask,
        "clean_bid": clean_bid,
        "clean_ask": clean_ask,
        "clean_delta": clean_bid - clean_ask,
        "flat_open_bid": flat_open_bid,
        "flat_open_ask": flat_open_ask,
        "stagnant_bid": stagnant_bid,
        "stagnant_ask": stagnant_ask,
        "active_bid": active_bid,
        "active_ask": active_ask,
    })
    print(f"Processed {month_str}: Raw BID={raw_bid}, ASK={raw_ask} | Syn BID={syn_bid}, ASK={syn_ask} | Clean BID={clean_bid}, ASK={clean_ask}")

df_res = pd.DataFrame(results)
out_csv = Path("docs/reports/phase_1c_bid_ask_dry_run_reconciliation.csv")
out_csv.parent.mkdir(parents=True, exist_ok=True)
df_res.to_csv(out_csv, index=False)

print("\n=== TOTAL SUMMARY ACROSS 34 MONTHS ===")
print("Total Raw BID Rows:            ", f"{df_res['raw_bid'].sum():,}")
print("Total Raw ASK Rows:            ", f"{df_res['raw_ask'].sum():,}")
print("Raw Row Delta (BID - ASK):     ", df_res['raw_delta'].sum())
print("Total Synthetic BID Filtered:  ", f"{df_res['syn_bid'].sum():,}")
print("Total Synthetic ASK Filtered:  ", f"{df_res['syn_ask'].sum():,}")
print("Synthetic Delta (BID - ASK):   ", df_res['syn_delta'].sum())
print("Total Clean BID Retained:      ", f"{df_res['clean_bid'].sum():,}")
print("Total Clean ASK Retained:      ", f"{df_res['clean_ask'].sum():,}")
print("Clean Row Delta (BID - ASK):   ", df_res['clean_delta'].sum())
print(f"Full table saved to {out_csv}")
