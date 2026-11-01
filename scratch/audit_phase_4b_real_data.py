"""Audit script for Phase 4B: Real XAUUSD archive Dealing Range & OTE evaluation."""

from datetime import UTC, datetime
from decimal import Decimal
from traderlab.application.analysis.resampling import DailyCloseMode, resample_m1_candles
from traderlab.core.types import PriceType, Timeframe
from traderlab.domain.market_structure.displacement import DisplacementConfig, detect_displacements
from traderlab.domain.market_structure.fvg import detect_fair_value_gaps
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
    detect_order_blocks,
)
from traderlab.domain.market_structure.ote import (
    calculate_ote_zone,
    compute_all_ote_order_block_intersections,
    generate_ote_zones,
)
from traderlab.domain.market_structure.premium_discount import (
    DealingRangeDirection,
    classify_premium_discount,
    identify_dealing_ranges,
)
from traderlab.domain.market_structure.swing import (
    SwingConfirmationStatus,
    SwingType,
    TiePolicy,
    detect_swings,
    filter_confirmed_swings,
)
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached

start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)

all_bid_m1 = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
all_ask_m1 = load_month_m1_candles_cached(2026, 9, PriceType.ASK) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)

results = []

for pt, m1_feed in [(PriceType.BID, all_bid_m1), (PriceType.ASK, all_ask_m1)]:
    configs = [
        ("D1_NY_1700", Timeframe.D1, DailyCloseMode.NEW_YORK_1700),
        ("D1_UTC_0000", Timeframe.D1, DailyCloseMode.UTC_MIDNIGHT),
        ("H4", Timeframe.H4, None),
        ("H2", Timeframe.H2, None),
    ]

    for label, tf, d_mode in configs:
        res = resample_m1_candles(m1_feed, tf, daily_close_mode=d_mode) if d_mode else resample_m1_candles(m1_feed, tf)
        candles = [rc.candle for rc in res if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed]
        
        swings = filter_confirmed_swings(detect_swings(candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT))
        sh_count = sum(1 for s in swings if s.swing_type == SwingType.SWING_HIGH)
        sl_count = sum(1 for s in swings if s.swing_type == SwingType.SWING_LOW)
        
        ranges = identify_dealing_ranges(swings)
        bull_ranges = [r for r in ranges if r.direction == DealingRangeDirection.BULLISH]
        bear_ranges = [r for r in ranges if r.direction == DealingRangeDirection.BEARISH]
        
        otes = generate_ote_zones(ranges)
        bull_otes = [o for o in otes if o.direction == DealingRangeDirection.BULLISH]
        bear_otes = [o for o in otes if o.direction == DealingRangeDirection.BEARISH]
        
        # OB detection
        ms_events, _ = detect_market_structure(candles, swings, config=MarketStructureConfig(Decimal("0.01"), 1))
        disps = detect_displacements(
            candles,
            config=DisplacementConfig(
                tick_size=Decimal("0.01"),
                min_body_ratio=Decimal("0.60"),
                min_bullish_close_location=Decimal("0.75"),
                max_bearish_close_location=Decimal("0.25"),
                atr_window=14,
                min_range_atr_multiple=Decimal("1.50"),
            ),
        )
        cands = filter_mss_candidates(ms_events)
        fvgs = detect_fair_value_gaps(candles)
        mss_records = evaluate_mss_confirmations(
            candles,
            cands,
            displacements=disps,
            fvgs=fvgs,
            config=MssConfirmationConfig(
                max_confirmation_bars=3, require_displacement=True, require_fvg_confirmation=True
            ),
        )
        obs = detect_order_blocks(
            candles,
            structure_breaks=ms_events,
            displacements=disps,
            mss_confirmations=mss_records,
            fvg_zones=fvgs,
            config=OrderBlockConfig(max_origin_lookback_bars=6),
        )
        
        intersections = compute_all_ote_order_block_intersections(otes, obs)
        
        # Premium/Discount assessments for candle closes
        prem_count = 0
        disc_count = 0
        eq_count = 0
        outside_count = 0
        undetermined_count = 0
        
        for c in candles:
            active_range = ranges[-1] if ranges else None
            if active_range is None:
                undetermined_count += 1
            else:
                zone = classify_premium_discount(c.close, active_range.range_low, active_range.range_high, active_range.equilibrium)
                if zone.value == "PREMIUM":
                    prem_count += 1
                elif zone.value == "DISCOUNT":
                    disc_count += 1
                elif zone.value == "EQUILIBRIUM":
                    eq_count += 1
                elif zone.value == "OUTSIDE_RANGE":
                    outside_count += 1
                else:
                    undetermined_count += 1
                    
        results.append({
            "price_type": pt.value,
            "timeframe": label,
            "candles": len(candles),
            "swing_highs": sh_count,
            "swing_lows": sl_count,
            "total_ranges": len(ranges),
            "bullish_ranges": len(bull_ranges),
            "bearish_ranges": len(bear_ranges),
            "bullish_otes": len(bull_otes),
            "bearish_otes": len(bear_otes),
            "total_obs": len(obs),
            "intersections": len(intersections),
            "prem_count": prem_count,
            "disc_count": disc_count,
            "eq_count": eq_count,
            "outside_count": outside_count,
            "undetermined_count": undetermined_count,
            "ranges_list": ranges,
            "otes_list": otes,
            "obs_list": obs,
            "intersections_list": intersections,
        })

