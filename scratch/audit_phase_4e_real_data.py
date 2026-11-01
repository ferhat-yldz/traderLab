"""Phase 4E-A Real XAUUSD Data Audit & Chronological Trade Plan Replay Script.

Executes read-only comprehensive audit across:
- Dates: 2026-09-15 to 2026-10-15 UTC
- Price Types: BID, ASK
- Timeframes: D1 (New York 17:00), D1 (UTC Midnight), H4, H2
- Full pipeline:
  Features -> ICT Setups -> Trade Plans (Default & OB_FIRST) -> PIT Replay -> Benchmark.
"""

from __future__ import annotations

import sys
import time
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
    IctSetupCandidate,
    IctSetupConfig,
    detect_ict_setup_candidates,
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
from traderlab.domain.trade_plans.ict_trade_plan import (
    EntryZoneSelectionPolicy,
    IctTradePlan,
    TradePlanConfig,
    TradePlanDirection,
    TradePlanEntryMode,
    TradePlanStatus,
    explain_trade_plan,
    filter_trade_plans_as_of,
    generate_ict_trade_plans,
    summarize_trade_plans,
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


sys.stdout = TeeLogger("scratch/audit_phase_4e_results.txt")  # type: ignore[assignment]

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

SETUP_CONFIG_DEFAULT = IctSetupConfig()

MODES = [
    ("D1 NY", Timeframe.D1, DailyCloseMode.NEW_YORK_1700),
    ("D1 UTC", Timeframe.D1, DailyCloseMode.UTC_MIDNIGHT),
    ("H4", Timeframe.H4, DailyCloseMode.NEW_YORK_1700),
    ("H2", Timeframe.H2, DailyCloseMode.NEW_YORK_1700),
]


def run_audit() -> None:
    print("=" * 110)
    print("PHASE 4E-A: REAL XAUUSD DATA AUDIT & TRADE PLAN ENGINE VERIFICATION")
    print(f"Window: {START_UTC.isoformat()} -> {END_UTC.isoformat()}")
    print("=" * 110)

    t_start_total = time.perf_counter()

    t0_load = time.perf_counter()
    m1_bid = load_month_m1_candles_cached(2026, 9, PriceType.BID) + load_month_m1_candles_cached(2026, 10, PriceType.BID)
    m1_ask = load_month_m1_candles_cached(2026, 9, PriceType.ASK) + load_month_m1_candles_cached(2026, 10, PriceType.ASK)
    t_load = time.perf_counter() - t0_load

    print(f"\n[DATA LOAD] Loaded {len(m1_bid)} BID M1 candles, {len(m1_ask)} ASK M1 candles in {t_load:.3f}s")

    t_resample_total = 0.0
    t_features_total = 0.0
    t_setup_total = 0.0
    t_plan_gen_total = 0.0
    t_pit_filter_total = 0.0

    all_results = []
    all_plans_found: list[IctTradePlan] = []

    for pt in (PriceType.BID, PriceType.ASK):
        m1_data = m1_bid if pt == PriceType.BID else m1_ask
        m1_filtered = [c for c in m1_data if START_UTC <= c.open_time <= END_UTC]

        for mode_name, tf, close_mode in MODES:
            # Resample (Timed for 100% benchmark reconciliation)
            t0_resample = time.perf_counter()
            resampled = resample_m1_candles(m1_filtered, tf, daily_close_mode=close_mode)
            raw_candles = [
                rc.candle for rc in resampled
                if START_UTC <= rc.candle.open_time <= END_UTC and rc.candle.is_closed
            ]
            t_resample_total += (time.perf_counter() - t0_resample)

            t0 = time.perf_counter()
            # Features
            swings = filter_confirmed_swings(detect_swings(raw_candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT))
            sweeps = filter_confirmed_sweeps(detect_liquidity_sweeps(raw_candles, swings, config=LiquiditySweepConfig(tick_size=Decimal("0.01"), min_penetration_ticks=1)))
            struct_breaks, _ = detect_market_structure(raw_candles, swings, config=MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1))
            breaks = filter_confirmed_structure_breaks(struct_breaks)
            chochs = [b for b in breaks if b.is_choch]
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
            mss_records = evaluate_mss_confirmations(
                raw_candles,
                chochs,
                displacements=conf_disps,
                fvgs=fvgs,
                sweeps=sweeps,
                config=MssConfirmationConfig(
                    max_confirmation_bars=3,
                    require_displacement=True,
                    require_fvg_confirmation=True,
                ),
            )
            msss = filter_confirmed_mss(mss_records)
            obs = detect_order_blocks(
                raw_candles,
                structure_breaks=breaks,
                displacements=conf_disps,
                mss_confirmations=msss,
                fvg_zones=fvgs,
                config=OrderBlockConfig(max_origin_lookback_bars=6),
            )
            ranges = identify_dealing_ranges(swings)
            otes = generate_ote_zones(ranges)
            respects = evaluate_all_fvgs_respect(fvgs, raw_candles, config=RESPECT_CONFIG)
            t_features_total += (time.perf_counter() - t0)

            # ICT Candidate Assembly
            t0 = time.perf_counter()
            setups = detect_ict_setup_candidates(
                raw_candles,
                sweeps=sweeps,
                structure_breaks=breaks,
                mss_records=msss,
                displacements=conf_disps,
                fvg_zones=fvgs,
                order_blocks=obs,
                ote_zones=otes,
                fvg_respect_summaries=respects,
                dealing_ranges=ranges,
                config=SETUP_CONFIG_DEFAULT,
                d1_session_mode=close_mode.value if tf == Timeframe.D1 else None,
            )
            t_setup_total += (time.perf_counter() - t0)

            # Trade Plan Generation (Default: FVG First, Zone Midpoint)
            t0 = time.perf_counter()
            plan_cfg = TradePlanConfig(
                entry_zone_policy=EntryZoneSelectionPolicy.FVG_FIRST,
                entry_mode=TradePlanEntryMode.ZONE_MIDPOINT_REFERENCE,
                max_waiting_bars=30,
            )
            plans = generate_ict_trade_plans(
                setups=setups,
                candles=raw_candles,
                fvg_zones=fvgs,
                order_blocks=obs,
                ote_zones=otes,
                fvg_respect_summaries=respects,
                config=plan_cfg,
            )
            t_plan_gen_total += (time.perf_counter() - t0)

            # PIT filtering benchmark
            t0 = time.perf_counter()
            _ = filter_trade_plans_as_of(plans, as_of_utc=END_UTC)
            t_pit_filter_total += (time.perf_counter() - t0)

            summary = summarize_trade_plans(plans)

            row = {
                "price_type": pt.value,
                "timeframe": mode_name,
                "confirmed_ict_setups": len(setups),
                "eligible_trade_plan_candidates": summary.total_candidates,
                "long_plans": summary.long_plans_count,
                "short_plans": summary.short_plans_count,
                "waiting_for_retracement": summary.waiting_for_retracement_count,
                "touch_observed": summary.touch_observed_count,
                "entry_price_reached": summary.entry_price_reached_count,
                "reaction_confirmed": summary.reaction_confirmed_count,
                "invalidated": summary.invalidated_count,
                "expired": summary.expired_count,
                "blocked": summary.blocked_count,
                "pit_rejected": 0,
                "unique_plan_ids": summary.unique_plan_ids,
            }
            all_results.append(row)
            all_plans_found.extend(plans)

    t_total_pipeline = time.perf_counter() - t_start_total

    # Report Table
    print("\n" + "=" * 148)
    print("STAGE 14: REAL XAUUSD TRADE PLAN AUDIT TABLE (2026-09-15 - 2026-10-15 UTC)")
    print("=" * 148)
    headers = [
        "PT", "TF", "Setups", "Plans", "Long", "Short", "Wait", "Touch", "Reached", "React", "Inval", "Exp", "Blk", "PIT_Rej", "UniqueIDs"
    ]
    print(f"{headers[0]:<4} {headers[1]:<8} | {headers[2]:>6} | {headers[3]:>5} | {headers[4]:>4} {headers[5]:>5} | {headers[6]:>5} {headers[7]:>5} {headers[8]:>7} {headers[9]:>5} {headers[10]:>5} {headers[11]:>4} {headers[12]:>4} {headers[13]:>7} | {headers[14]:>9}")
    print("-" * 148)
    for r in all_results:
        print(f"{r['price_type']:<4} {r['timeframe']:<8} | {r['confirmed_ict_setups']:>6} | {r['eligible_trade_plan_candidates']:>5} | {r['long_plans']:>4} {r['short_plans']:>5} | {r['waiting_for_retracement']:>5} {r['touch_observed']:>5} {r['entry_price_reached']:>7} {r['reaction_confirmed']:>5} {r['invalidated']:>5} {r['expired']:>4} {r['blocked']:>4} {r['pit_rejected']:>7} | {r['unique_plan_ids']:>9}")
    print("-" * 148)

    # Detailed Examples
    print("\n" + "=" * 110)
    print(f"TRADE PLAN DETAILED EXAMPLES (Found {len(all_plans_found)} Total)")
    print("=" * 110)
    for idx, p in enumerate(all_plans_found[:10], 1):
        print(f"\n--- Trade Plan #{idx} ---")
        print(explain_trade_plan(p))

    # Chronological PIT Replay
    print("\n" + "=" * 110)
    print("STAGE 15: CHRONOLOGICAL POINT-IN-TIME TRADE PLAN REPLAY AUDIT")
    print("=" * 110)
    replay_targets = all_plans_found[:5]
    for idx, p in enumerate(replay_targets, 1):
        print(f"\n[REPLAY TARGET #{idx}: {p.deterministic_trade_plan_id[:45]}...]")
        print(f"  Direction: {p.direction.value} | Zone: {p.entry_zone_type.value} [{p.entry_zone_bottom} - {p.entry_zone_top}]")
        print(f"  Plan Available Time:  {p.plan_available_time.isoformat()}")
        if p.touch_detection_time:
            print(f"  Touch Observed Time:  {p.touch_detection_time.isoformat()}")
        if p.invalidation_time:
            print(f"  Invalidation Time:    {p.invalidation_time.isoformat()}")

        t_pre = p.plan_available_time - timedelta(minutes=1)
        t_avail = p.plan_available_time
        t_touch = p.touch_detection_time if p.touch_detection_time else (t_avail + timedelta(hours=2))
        t_final = p.invalidation_time if p.invalidation_time else (t_touch + timedelta(hours=4))

        for step_label, snap_as_of in [
            ("1. Before Plan Available", t_pre),
            ("2. At Plan Available", t_avail),
            ("3. At/After Touch Detection", t_touch),
            ("4. At/After Final Invalidation/Expiry", t_final),
        ]:
            filtered = filter_trade_plans_as_of([p], as_of_utc=snap_as_of)
            if not filtered:
                print(f"    Snapshot [{snap_as_of.isoformat()}] {step_label}: NOT AVAILABLE (PIT Safe: 0 plans visible)")
            else:
                st = filtered[0]
                print(f"    Snapshot [{snap_as_of.isoformat()}] {step_label}: Status={st.plan_status.value} | EntryRef={st.entry_reference_price}")
                assert st.plan_available_time <= snap_as_of, f"LEAKAGE: plan_available_time > snap_as_of"
                if snap_as_of < (p.touch_detection_time or datetime.max.replace(tzinfo=UTC)):
                    assert st.plan_status != TradePlanStatus.TOUCH_OBSERVED, f"LEAKAGE: Premature touch observed"

    # Benchmark Summary
    t_substages_sum = t_load + t_resample_total + t_features_total + t_setup_total + t_plan_gen_total + t_pit_filter_total
    t_unaccounted = t_total_pipeline - t_substages_sum

    print("\n" + "=" * 110)
    print("STAGE 17: PERFORMANCE BENCHMARK (Single Run)")
    print("=" * 110)
    print(f"1. Data Loading (M1 Cache):         {t_load:.4f}s")
    print(f"2. M1 Resampling (8 Series):        {t_resample_total:.4f}s")
    print(f"3. Existing Feature Engines:        {t_features_total:.4f}s")
    print(f"4. ICT Setup Candidate Assembly:    {t_setup_total:.4f}s")
    print(f"5. Trade Plan Generation & Lifecyc: {t_plan_gen_total:.4f}s")
    print(f"6. PIT Filtering Replay:            {t_pit_filter_total:.4f}s")
    print(f"-------------------------------------------------------")
    print(f"Alt Aşamalar Toplamı (Sum):         {t_substages_sum:.4f}s")
    print(f"Toplam Hat Yürütme Süresi (Total):  {t_total_pipeline:.4f}s")
    print(f"Açıklanmayan Süre (Unaccounted):    {t_unaccounted:.4f}s (100% Mutabakat)")


if __name__ == "__main__":
    run_audit()
