from datetime import datetime, UTC
from decimal import Decimal
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached
from traderlab.application.analysis.resampling import resample_m1_candles, DailyCloseMode
from traderlab.core.types import PriceType, Timeframe
from traderlab.domain.market_structure.order_block import detect_order_blocks, OrderBlockConfig, OrderBlockZoneMode
from traderlab.domain.market_structure.displacement import detect_displacements, DisplacementConfig
from traderlab.domain.market_structure.market_structure import detect_market_structure, MarketStructureConfig, filter_mss_candidates
from traderlab.domain.market_structure.mss_confirmation import evaluate_mss_confirmations, MssConfirmationConfig
from traderlab.domain.market_structure.swing import detect_swings, filter_confirmed_swings, TiePolicy
from traderlab.domain.market_structure.fvg import detect_fair_value_gaps

start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)
tick_size = Decimal('0.01')
ms_config = MarketStructureConfig(tick_size=tick_size, break_threshold_ticks=1)
disp_config = DisplacementConfig(tick_size=tick_size, min_body_ratio=Decimal('0.60'), min_bullish_close_location=Decimal('0.75'), max_bearish_close_location=Decimal('0.25'), atr_window=14, min_range_atr_multiple=Decimal('1.50'))
mss_config = MssConfirmationConfig(max_confirmation_bars=3, require_displacement=True, require_fvg_confirmation=True)
ob_config = OrderBlockConfig(max_origin_lookback_bars=6, zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE, tick_size=tick_size, invalidation_threshold_ticks=0, require_fvg=False)

all_detected_obs = []
for p_type in (PriceType.BID, PriceType.ASK):
    all_m1 = load_month_m1_candles_cached(2026, 9, p_type) + load_month_m1_candles_cached(2026, 10, p_type)
    for tf, name in [(Timeframe.D1, 'D1'), (Timeframe.H4, 'H4'), (Timeframe.H2, 'H2')]:
        c_list = [rc.candle for rc in resample_m1_candles(all_m1, tf) if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed]
        swings = filter_confirmed_swings(detect_swings(c_list, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT))
        ms_events, _ = detect_market_structure(c_list, swings, config=ms_config)
        disps = detect_displacements(c_list, config=disp_config)
        cands = filter_mss_candidates(ms_events)
        fvgs = detect_fair_value_gaps(c_list)
        mss_records = evaluate_mss_confirmations(c_list, cands, displacements=disps, fvgs=fvgs, config=mss_config)
        obs = detect_order_blocks(c_list, structure_breaks=ms_events, displacements=disps, mss_confirmations=mss_records, fvg_zones=fvgs, config=ob_config)
        all_detected_obs.append((p_type, name, c_list, obs))

collected = []
for pt, name, c_list, obs in all_detected_obs:
    for ob in obs:
        collected.append((pt, name, c_list, ob))

print(f"Total collected: {len(collected)}")
for idx, (pt, name, c_list, ob) in enumerate(collected[:12], 1):
    cand_map = {c.open_time: c for c in c_list}
    c = cand_map.get(ob.origin_candle_open_time)
    c_type = "BULLISH" if c and c.close > c.open else ("BEARISH" if c and c.close < c.open else "DOJI")
    print(f"PROOF #{idx}: {name} {pt.value} {ob.direction.value}")
    print(f"   OB_ID: {ob.order_block_id}")
    print(f"   Origin: {ob.origin_candle_open_time}")
    print(f"   Zone: [{ob.zone_bottom}, {ob.zone_top}]")
    if c:
        print(f"   Candle OHLC: O={c.open} H={c.high} L={c.low} C={c.close} ({c_type})")
        inv_dir = (ob.direction.value == "BULLISH" and c_type == "BEARISH") or (ob.direction.value == "BEARISH" and c_type == "BULLISH")
        inv_zone = (ob.zone_bottom == c.low and ob.zone_top == c.high)
        print(f"   Invariants: dir_match={inv_dir} zone_match={inv_zone}")
    print(f"   Confirmation Time: {ob.confirmation_time}")
    print(f"   Lifecycle transitions: {len(ob.lifecycle_events)}")
    for ev in ob.lifecycle_events:
        print(f"      -> ev_time={ev.event_time} bar_open={ev.bar_open_time} {ev.status_before.value}->{ev.status_after.value} trig={ev.trigger_price}")
