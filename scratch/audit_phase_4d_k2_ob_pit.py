"""Audit script for Phase 4D-K2: Historical Order Block PIT Reconstruction Audit."""
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
    evaluate_order_block_lifecycle,
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
        resampled = resample_m1_candles(m1_bid, tf, daily_close_mode=DailyCloseMode.NEW_YORK_1700)
        raw_candles = [rc.candle for rc in resampled if START_UTC <= rc.candle.open_time <= END_UTC and rc.candle.is_closed]

        swings = detect_swings(raw_candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
        conf_swings = filter_confirmed_swings(swings)
        sweeps = detect_liquidity_sweeps(raw_candles, conf_swings, config=LiquiditySweepConfig(tick_size=Decimal("0.01"), min_penetration_ticks=1))
        conf_sweeps = filter_confirmed_sweeps(sweeps)
        sb, _ = detect_market_structure(raw_candles, conf_swings, config=MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1))
        conf_sb = filter_confirmed_structure_breaks(sb)
        disps = detect_displacements(
            raw_candles,
            config=DisplacementConfig(
                tick_size=Decimal("0.01"),
                min_body_ratio=Decimal("0.60"),
                min_bullish_close_location=Decimal("0.75"),
                max_bearish_close_location=Decimal("0.25"),
                atr_window=14,
                min_range_atr_multiple=Decimal("1.50"),
            ),
        )
        conf_disps = [d for d in disps if getattr(d, "confirmation_status", "CONFIRMED") == "CONFIRMED"]
        fvgs = detect_fair_value_gaps(raw_candles)
        mss = evaluate_mss_confirmations(
            raw_candles,
            [b for b in conf_sb if b.is_choch],
            displacements=conf_disps,
            fvgs=fvgs,
            sweeps=conf_sweeps,
            config=MssConfirmationConfig(max_confirmation_bars=3, require_displacement=True, require_fvg_confirmation=True),
        )
        conf_mss = filter_confirmed_mss(mss)

        ranges = identify_dealing_ranges(conf_swings)
        otes = generate_ote_zones(ranges)
        respect_summaries = evaluate_all_fvgs_respect(fvgs, raw_candles, config=RESPECT_CONFIG)

        obs = detect_order_blocks(
            raw_candles,
            structure_breaks=conf_sb,
            displacements=conf_disps,
            mss_confirmations=conf_mss,
            fvg_zones=fvgs,
            config=OrderBlockConfig(max_origin_lookback_bars=6),
        )

        inval_obs = [ob for ob in obs if ob.status == OrderBlockStatus.INVALIDATED]

        # Let's test setup candidate detection across the whole series
        all_setups = detect_ict_setup_candidates(
            raw_candles,
            sweeps=conf_sweeps,
            structure_breaks=conf_sb,
            mss_records=conf_mss,
            displacements=conf_disps,
            fvg_zones=fvgs,
            order_blocks=obs,
            ote_zones=otes,
            fvg_respect_summaries=respect_summaries,
            dealing_ranges=ranges,
            config=SETUP_CONFIG,
        )

        print(f"\n=======================================================")
        print(f"Timeframe {tf.name}: Total Setups={len(all_setups)}, Invalidated OBs={len(inval_obs)}")
        print(f"=======================================================")

        for i, ob in enumerate(inval_obs, 1):
            assert ob.invalidation_time is not None
            t_pre = ob.confirmation_time - timedelta(minutes=1)
            t_mid = ob.confirmation_time + (ob.invalidation_time - ob.confirmation_time) / 2
            t_post = ob.invalidation_time + timedelta(minutes=1)

            print(f"\n--- Example {tf.name} #{i} ---")
            print(f"OB ID:  {ob.order_block_id[:60]}...")
            print(f"Conf:   {ob.confirmation_time}")
            print(f"Inval:  {ob.invalidation_time}")

            # Re-evaluate OB lifecycle at the 3 timestamps
            ob_pre = evaluate_order_block_lifecycle(raw_candles, ob, as_of_utc=t_pre)
            ob_mid = evaluate_order_block_lifecycle(raw_candles, ob, as_of_utc=t_mid)
            ob_post = evaluate_order_block_lifecycle(raw_candles, ob, as_of_utc=t_post)

            print(f"  At t_pre  ({t_pre}): is_available={ob.is_available_at(t_pre)}, status={ob_pre.status.value}")
            print(f"  At t_mid  ({t_mid}): is_available={ob.is_available_at(t_mid)}, status={ob_mid.status.value}")
            print(f"  At t_post ({t_post}): is_available={ob.is_available_at(t_post)}, status={ob_post.status.value}")

            # Also check if any setup is causally linked to this OB's structure reference
            linked_setups = [
                s for s in all_setups
                if s.structure_event_id == ob.structure_break_event_id
                or s.mss_confirmation_id == ob.mss_confirmation_id
            ]
            print(f"  Linked Setups in all_setups: {len(linked_setups)}")
            for s in linked_setups:
                print(f"    Setup ID: {s.setup_id[:60]}... | Available: {s.setup_available_time}")
                print(f"    Setup order_block_id: {s.order_block_id}")
                ob_confs = [c for c in s.confluences if c.evidence_type == IctConfluenceType.ORDER_BLOCK]
                print(f"    Setup OB Confluences: {len(ob_confs)}")


if __name__ == "__main__":
    main()
