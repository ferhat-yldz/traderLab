from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from traderlab.infrastructure.calendars.forex_session import XAUUSDTradingCalendar, MarketSessionState

DATA_DIR = Path(r"C:\trader\traderLab\Market-Data-Lab\xauusd\bid\m1")
cal = XAUUSDTradingCalendar()

months = [6, 7, 8, 9, 10]

for m in months:
    fname = f"xauusd_bid_m1_2026_{m:02d}.csv"
    df = pd.read_csv(DATA_DIR / fname)
    df["dt"] = pd.to_datetime(df.timestamp, unit="ms", utc=True)
    df["step"] = df.timestamp.diff()
    
    # Check Saturdays & Sundays
    dows = df.dt.dt.dayofweek
    sat_count = (dows == 5).sum()
    sun_count = (dows == 6).sum()
    sun_pre22 = ((dows == 6) & (df.dt.dt.hour < 22)).sum()
    sun_post22 = ((dows == 6) & (df.dt.dt.hour >= 22)).sum()
    
    # Rollover hours: Mon-Thu 21:00-22:00 UTC (EDT summer)
    weekday_rollover = ((dows < 4) & (df.dt.dt.hour == 21)).sum()
    
    # Flat bars (O=H=L=C)
    flat_mask = (df.open == df.high) & (df.high == df.low) & (df.low == df.close)
    flat_count = flat_mask.sum()
    
    # Flat bars during rollover vs during weekend vs daytime
    flat_rollover = (flat_mask & (dows < 4) & (df.dt.dt.hour == 21)).sum()
    flat_sun = (flat_mask & (dows == 6)).sum()
    
    # Gaps (> 60k ms)
    gap_rows = df[df.step > 60000]
    
    trading_gaps = 0
    off_session_gaps = 0
    gap_details = []
    for _, r in gap_rows.iterrows():
        prev_t = r["dt"] - pd.Timedelta(milliseconds=r["step"])
        duration_mins = int(r["step"] // 60000)
        
        # Count minutes in gap that were market open
        curr = prev_t + pd.Timedelta(minutes=1)
        open_mins = 0
        while curr < r["dt"]:
            if cal.is_market_open(curr.to_pydatetime()):
                open_mins += 1
            curr += pd.Timedelta(minutes=1)
            
        if open_mins > 0:
            trading_gaps += 1
        else:
            off_session_gaps += 1
            
        gap_details.append({
            "start": prev_t,
            "end": r["dt"],
            "duration_mins": duration_mins,
            "open_mins": open_mins,
        })
        
    print(f"\n=================== 2026-{m:02d} ===================")
    print(f"Total Rows: {len(df):,}")
    print(f"Saturday Rows: {sat_count:,} | Sunday Rows: {sun_count:,} (Pre-22: {sun_pre22:,}, Post-22: {sun_post22:,})")
    print(f"Weekday Rollover (21:00 UTC) Rows: {weekday_rollover:,}")
    print(f"Total Flat Bars: {flat_count:,} (in Rollover: {flat_rollover:,}, in Sunday: {flat_sun:,})")
    print(f"Total Gaps (>60s): {len(gap_rows)} (Trading gaps: {trading_gaps}, Off-session weekend gaps: {off_session_gaps})")
    print("Gap Samples (first 4):")
    for gd in gap_details[:4]:
        print(f"  {gd['start']} -> {gd['end']} ({gd['duration_mins']} mins, {gd['open_mins']} open market mins)")
