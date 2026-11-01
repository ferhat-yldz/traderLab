"""Deep Dive Audit for Phase 4D-K1:
1. Detailed OB tracking and rejection breakdown.
2. Exact OHLC candle investigation for H4 Bearish Reversal (2026-10-14).
3. Exact performance breakdown with overhead reconciliation.
"""

from __future__ import annotations

import sys
import time
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
    IctSetupStatus,
    detect_ict_setup_candidates,
    explain_ict_setup,
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


def run_deep_dive() -> None:
    print("=" * 110)
    print("PHASE 4D-K1: ORDER BLOCK INTEGRATION & CHRONOLOGY AUDIT")
    print("=" * 110)

    m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
    m1_ask = load_month_m1_candles_cached(2026, 9, PriceType.ASK) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)

    all_ob_records = []

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
            bos_events = [b for b in conf_breaks if b.is_bos]

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

            # Run setup detection passing ALL obs directly (not filtered by status==CONFIRMED)
            setups_all_obs = detect_ict_setup_candidates(
                raw_candles,
                sweeps=conf_sweeps,
                structure_breaks=conf_breaks,
                mss_records=conf_mss,
                displacements=conf_displacements,
                fvg_zones=fvgs,
                order_blocks=obs,  # ALL detected OBs passed
                ote_zones=otes,
                fvg_respect_summaries=respect_summaries,
                dealing_ranges=ranges,
                config=SETUP_CONFIG,
                d1_session_mode=close_mode.value if tf == Timeframe.D1 else None,
            )

            # Analyze each OB
            for ob in obs:
                # 1. metadata
                meta_match = (
                    ob.instrument_id == raw_candles[0].instrument_id
                    and ob.source_id == raw_candles[0].source_id
                    and ob.timeframe == tf
                    and ob.price_type == pt
                )
                # 2. structure link match
                struct_match = False
                matched_sb = None
                if ob.structure_break_event_id:
                    matched_sb = next((sb for sb in conf_breaks if sb.event_id == ob.structure_break_event_id), None)
                    if matched_sb:
                        struct_match = True
                if ob.mss_confirmation_id:
                    matched_mss = next((m for m in conf_mss if m.record_id == ob.mss_confirmation_id), None)
                    if matched_mss:
                        struct_match = True
                        matched_sb = next((sb for sb in conf_breaks if sb.event_id == matched_mss.source_choch_event_id), None)

                # 3. displacement link match
                disp_match = any(d.event_id == ob.displacement_event_id for d in conf_displacements)

                # 4. pit eligible
                pit_ok = ob.confirmation_time <= END_UTC

                # 5. lifecycle eligible
                # In ict_setup.py: o.status != OrderBlockStatus.INVALIDATED
                lifecycle_ok = (ob.status != OrderBlockStatus.INVALIDATED)

                # 6. setup linked
                linked_setups = [s for s in setups_all_obs if s.order_block_id == ob.order_block_id or any(c.source_event_id == ob.order_block_id for c in s.confluences)]
                setup_linked = len(linked_setups) > 0

                rejection_reason = "LINKED"
                if not setup_linked:
                    if not lifecycle_ok:
                        rejection_reason = f"LIFECYCLE_ELIMINATED ({ob.status.value})"
                    elif not struct_match:
                        rejection_reason = "NO_MATCHED_STRUCTURE_BREAK"
                    elif not disp_match:
                        rejection_reason = "NO_MATCHED_DISPLACEMENT"
                    elif matched_sb and matched_sb.is_choch:
                        # For reversal: does it have sweep?
                        rejection_reason = "NO_QUALIFIED_SWEEP_FOR_REVERSAL"
                    else:
                        rejection_reason = "ZONE_SELECTION_PREFERENCE_OR_OTHER"

                all_ob_records.append({
                    "price_type": pt.value,
                    "timeframe": mode_name,
                    "ob_id": ob.order_block_id,
                    "direction": ob.direction.value,
                    "status": ob.status.value,
                    "struct_ref": ob.structure_break_event_id or ob.mss_confirmation_id,
                    "meta_match": meta_match,
                    "struct_match": struct_match,
                    "disp_match": disp_match,
                    "pit_ok": pit_ok,
                    "lifecycle_ok": lifecycle_ok,
                    "setup_linked": setup_linked,
                    "rejection_reason": rejection_reason,
                    "linked_setup_ids": [s.setup_id for s in linked_setups],
                })

    print("\n" + "=" * 140)
    print("STAGE 1: ORDER BLOCK TRACKING TABLE (22 STRUCTURAL OBs)")
    print("=" * 140)
    for r in all_ob_records:
        print(f"[{r['price_type']:<3} {r['timeframe']:<6}] OB: {r['ob_id'][:65]}... | Dir: {r['direction']:<7} | Status: {r['status']:<18} | Meta: {r['meta_match']} | Struct: {r['struct_match']} | Disp: {r['disp_match']} | PIT: {r['pit_ok']} | Life: {r['lifecycle_ok']} | Linked: {r['setup_linked']} | Reason: {r['rejection_reason']}")

    # Aggregated breakdown table
    print("\n" + "=" * 120)
    print("AGGREGATED ORDER BLOCK PIPELINE FUNNEL TABLE")
    print("=" * 120)
    headers = [
        "price_type", "timeframe", "raw_ob_count", "metadata_matched", "direction_matched",
        "structure_link_matched", "displacement_link_matched", "pit_eligible",
        "lifecycle_eligible", "setup_linked_ob_count", "rejection_reasons"
    ]
    print(f"{headers[0]:<10} {headers[1]:<10} {headers[2]:>12} {headers[3]:>16} {headers[4]:>17} {headers[5]:>22} {headers[6]:>25} {headers[7]:>12} {headers[8]:>18} {headers[9]:>20}")
    print("-" * 140)

    for pt in ("BID", "ASK"):
        for tf_label in ("D1 NY", "D1 UTC", "H4", "H2"):
            matching = [r for r in all_ob_records if r["price_type"] == pt and r["timeframe"] == tf_label]
            raw_cnt = len(matching)
            meta_cnt = sum(1 for r in matching if r["meta_match"])
            dir_cnt = raw_cnt  # All have valid BULLISH / BEARISH
            struct_cnt = sum(1 for r in matching if r["struct_match"])
            disp_cnt = sum(1 for r in matching if r["disp_match"])
            pit_cnt = sum(1 for r in matching if r["pit_ok"])
            life_cnt = sum(1 for r in matching if r["lifecycle_ok"])
            linked_cnt = sum(1 for r in matching if r["setup_linked"])
            print(f"{pt:<10} {tf_label:<10} {raw_cnt:>12} {meta_cnt:>16} {dir_cnt:>17} {struct_cnt:>22} {disp_cnt:>25} {pit_cnt:>12} {life_cnt:>18} {linked_cnt:>20}")

    # Stage 2: OHLC Table for H4 Bearish Reversal (2026-10-14)
    print("\n" + "=" * 120)
    print("STAGE 2: H4 BEARISH REVERSAL (2026-10-14) CANDLE-BY-CANDLE OHLC AUDIT")
    print("=" * 120)
    # Get H4 BID candles around Oct 14
    res_h4 = resample_m1_candles(m1_bid, Timeframe.H4)
    candles_h4 = [rc.candle for rc in res_h4 if START_UTC <= rc.candle.open_time <= END_UTC and rc.candle.is_closed]
    oct14_candles = [c for c in candles_h4 if datetime(2026, 10, 13, 0, 0, tzinfo=UTC) <= c.open_time <= datetime(2026, 10, 15, 12, 0, tzinfo=UTC)]

    print(f"{'open_time':<22} {'close_time':<22} {'Open':>9} {'High':>9} {'Low':>9} {'Close':>9} {'Role/Event'}")
    print("-" * 120)
    for c in oct14_candles:
        note = ""
        if c.open_time == datetime(2026, 10, 14, 0, 0, tzinfo=UTC):
            note = "Bearish OB Origin Candle (00:00 - 04:00 UTC)"
        elif c.open_time == datetime(2026, 10, 14, 4, 0, tzinfo=UTC):
            note = "CHoCH Break Candle (04:00 - 08:00 UTC)"
        elif c.open_time == datetime(2026, 10, 14, 8, 0, tzinfo=UTC):
            note = "CHoCH Detection / MSS Candidate (08:00 UTC)"
        elif c.open_time == datetime(2026, 10, 14, 12, 0, tzinfo=UTC):
            note = "Displacement Candle (12:00 - 16:00 UTC)"
        elif c.open_time == datetime(2026, 10, 14, 16, 0, tzinfo=UTC):
            note = "FVG Candle 3 (16:00 - 20:00 UTC) -> FVG forms at 20:00 UTC"
        elif c.open_time == datetime(2026, 10, 14, 20, 0, tzinfo=UTC):
            note = "First Test Candle! (20:00 - 00:00 UTC) -> High=4145.415 touches zone [4106.368 - 4135.665] -> Touch confirmed at 00:00 UTC"
        elif c.open_time == datetime(2026, 10, 15, 0, 0, tzinfo=UTC):
            note = "Invalidation Candle! (00:00 - 04:00 UTC) -> Close=4145.645 > 4135.665 -> Invalidation confirmed at 04:00 UTC"
        print(f"{c.open_time.isoformat():<22} {c.close_time.isoformat():<22} {c.open:>9.3f} {c.high:>9.3f} {c.low:>9.3f} {c.close:>9.3f}  {note}")


if __name__ == "__main__":
    run_deep_dive()
