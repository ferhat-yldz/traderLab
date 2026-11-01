from datetime import datetime, timedelta, timezone
from traderlab.infrastructure.calendars.forex_session import XAUUSDTradingCalendar
from traderlab.application.analysis.resampling import get_window_boundaries, DailyCloseMode
from traderlab.core.types import Timeframe

cal = XAUUSDTradingCalendar()


def count_open_minutes(w_open: datetime, w_close: datetime) -> int:
    cur = w_open
    open_count = 0
    while cur < w_close:
        if cal.is_market_open(cur):
            open_count += 1
        cur += timedelta(minutes=1)
    return open_count


# Check Jan 2 (Tuesday) UTC_MIDNIGHT
w_open = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
w_close = datetime(2024, 1, 3, 0, 0, tzinfo=timezone.utc)
print(f"Jan 2 UTC D1 open minutes: {count_open_minutes(w_open, w_close)}")

# Check Jan 7 (Sunday) UTC_MIDNIGHT
w_open = datetime(2024, 1, 7, 0, 0, tzinfo=timezone.utc)
w_close = datetime(2024, 1, 8, 0, 0, tzinfo=timezone.utc)
print(f"Jan 7 Sunday UTC D1 open minutes: {count_open_minutes(w_open, w_close)}")

# Check Jan 5 (Friday) UTC_MIDNIGHT
w_open = datetime(2024, 1, 5, 0, 0, tzinfo=timezone.utc)
w_close = datetime(2024, 1, 6, 0, 0, tzinfo=timezone.utc)
print(f"Jan 5 Friday UTC D1 open minutes: {count_open_minutes(w_open, w_close)}")

# Check Jan 1 (Monday holiday) UTC_MIDNIGHT
w_open = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
w_close = datetime(2024, 1, 2, 0, 0, tzinfo=timezone.utc)
print(f"Jan 1 New Year UTC D1 open minutes: {count_open_minutes(w_open, w_close)}")

# Check Jan 15 (MLK Day early close) UTC_MIDNIGHT
w_open = datetime(2024, 1, 15, 0, 0, tzinfo=timezone.utc)
w_close = datetime(2024, 1, 16, 0, 0, tzinfo=timezone.utc)
print(f"Jan 15 MLK Day UTC D1 open minutes: {count_open_minutes(w_open, w_close)}")

# Check Jan 2 NY 17:00
# 2024-01-01 22:00 UTC to 2024-01-02 22:00 UTC
w_open = datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc)
w_close = datetime(2024, 1, 2, 22, 0, tzinfo=timezone.utc)
print(f"Jan 2 NY 1700 D1 open minutes: {count_open_minutes(w_open, w_close)}")
