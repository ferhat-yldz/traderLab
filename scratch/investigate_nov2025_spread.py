import pandas as pd
from datetime import datetime, timezone
import zoneinfo

bid_path = r"C:\trader\traderLab\Market-Data-Lab\xauusd\bid\m1\xauusd_bid_m1_2025_11.csv"
ask_path = r"C:\trader\traderLab\Market-Data-Lab\xauusd\ask\m1\xauusd_ask_m1_2025_11.csv"

df_bid = pd.read_csv(bid_path)
df_ask = pd.read_csv(ask_path)

df = pd.merge(df_bid, df_ask, on="timestamp", suffixes=("_bid", "_ask"))
df["spread_close"] = df["close_ask"] - df["close_bid"]

max_idx = df["spread_close"].idxmax()
row = df.loc[max_idx]

dt_utc = datetime.fromtimestamp(row["timestamp"] / 1000, tz=timezone.utc)
dt_ny = dt_utc.astimezone(zoneinfo.ZoneInfo("America/New_York"))

print("=== 2025-11 MAX SPREAD ANALYSIS ===")
print(f"Timestamp UTC: {dt_utc}")
print(f"Timestamp NY:  {dt_ny}")
print(f"BID: Open={row['open_bid']}, High={row['high_bid']}, Low={row['low_bid']}, Close={row['close_bid']}")
print(f"ASK: Open={row['open_ask']}, High={row['high_ask']}, Low={row['low_ask']}, Close={row['close_ask']}")
print(f"Spread Close: {row['spread_close']:.4f}")

start_idx = max(0, max_idx - 3)
end_idx = min(len(df), max_idx + 4)
print("\nSurrounding minutes:")
for i in range(start_idx, end_idx):
    r = df.loc[i]
    t = datetime.fromtimestamp(r["timestamp"] / 1000, tz=timezone.utc)
    marker = " <-- [MAX SPREAD]" if i == max_idx else ""
    print(f"{t.strftime('%Y-%m-%d %H:%M')} UTC | BID Close={r['close_bid']:.3f} | ASK Close={r['close_ask']:.3f} | Spread={r['spread_close']:.4f}{marker}")
