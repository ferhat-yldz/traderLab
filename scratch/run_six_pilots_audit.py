"""Run 6 distinct pilots on real XAUUSD January 2024 data (D1, H4, H2 for BID and ASK).

Produces comprehensive audit tables, completeness breakdown, and manual OHLC recalculation
for at least 3 sample FVGs per timeframe.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from traderlab.application.analysis.resampling import WindowCompleteness, resample_m1_candles
from traderlab.core.types import PriceType, Timeframe
from traderlab.domain.market_structure.fvg import FvgDirection, detect_fair_value_gaps
from traderlab.infrastructure.providers.market_data_lab.config import get_market_data_lab_settings
from traderlab.infrastructure.providers.market_data_lab.csv_reader import (
    bar_to_candle,
    read_m1_csv,
)


def run_pilots() -> None:
    settings = get_market_data_lab_settings()
    bid_path, ask_path = settings.validate_dataset_files(2024, 1)

    print(f"Loading real January 2024 raw CSV files...")
    print(f"BID Path: {bid_path} (exists: {bid_path.exists()})")
    print(f"ASK Path: {ask_path} (exists: {ask_path.exists()})")

    raw_bid_bars = read_m1_csv(bid_path, PriceType.BID)
    raw_ask_bars = read_m1_csv(ask_path, PriceType.ASK)
    print(f"Loaded {len(raw_bid_bars):,} BID M1 bars, {len(raw_ask_bars):,} ASK M1 bars.")

    bid_candles = [bar_to_candle(b) for b in raw_bid_bars]
    ask_candles = [bar_to_candle(b) for b in raw_ask_bars]

    timeframes = [
        (Timeframe.D1, "D1 (Daily)"),
        (Timeframe.H4, "H4 (4 Hours)"),
        (Timeframe.H2, "H2 (2 Hours)"),
    ]

    price_types = [
        (PriceType.BID, bid_candles),
        (PriceType.ASK, ask_candles),
    ]

    results = []

    print("\n" + "=" * 90)
    print("PILOT AUDIT RESULTS (6 COMBINATIONS)")
    print("=" * 90)

    for tf, tf_name in timeframes:
        for pt, candles in price_types:
            # 1. Resample raw M1 candles
            resampled = resample_m1_candles(candles, tf)
            total_resampled = len(resampled)

            # Completeness breakdown
            complete_count = sum(
                1 for r in resampled if r.completeness == WindowCompleteness.COMPLETE
            )
            partial_count = sum(
                1 for r in resampled if r.completeness == WindowCompleteness.PARTIAL
            )
            all_synth_count = sum(
                1 for r in resampled if r.completeness == WindowCompleteness.ALL_SYNTHETIC_CLOSED
            )

            # Active domain candles
            domain_candles = [r.candle for r in resampled]

            # In FVG detection:
            # Check behavior with require_closed=True vs require_closed=False
            fvgs_closed_only = detect_fair_value_gaps(domain_candles, require_closed=True)
            fvgs_all = detect_fair_value_gaps(domain_candles, require_closed=False)

            # Filter out all-synthetic closed bars (market closed)
            non_synth_candles = [c for c in domain_candles if not c.is_synthetic]
            eliminated_synth = total_resampled - len(non_synth_candles)

            bullish_count = sum(1 for f in fvgs_closed_only if f.is_bullish)
            bearish_count = sum(1 for f in fvgs_closed_only if f.is_bearish)

            start_dt = domain_candles[0].open_time if domain_candles else None
            end_dt = domain_candles[-1].close_time if domain_candles else None

            pilot_summary = {
                "timeframe": tf.name,
                "tf_label": tf_name,
                "price_type": pt.value,
                "start_dt": start_dt,
                "end_dt": end_dt,
                "total_resampled": total_resampled,
                "complete_count": complete_count,
                "partial_count": partial_count,
                "all_synth_count": all_synth_count,
                "eligible_candles": len(non_synth_candles),
                "eliminated_synth": eliminated_synth,
                "bullish_fvgs": bullish_count,
                "bearish_fvgs": bearish_count,
                "total_fvgs": len(fvgs_closed_only),
                "fvgs": fvgs_closed_only,
                "domain_candles_map": {c.close_time: c for c in domain_candles},
                "open_map": {c.open_time: c for c in domain_candles},
            }
            results.append(pilot_summary)

            print(f"\n[{tf.name} - {pt.value}] ({tf_name})")
            print(f"  Tarih Aralığı: {start_dt} -> {end_dt}")
            print(f"  Toplam Resampled Mum: {total_resampled}")
            print(
                f"  COMPLETE: {complete_count} | PARTIAL: {partial_count} | ALL_SYNTHETIC: {all_synth_count}"
            )
            print(f"  Analize Uygun (Non-Synthetic): {len(non_synth_candles)}")
            print(f"  Elenen Sentetik/Kapalı Bar: {eliminated_synth}")
            print(
                f"  Bullish FVG: {bullish_count} | Bearish FVG: {bearish_count} | Toplam: {len(fvgs_closed_only)}"
            )

    # Detailed inspection of 3 sample FVGs for D1, H4, and H2
    print("\n" + "=" * 90)
    print("DETAILED 3-CANDLE INDEPENDENT RE-EVALUATION FOR SAMPLE FVGS")
    print("=" * 90)

    for res in results:
        fvgs = res["fvgs"]
        tf_name = res["timeframe"]
        pt = res["price_type"]
        open_map = res["open_map"]

        print(f"\n--- SAMPLES FOR {tf_name} {pt} (Total Found: {len(fvgs)}) ---")
        samples = fvgs[:3] if len(fvgs) >= 3 else fvgs
        if not samples:
            print("  (No FVGs detected under current parameters)")
            continue

        for idx, fvg in enumerate(samples, start=1):
            c1 = open_map.get(fvg.candle_1_open)
            c2 = open_map.get(fvg.candle_2_open)
            c3 = open_map.get(fvg.candle_3_open)

            print(f"\n  Sample #{idx} [{fvg.direction.value}] - ID: {fvg.id}")
            print(f"    Candle 1: Open={c1.open_time} Close={c1.close_time}")
            print(f"      OHLC: O={c1.open} H={c1.high} L={c1.low} C={c1.close}")
            print(f"    Candle 2 (Displacement): Open={c2.open_time} Close={c2.close_time}")
            print(f"      OHLC: O={c2.open} H={c2.high} L={c2.low} C={c2.close}")
            print(f"    Candle 3: Open={c3.open_time} Close={c3.close_time}")
            print(f"      OHLC: O={c3.open} H={c3.high} L={c3.low} C={c3.close}")

            # Independent calculation
            if fvg.is_bullish:
                ind_cond = c3.low > c1.high
                ind_bottom = c1.high
                ind_top = c3.low
                formula_str = f"c3.low ({c3.low}) > c1.high ({c1.high})"
            else:
                ind_cond = c3.high < c1.low
                ind_bottom = c3.high
                ind_top = c1.low
                formula_str = f"c3.high ({c3.high}) < c1.low ({c1.low})"

            ind_width = ind_top - ind_bottom
            ind_ce = (ind_top + ind_bottom) / Decimal(2)

            print(f"    Matematiksel Doğrulama:")
            print(f"      Koşul: {formula_str} -> {ind_cond}")
            print(
                f"      Hesaplanan Sınırlar: [{ind_bottom}, {ind_top}] (Motor Değeri: [{fvg.bottom_price}, {fvg.top_price}])"
            )
            print(f"      Hesaplanan Genişlik: {ind_width} (Motor Değeri: {fvg.width})")
            print(f"      Hesaplanan CE (%50): {ind_ce} (Motor Değeri: {fvg.ce_price})")
            print(
                f"      Oluşum Zamanı (c3.close_time): {c3.close_time} (Motor Değeri: {fvg.formation_time})"
            )

            # Assert exact match
            assert ind_cond is True
            assert ind_bottom == fvg.bottom_price
            assert ind_top == fvg.top_price
            assert ind_width == fvg.width
            assert ind_ce == fvg.ce_price
            assert c3.close_time == fvg.formation_time
            print(f"      Sonuç: %100 BİREBİR SAYISAL DOĞRULAMA BAŞARILI")


if __name__ == "__main__":
    run_pilots()
