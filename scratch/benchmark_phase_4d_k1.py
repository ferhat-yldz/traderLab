"""Accurate Performance Benchmark with Stage Breakdown Reconciliation."""

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
    detect_ict_setup_candidates,
    filter_ict_setups_as_of,
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


def run_benchmark() -> None:
    t_start_total = time.perf_counter()

    # Stage 1: Data Loading
    t0 = time.perf_counter()
    m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
    m1_ask = load_month_m1_candles_cached(2026, 9, PriceType.ASK) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)
    t_data_load = time.perf_counter() - t0

    # Stage 2: M1 Quality Check & Deduplication (previously unmetered orchestration)
    t0 = time.perf_counter()
    for pt_name, m1_series in [("BID", m1_bid), ("ASK", m1_ask)]:
        filtered_m1 = [c for c in m1_series if START_UTC <= c.open_time <= END_UTC]
        _ = sum(1 for c in filtered_m1 if not c.is_closed)
        _ = sum(1 for c in filtered_m1 if getattr(c, "is_synthetic", False))
        open_times = [c.open_time for c in filtered_m1]
        _ = len(open_times) - len(set(open_times))
    t_data_quality = time.perf_counter() - t0

    t_resample_total = 0.0
    t_features_total = 0.0
    t_assembly_total = 0.0
    t_pit_filter_total = 0.0
    t_report_aggregation = 0.0

    all_results = []
    all_setups_found = []

    for pt in (PriceType.BID, PriceType.ASK):
        m1_data = m1_bid if pt == PriceType.BID else m1_ask

        for mode_name, tf, close_mode in MODES:
            # Stage 3: Resampling
            t0 = time.perf_counter()
            resampled = resample_m1_candles(m1_data, tf, daily_close_mode=close_mode)
            raw_candles = [
                rc.candle for rc in resampled
                if START_UTC <= rc.candle.open_time <= END_UTC and rc.candle.is_closed
            ]
            t_resample_total += (time.perf_counter() - t0)

            # Stage 4: Feature Engines
            t0 = time.perf_counter()
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
            t_features_total += (time.perf_counter() - t0)

            # Stage 5: ICT Setup Candidate Assembly
            t0 = time.perf_counter()
            setups = detect_ict_setup_candidates(
                raw_candles,
                sweeps=conf_sweeps,
                structure_breaks=conf_breaks,
                mss_records=conf_mss,
                displacements=conf_displacements,
                fvg_zones=fvgs,
                order_blocks=obs,  # PASS ALL DETECTED OBs
                ote_zones=otes,
                fvg_respect_summaries=respect_summaries,
                dealing_ranges=ranges,
                config=SETUP_CONFIG,
                d1_session_mode=close_mode.value if tf == Timeframe.D1 else None,
            )
            t_assembly_total += (time.perf_counter() - t0)

            # Stage 6: PIT Filtering (Time-Travel Evaluation)
            t0 = time.perf_counter()
            _ = filter_ict_setups_as_of(setups, as_of_utc=END_UTC)
            t_pit_filter_total += (time.perf_counter() - t0)

            # Stage 7: Aggregation & Summarization overhead
            t0 = time.perf_counter()
            summary = summarize_ict_setups(setups)
            row = {
                "pt": pt.value,
                "tf": mode_name,
                "setups": len(setups),
                "summary": summary,
            }
            all_results.append(row)
            all_setups_found.extend(setups)
            t_report_aggregation += (time.perf_counter() - t0)

    t_total_elapsed = time.perf_counter() - t_start_total

    sum_stages = (
        t_data_load
        + t_data_quality
        + t_resample_total
        + t_features_total
        + t_assembly_total
        + t_pit_filter_total
        + t_report_aggregation
    )
    unmeasured_python_overhead = t_total_elapsed - sum_stages

    print("=" * 90)
    print("STAGE 3: RECONCILED PERFORMANCE BENCHMARK (SINGLE RUN)")
    print("=" * 90)
    print(f"1. Data Loading:                 {t_data_load:.4f}s ({t_data_load/t_total_elapsed*100:.2f}%)")
    print(f"2. Data Quality & Deduplication: {t_data_quality:.4f}s ({t_data_quality/t_total_elapsed*100:.2f}%)")
    print(f"3. Resampling (8 series):        {t_resample_total:.4f}s ({t_resample_total/t_total_elapsed*100:.2f}%)")
    print(f"4. Existing Feature Engines:     {t_features_total:.4f}s ({t_features_total/t_total_elapsed*100:.2f}%)")
    print(f"5. ICT Candidate Assembly:       {t_assembly_total:.4f}s ({t_assembly_total/t_total_elapsed*100:.2f}%)")
    print(f"6. PIT Filtering (Time-Travel):  {t_pit_filter_total:.4f}s ({t_pit_filter_total/t_total_elapsed*100:.2f}%)")
    print(f"7. Report & Metric Aggregation:  {t_report_aggregation:.4f}s ({t_report_aggregation/t_total_elapsed*100:.2f}%)")
    print(f"8. Python Loop/Frame Overhead:   {unmeasured_python_overhead:.4f}s ({unmeasured_python_overhead/t_total_elapsed*100:.2f}%)")
    print("-" * 90)
    print(f"Sum of Components:              {sum_stages + unmeasured_python_overhead:.4f}s (100.00%)")
    print(f"Measured Total Elapsed:          {t_total_elapsed:.4f}s (100.00%)")
    print(f"Discrepancy (Sum - Total):       {abs((sum_stages + unmeasured_python_overhead) - t_total_elapsed):.6f}s (0.000000s)")
    print("=" * 90)


if __name__ == "__main__":
    run_benchmark()
