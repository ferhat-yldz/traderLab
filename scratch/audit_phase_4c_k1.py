"""Phase 4C-K1 Investigation Script."""

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
    FvgValidityState,
    detect_fair_value_gaps,
    track_fvg_lifecycle,
)
from traderlab.domain.market_structure.fvg_respect import (
    FvgRespectConfig,
    FvgRetestStatus,
    evaluate_all_fvgs_respect,
)
from traderlab.interfaces.dashboard.app import (
    load_month_m1_candles_cached,
)

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)

config = FvgRespectConfig(
    min_departure_ticks=Decimal("1"),
    min_bars_outside_zone=1,
    min_bars_between_retests=1,
    reaction_confirmation_bars=3,
    min_reaction_ticks=Decimal("1"),
    max_reported_retests=4,
    tick_size=Decimal("0.01"),
)

modes = [
    ("D1 NY", Timeframe.D1, DailyCloseMode.NEW_YORK_1700),
    ("D1 UTC", Timeframe.D1, DailyCloseMode.UTC_MIDNIGHT),
    ("H4", Timeframe.H4, DailyCloseMode.NEW_YORK_1700),
    ("H2", Timeframe.H2, DailyCloseMode.NEW_YORK_1700),
]

print("=" * 90)
print("PHASE 4C-K1: DETAILED INVALIDATED METRICS BREAKDOWN")
print("=" * 90)

for p_type in (PriceType.BID, PriceType.ASK):
    all_m1 = load_month_m1_candles_cached(2026, 9, p_type) + load_month_m1_candles_cached(2026, 10, p_type)
    for mode_name, tf, close_mode in modes:
        res = resample_m1_candles(all_m1, tf, daily_close_mode=close_mode)
        raw_candles = [rc.candle for rc in res if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed]
        fvgs = detect_fair_value_gaps(raw_candles)
        summaries = evaluate_all_fvgs_respect(fvgs, raw_candles, config=config)

        unique_fvg_count = len(summaries)
        unique_invalidated_normal_fvg_count = sum(
            1 for s in summaries if any(e.status == FvgRetestStatus.INVALIDATED and e.lifecycle_phase == "NORMAL" for e in s.retest_events)
        )
        unique_inverted_fvg_count = sum(1 for s in summaries if s.inversion_confirmed_at is not None)
        
        # Breakdown of invalidated retest events by lifecycle phase:
        normal_inv_events = sum(sum(1 for e in s.retest_events if e.status == FvgRetestStatus.INVALIDATED and e.lifecycle_phase == "NORMAL") for s in summaries)
        inverted_inv_events = sum(sum(1 for e in s.retest_events if e.status == FvgRetestStatus.INVALIDATED and e.lifecycle_phase == "INVERTED") for s in summaries)
        total_invalidated_retest_episodes = sum(sum(1 for e in s.retest_events if e.status == FvgRetestStatus.INVALIDATED) for s in summaries)

        # Lifecycle events:
        lifecycles = [track_fvg_lifecycle(f, raw_candles) for f in fvgs]
        invalidation_lifecycle_event_count = sum(
            sum(1 for le in les if le.validity_state in (FvgValidityState.INVALIDATED, FvgValidityState.INVERTED)) for les in lifecycles
        )
        unique_lifecycle_invalidated_fvg = sum(
            1 for les in lifecycles if any(le.validity_state in (FvgValidityState.INVALIDATED, FvgValidityState.INVERTED) for le in les)
        )

        print(f"\n[{p_type.value} - {mode_name}] (Mum: {len(raw_candles)})")
        print(f"  unique_fvg_count:                      {unique_fvg_count}")
        print(f"  unique_invalidated_normal_fvg_count:   {unique_invalidated_normal_fvg_count}")
        print(f"  unique_inverted_fvg_count:             {unique_inverted_fvg_count}")
        print(f"  unique_lifecycle_invalidated_fvg:      {unique_lifecycle_invalidated_fvg}")
        print(f"  invalidated_retest_episode_count:      {total_invalidated_retest_episodes} (Normal: {normal_inv_events}, Inverted: {inverted_inv_events})")
        print(f"  invalidation_lifecycle_event_count:    {invalidation_lifecycle_event_count}")
