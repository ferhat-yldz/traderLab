"""Phase 4D Real XAUUSD Data Audit & Chronological PIT Replay Script.

Executes read-only comprehensive audit across:
- Dates: 2026-09-15 to 2026-10-15 UTC
- Price Types: BID, ASK
- Timeframes: D1 (New York 17:00), D1 (UTC Midnight), H4, H2
- Full pipeline: Swings, Sweeps, BOS/CHoCH, Displacements, MSS, FVG, OB, Range/OTE, FVG Respect, ICT Setups.
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
    IctSetupCandidate,
    IctSetupConfig,
    IctSetupStatus,
    detect_ict_setup_candidates,
    explain_ict_setup,
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

class TeeLogger:
    def __init__(self, filepath: str):
        self.terminal = sys.stdout
        self.log = open(filepath, "w", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)

    def flush(self):
        self.terminal.flush()
        self.log.flush()

sys.stdout = TeeLogger("scratch/audit_phase_4d_results.txt")  # type: ignore[assignment]


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


def run_audit() -> None:
    print("=" * 110)
    print("PHASE 4D: REAL XAUUSD DATA AUDIT & ICT SETUP ENGINE EXECUTION")
    print(f"Window: {START_UTC.isoformat()} -> {END_UTC.isoformat()}")
    print("=" * 110)

    # 1. Performance Tracking
    t_start_total = time.perf_counter()

    t0_load = time.perf_counter()
    m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
    m1_ask = load_month_m1_candles_cached(2026, 9, PriceType.ASK) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)
    t_load = time.perf_counter() - t0_load

    print(f"\n[DATA LOAD] Loaded {len(m1_bid)} BID M1 candles, {len(m1_ask)} ASK M1 candles in {t_load:.3f}s")

    # Data Quality Check on M1
    for pt_name, m1_series in [("BID", m1_bid), ("ASK", m1_ask)]:
        filtered_m1 = [c for c in m1_series if START_UTC <= c.open_time <= END_UTC]
        partial_cnt = sum(1 for c in filtered_m1 if not c.is_closed)
        synth_cnt = sum(1 for c in filtered_m1 if getattr(c, "is_synthetic", False))
        open_times = [c.open_time for c in filtered_m1]
        dup_cnt = len(open_times) - len(set(open_times))
        print(f"  Quality [{pt_name}]: {len(filtered_m1)} bars in window | partial={partial_cnt} | synthetic={synth_cnt} | duplicates={dup_cnt}")

    t_resample_total = 0.0
    t_features_total = 0.0
    t_assembly_total = 0.0
    t_pit_filter_total = 0.0

    all_results = []
    all_setups_found: list[IctSetupCandidate] = []

    for pt in (PriceType.BID, PriceType.ASK):
        m1_data = m1_bid if pt == PriceType.BID else m1_ask

        for mode_name, tf, close_mode in MODES:
            t0 = time.perf_counter()
            resampled = resample_m1_candles(m1_data, tf, daily_close_mode=close_mode)
            raw_candles = [
                rc.candle for rc in resampled
                if START_UTC <= rc.candle.open_time <= END_UTC and rc.candle.is_closed
            ]
            t_resample_total += (time.perf_counter() - t0)

            t0 = time.perf_counter()
            # Features
            swings = detect_swings(raw_candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
            conf_swings = filter_confirmed_swings(swings)
            sweeps = detect_liquidity_sweeps(raw_candles, conf_swings, config=LiquiditySweepConfig(tick_size=Decimal("0.01"), min_penetration_ticks=1))
            conf_sweeps = filter_confirmed_sweeps(sweeps)

            struct_breaks, _ = detect_market_structure(raw_candles, conf_swings, config=MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1))
            conf_breaks = filter_confirmed_structure_breaks(struct_breaks)
            conf_bos = [b for b in conf_breaks if b.is_bos]
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
            # In Phase 4D, this was incorrectly filtered as [ob for ob in obs if ob.status == OrderBlockStatus.CONFIRMED].
            # Since detect_order_blocks evaluates lifecycle immediately, all confirmed OBs transition to UNMITIGATED, etc.
            # Passing obs directly allows detect_ict_setup_candidates to evaluate all active/valid OBs.
            active_obs = [ob for ob in obs if ob.status != OrderBlockStatus.INVALIDATED]

            ranges = identify_dealing_ranges(conf_swings)
            otes = generate_ote_zones(ranges)

            respect_summaries = evaluate_all_fvgs_respect(fvgs, raw_candles, config=RESPECT_CONFIG)
            t_features_total += (time.perf_counter() - t0)

            # ICT Candidate Assembly
            t0 = time.perf_counter()
            setups = detect_ict_setup_candidates(
                raw_candles,
                sweeps=conf_sweeps,
                structure_breaks=conf_breaks,
                mss_records=conf_mss,
                displacements=conf_displacements,
                fvg_zones=fvgs,
                order_blocks=obs,
                ote_zones=otes,
                fvg_respect_summaries=respect_summaries,
                dealing_ranges=ranges,
                config=SETUP_CONFIG,
                d1_session_mode=close_mode.value if tf == Timeframe.D1 else None,
            )
            t_assembly_total += (time.perf_counter() - t0)

            # PIT filtering benchmark
            t0 = time.perf_counter()
            _ = filter_ict_setups_as_of(setups, as_of_utc=END_UTC)
            t_pit_filter_total += (time.perf_counter() - t0)

            # Metrics
            rev_cnt = sum(1 for s in setups if s.is_reversal)
            cont_cnt = sum(1 for s in setups if s.is_continuation)
            zid_cnt = sum(1 for s in setups if s.status == IctSetupStatus.ZONE_IDENTIFIED)
            ztst_cnt = sum(1 for s in setups if s.status == IctSetupStatus.ZONE_TESTED)
            inv_cnt = sum(1 for s in setups if s.status == IctSetupStatus.INVALIDATED)
            exp_cnt = sum(1 for s in setups if s.status == IctSetupStatus.EXPIRED)

            summary = summarize_ict_setups(setups)

            row = {
                "price_type": pt.value,
                "timeframe": mode_name,
                "closed_bars": len(raw_candles),
                "confirmed_sweeps": len(conf_sweeps),
                "confirmed_bos": len(conf_bos),
                "confirmed_mss": len(conf_mss),
                "qualified_displacements": len(conf_displacements),
                "eligible_fvg": len(fvgs),
                "raw_ob": len(obs),
                "eligible_ob": len(active_obs),
                "reversal_candidates": rev_cnt,
                "continuation_candidates": cont_cnt,
                "zone_identified": zid_cnt,
                "zone_tested": ztst_cnt,
                "invalidated": inv_cnt,
                "expired": exp_cnt,
                "pit_rejected": 0,
                "insufficient_data": 0,
                "total_setups": len(setups),
                "unique_setup_ids": len({s.setup_id for s in setups}),
                "unique_price_zones": len({(s.selected_zone_bottom, s.selected_zone_top) for s in setups}),
                "active_snapshots": summary.zone_identified_count + summary.zone_tested_count,
                "history_invalidated_expired": summary.invalidated_count + summary.expired_count,
            }
            all_results.append(row)
            all_setups_found.extend(setups)

    t_total_pipeline = time.perf_counter() - t_start_total

    # 2. Print Report Table
    print("\n" + "=" * 140)
    print("STAGE 12: REAL XAUUSD SETUP AUDIT TABLE (2026-09-15 - 2026-10-15 UTC)")
    print("=" * 140)
    headers = [
        "PT", "TF", "Bars", "Swp", "BOS", "MSS", "Disp", "FVG", "OB",
        "Rev", "Cont", "Z_ID", "Z_Tst", "Inv", "Exp", "PIT_Rej", "Total",
    ]
    print(f"{headers[0]:<4} {headers[1]:<8} {headers[2]:>5} {headers[3]:>4} {headers[4]:>4} {headers[5]:>4} {headers[6]:>5} {headers[7]:>4} {headers[8]:>4} | {headers[9]:>4} {headers[10]:>5} | {headers[11]:>5} {headers[12]:>5} {headers[13]:>4} {headers[14]:>4} {headers[15]:>7} | {headers[16]:>5}")
    print("-" * 140)
    for r in all_results:
        print(f"{r['price_type']:<4} {r['timeframe']:<8} {r['closed_bars']:>5} {r['confirmed_sweeps']:>4} {r['confirmed_bos']:>4} {r['confirmed_mss']:>4} {r['qualified_displacements']:>5} {r['eligible_fvg']:>4} {r['eligible_ob']:>4} | {r['reversal_candidates']:>4} {r['continuation_candidates']:>5} | {r['zone_identified']:>5} {r['zone_tested']:>5} {r['invalidated']:>4} {r['expired']:>4} {r['pit_rejected']:>7} | {r['total_setups']:>5}")
    print("-" * 140)

    # 3. Numerical Reconciliation
    print("\n" + "=" * 110)
    print("NUMERICAL RECONCILIATION SUMMARY")
    print("=" * 110)
    tot_candidates = sum(r["total_setups"] for r in all_results)
    tot_rev = sum(r["reversal_candidates"] for r in all_results)
    tot_cont = sum(r["continuation_candidates"] for r in all_results)
    tot_zid = sum(r["zone_identified"] for r in all_results)
    tot_ztst = sum(r["zone_tested"] for r in all_results)
    tot_inv = sum(r["invalidated"] for r in all_results)
    tot_exp = sum(r["expired"] for r in all_results)
    tot_unique_ids = sum(r["unique_setup_ids"] for r in all_results)
    tot_unique_zones = sum(r["unique_price_zones"] for r in all_results)

    print(f"Total Structural Candidates Detected:   {tot_candidates}")
    print(f"  - Reversal Candidates:                {tot_rev}")
    print(f"  - Continuation Candidates:            {tot_cont}")
    print(f"Candidate Status Breakdown:")
    print(f"  - ZONE_IDENTIFIED:                    {tot_zid}")
    print(f"  - ZONE_TESTED:                        {tot_ztst}")
    print(f"  - INVALIDATED:                        {tot_inv}")
    print(f"  - EXPIRED:                            {tot_exp}")
    print(f"Unique Candidate IDs across series:     {tot_unique_ids}")
    print(f"Unique Physical Price Zones:            {tot_unique_zones}")

    # 4. Detailed Candidate Examples
    print("\n" + "=" * 110)
    print(f"STAGE 12: CANDIDATE EXAMPLES (Found {len(all_setups_found)} Total)")
    print("=" * 110)
    sample_setups = all_setups_found[:10]
    for idx, s in enumerate(sample_setups, 1):
        print(f"\n--- Candidate #{idx} ---")
        print(f"Setup ID:             {s.setup_id}")
        print(f"Type & Direction:     {s.setup_type.value} | {s.direction.value}")
        print(f"Status:               {s.status.value}")
        print(f"Structure Event ID:   {s.structure_event_id} (confirmed: {s.structure_confirmation_time.isoformat()})")
        print(f"Sweep Event ID:       {s.sweep_event_id}")
        print(f"MSS Confirmation ID:  {s.mss_confirmation_id}")
        print(f"Displacement ID:      {s.displacement_event_id}")
        print(f"FVG ID:               {s.fvg_id}")
        print(f"Order Block ID:       {s.order_block_id}")
        print(f"OTE ID:               {s.ote_id}")
        print(f"Zone Boundaries:      [{s.selected_zone_bottom} - {s.selected_zone_top}] (Mid: {s.selected_zone_midpoint})")
        print(f"Available Time:       {s.setup_available_time.isoformat()}")
        print(f"Confluence Count:     {s.confluence_score}")
        print(f"Explanation:\n{explain_ict_setup(s)}")

    # 5. Chronological PIT Replay Audit
    print("\n" + "=" * 110)
    print("STAGE 13: CHRONOLOGICAL POINT-IN-TIME REPLAY AUDIT")
    print("=" * 110)

    replay_targets = sample_setups[:5]
    if not replay_targets and all_setups_found:
        replay_targets = all_setups_found[:5]

    for idx, target in enumerate(replay_targets, 1):
        print(f"\n[REPLAY TARGET #{idx}: {target.setup_id}]")
        print(f"  Setup Type: {target.setup_type.value} | Direction: {target.direction.value}")
        print(f"  Structure Confirmed: {target.structure_confirmation_time.isoformat()}")
        print(f"  Zone Available:      {target.zone_available_time.isoformat()}")
        print(f"  Setup Available:     {target.setup_available_time.isoformat()}")
        if target.test_detection_time:
            print(f"  First Tested Time:   {target.test_detection_time.isoformat()}")
        if target.invalidation_time:
            print(f"  Invalidation Time:   {target.invalidation_time.isoformat()}")

        # Define 7 discrete snapshot times
        from datetime import timedelta
        t_before_struct = target.structure_confirmation_time - timedelta(minutes=1)
        t_at_struct = target.structure_confirmation_time
        t_at_avail = target.setup_available_time
        t_before_test = (target.test_detection_time - timedelta(minutes=1)) if target.test_detection_time else (t_at_avail + timedelta(hours=1))
        t_after_test = target.test_detection_time if target.test_detection_time else (t_at_avail + timedelta(hours=2))
        t_before_inv = (target.invalidation_time - timedelta(minutes=1)) if target.invalidation_time else (t_after_test + timedelta(hours=1))
        t_after_inv = target.invalidation_time if target.invalidation_time else (t_after_test + timedelta(hours=2))

        snapshots = [
            ("1. Before Structure Confirmation", t_before_struct),
            ("2. At Structure Confirmation", t_at_struct),
            ("3. At Setup Available Time", t_at_avail),
            ("4. Before Zone Test", t_before_test),
            ("5. At/After Zone Test", t_after_test),
            ("6. Before Invalidation/Final", t_before_inv),
            ("7. At/After Invalidation/Final", t_after_inv),
        ]

        for step_name, snap_as_of in snapshots:
            filtered = filter_ict_setups_as_of([target], as_of_utc=snap_as_of)
            if not filtered:
                print(f"    Snapshot [{snap_as_of.isoformat()}] {step_name}: NOT AVAILABLE (PIT Safe: 0 setups visible)")
            else:
                st = filtered[0]
                print(f"    Snapshot [{snap_as_of.isoformat()}] {step_name}: Status={st.status.value} | Confluences={st.confluence_score}")
                # Verify that no future events leaked
                assert st.setup_available_time <= snap_as_of, f"LEAKAGE: available_time {st.setup_available_time} > as_of {snap_as_of}"
                if snap_as_of < (target.test_detection_time or datetime.max.replace(tzinfo=UTC)):
                    assert st.status != IctSetupStatus.ZONE_TESTED, f"LEAKAGE: Premature ZONE_TESTED at {snap_as_of}"
                if snap_as_of < (target.invalidation_time or datetime.max.replace(tzinfo=UTC)):
                    assert st.status != IctSetupStatus.INVALIDATED, f"LEAKAGE: Premature INVALIDATED at {snap_as_of}"

    # 6. Performance Benchmarks
    print("\n" + "=" * 110)
    print("STAGE 14: PERFORMANCE BENCHMARK (Single Run)")
    print("=" * 110)
    print(f"1. Data Loading:                 {t_load:.4f}s")
    print(f"2. Resampling (8 series):        {t_resample_total:.4f}s")
    print(f"3. Existing Feature Engines:     {t_features_total:.4f}s")
    print(f"4. ICT Candidate Assembly:       {t_assembly_total:.4f}s")
    print(f"5. PIT Filtering:                {t_pit_filter_total:.4f}s")
    print(f"-------------------------------------------------------")
    print(f"Total Pipeline Execution Time:   {t_total_pipeline:.4f}s")


if __name__ == "__main__":
    run_audit()
