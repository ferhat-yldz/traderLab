"""Exhaustive chronology extraction for the 6-respect H4 Bearish FVG."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from decimal import Decimal

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    resample_m1_candles,
)
from traderlab.core.types import PriceType, Timeframe
from traderlab.domain.market_structure.fvg import (
    detect_fair_value_gaps,
)
from traderlab.domain.market_structure.fvg_respect import (
    FvgReactionDirection,
    FvgRespectConfig,
    evaluate_fvg_respect,
)
from traderlab.interfaces.dashboard.app import (
    load_month_m1_candles_cached,
)

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)

all_m1 = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
res = resample_m1_candles(all_m1, Timeframe.H4, daily_close_mode=DailyCloseMode.NEW_YORK_1700)
raw_candles = [rc.candle for rc in res if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed]
fvgs = detect_fair_value_gaps(raw_candles)

target_id = "XAUUSD:market_data_lab_m1_derived_h4:BID:H4:20260928080000:BEARISH:r0"
fvg = next((f for f in fvgs if f.id == target_id), None)
assert fvg is not None

config = FvgRespectConfig(
    min_departure_ticks=Decimal("1"),
    min_bars_outside_zone=1,
    min_bars_between_retests=1,
    reaction_confirmation_bars=3,
    min_reaction_ticks=Decimal("1"),
    max_reported_retests=4,
    tick_size=Decimal("0.01"),
)

summary = evaluate_fvg_respect(fvg, raw_candles, config=config)

print("=" * 110)
print(f"CHRONOLOGY OF 6-RESPECT H4 BEARISH FVG: {fvg.id}")
print(f"Zone: [{fvg.bottom_price} - {fvg.top_price}] | Width: {fvg.width} | Formation: {fvg.formation_time.isoformat()}")
print("=" * 110)

departure_dist = config.min_departure_ticks * config.tick_size
reaction_dist = config.min_reaction_ticks * config.tick_size

# Trace candle by candle between retests to find exact departure candles and reaction confirmation candles
n_candles = len(raw_candles)
events = summary.retest_events

for ev in events:
    seq = ev.retest_sequence_number
    t_open = ev.touch_bar_open_time
    t_close = ev.touch_detection_time

    # Find touch candle index
    touch_idx = next(i for i, c in enumerate(raw_candles) if c.open_time == t_open)
    touch_candle = raw_candles[touch_idx]

    # Find reaction confirmation candle
    conf_candle = None
    if ev.reaction_confirmation_time is not None:
        conf_candle = next(c for c in raw_candles if c.close_time == ev.reaction_confirmation_time)

    # Find departure candle after this event
    # A departure candle for Bearish Resistance has high < zone_bottom and close <= zone_bottom - departure_dist
    dep_candle = None
    for k in range(touch_idx + 1, n_candles):
        c_k = raw_candles[k]
        if (c_k.high < fvg.bottom_price) and (c_k.close <= fvg.bottom_price - departure_dist):
            dep_candle = c_k
            break

    print(f"\n[Retest #{seq}]")
    print(f"  Touch Bar:       Open={touch_candle.open_time.isoformat()} | Close={touch_candle.close_time.isoformat()}")
    print(f"                   OHLC: O={touch_candle.open} H={touch_candle.high} L={touch_candle.low} C={touch_candle.close}")
    print(f"  Touch Detection: {ev.touch_detection_time.isoformat()}")
    print(f"  Touch Price:     {ev.touch_price} (High into Bearish Zone)")
    print(f"  Penetration:     Depth={ev.penetration_depth} ({ev.penetration_percentage:.2f}%)")
    print(f"  Effective Role:  {ev.effective_direction.value} ({ev.lifecycle_phase})")
    print(f"  Status:          {ev.status.value}")
    if conf_candle:
        print(f"  Reaction Conf:   Bar Open={conf_candle.open_time.isoformat()} | Close={conf_candle.close_time.isoformat()}")
        print(f"                   OHLC: O={conf_candle.open} H={conf_candle.high} L={conf_candle.low} C={conf_candle.close}")
        print(f"                   Distance: {ev.reaction_distance} (Close {conf_candle.close} <= {fvg.bottom_price - reaction_dist})")
        print(f"                   Conf Time > Detection Time: {ev.reaction_confirmation_time > ev.touch_detection_time} ({ev.reaction_confirmation_time} > {ev.touch_detection_time})")
    if dep_candle:
        print(f"  Departure Bar:   Open={dep_candle.open_time.isoformat()} | Close={dep_candle.close_time.isoformat()}")
        print(f"                   OHLC: O={dep_candle.open} H={dep_candle.high} L={dep_candle.low} C={dep_candle.close}")
        print(f"                   Condition: High {dep_candle.high} < {fvg.bottom_price} and Close {dep_candle.close} <= {fvg.bottom_price - departure_dist} (True)")
