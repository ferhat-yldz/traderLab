"""Investigation of PIT reconstruction of Order Blocks and Setup Candidates."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from traderlab.domain.market_structure.fvg import (
    detect_fair_value_gaps,
)
from traderlab.domain.market_structure.fvg_respect import (
    FvgRespectConfig,
    evaluate_all_fvgs_respect,
)
from traderlab.domain.market_structure.ict_setup import (
    IctConfluenceType,
    IctSetupConfig,
    detect_ict_setup_candidates,
    filter_ict_setups_as_of,
)
from traderlab.domain.market_structure.liquidity_sweep import (
    LiquiditySweepConfig,
    detect_liquidity_sweeps,
    filter_confirmed_sweeps,
)
from traderlab.domain.market_structure.market_structure import (
    MarketStructureConfig,
    detect_market_structure,
    filter_confirmed_structure_breaks,
)
from traderlab.domain.market_structure.mss_confirmation import (
    MssConfirmationConfig,
    evaluate_mss_confirmations,
    filter_confirmed_mss,
)
from traderlab.domain.market_structure.order_block import (
    OrderBlockConfig,
    OrderBlockRecord,
    OrderBlockStatus,
    detect_order_blocks,
    filter_active_order_blocks,
    filter_order_blocks_as_of,
)
from traderlab.domain.market_structure.ote import (
    generate_ote_zones,
)
from traderlab.domain.market_structure.premium_discount import (
    identify_dealing_ranges,
)
from traderlab.domain.market_structure.swing import (
    TiePolicy,
    detect_swings,
    filter_confirmed_swings,
)
from traderlab.interfaces.dashboard.app import (
    load_month_m1_candles_cached,
)

START_UTC = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
END_UTC = datetime(2026, 10, 15, 23, 59, 59, tzinfo=UTC)

RESPECT_CONFIG = FvgRespectConfig(
    min_departure_ticks=Decimal("1"),
    min_bars_outside_zone=1,
    min_bars_between_retests=1,
    reaction_confirmation_bars=3,
    min_reaction_ticks=Decimal("1"),
    max_reported_retests=4,
    tick_size=Decimal("0.01"),
)
SETUP_CONFIG = IctSetupConfig()


def main() -> None:
    m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)

    for tf in [Timeframe.H4, Timeframe.H2]:
        res = resample_m1_candles(m1_bid, tf, daily_close_mode=DailyCloseMode.NEW_YORK_1700)
        c = [rc.candle for rc in res if START_UTC <= rc.candle.open_time <= END_UTC and rc.candle.is_closed]
        sw = filter_confirmed_swings(detect_swings(c, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT))
        sb, _ = detect_market_structure(c, sw, config=MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1))
        conf_sb = filter_confirmed_structure_breaks(sb)
        disp = detect_displacements(
            c,
            config=DisplacementConfig(
                tick_size=Decimal("0.01"),
                min_body_ratio=Decimal("0.60"),
                min_bullish_close_location=Decimal("0.75"),
                max_bearish_close_location=Decimal("0.25"),
                atr_window=14,
                min_range_atr_multiple=Decimal("1.50"),
            ),
        )
        conf_disp = [d for d in disp if getattr(d, "confirmation_status", "CONFIRMED") == "CONFIRMED"]
        fvg = detect_fair_value_gaps(c)
        mss = evaluate_mss_confirmations(
            c,
            [b for b in conf_sb if b.is_choch],
            displacements=conf_disp,
            fvgs=fvg,
            sweeps=filter_confirmed_sweeps(detect_liquidity_sweeps(c, sw, config=LiquiditySweepConfig(tick_size=Decimal("0.01"), min_penetration_ticks=1))),
            config=MssConfirmationConfig(max_confirmation_bars=3, require_displacement=True, require_fvg_confirmation=True),
        )
        conf_mss = filter_confirmed_mss(mss)
        obs = detect_order_blocks(
            c,
            structure_breaks=conf_sb,
            displacements=conf_disp,
            mss_confirmations=conf_mss,
            fvg_zones=fvg,
            config=OrderBlockConfig(max_origin_lookback_bars=6),
        )

        inval_obs = [o for o in obs if o.status == OrderBlockStatus.INVALIDATED]
        print(f"\n[{tf.name}] Found {len(obs)} OBs, {len(inval_obs)} Invalidated")
        for o in inval_obs:
            print(f"  OB conf={o.confirmation_time} | inval={o.invalidation_time}")


if __name__ == "__main__":
    main()
