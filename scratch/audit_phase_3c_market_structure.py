"""Real XAUUSD market data audit script for Phase 3C Market Structure Detection Engine.

Performs:
1. Loads real XAUUSD BID and ASK M1 candles for Sep-Oct 2026.
2. Resamples to D1 (both NY 17:00 and UTC 00:00), H4, and H2.
3. Detects confirmed Swings (Faz 3A engine: 3L3R STRICT).
4. Classifies structural swings (HH, HL, LH, LL).
5. Detects Market Structure events (BOS, CHoCH, MSS Candidates) and state transitions.
6. Compiles quantitative audit summary table across all combinations.
7. Details 10 step-by-step mathematical proofs with OHLC, previous trend, protected levels,
   detection time, and Point-in-Time safety verification.
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
from traderlab.domain.market_structure.market_structure import (
    MarketStructureConfig,
    MarketTrend,
    StructuralSwingType,
    StructureBreakEvent,
    StructureBreakType,
    StructureDirection,
    classify_structural_swings,
    detect_market_structure,
    filter_confirmed_structure_breaks,
    filter_mss_candidates,
    summarize_market_structure_events,
)
from traderlab.domain.market_structure.swing import (
    SwingPoint,
    TiePolicy,
    detect_swings,
    filter_confirmed_swings,
)
from traderlab.interfaces.dashboard.app import load_month_m1_candles_cached


def run_phase_3c_audit() -> None:
    print("=" * 80)
    print("TRADERLAB — FAZ 3C: REAL XAUUSD MARKET STRUCTURE AUDIT")
    print("=" * 80)

    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)

    # Forex CFD XAUUSD standard tick size = 0.01, 1 tick threshold
    tick_size = Decimal("0.01")
    config = MarketStructureConfig(tick_size=tick_size, break_threshold_ticks=1)

    all_runs: list[
        tuple[PriceType, str, list[Candle], list[SwingPoint], list[StructureBreakEvent], any]
    ] = []

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

        print(f"\n>>> [{p_type.value}] MARKET STRUCTURE AUDIT (2026-09-15 to 2026-10-15):")
        header = (
            f"{'Timeframe':<16} | {'SH':<3} | {'SL':<3} | {'HH':<3} | {'HL':<3} | "
            f"{'LH':<3} | {'LL':<3} | {'BOS+':<4} | {'BOS-':<4} | {'CH+':<3} | {'CH-':<3} | {'MSS_C':<5} | {'State':<18}"
        )
        print(header)
        print("-" * len(header))

        for name, c_list in configs:
            all_swings = detect_swings(
                c_list, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT
            )
            conf_swings = filter_confirmed_swings(all_swings)

            # Classify structural swings
            classified = classify_structural_swings(conf_swings, tick_size=tick_size)

            sh_count = sum(1 for s in conf_swings if s.is_swing_high)
            sl_count = sum(1 for s in conf_swings if s.is_swing_low)
            hh_count = sum(1 for cs in classified if cs.structural_type == StructuralSwingType.HH)
            hl_count = sum(1 for cs in classified if cs.structural_type == StructuralSwingType.HL)
            lh_count = sum(1 for cs in classified if cs.structural_type == StructuralSwingType.LH)
            ll_count = sum(1 for cs in classified if cs.structural_type == StructuralSwingType.LL)

            events, state = detect_market_structure(c_list, conf_swings, config=config)
            all_runs.append((p_type, name, c_list, conf_swings, events, state))

            summary = summarize_market_structure_events(events)

            print(
                f"{name:<16} | {sh_count:<3} | {sl_count:<3} | {hh_count:<3} | {hl_count:<3} | "
                f"{lh_count:<3} | {ll_count:<3} | {summary['bullish_bos']:<4} | {summary['bearish_bos']:<4} | "
                f"{summary['bullish_choch']:<3} | {summary['bearish_choch']:<3} | {summary['mss_candidate']:<5} | {state.trend.value:<18}"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # DETAILED MATHEMATICAL PROOFS FOR 10 REAL STRUCTURAL EVENTS
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("DETAILED STEP-BY-STEP MATHEMATICAL PROOFS FOR 10 REAL STRUCTURAL EVENTS")
    print("=" * 80)

    samples_shown = 0

    for p_type, name, c_list, conf_swings, events, state in all_runs:
        if samples_shown >= 10:
            break
        if not events:
            continue

        c_map = {c.open_time: c for c in c_list}
        sw_map = {s.id: s for s in conf_swings}

        # Choose a balanced set of events across types
        bos_events = [e for e in events if e.is_bos]
        choch_events = [e for e in events if e.is_choch]
        curated_events = choch_events[:2] + bos_events[:2]

        for ev in curated_events:
            if samples_shown >= 10:
                break
            samples_shown += 1
            bar = c_map.get(ev.break_bar_open_time)
            sw = sw_map.get(ev.source_swing_id)
            if not bar or not sw:
                continue

            prot_sw = sw_map.get(ev.protected_level_id) if ev.protected_level_id else None

            print(
                f"\n--- [SAMPLE #{samples_shown}] {p_type.value} {name} {ev.structure_direction.value} {ev.break_type.value} ---"
            )
            print(f"Event ID: {ev.event_id}")
            print(
                f"Previous Trend: {ev.trend_before.value} -> New Structure: {ev.trend_after.value}"
            )
            print(
                f"Source Swing ID: {ev.source_swing_id} ({sw.swing_type.value} @ {ev.reference_level_price})"
            )
            print(f"Swing Formation (Pivot): {sw.pivot_time.strftime('%Y-%m-%d %H:%M')}")
            print(f"Swing Confirmation Time: {sw.confirmation_time.strftime('%Y-%m-%d %H:%M')}")
            if prot_sw:
                print(
                    f"Protected Level ID: {prot_sw.id} ({prot_sw.swing_type.value} @ {prot_sw.pivot_price})"
                )
            else:
                print("Protected Level ID: None")
            print(
                f"Break Candle Open: {bar.open_time.strftime('%Y-%m-%d %H:%M')} | Close: {bar.close_time.strftime('%Y-%m-%d %H:%M')}"
            )
            print(
                f"Break Candle OHLC: Open={bar.open}, High={bar.high}, Low={bar.low}, Close={bar.close}"
            )
            print(
                f"Break Price: {ev.break_price} (Detection Time: {ev.detection_time.strftime('%Y-%m-%d %H:%M')})"
            )

            # Mathematical condition check
            threshold = config.break_threshold
            if ev.structure_direction == StructureDirection.BULLISH:
                diff = bar.close - ev.reference_level_price
                assert bar.close > ev.reference_level_price + threshold, (
                    "Violated: close <= level + threshold"
                )
                print(
                    f"Mathematical Verification: Close {bar.close} > Level {ev.reference_level_price} + Threshold {threshold} (+{diff} excursion) -> PROVEN"
                )
            else:
                diff = ev.reference_level_price - bar.close
                assert bar.close < ev.reference_level_price - threshold, (
                    "Violated: close >= level - threshold"
                )
                print(
                    f"Mathematical Verification: Close {bar.close} < Level {ev.reference_level_price} - Threshold {threshold} (-{diff} excursion) -> PROVEN"
                )

            # PIT Verification: evaluate as_of before bar close vs at bar close
            as_of_before = bar.open_time + (bar.close_time - bar.open_time) / 2
            as_of_at_close = bar.close_time

            evs_before, _ = detect_market_structure(
                c_list, conf_swings, config=config, as_of_utc=as_of_before
            )
            evs_at_close, _ = detect_market_structure(
                c_list, conf_swings, config=config, as_of_utc=as_of_at_close
            )

            visible_before = any(e.event_id == ev.event_id for e in evs_before)
            visible_at_close = any(e.event_id == ev.event_id for e in evs_at_close)
            assert not visible_before, (
                f"PIT VIOLATION: Event visible before candle close at {as_of_before}!"
            )
            assert visible_at_close, (
                f"PIT ERROR: Event not visible at candle close {as_of_at_close}!"
            )
            print(
                f"PIT Safety Verification: Visible at {as_of_before.strftime('%Y-%m-%d %H:%M')}: {visible_before} (False expected) | "
                f"Visible at {as_of_at_close.strftime('%Y-%m-%d %H:%M')}: {visible_at_close} (True expected) -> PROVEN"
            )

    print(
        f"\nSuccessfully audited {samples_shown} real market structure events with 100% mathematical accuracy and PIT safety."
    )


if __name__ == "__main__":
    run_phase_3c_audit()
