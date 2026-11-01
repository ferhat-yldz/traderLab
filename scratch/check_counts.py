from datetime import UTC, datetime
from decimal import Decimal
from traderlab.core.types import PriceType, Timeframe
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached
from traderlab.application.analysis.resampling import resample_m1_candles, DailyCloseMode
from traderlab.domain.market_structure.swing import (
    detect_swings,
    filter_confirmed_swings,
    TiePolicy,
)
from traderlab.domain.market_structure.market_structure import (
    detect_market_structure,
    filter_mss_candidates,
    MarketStructureConfig,
)
from traderlab.domain.market_structure.fvg import detect_fair_value_gaps
from traderlab.domain.market_structure.displacement import (
    detect_displacements,
    summarize_displacements,
    DisplacementConfig,
)
from traderlab.domain.market_structure.mss_confirmation import (
    evaluate_mss_confirmations,
    summarize_mss_confirmations,
    MssConfirmationConfig,
)

start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)
tick_size = Decimal("0.01")
ms_config = MarketStructureConfig(tick_size=tick_size, break_threshold_ticks=1)
disp_config = DisplacementConfig(tick_size=tick_size)
mss_config = MssConfirmationConfig(max_confirmation_bars=3)

for p_type in (PriceType.BID, PriceType.ASK):
    all_m1 = load_month_m1_candles_cached(2026, 9, p_type) + load_month_m1_candles_cached(
        2026, 10, p_type
    )
    d1_ny_c = [
        rc.candle
        for rc in resample_m1_candles(
            all_m1, Timeframe.D1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
        )
        if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
    ]
    d1_utc_c = [
        rc.candle
        for rc in resample_m1_candles(
            all_m1, Timeframe.D1, daily_close_mode=DailyCloseMode.UTC_MIDNIGHT
        )
        if start_utc <= rc.candle.open_time <= end_utc
        and rc.candle.is_closed
        and not (rc.candle.open_time.weekday() == 6 and rc.candle.open_time.hour == 0)
    ]
    h4_c = [
        rc.candle
        for rc in resample_m1_candles(all_m1, Timeframe.H4)
        if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
    ]
    h2_c = [
        rc.candle
        for rc in resample_m1_candles(all_m1, Timeframe.H2)
        if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
    ]

    print(f"\n=== {p_type.value} ===")
    for name, c_list in [("D1 NY", d1_ny_c), ("D1 UTC", d1_utc_c), ("H4", h4_c), ("H2", h2_c)]:
        swings = filter_confirmed_swings(
            detect_swings(c_list, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
        )
        evs, _ = detect_market_structure(c_list, swings, config=ms_config)
        cands = filter_mss_candidates(evs)
        disps = detect_displacements(c_list, config=disp_config)
        fvgs = detect_fair_value_gaps(c_list)
        recs = evaluate_mss_confirmations(
            c_list, cands, displacements=disps, fvgs=fvgs, config=mss_config
        )
        d_sum = summarize_displacements(disps)
        m_sum = summarize_mss_confirmations(recs)
        print(
            f"{name:<8} | Bars: {len(c_list):<3} | Disp+: {d_sum['bullish_displacements']:<2} Disp-: {d_sum['bearish_displacements']:<2} | MSS_C: {len(cands):<2} | Conf: {m_sum['mss_confirmed']:<2} | Pending: {m_sum['pending']:<2} | Rej: {m_sum['rejected']:<2}"
        )
