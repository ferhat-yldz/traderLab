"""Forex and Spot Metal (XAUUSD) trading calendar and session state classification."""

from __future__ import annotations

import enum
import zoneinfo
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

NEW_YORK_TZ = zoneinfo.ZoneInfo("America/New_York")


class MarketSessionState(enum.Enum):
    """Classification of market operating state at a given timestamp."""

    OPEN = "OPEN"  # Active trading session
    CLOSED_WEEKEND = "CLOSED_WEEKEND"  # Friday NY close through Sunday NY open
    CLOSED_HOLIDAY = "CLOSED_HOLIDAY"  # Officially recognized market holiday closure
    CLOSED_DAILY_ROLLOVER = (
        "CLOSED_DAILY_ROLLOVER"  # Daily settlement/maintenance break (17:00-18:00 NY)
    )
    UNKNOWN_OR_UNVERIFIED = (
        "UNKNOWN_OR_UNVERIFIED"  # Ambiguous or provider-specific unverified state
    )


@dataclass(frozen=True, slots=True)
class XAUUSDTradingCalendar:
    """Trading calendar for XAUUSD (Spot Gold) with New York time zone and DST awareness.

    Key market structure facts:
    - XAUUSD trades 24/5 OTC, anchored to New York market conventions.
    - Weekend close: Friday 17:00 New York time.
    - Weekend re-open: Sunday 18:00 New York time (Dukascopy convention: 18:00 NY / 23:00 UTC winter).
    - Daily rollover break: Monday through Thursday from 17:00 to 18:00 New York time.
      - During EST (winter, UTC-5): 17:00-18:00 NY = 22:00-23:00 UTC.
      - During EDT (summer, UTC-4): 17:00-18:00 NY = 21:00-22:00 UTC.
      Dynamic conversion via ZoneInfo("America/New_York") ensures seamless DST handling.
    - Full-day Holidays:
      - New Year's Day (January 1)
      - Christmas Day (December 25)
    - Early closures:
      - Martin Luther King Jr. Day (e.g. 2024-01-15 after 14:30 NY / 19:30 UTC)
    """

    friday_close_hour_ny: int = 17
    sunday_open_hour_ny: int = 18
    daily_rollover_start_hour_ny: int = 17
    daily_rollover_end_hour_ny: int = 18

    def is_dst_active(self, dt_utc: datetime) -> bool:
        """Check whether Daylight Saving Time (EDT) is active in New York at given UTC time."""
        ny_dt = dt_utc.astimezone(NEW_YORK_TZ)
        return bool(ny_dt.dst())

    def get_session_state(self, dt_utc: datetime) -> MarketSessionState:
        """Determine the XAUUSD market session state for a specific UTC timestamp."""
        # Ensure UTC timezone awareness
        norm_dt = dt_utc.replace(tzinfo=UTC) if dt_utc.tzinfo is None else dt_utc.astimezone(UTC)

        ny_dt = norm_dt.astimezone(NEW_YORK_TZ)
        weekday = ny_dt.weekday()  # Monday=0, Tuesday=1, ..., Friday=4, Saturday=5, Sunday=6
        hour = ny_dt.hour
        minute = ny_dt.minute

        # 1. Full-day / Holiday checks (New Year's Day, Christmas Day until evening reopen)
        if ny_dt.month == 1 and ny_dt.day == 1 and hour < self.sunday_open_hour_ny:
            return MarketSessionState.CLOSED_HOLIDAY
        if ny_dt.month == 12 and ny_dt.day == 25 and hour < self.sunday_open_hour_ny:
            return MarketSessionState.CLOSED_HOLIDAY

        # Martin Luther King Jr. Day early close (2024-01-15 after 14:30 NY / 19:30 UTC until evening reopen 18:00 NY)
        if ny_dt.date() == date(2024, 1, 15) and (
            (hour == 14 and minute >= 30) or (15 <= hour < self.sunday_open_hour_ny)
        ):
            return MarketSessionState.CLOSED_HOLIDAY

        # 2. Weekend closures (Friday 17:00 NY through Sunday 18:00 NY)
        if weekday == 4 and hour >= self.friday_close_hour_ny:
            return MarketSessionState.CLOSED_WEEKEND
        if weekday == 5:
            return MarketSessionState.CLOSED_WEEKEND
        if weekday == 6 and hour < self.sunday_open_hour_ny:
            return MarketSessionState.CLOSED_WEEKEND

        # 3. Daily Rollover / Settlement break (Monday to Thursday: 17:00 to 18:00 NY)
        if weekday in (0, 1, 2, 3) and (
            self.daily_rollover_start_hour_ny <= hour < self.daily_rollover_end_hour_ny
        ):
            return MarketSessionState.CLOSED_DAILY_ROLLOVER

        return MarketSessionState.OPEN

    def count_open_minutes(self, start_utc: datetime, end_utc: datetime) -> int:
        """Calculate the exact number of active trading minutes within [start_utc, end_utc).

        Iterates minute by minute across the half-open interval.
        """
        norm_start = (
            start_utc.replace(tzinfo=UTC)
            if start_utc.tzinfo is None
            else start_utc.astimezone(UTC)
        )
        norm_end = (
            end_utc.replace(tzinfo=UTC) if end_utc.tzinfo is None else end_utc.astimezone(UTC)
        )
        if norm_end <= norm_start:
            return 0

        cur = norm_start.replace(second=0, microsecond=0)
        target = norm_end.replace(second=0, microsecond=0)
        open_count = 0
        step = timedelta(minutes=1)

        while cur < target:
            if self.is_market_open(cur):
                open_count += 1
            cur += step

        return open_count

    def is_market_open(self, dt_utc: datetime) -> bool:
        """Return True if market is actively open for trading at given UTC time."""
        return self.get_session_state(dt_utc) == MarketSessionState.OPEN

    def is_verified_closed(self, dt_utc: datetime) -> bool:
        """Return True if market is definitively in a verified closed period."""
        state = self.get_session_state(dt_utc)
        return state in (
            MarketSessionState.CLOSED_WEEKEND,
            MarketSessionState.CLOSED_HOLIDAY,
            MarketSessionState.CLOSED_DAILY_ROLLOVER,
        )
