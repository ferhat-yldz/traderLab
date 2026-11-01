"""Test setup candidate generation when passing all detected Order Blocks."""

from __future__ import annotations

import sys
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
from traderlab.domain.market_structure.fvg import (
    detect_fair_value_gaps,
)
from traderlab.domain.market_structure.fvg_respect import (
    FvgRespectConfig,
    evaluate_all_fvgs_respect,
)
from traderlab.domain.market_structure.ict_setup import (
    IctSetupConfig,
    detect_ict_setup_candidates,
    summarize_ict_setups,
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
    OrderBlockStatus,
    detect_order_blocks,
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

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

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

MODES = [
    ("D1 NY", Timeframe.D1, DailyCloseMode.NEW_YORK_1700),
    ("D1 UTC", Timeframe.D1, DailyCloseMode.UTC_MIDNIGHT),
    ("H4", Timeframe.H4, DailyCloseMode.NEW_YORK_1700),
    ("H2", Timeframe.H2, DailyCloseMode.NEW_YORK_1700),
]

m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
m1_ask = load_month_m1_candles_cached(2026, 9, PriceType.ASK) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)

total_setups_with_obs = 0
ob_confluence_count = 0
primary_ob_count = 0

for pt in (PriceType.BID, PriceType.ASK):
    m1_data = m1_bid if pt == PriceType.BID else m1_ask
    for mode_name, tf, close_mode in MODES:
        resampled = resample_m1_candles(m1_data, tf, daily_close_mode=close_mode)
        raw_candles = [
            rc.candle for rc in resampled
            if START_UTC <= rc.candle.open_time <= END_UTC and rc.candle.is_closed
        ]
        swings = detect_swings(raw_candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
        conf_swings = filter_confirmed_swings(swings)
        sweeps = detect_liquidity_sweeps(raw_candles, conf_swings, config=LiquiditySweepConfig(tick_size=Decimal("0.01"), min_penetration_ticks=1))
        conf_sweeps = filter_confirmed_sweeps(sweeps)

        struct_breaks, _ = detect_market_structure(raw_candles, conf_swings, config=MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1))
        conf_breaks = filter_confirmed_structure_breaks(struct_breaks)
        choch_events = [b for b in conf_breaks if b.is_choch]

        displacements = detect_displacements(
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
        conf_displacements = [d for d in displacements if getattr(d, "confirmation_status", "CONFIRMED") == "CONFIRMED"]
        fvgs = detect_fair_value_gaps(raw_candles)
        mss_records = evaluate_mss_confirmations(
            raw_candles,
            choch_events,
            displacements=conf_displacements,
            fvgs=fvgs,
            sweeps=conf_sweeps,
            config=MssConfirmationConfig(
                max_confirmation_bars=3,
                require_displacement=True,
                require_fvg_confirmation=True,
            ),
        )
        conf_mss = filter_confirmed_mss(mss_records)

        obs = detect_order_blocks(
            raw_candles,
            structure_breaks=conf_breaks,
            displacements=conf_displacements,
            mss_confirmations=conf_mss,
            fvg_zones=fvgs,
            config=OrderBlockConfig(max_origin_lookback_bars=6),
        )

        ranges = identify_dealing_ranges(conf_swings)
        otes = generate_ote_zones(ranges)
        respect_summaries = evaluate_all_fvgs_respect(fvgs, raw_candles, config=RESPECT_CONFIG)

        setups = detect_ict_setup_candidates(
            raw_candles,
            sweeps=conf_sweeps,
            structure_breaks=conf_breaks,
            mss_records=conf_mss,
            displacements=conf_displacements,
            fvg_zones=fvgs,
            order_blocks=obs,  # PASSING OBS DIRECTLY!
            ote_zones=otes,
            fvg_respect_summaries=respect_summaries,
            dealing_ranges=ranges,
            config=SETUP_CONFIG,
            d1_session_mode=close_mode.value if tf == Timeframe.D1 else None,
        )

        total_setups_with_obs += len(setups)
        for s in setups:
            ob_confs = [c for c in s.confluences if c.evidence_type.value == "ORDER_BLOCK"]
            if ob_confs:
                ob_confluence_count += 1
                print(f"[{pt.value} {mode_name}] Setup {s.setup_id} linked OB: {ob_confs[0].source_event_id}")
            if s.order_block_id:
                primary_ob_count += 1

print(f"\nTotal Setups with OBs: {total_setups_with_obs}")
print(f"Setups with OB Confluence: {ob_confluence_count}")
print(f"Setups with OB as Primary Zone: {primary_ob_count}")
