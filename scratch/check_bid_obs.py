from datetime import datetime, UTC
from decimal import Decimal
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached
from traderlab.application.analysis.resampling import resample_m1_candles
from traderlab.core.types import PriceType, Timeframe
from traderlab.domain.market_structure.order_block import detect_order_blocks, OrderBlockConfig
from traderlab.domain.market_structure.displacement import detect_displacements, DisplacementConfig
from traderlab.domain.market_structure.market_structure import detect_market_structure, MarketStructureConfig, filter_mss_candidates
from traderlab.domain.market_structure.mss_confirmation import evaluate_mss_confirmations, MssConfirmationConfig
from traderlab.domain.market_structure.swing import detect_swings, filter_confirmed_swings, TiePolicy
from traderlab.domain.market_structure.fvg import detect_fair_value_gaps

start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)
all_m1 = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
for tf, name in [(Timeframe.H4, 'H4'), (Timeframe.H2, 'H2')]:
    res = resample_m1_candles(all_m1, tf)
    c_list = [rc.candle for rc in res if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed]
    cand_map = {c.open_time: c for c in c_list}
    swings = filter_confirmed_swings(detect_swings(c_list, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT))
    ms_events, _ = detect_market_structure(c_list, swings, config=MarketStructureConfig(tick_size=Decimal('0.01'), break_threshold_ticks=1))
    disps = detect_displacements(c_list, config=DisplacementConfig(tick_size=Decimal('0.01'), min_body_ratio=Decimal('0.60'), min_bullish_close_location=Decimal('0.75'), max_bearish_close_location=Decimal('0.25'), atr_window=14, min_range_atr_multiple=Decimal('1.50')))
    cands = filter_mss_candidates(ms_events)
    fvgs = detect_fair_value_gaps(c_list)
    mss_records = evaluate_mss_confirmations(c_list, cands, displacements=disps, fvgs=fvgs, config=MssConfirmationConfig(max_confirmation_bars=3, require_displacement=True, require_fvg_confirmation=True))
    obs = detect_order_blocks(c_list, structure_breaks=ms_events, displacements=disps, mss_confirmations=mss_records, fvg_zones=fvgs, config=OrderBlockConfig(max_origin_lookback_bars=6))
    print(f"=== BID {name} ===")
    for i, ob in enumerate(obs, 1):
        orig_c = cand_map.get(ob.origin_candle_open_time)
        print(f"[{i}] OB_ID: {ob.order_block_id}")
        print(f"    Direction: {ob.direction.value}")
        print(f"    Origin Open: {ob.origin_candle_open_time}")
        print(f"    OB Zone: bottom={ob.zone_bottom} top={ob.zone_top} mid={ob.midpoint_50}")
        if orig_c:
            c_type = 'BULLISH' if orig_c.close > orig_c.open else 'BEARISH'
            print(f"    Origin Candle OHLC: O={orig_c.open} H={orig_c.high} L={orig_c.low} C={orig_c.close} ({c_type})")
            dir_match = (ob.direction.value == "BULLISH" and c_type == "BEARISH") or (ob.direction.value == "BEARISH" and c_type == "BULLISH")
            zone_match = (ob.zone_bottom == orig_c.low and ob.zone_top == orig_c.high)
            print(f"    Matches: dir_match={dir_match} zone_match={zone_match}")
