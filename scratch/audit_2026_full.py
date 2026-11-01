import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from traderlab.infrastructure.providers.market_data_lab.bar_classifier import classify_m1_series
from traderlab.infrastructure.providers.market_data_lab.csv_reader import read_m1_csv
from traderlab.core.types import PriceType

DATA_DIR = Path(r"C:\trader\traderLab\Market-Data-Lab\xauusd")

results = []


def calc_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


print("Auditing 2026 months (01 through 09)...")

for m in range(1, 10):
    bid_file = DATA_DIR / "bid" / "m1" / f"xauusd_bid_m1_2026_{m:02d}.csv"
    ask_file = DATA_DIR / "ask" / "m1" / f"xauusd_ask_m1_2026_{m:02d}.csv"

    bid_sz = bid_file.stat().st_size
    ask_sz = ask_file.stat().st_size
    bid_sha = calc_sha256(bid_file)
    ask_sha = calc_sha256(ask_file)

    df_bid = pd.read_csv(bid_file)
    df_ask = pd.read_csv(ask_file)

    bid_count = len(df_bid)
    ask_count = len(df_ask)
    matched = (
        (df_bid["timestamp"] == df_ask["timestamp"]).all() if bid_count == ask_count else False
    )
    dups = df_bid["timestamp"].duplicated().sum()

    start_utc = datetime.fromtimestamp(
        df_bid["timestamp"].iloc[0] / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M")
    end_utc = datetime.fromtimestamp(
        df_bid["timestamp"].iloc[-1] / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M")

    diffs = df_bid["timestamp"].diff().dropna()
    eq_60k = (diffs == 60000).sum()
    gt_60k = (diffs > 60000).sum()

    # Session classification / weekend check
    dts = pd.to_datetime(df_bid["timestamp"], unit="ms", utc=True)
    dayofweek = dts.dt.dayofweek  # 5 = Sat, 6 = Sun
    hour = dts.dt.hour

    sat_rows = (dayofweek == 5).sum()
    sun_pre22_rows = ((dayofweek == 6) & (hour < 22)).sum()
    sun_post22_rows = ((dayofweek == 6) & (hour >= 22)).sum()

    # Determine storage policy
    if gt_60k == 0 and sat_rows > 0:
        policy = "CONTINUOUS_WITH_SYNTHETIC"
    elif gt_60k > 0 and sat_rows == 0:
        policy = "TRADING_SESSION_ONLY"
    elif gt_60k > 0 and sat_rows > 0:
        policy = "MIXED"
    else:
        policy = "UNKNOWN"

    # Price stats
    min_price = min(df_bid["low"].min(), df_ask["low"].min())
    max_price = max(df_bid["high"].max(), df_ask["high"].max())

    # Spread analysis on timestamp-matched bars
    df_merged = pd.merge(df_bid, df_ask, on="timestamp", suffixes=("_bid", "_ask"))
    spreads = df_merged["close_ask"] - df_merged["close_bid"]
    neg_spreads = (spreads < 0).sum()
    min_spread = spreads.min() if len(spreads) > 0 else 0.0
    max_spread = spreads.max() if len(spreads) > 0 else 0.0
    avg_spread = spreads.mean() if len(spreads) > 0 else 0.0

    # Domain classification for synthetic %
    raw_bid_bars = read_m1_csv(bid_file, price_type=PriceType.BID)
    classified = classify_m1_series(raw_bid_bars)
    from traderlab.infrastructure.providers.market_data_lab.bar_classifier import (
        BarQualityClassification,
    )

    synthetic_count = sum(
        1
        for c in classified
        if c.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD
    )
    uncertain_count = sum(
        1 for c in classified if c.classification == BarQualityClassification.UNCERTAIN_STAGNANT
    )
    synthetic_pct = round((synthetic_count / len(classified)) * 100, 2)
    uncertain_pct = round((uncertain_count / len(classified)) * 100, 2)

    results.append(
        {
            "year": 2026,
            "month": m,
            "bid_count": bid_count,
            "ask_count": ask_count,
            "matched": matched,
            "dups": int(dups),
            "start_utc": start_utc,
            "end_utc": end_utc,
            "eq_60k": int(eq_60k),
            "gt_60k": int(gt_60k),
            "policy": policy,
            "sat_rows": int(sat_rows),
            "sun_pre22_rows": int(sun_pre22_rows),
            "sun_post22_rows": int(sun_post22_rows),
            "min_price": float(min_price),
            "max_price": float(max_price),
            "min_spread": float(round(min_spread, 4)),
            "max_spread": float(round(max_spread, 4)),
            "avg_spread": float(round(avg_spread, 4)),
            "neg_spreads": int(neg_spreads),
            "synthetic_pct": synthetic_pct,
            "uncertain_pct": uncertain_pct,
            "bid_sz": bid_sz,
            "ask_sz": ask_sz,
            "bid_sha256": bid_sha,
            "ask_sha256": ask_sha,
        }
    )
    print(
        f"2026-{m:02d} done: rows={bid_count}, policy={policy}, max_spread={max_spread:.4f}, synthetic={synthetic_pct}%"
    )

out_csv = Path(r"C:\trader\traderLab\traderlab\docs\reports\audit_2026_summary.csv")
keys = list(results[0].keys())
with open(out_csv, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=keys)
    writer.writeheader()
    writer.writerows(results)

print(f"Summary written to {out_csv}")
