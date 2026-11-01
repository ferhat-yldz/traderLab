"""Diagnose Order Blocks generated in Faz 4A across all timeframes."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    resample_m1_candles,
)
from traderlab.core.types import PriceType, Timeframe
from traderlab.domain.market_structure.displacement import (
    DisplacementConfig,
    detect_displacements,
)
from traderlab.domain.market_structure.fvg import detect_fair_value_gaps
from traderlab.domain.market_structure.liquidity_sweep import (
    detect_liquidity_sweeps,
)
from traderlab.domain.market_structure.market_structure import (
    MarketStructureConfig,
    detect_market_structure,
    filter_mss_candidates,
)
from traderlab.domain.market_structure.mss_confirmation import (
    MssConfirmationConfig,
    evaluate_mss_confirmations,
)
from traderlab.domain.market_structure.order_block import (
    OrderBlockConfig,
    OrderBlockDirection,
    OrderBlockZoneMode,
    detect_order_blocks,
)
from traderlab.domain.market_structure.swing import (
    TiePolicy,
    detect_swings,
    filter_confirmed_swings,
)
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached


def diagnose():
    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)

    tick_size = Decimal("0.01")
    ms_config = MarketStructureConfig(tick_size=tick_size, break_threshold_ticks=1)
    disp_config = DisplacementConfig(
        tick_size=tick_size,
        min_body_ratio=Decimal("0.60"),
        min_bullish_close_location=Decimal("0.75"),
        max_bearish_close_location=Decimal("0.25"),
        atr_window=14,
        min_range_atr_multiple=Decimal("1.50"),
    )
    mss_config = MssConfirmationConfig(
        max_confirmation_bars=3,
        require_displacement=True,
        require_fvg_confirmation=True,
    )
    ob_config = OrderBlockConfig(
        max_origin_lookback_bars=6,
        zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE,
        tick_size=tick_size,
        invalidation_threshold_ticks=0,
        require_fvg=False,
    )

    for p_type in (PriceType.BID, PriceType.ASK):
        m1_sep = load_month_m1_candles_cached(2026, 9, p_type)
        m1_oct = load_month_m1_candles_cached(2026, 10, p_type)
        all_m1 = m1_sep + m1_oct

        h4_res = resample_m1_candles(all_m1, Timeframe.H4)
        h2_res = resample_m1_candles(all_m1, Timeframe.H2)

        h4_c = [
            rc.candle
            for rc in h4_res
            if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
        ]
        h2_c = [
            rc.candle
            for rc in h2_res
            if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
        ]

        for name, c_list in [("H4", h4_c), ("H2", h2_c)]:
            cand_map = {c.open_time: c for c in c_list}
            swings = filter_confirmed_swings(
                detect_swings(c_list, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
            )
            sweeps = detect_liquidity_sweeps(c_list, swings, tick_size=tick_size)
            ms_events, _ = detect_market_structure(c_list, swings, config=ms_config)
            candidates = filter_mss_candidates(ms_events)
            fvgs = detect_fair_value_gaps(c_list)
            displacements = detect_displacements(c_list, config=disp_config)
            mss_records = evaluate_mss_confirmations(
                c_list,
                candidates,
                displacements=displacements,
                fvgs=fvgs,
                sweeps=sweeps,
                config=mss_config,
            )
            obs = detect_order_blocks(
                c_list,
                structure_breaks=ms_events,
                displacements=displacements,
                mss_confirmations=mss_records,
                fvg_zones=fvgs,
                config=ob_config,
            )

            print(f"\n==================== {p_type.value} {name} (Total OB: {len(obs)}) ====================")
            for i, ob in enumerate(obs, 1):
                orig_c = cand_map.get(ob.origin_candle_open_time)
                print(f"[{i}] ID: {ob.order_block_id}")
                print(f"    Direction: {ob.direction.value}")
                print(f"    Origin Open: {ob.origin_candle_open_time} | Origin Close: {ob.origin_candle_close_time}")
                if orig_c is not None:
                    print(f"    Candle OHLC: O={orig_c.open} H={orig_c.high} L={orig_c.low} C={orig_c.close}")
                    is_bull_c = orig_c.close > orig_c.open
                    is_bear_c = orig_c.close < orig_c.open
                    print(f"    Candle Type: {'BULLISH' if is_bull_c else ('BEARISH' if is_bear_c else 'DOJI')}")
                    # Check invariant:
                    inv_dir = (ob.direction == OrderBlockDirection.BULLISH and is_bear_c) or (
                        ob.direction == OrderBlockDirection.BEARISH and is_bull_c
                    )
                    inv_zone = (ob.zone_bottom == orig_c.low) and (ob.zone_top == orig_c.high)
                    print(f"    INVARIANTS -> Direction Matches Opposing Candle: {inv_dir} | Zone Matches Origin Low/High: {inv_zone}")
                    if not inv_dir:
                        print(f"    *** INVARIANT VIOLATION: OB is {ob.direction.value} but candle is {'BULLISH' if is_bull_c else 'BEARISH'}")
                    if not inv_zone:
                        print(f"    *** ZONE MISMATCH: ob.zone=[{ob.zone_bottom}, {ob.zone_top}] vs cand=[{orig_c.low}, {orig_c.high}]")
                print(f"    Zone: bottom={ob.zone_bottom} top={ob.zone_top} mid={ob.midpoint_50}")
                print(f"    Confirmation Time: {ob.confirmation_time}")
                print(f"    Status: {ob.status.value}")
                print(f"    Lifecycle transitions: {len(ob.lifecycle_events)}")
                for ev in ob.lifecycle_events:
                    print(f"        -> {ev.event_time} {ev.status_before.value}->{ev.status_after.value} trigger={ev.trigger_price} bar_open={ev.bar_open_time}")


if __name__ == "__main__":
    diagnose()
