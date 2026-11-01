"""Phase 4D-K3 Real XAUUSD Data Audit Script.

Analyzes 2026-09-15 to 2026-10-15 UTC real XAUUSD dataset across:
- BID / ASK
- H4 / H2 / D1 (NY 17:00) / D1 (UTC Midnight)
- Compares setup candidates, entry zones (FVG vs OB), and OB confluences.
"""

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
    IctConfluenceType,
    IctSetupCandidate,
    IctSetupConfig,
    IctSetupStatus,
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

SETUP_CONFIG_DEFAULT = IctSetupConfig()  # prefer_order_block_over_fvg=False (Default)
SETUP_CONFIG_PREFER_OB = IctSetupConfig(prefer_order_block_over_fvg=True)

MODES = [
    ("D1 NY", Timeframe.D1, DailyCloseMode.NEW_YORK_1700, "NEW_YORK_1700"),
    ("D1 UTC", Timeframe.D1, DailyCloseMode.UTC_MIDNIGHT, "UTC_MIDNIGHT"),
    ("H4", Timeframe.H4, DailyCloseMode.NEW_YORK_1700, None),
    ("H2", Timeframe.H2, DailyCloseMode.NEW_YORK_1700, None),
]


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

sys.stdout = TeeLogger("scratch/audit_phase_4d_k3_results.txt")  # type: ignore[assignment]


def run_audit() -> None:
    print("=" * 110)
    print("TRADERLAB FAZ 4D-K3 REAL XAUUSD DATA AUDIT")
    print(f"Period: {START_UTC.strftime('%Y-%m-%d %H:%M UTC')} to {END_UTC.strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 110)

    for pt in [PriceType.BID, PriceType.ASK]:
        raw_m1 = load_month_m1_candles_cached(2026, 9, pt) + load_month_m1_candles_cached(2026, 10, pt)
        m1_filtered = [c for c in raw_m1 if START_UTC <= c.open_time <= END_UTC]
        print(f"\n>>> PRICE TYPE: {pt.value} (Loaded {len(m1_filtered)} M1 candles)")

        for label, tf, d1_mode, d1_sess in MODES:
            # Resample
            resampled = resample_m1_candles(m1_filtered, tf, daily_close_mode=d1_mode)
            candles = [
                rc.candle for rc in resampled
                if START_UTC <= rc.candle.open_time <= END_UTC and rc.candle.is_closed
            ]

            # Features
            swings = filter_confirmed_swings(detect_swings(candles, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT))
            sweeps = filter_confirmed_sweeps(detect_liquidity_sweeps(candles, swings, config=LiquiditySweepConfig(tick_size=Decimal("0.01"), min_penetration_ticks=1)))
            struct_breaks, _ = detect_market_structure(candles, swings, config=MarketStructureConfig(tick_size=Decimal("0.01"), break_threshold_ticks=1))
            breaks = filter_confirmed_structure_breaks(struct_breaks)
            chochs = [b for b in breaks if b.is_choch]
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
            conf_disps = [d for d in disps if getattr(d, "confirmation_status", "CONFIRMED") == "CONFIRMED"]
            fvgs = detect_fair_value_gaps(candles)
            mss_records = evaluate_mss_confirmations(
                candles,
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
                candles,
                structure_breaks=breaks,
                displacements=conf_disps,
                mss_confirmations=msss,
                fvg_zones=fvgs,
                config=OrderBlockConfig(max_origin_lookback_bars=6),
            )
            ranges = identify_dealing_ranges(swings)
            otes = generate_ote_zones(ranges)
            respects = evaluate_all_fvgs_respect(fvgs, candles, config=RESPECT_CONFIG)

            # Detect setups with default config (FVG first)
            setups_def = detect_ict_setup_candidates(
                candles=candles,
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
                d1_session_mode=d1_sess,
            )

            # Detect setups with prefer_order_block_over_fvg=True
            setups_ob_pref = detect_ict_setup_candidates(
                candles=candles,
                sweeps=sweeps,
                structure_breaks=breaks,
                mss_records=msss,
                displacements=conf_disps,
                fvg_zones=fvgs,
                order_blocks=obs,
                ote_zones=otes,
                fvg_respect_summaries=respects,
                dealing_ranges=ranges,
                config=SETUP_CONFIG_PREFER_OB,
                d1_session_mode=d1_sess,
            )

            fvg_dict = {f.id: f for f in fvgs}
            ob_dict = {o.order_block_id: o for o in obs}

            # Count breakdowns for default
            tot_def = len(setups_def)
            fvg_zone_def = sum(1 for s in setups_def if s.fvg_id in fvg_dict and s.selected_zone_top == fvg_dict[s.fvg_id].top_price)
            ob_zone_def = sum(1 for s in setups_def if s.order_block_id in ob_dict and s.selected_zone_top == ob_dict[s.order_block_id].zone_top)
            ob_conf_def = sum(1 for s in setups_def if any(c.evidence_type == IctConfluenceType.ORDER_BLOCK for c in s.confluences))

            # Count breakdowns for prefer OB
            tot_ob = len(setups_ob_pref)
            fvg_zone_ob = sum(1 for s in setups_ob_pref if s.fvg_id in fvg_dict and s.selected_zone_top == fvg_dict[s.fvg_id].top_price)
            ob_zone_ob = sum(1 for s in setups_ob_pref if s.order_block_id in ob_dict and s.selected_zone_top == ob_dict[s.order_block_id].zone_top)
            ob_conf_ob = sum(1 for s in setups_ob_pref if any(c.evidence_type == IctConfluenceType.ORDER_BLOCK for c in s.confluences))

            print(f"\n--- {pt.value} {label} ---")
            print(f"Total Bars: {len(candles)} | Conf Breaks: {len(breaks)} | Conf MSS: {len(msss)} | Total OBs: {len(obs)}")
            print(f"  [Default Config - FVG First]:")
            print(f"    Total Setups:        {tot_def}")
            print(f"    Zone is FVG:         {fvg_zone_def}")
            print(f"    Zone is OB:          {ob_zone_def}")
            print(f"    With OB Confluence:  {ob_conf_def}")
            print(f"  [Prefer OB Over FVG Config]:")
            print(f"    Total Setups:        {tot_ob}")
            print(f"    Zone is FVG:         {fvg_zone_ob}")
            print(f"    Zone is OB:          {ob_zone_ob}")
            print(f"    With OB Confluence:  {ob_conf_ob}")

            # Print sample details if any OB was selected as entry zone
            for s in setups_ob_pref:
                if s.order_block_id:
                    print(f"    -> Setup {s.setup_id[:45]}... | Type: {s.setup_type.value} {s.direction.value} | OB ID: {s.order_block_id} | Zone: [{s.selected_zone_bottom} - {s.selected_zone_top}]")


if __name__ == "__main__":
    run_audit()