with open("scratch/phase_4b_audit_output.txt", "w", encoding="utf-8") as out:
    out.write("=" * 80 + "\n")
    for r in results:
        out.write(f"{r['price_type']} {r['timeframe']}: Candles={r['candles']}, SH={r['swing_highs']}, SL={r['swing_lows']}, Ranges={r['total_ranges']} (Bull={r['bullish_ranges']}, Bear={r['bearish_ranges']}), OTE={len(r['otes_list'])}, OBs={r['total_obs']}, Intersections={r['intersections']}, Prem={r['prem_count']}, Disc={r['disc_count']}, Eq={r['eq_count']}, Out={r['outside_count']}, Undet={r['undetermined_count']}\n")

    out.write("\n--- DETAILED EXAMPLES ---\n")
    sample_count = 0
    for r in results:
        if r["ranges_list"]:
            for dr, ote in zip(r["ranges_list"][:4], r["otes_list"][:4]):
                sample_count += 1
                out.write(f"EXAMPLE #{sample_count} ({r['price_type']} {r['timeframe']} {dr.direction.value}):\n")
                out.write(f"   Range ID: {dr.range_id}\n")
                out.write(f"   Start: {dr.start_pivot_time} (Low={dr.range_low} / High={dr.range_high})\n")
                out.write(f"   End:   {dr.end_pivot_time}\n")
                out.write(f"   Avail: {dr.range_available_at}\n")
                out.write(f"   Equilibrium: {dr.equilibrium}\n")
                out.write(f"   Range Size:  {dr.range_size}\n")
                out.write(f"   OTE 62:      {ote.ote_62}\n")
                out.write(f"   OTE 705:     {ote.ote_705} (Reference Sweet Spot)\n")
                out.write(f"   OTE 79:      {ote.ote_79}\n")
                out.write(f"   Zone:        [{ote.zone_bottom}, {ote.zone_top}]\n")
                if sample_count >= 14:
                    break
        if sample_count >= 14:
            break

    out.write("\n--- INTERSECTIONS FOUND ---\n")
    for r in results:
        if r["intersections_list"]:
            out.write(f"{r['price_type']} {r['timeframe']} has {len(r['intersections_list'])} intersections:\n")
            for inter in r["intersections_list"][:5]:
                out.write(f"   OTE: {inter.ote_id}\n")
                out.write(f"   OB:  {inter.order_block_id}\n")
                out.write(f"   Intersection: [{inter.intersection_bottom}, {inter.intersection_top}] width={inter.intersection_width} touch={inter.is_touch_only}\n")
print("Finished writing to scratch/phase_4b_audit_output.txt")
sample_count = 0
for r in results:
    if r['ranges_list']:
        for dr, ote in zip(r['ranges_list'][:3], r['otes_list'][:3]):
            sample_count += 1
            print(f"EXAMPLE #{sample_count} ({r['price_type']} {r['timeframe']} {dr.direction.value}):")
            print(f"   Range ID: {dr.range_id}")
            print(f"   Start: {dr.start_pivot_time} (Low={dr.range_low} / High={dr.range_high})")
            print(f"   End:   {dr.end_pivot_time}")
            print(f"   Avail: {dr.range_available_at}")
            print(f"   Equilibrium: {dr.equilibrium}")
            print(f"   Range Size:  {dr.range_size}")
            print(f"   OTE 62:      {ote.ote_62}")
            print(f"   OTE 705:     {ote.ote_705} (Reference Sweet Spot)")
            print(f"   OTE 79:      {ote.ote_79}")
            print(f"   Zone:        [{ote.zone_bottom}, {ote.zone_top}]")
            if sample_count >= 12:
                break
    if sample_count >= 12:
        break

print("\n--- INTERSECTIONS FOUND ---")
for r in results:
    if r['intersections_list']:
        print(f"{r['price_type']} {r['timeframe']} has {len(r['intersections_list'])} intersections:")
        for inter in r['intersections_list']:
            print(f"   OTE: {inter.ote_id}")
            print(f"   OB:  {inter.order_block_id}")
            print(f"   Intersection: [{inter.intersection_bottom}, {inter.intersection_top}] width={inter.intersection_width} touch={inter.is_touch_only}")
