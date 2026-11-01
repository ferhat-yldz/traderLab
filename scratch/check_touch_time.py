from datetime import datetime, UTC
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached
from traderlab.core.types import PriceType
from decimal import Decimal

m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
m1_ask = load_month_m1_candles_cached(2026, 9, PriceType.ASK) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)

plan_avail = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
target_price = Decimal("4238.210")

print(f"Searching for BID high >= {target_price} after {plan_avail}...")
found = []
for c in m1_bid:
    if c.open_time >= plan_avail:
        if c.high >= target_price:
            found.append(c)
            if len(found) <= 10:
                print(f"  Touch at {c.open_time.strftime('%Y-%m-%d %H:%M UTC')}: Open={c.open} High={c.high} Low={c.low} Close={c.close}")

print(f"Total M1 candles touching 4238.210 on BID: {len(found)}")
if found:
    delta = found[0].open_time - plan_avail
    print(f"First touch was {delta} after plan available time.")
