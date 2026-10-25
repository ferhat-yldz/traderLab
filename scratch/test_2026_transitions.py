import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(r"C:\trader\traderLab\Market-Data-Lab\xauusd\bid\m1")

def check_transition(f1_name, f2_name, desc):
    df1 = pd.read_csv(DATA_DIR / f1_name)
    df2 = pd.read_csv(DATA_DIR / f2_name)
    
    t1_end = df1.iloc[-1]
    t2_start = df2.iloc[0]
    
    dt1 = datetime.fromtimestamp(t1_end["timestamp"] / 1000, tz=timezone.utc)
    dt2 = datetime.fromtimestamp(t2_start["timestamp"] / 1000, tz=timezone.utc)
    
    step_ms = t2_start["timestamp"] - t1_end["timestamp"]
    price1 = t1_end["close"]
    price2 = t2_start["open"]
    
    print(f"=== {desc} ===")
    print(f"End 1:   {dt1} | Close: {price1}")
    print(f"Start 2: {dt2} | Open:  {price2}")
    print(f"Step ms: {step_ms} ({step_ms / 1000}s) | Price jump: {price2 - price1:.3f}")

# 1. 2025-12 -> 2026-01
check_transition("xauusd_bid_m1_2025_12.csv", "xauusd_bid_m1_2026_01.csv", "31 Dec 2025 -> 1 Jan 2026")

# 2. 2026-01 -> 2026-02
check_transition("xauusd_bid_m1_2026_01.csv", "xauusd_bid_m1_2026_02.csv", "31 Jan 2026 -> 1 Feb 2026")

# 3. 2026-02 -> 2026-03
check_transition("xauusd_bid_m1_2026_02.csv", "xauusd_bid_m1_2026_03.csv", "28 Feb 2026 -> 1 Mar 2026")

# 4. 2026-03 -> 2026-04
check_transition("xauusd_bid_m1_2026_03.csv", "xauusd_bid_m1_2026_04.csv", "31 Mar 2026 -> 1 Apr 2026")

# 5. 2026-09 -> 2026-10
check_transition("xauusd_bid_m1_2026_09.csv", "xauusd_bid_m1_2026_10.csv", "30 Sep 2026 -> 1 Oct 2026")
