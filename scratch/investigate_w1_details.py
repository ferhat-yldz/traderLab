import csv
from pathlib import Path
from datetime import datetime, UTC, timedelta
from traderlab.infrastructure.providers.market_data_lab.csv_reader import read_m1_csv
from traderlab.infrastructure.providers.market_data_lab.config import get_market_data_lab_settings
from traderlab.core.types import PriceType

DATA_ROOT = get_market_data_lab_settings().resolve_root_dir()

# 1. Investigate MIXED min/max
csv_path = Path("docs/reports/w1_candle_audit_table.csv")
rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
mixed = [r for r in rows if r["quality_class"] == "MIXED"]

min_act = min(int(r["active_m1_count"]) for r in mixed)
max_act = max(int(r["active_m1_count"]) for r in mixed)
min_syn = min(int(r["synthetic_m1_count"]) for r in mixed)
max_syn = max(int(r["synthetic_m1_count"]) for r in mixed)

print("=" * 60)
print(f"MIXED weeks total: {len(mixed)}")
print(f"Active M1 true range: min = {min_act}, max = {max_act}")
print(f"Synthetic M1 true range: min = {min_syn}, max = {max_syn}")

for r in mixed:
    act = int(r["active_m1_count"])
    syn = int(r["synthetic_m1_count"])
    if act == min_act:
        print(f"Min active: Week {r['week_index']} ({r['week_start_utc']}): act={act}, syn={syn}")
    if syn == max_syn:
        print(
            f"Max synthetic: Week {r['week_index']} ({r['week_start_utc']}): act={act}, syn={syn}"
        )

# 2. Investigate the 1 missing minute in week 144 (2026-09-27 to 2026-10-04)
print("=" * 60)
print("Investigating Week 144 (2026-09-27 21:00 UTC to 2026-10-04 21:00 UTC)")
# Load September and October 2026 raw bars
b_sep = read_m1_csv(
    DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_09.csv", PriceType.BID
)
b_oct = read_m1_csv(
    DATA_ROOT / "xauusd" / "bid" / "m1" / "xauusd_bid_m1_2026_10.csv", PriceType.BID
)

w144_bars = [
    b
    for b in b_sep + b_oct
    if datetime(2026, 9, 27, 21, 0, tzinfo=UTC)
    <= b.open_time
    < datetime(2026, 10, 4, 21, 0, tzinfo=UTC)
]
print(f"Total bars in Week 144: {len(w144_bars)}")

# Find timestamps and gaps
timestamps = {b.open_time for b in w144_bars}

# Check day by day
current_t = datetime(2026, 9, 27, 21, 0, tzinfo=UTC)
w_end = datetime(2026, 10, 4, 21, 0, tzinfo=UTC)

missing_minutes = []
while current_t < w_end:
    if current_t not in timestamps:
        missing_minutes.append(current_t)
    current_t += timedelta(minutes=1)

print(f"Total missing minutes in Week 144: {len(missing_minutes)}")

# Weekend closure is Friday 21:00 UTC (17:00 NY) to Sunday 22:00 UTC (18:00 NY) = 49 hours = 2940 mins
# Daily rollovers Monday-Thursday: 21:00 to 22:00 UTC = 4 x 60 = 240 mins
# Total expected closed: 2940 + 240 = 3180 mins.
# Let's find which missing minute is during NORMAL TRADING HOURS!
trading_missing = []
for m in missing_minutes:
    wd = m.weekday()  # Monday=0, Sunday=6
    h = m.hour
    is_weekend = (wd == 4 and h >= 21) or (wd == 5) or (wd == 6 and h < 22)
    is_rollover = wd in (0, 1, 2, 3) and (21 <= h < 22)
    if not is_weekend and not is_rollover:
        trading_missing.append(m)

print(f"Missing minutes outside standard closure hours: {len(trading_missing)}")
for tm in trading_missing:
    print(f" -> Missing trading minute: {tm} (Weekday {tm.strftime('%A %Y-%m-%d %H:%M UTC')})")

# 3. Investigate the 151 missing minutes in Week 141 (2026-09-06 to 2026-09-13, US Labor Day)
print("=" * 60)
print("Investigating Week 141 (2026-09-06 21:00 UTC to 2026-09-13 21:00 UTC)")
w141_bars = [
    b
    for b in b_sep
    if datetime(2026, 9, 6, 21, 0, tzinfo=UTC)
    <= b.open_time
    < datetime(2026, 9, 13, 21, 0, tzinfo=UTC)
]
print(f"Total bars in Week 141: {len(w141_bars)}")
ts141 = {b.open_time for b in w141_bars}

missing_141 = []
cur = datetime(2026, 9, 6, 21, 0, tzinfo=UTC)
end141 = datetime(2026, 9, 13, 21, 0, tzinfo=UTC)
while cur < end141:
    if cur not in ts141:
        missing_141.append(cur)
    cur += timedelta(minutes=1)

trading_missing_141 = []
for m in missing_141:
    wd = m.weekday()
    h = m.hour
    is_weekend = (wd == 4 and h >= 21) or (wd == 5) or (wd == 6 and h < 22)
    is_rollover = wd in (0, 1, 2, 3) and (21 <= h < 22)
    if not is_weekend and not is_rollover:
        trading_missing_141.append(m)

print(f"Missing minutes outside standard closure hours in Week 141: {len(trading_missing_141)}")
if trading_missing_141:
    print(f"Start of holiday gap: {trading_missing_141[0]} UTC")
    print(f"End of holiday gap:   {trading_missing_141[-1]} UTC")
    print(f"Total minutes in this holiday gap: {len(trading_missing_141)}")
    # Group by date
    from collections import Counter

    days_cnt = Counter(tm.strftime("%Y-%m-%d") for tm in trading_missing_141)
    for day, cnt in days_cnt.items():
        print(f"  Date {day}: {cnt} missing minutes")
