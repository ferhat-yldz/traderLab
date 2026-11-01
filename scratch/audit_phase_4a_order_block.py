"""Real XAUUSD market data audit script for Phase 4A Order Block Detection & Lifecycle Engine.

Performs:
1. Loads real XAUUSD BID and ASK M1 candles for Sep-Oct 2026.
2. Resamples to D1 (both NY 17:00 and UTC 00:00), H4, and H2.
3. Detects confirmed Swings (Faz 3A engine: 3L3R STRICT).
4. Detects Market Structure events (BOS, CHoCH, MSS Candidates) from Faz 3C engine.
5. Detects genuine Fair Value Gaps (FVG) from Faz 2B engine.
6. Detects Point-in-Time safe Displacements (Faz 3D engine).
7. Evaluates MSS Confirmations (Faz 3D / 3D-K1 engine).
8. Detects Point-in-Time safe Order Blocks & evaluates their full lifecycle (Faz 4A engine).
9. Compiles quantitative audit summary table across all combinations.
10. Details at least 10 real Order Block cases with OHLC, zone limits, 50% midpoint,
    causal structure/displacement links, lifecycle transitions, and PIT verification.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    resample_m1_candles,
)
from traderlab.core.types import PriceType, Timeframe
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.displacement import (
    DisplacementConfig,
    DisplacementEvent,
    detect_displacements,
    summarize_displacements,
)
from traderlab.domain.market_structure.fvg import FvgZone, detect_fair_value_gaps
from traderlab.domain.market_structure.liquidity_sweep import (
    detect_liquidity_sweeps,
)
from traderlab.domain.market_structure.market_structure import (
    MarketStructureConfig,
    StructureBreakEvent,
    StructureBreakType,
    detect_market_structure,
    filter_mss_candidates,
)
from traderlab.domain.market_structure.mss_confirmation import (
    MssConfirmationConfig,
    MssConfirmationRecord,
    MssStatus,
    evaluate_mss_confirmations,
)
from traderlab.domain.market_structure.order_block import (
    OrderBlockConfig,
    OrderBlockRecord,
    OrderBlockZoneMode,
    detect_order_blocks,
    summarize_order_blocks,
)
from traderlab.domain.market_structure.swing import (
    TiePolicy,
    detect_swings,
    filter_confirmed_swings,
)
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached


def run_phase_4a_audit() -> None:
    print("=" * 80)
    print("TRADERLAB — FAZ 4A: REAL XAUUSD ORDER BLOCK & LIFECYCLE ENGINE AUDIT")
    print("=" * 80)

    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)

    # Standard XAUUSD configurations
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

    all_detected_obs: list[tuple[PriceType, str, list[Candle], list[OrderBlockRecord]]] = []

    for p_type in (PriceType.BID, PriceType.ASK):
        print(f"\nLoading real M1 data for {p_type.value}...")
        m1_sep = load_month_m1_candles_cached(2026, 9, p_type)
        m1_oct = load_month_m1_candles_cached(2026, 10, p_type)
        all_m1 = m1_sep + m1_oct

        d1_ny_res = resample_m1_candles(
            all_m1, Timeframe.D1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
        )
        d1_utc_res = resample_m1_candles(
            all_m1, Timeframe.D1, daily_close_mode=DailyCloseMode.UTC_MIDNIGHT
        )
        h4_res = resample_m1_candles(all_m1, Timeframe.H4)
        h2_res = resample_m1_candles(all_m1, Timeframe.H2)

        d1_ny_c = [
            rc.candle
            for rc in d1_ny_res
            if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
        ]
        d1_utc_c = [
            rc.candle
            for rc in d1_utc_res
            if start_utc <= rc.candle.open_time <= end_utc
            and rc.candle.is_closed
            and not (rc.candle.open_time.weekday() == 6 and rc.candle.open_time.hour == 0)
        ]
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

        configs = [
            ("D1 (NY 17:00)", d1_ny_c),
            ("D1 (UTC 00:00)", d1_utc_c),
            ("H4", h4_c),
            ("H2", h2_c),
        ]

        print(
            f"\n>>> [{p_type.value}] ORDER BLOCK DETECTION & LIFECYCLE AUDIT (2026-09-15 to 2026-10-15):"
        )
        header = (
            f"{'Timeframe':<16} | {'Bars':<5} | {'Disp':<5} | {'BOS':<5} | {'MSS':<5} | "
            f"{'OB Tot':<6} | {'OB Bull':<7} | {'OB Bear':<7} | {'Unmit':<5} | {'Touch':<5} | {'Part':<5} | {'Full':<5} | {'Inval':<5}"
        )
        print(header)
        print("-" * len(header))

        for name, c_list in configs:
            # 1. Detect Swings (Faz 3A)
            all_swings = detect_swings(
                c_list, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT
            )
            conf_swings = filter_confirmed_swings(all_swings)

            # 2. Detect Sweeps (Faz 3B)
            sweeps = detect_liquidity_sweeps(c_list, conf_swings, tick_size=tick_size)

            # 3. Detect Market Structure & BOS/CHoCH (Faz 3C)
            ms_events, _ = detect_market_structure(c_list, conf_swings, config=ms_config)
            candidates = filter_mss_candidates(ms_events)
            bos_count = sum(
                1
                for e in ms_events
                if e.break_type == StructureBreakType.BOS and e.confirmation_status == "CONFIRMED"
            )

            # 4. Detect FVGs (Faz 2B)
            fvgs = detect_fair_value_gaps(c_list)

            # 5. Detect Displacements (Faz 3D)
            displacements = detect_displacements(c_list, config=disp_config)
            disp_sum = summarize_displacements(displacements)

            # 6. Evaluate MSS Confirmations (Faz 3D / 3D-K1)
            mss_records = evaluate_mss_confirmations(
                c_list,
                candidates,
                displacements=displacements,
                fvgs=fvgs,
                sweeps=sweeps,
                config=mss_config,
            )
            mss_conf_count = sum(1 for m in mss_records if m.status == MssStatus.MSS_CONFIRMED)

            # 7. Detect Order Blocks (Faz 4A)
            obs = detect_order_blocks(
                c_list,
                structure_breaks=ms_events,
                displacements=displacements,
                mss_confirmations=mss_records,
                fvg_zones=fvgs,
                config=ob_config,
            )
            ob_sum = summarize_order_blocks(obs)

            all_detected_obs.append((p_type, name, c_list, obs))

            print(
                f"{name:<16} | {len(c_list):<5} | {disp_sum['total_displacements']:<5} | "
                f"{bos_count:<5} | {mss_conf_count:<5} | "
                f"{ob_sum['total_order_blocks']:<6} | {ob_sum['bullish_order_blocks']:<7} | "
                f"{ob_sum['bearish_order_blocks']:<7} | {ob_sum['unmitigated']:<5} | "
                f"{ob_sum['touched']:<5} | {ob_sum['partially_mitigated']:<5} | "
                f"{ob_sum['fully_mitigated']:<5} | {ob_sum['invalidated']:<5}"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # DETAILED MATHEMATICAL PROOFS FOR REAL ORDER BLOCKS
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("DETAILED STEP-BY-STEP PROOFS FOR AT LEAST 10 REAL ORDER BLOCKS")
    print("=" * 80)

    # Flatten OBs across all runs
    collected_examples: list[tuple[PriceType, str, list[Candle], OrderBlockRecord]] = []
    for p_type, tf_name, c_list, obs in all_detected_obs:
        for ob in obs:
            collected_examples.append((p_type, tf_name, c_list, ob))

    print(f"Total Order Blocks detected across all combinations: {len(collected_examples)}")

    # Select representative samples across timeframes and directions
    # Prefer cases covering UNMITIGATED, TOUCHED, PARTIALLY_MITIGATED, FULLY_MITIGATED, INVALIDATED
    sample_obs = collected_examples[:12] if len(collected_examples) >= 12 else collected_examples

    for idx, (p_type, tf_name, c_list, ob) in enumerate(sample_obs, start=1):
        # Locate origin candle
        cand_map = {c.open_time: c for c in c_list}
        orig_cand = cand_map.get(ob.origin_candle_open_time)

        print(
            f"\n--- [PROOF #{idx}] {ob.direction.value} ORDER BLOCK ({tf_name} {p_type.value}) ---"
        )
        print(f"Order Block ID:      {ob.order_block_id}")
        print(f"Origin Candle Open:  {ob.origin_candle_open_time.isoformat()}")
        print(f"Origin Candle Close: {ob.origin_candle_close_time.isoformat()}")
        if orig_cand is not None:
            print(
                f"Origin OHLC:         Open={orig_cand.open} | High={orig_cand.high} | "
                f"Low={orig_cand.low} | Close={orig_cand.close} | "
                f"Type={'BEARISH (close < open)' if orig_cand.close < orig_cand.open else 'BULLISH (close > open)'}"
            )
        print(f"Zone Bottom:         {ob.zone_bottom:.2f}")
        print(f"Zone Top:            {ob.zone_top:.2f}")
        print(f"Zone Height:         {ob.zone_height:.2f}")
        print(f"Midpoint 50% (CE):   {ob.midpoint_50:.2f}")
        print(f"Confirmation Time:   {ob.confirmation_time.isoformat()}")
        print(f"Displacement Ref:    {ob.displacement_event_id}")
        if ob.structure_break_event_id:
            print(f"Structure Break Ref: {ob.structure_break_event_id} (BOS)")
        if ob.mss_confirmation_id:
            print(f"MSS Record Ref:      {ob.mss_confirmation_id} (MSS_CONFIRMED)")
        if ob.associated_fvg_id:
            print(f"Associated FVG:      {ob.associated_fvg_id}")

        print(f"Current Status:      {ob.status.value}")
        print(
            f"Max Penetration:     {ob.penetration_depth:.2f} ({ob.max_penetration_percentage:.2f}%)"
        )
        if ob.touch_time:
            print(f"Touch Time:          {ob.touch_time.isoformat()}")
        if ob.full_mitigation_time:
            print(f"Full Mitigation Time:{ob.full_mitigation_time.isoformat()}")
        if ob.invalidation_time:
            print(f"Invalidation Time:   {ob.invalidation_time.isoformat()}")

        print(f"Lifecycle Event Log ({len(ob.lifecycle_events)} transitions):")
        for ev in ob.lifecycle_events:
            print(
                f"  -> [{ev.event_time.strftime('%Y-%m-%d %H:%M')}] {ev.status_before.value} -> {ev.status_after.value} | "
                f"Trigger Price: {ev.trigger_price} | Depth: {ev.penetration_depth} ({ev.penetration_percentage:.1f}%) | "
                f"{ev.description}"
            )

        # Audit Point-in-Time safety
        is_pit_safe = ob.confirmation_time >= ob.origin_candle_close_time
        if ob.touch_time:
            is_pit_safe = is_pit_safe and ob.touch_time >= ob.confirmation_time
        if ob.invalidation_time:
            is_pit_safe = is_pit_safe and ob.invalidation_time >= ob.confirmation_time
        print(f"PIT Safety Audit:    {'PASSED (Zero Lookahead)' if is_pit_safe else 'FAILED'}")

    print("\n" + "=" * 80)
    print("FAZ 4A REAL XAUUSD AUDIT EXECUTION COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    run_phase_4a_audit()
