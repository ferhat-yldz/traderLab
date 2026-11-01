from pathlib import Path
import pandas as pd

DATA_DIR = Path(r"C:\trader\traderLab\Market-Data-Lab\xauusd")

records = []
years = [2024, 2025, 2026]

for y in years:
    months = range(1, 13) if y < 2026 else range(1, 11)
    for m in months:
        bid_file = DATA_DIR / "bid" / "m1" / f"xauusd_bid_m1_{y}_{m:02d}.csv"
        ask_file = DATA_DIR / "ask" / "m1" / f"xauusd_ask_m1_{y}_{m:02d}.csv"

        bid_df = pd.read_csv(bid_file)
        ask_df = pd.read_csv(ask_file)

        b_count = len(bid_df)
        a_count = len(ask_df)

        records.append(
            {
                "year": y,
                "month": m,
                "bid_count": b_count,
                "ask_count": a_count,
                "diff": a_count - b_count,
            }
        )

df = pd.DataFrame(records)
print("=== MONTHLY COUNTS ===")
for y in years:
    sub = df[df.year == y]
    b_sum = sub.bid_count.sum()
    a_sum = sub.ask_count.sum()
    print(f"Year {y}: BID={b_sum:,} | ASK={a_sum:,} | Diff={a_sum - b_sum}")
    for _, r in sub.iterrows():
        print(f"  {r.year}-{r.month:02d}: BID={r.bid_count:,} | ASK={r.ask_count:,}")

total_bid = df.bid_count.sum()
total_ask = df.ask_count.sum()
print("\n=== GRAND TOTAL (2024-2026) ===")
print(f"Total BID: {total_bid:,}")
print(f"Total ASK: {total_ask:,}")
print(f"Net Diff (ASK - BID): {total_ask - total_bid}")
