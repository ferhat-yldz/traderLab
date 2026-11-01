import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from traderlab.infrastructure.calendars.forex_session import XAUUSDTradingCalendar
from traderlab.infrastructure.providers.market_data_lab.bar_classifier import (
    classify_m1_series,
    BarQualityClassification,
)
from traderlab.infrastructure.providers.market_data_lab.csv_reader import read_m1_csv
from traderlab.core.types import PriceType

DATA_DIR = Path(r"C:\trader\traderLab\Market-Data-Lab\xauusd")
GIT_SHA = "78fd60f8bf3a1cebe198828147e54e4ed32385d8"
cal = XAUUSDTradingCalendar()


def calc_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


months_list = (
    [(2024, m) for m in range(1, 13)]
    + [(2025, m) for m in range(1, 13)]
    + [(2026, m) for m in range(1, 11)]
)

records = []
print(f"Generating unified reconciled inventory for {len(months_list)} months (68 CSV files)...")

for y, m in months_list:
    m_str = f"{y}-{m:02d}"
    bid_file = DATA_DIR / "bid" / "m1" / f"xauusd_bid_m1_{y}_{m:02d}.csv"
    ask_file = DATA_DIR / "ask" / "m1" / f"xauusd_ask_m1_{y}_{m:02d}.csv"

    bid_sha = calc_sha256(bid_file)
    ask_sha = calc_sha256(ask_file)

    df_bid = pd.read_csv(bid_file)
    df_ask = pd.read_csv(ask_file)

    b_count = len(df_bid)
    a_count = len(df_ask)

    set_b = set(df_bid["timestamp"])
    set_a = set(df_ask["timestamp"])

    matched_count = len(set_b & set_a)
    unmatched_bid = len(set_b - set_a)
    unmatched_ask = len(set_a - set_b)

    start_utc = datetime.fromtimestamp(
        df_bid["timestamp"].iloc[0] / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M")
    end_utc = datetime.fromtimestamp(
        df_bid["timestamp"].iloc[-1] / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M")

    # Time steps & Gaps
    df_bid["dt"] = pd.to_datetime(df_bid["timestamp"], unit="ms", utc=True)
    steps = df_bid["timestamp"].diff().dropna()
    gaps_gt_60k = (steps > 60000).sum()

    # Day of week
    dows = df_bid["dt"].dt.dayofweek
    sat_rows = (dows == 5).sum()
    sun_pre22 = ((dows == 6) & (df_bid["dt"].dt.hour < 22)).sum()

    # Determine Storage Policy
    if gaps_gt_60k == 0 and sat_rows > 0:
        policy = "CONTINUOUS_WITH_SYNTHETIC"
    elif sat_rows == 0 and sun_pre22 > 0 and gaps_gt_60k > 0:
        policy = "MIXED"
    elif sat_rows == 0 and sun_pre22 == 0 and gaps_gt_60k > 0:
        policy = "TRADING_SESSION_ONLY"
    else:
        policy = "UNKNOWN"

    # Full month status
    if policy == "CONTINUOUS_WITH_SYNTHETIC":
        month_status = "AVAILABLE_FULL_MONTH"
    elif policy == "MIXED":
        month_status = "AVAILABLE_MIXED_MONTH"
    elif y == 2026 and m == 10:
        month_status = "AVAILABLE_PARTIAL_MONTH"
    else:
        month_status = "AVAILABLE_SESSION_MONTH"

    # Count active trading gap minutes (active session missing)
    gap_rows = df_bid[df_bid["timestamp"].diff() > 60000]
    active_missing_mins = 0
    for _, r in gap_rows.iterrows():
        prev_ts = r["dt"] - pd.Timedelta(
            milliseconds=r["timestamp"] - df_bid.loc[r.name - 1, "timestamp"]
        )
        curr = prev_ts + pd.Timedelta(minutes=1)
        while curr < r["dt"]:
            if cal.is_market_open(curr.to_pydatetime()):
                active_missing_mins += 1
            curr += pd.Timedelta(minutes=1)

    # Classify bars for synthetic & uncertain counts
    raw_bid_bars = read_m1_csv(bid_file, price_type=PriceType.BID)
    classified = classify_m1_series(raw_bid_bars)
    synthetic_count = sum(
        1
        for c in classified
        if c.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD
    )
    uncertain_count = sum(
        1 for c in classified if c.classification == BarQualityClassification.UNCERTAIN_STAGNANT
    )

    records.append(
        {
            "calendar_month": m_str,
            "bid_filename": bid_file.name,
            "ask_filename": ask_file.name,
            "git_commit_sha": GIT_SHA,
            "bid_sha256": bid_sha,
            "ask_sha256": ask_sha,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "bid_raw_count": b_count,
            "ask_raw_count": a_count,
            "matched_count": matched_count,
            "unmatched_bid": unmatched_bid,
            "unmatched_ask": unmatched_ask,
            "active_missing_mins": active_missing_mins,
            "synthetic_count": synthetic_count,
            "uncertain_count": uncertain_count,
            "storage_policy": policy,
            "month_status": month_status,
        }
    )
    print(
        f"[{m_str}] BID={b_count:,} ASK={a_count:,} Matched={matched_count:,} Policy={policy} MissingMins={active_missing_mins}"
    )

out_path = Path(r"C:\trader\traderLab\traderlab\docs\reports\reconciled_inventory_2024_2026.csv")
keys = list(records[0].keys())
with open(out_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=keys)
    writer.writeheader()
    writer.writerows(records)

print(f"\nGenerated unified reconciled inventory: {out_path}")

# Print summary table
df_out = pd.DataFrame(records)
print("\n=== YEARLY AGGREGATIONS ===")
df_out["year"] = df_out["calendar_month"].apply(lambda s: int(s.split("-")[0]))
for yr, grp in df_out.groupby("year"):
    print(
        f"Year {yr}: BID={grp.bid_raw_count.sum():,} | ASK={grp.ask_raw_count.sum():,} | Matched={grp.matched_count.sum():,} | UnmatchedBID={grp.unmatched_bid.sum()} | UnmatchedASK={grp.unmatched_ask.sum()}"
    )

print(
    f"GRAND TOTAL: BID={df_out.bid_raw_count.sum():,} | ASK={df_out.ask_raw_count.sum():,} | Matched={df_out.matched_count.sum():,} | UnmatchedBID={df_out.unmatched_bid.sum()} | UnmatchedASK={df_out.unmatched_ask.sum()}"
)
