from datetime import datetime, UTC
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached
from traderlab.application.analysis.resampling import resample_m1_candles
from traderlab.core.types import Timeframe, PriceType
from traderlab.domain.market_structure.fvg import (
    detect_fair_value_gaps,
    track_fvg_lifecycle,
    FvgValidityState,
)

start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
cutoff_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)

m1 = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(
    2026, 10, PriceType.BID
)

for tf in (Timeframe.H4, Timeframe.H2):
    candles_res = resample_m1_candles(m1, tf)
    candles = [rc.candle for rc in candles_res if start_utc <= rc.candle.open_time <= cutoff_utc]
    c_by_open = {c.open_time: c for c in candles}
    fvgs = detect_fair_value_gaps(candles)
    print(f"\n==========================================")
    print(f"REAL ZONES FOR {tf.name}")
    print(f"==========================================")
    count = 0
    for f in fvgs:
        evs = track_fvg_lifecycle(
            f, candles, as_of_utc=cutoff_utc, confirm_inversion_on_retest=True
        )
        if not evs or evs[-1].validity_state != FvgValidityState.INVERTED:
            continue
        last_ev = evs[-1]
        inv_candle = c_by_open.get(last_ev.invalidated_by_candle_open)
        retest_ev = next((e for e in evs if e.validity_state == FvgValidityState.INVERTED), None)
        retest_candle = c_by_open.get(retest_ev.bar_open_time) if retest_ev else None
        count += 1
        rule_desc = "close < bottom" if f.is_bullish else "close > top"
        print(f"[{tf.name} Zone #{count}] {f.direction.value} {f.id}")
        print(
            f"  1. Formation: {f.formation_time} | Range: [{f.bottom_price} - {f.top_price}] | CE: {f.ce_price}"
        )
        if inv_candle:
            print(
                f"  2. Body Invalidation: Bar {inv_candle.open_time} -> {inv_candle.close_time} | Close: {inv_candle.close} (Rule: {rule_desc})"
            )
        if retest_candle:
            print(
                f"  3. Confirmed Inversion Retest: Bar {retest_candle.open_time} -> {retest_candle.close_time} | High: {retest_candle.high}, Low: {retest_candle.low}, Close: {retest_candle.close} | Confirmed at: {last_ev.inversion_confirmed_at}"
            )
        if count >= 5:
            break
