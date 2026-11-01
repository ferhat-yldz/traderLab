from datetime import datetime, UTC
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached
from traderlab.core.types import PriceType

m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
m1_ask = load_month_m1_candles_cached(2026, 9, PriceType.ASK) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)

plan_avail = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
bid_after = [c for c in m1_bid if c.open_time >= plan_avail]
ask_after = [c for c in m1_ask if c.open_time >= plan_avail]

max_bid = max(c.high for c in bid_after)
max_ask = max(c.high for c in ask_after)
print(f"Max BID High after 2026-09-28 04:00 UTC: {max_bid}")
print(f"Max ASK High after 2026-09-28 04:00 UTC: {max_ask}")

# Also check between 04:00 and next days
for days in [1, 2, 5, 10, 20]:
    end_t = datetime(2026, 9, 28 + min(days, 2), 4, 0, tzinfo=UTC) if days < 3 else datetime(2026, 10, min(days-2, 15), 0, 0, tzinfo=UTC)
    b_window = [c for c in m1_bid if plan_avail <= c.open_time <= end_t]
    if b_window:
        print(f"Window up to {end_t.strftime('%Y-%m-%d %H:%M')}: Max BID High={max(c.high for c in b_window)}")
