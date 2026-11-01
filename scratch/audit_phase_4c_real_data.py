"""Phase 4C Real XAUUSD Market Data Audit Script.

Analyzes 2026-09-15 to 2026-10-15 UTC for BID and ASK across:
- D1 NY 17:00
- D1 UTC 00:00
- H4
- H2

Computes:
- FVG Count
- IFVG Count
- FVG with >=1 Touch
- FVG with >=1 Confirmed Respect
- FVG with >=2 Respects
- FVG with >=3 Respects
- FVG with >=4 Respects
- Failed Reaction Count
- Invalidated Count
- Pending Reaction Count
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    resample_m1_candles,
)
from traderlab.core.types import PriceType, Timeframe
from traderlab.domain.market_structure.fvg import (
    detect_fair_value_gaps,
)
from traderlab.domain.market_structure.fvg_respect import (
    FvgRespectConfig,
    FvgRetestStatus,
    evaluate_all_fvgs_respect,
)
from traderlab.interfaces.dashboard.app import (
    load_month_m1_candles_cached,
)


def run_audit() -> None:
    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)

    config = FvgRespectConfig(
        min_departure_ticks=Decimal("1"),
        min_bars_outside_zone=1,
        min_bars_between_retests=1,
        reaction_confirmation_bars=3,
        min_reaction_ticks=Decimal("1"),
        max_reported_retests=4,
        tick_size=Decimal("0.01"),
    )

    modes = [
        ("D1 NY 17:00", Timeframe.D1, DailyCloseMode.NEW_YORK_1700),
        ("D1 UTC 00:00", Timeframe.D1, DailyCloseMode.UTC_MIDNIGHT),
        ("H4", Timeframe.H4, DailyCloseMode.NEW_YORK_1700),
        ("H2", Timeframe.H2, DailyCloseMode.NEW_YORK_1700),
    ]

    print("=" * 110)
    print("FAZ 4C: GERÇEK XAUUSD PİYASA VERİSİ DENETİMİ (2026-09-15 — 2026-10-15 UTC)")
    print("=" * 110)

    summary_table: list[dict] = []
    h4_multi_respects: list[tuple[str, str, any]] = []

    for price_type in (PriceType.BID, PriceType.ASK):
        t0 = time.perf_counter()
        m1_sep = load_month_m1_candles_cached(2026, 9, price_type)
        m1_oct = load_month_m1_candles_cached(2026, 10, price_type)
        combined_m1 = m1_sep + m1_oct

        for mode_name, tf, close_mode in modes:
            t_res_start = time.perf_counter()
            res = resample_m1_candles(
                combined_m1,
                tf,
                daily_close_mode=close_mode,
            )
            raw_candles = [
                rc.candle
                for rc in res
                if start_utc <= rc.candle.open_time <= end_utc and rc.candle.is_closed
            ]
            t_res = time.perf_counter() - t_res_start

            # Detect FVGs
            t_fvg_start = time.perf_counter()
            fvgs = detect_fair_value_gaps(raw_candles)
            t_fvg = time.perf_counter() - t_fvg_start

            # Evaluate Respect & Retests
            t_eval_start = time.perf_counter()
            summaries = evaluate_all_fvgs_respect(
                fvgs,
                raw_candles,
                config=config,
            )
            t_eval = time.perf_counter() - t_eval_start

            fvg_count = len(summaries)
            ifvg_count = sum(1 for s in summaries if s.inversion_confirmed_at is not None)
            ge_1_touch = sum(1 for s in summaries if s.total_touch_count >= 1)
            ge_1_respect = sum(1 for s in summaries if s.confirmed_respect_count >= 1)
            ge_2_respect = sum(1 for s in summaries if s.confirmed_respect_count >= 2)
            ge_3_respect = sum(1 for s in summaries if s.confirmed_respect_count >= 3)
            ge_4_respect = sum(1 for s in summaries if s.confirmed_respect_count >= 4)
            failed_count = sum(s.failed_reaction_count for s in summaries)
            pending_count = sum(s.pending_reaction_count for s in summaries)
            invalidated_count = sum(
                sum(1 for e in s.retest_events if e.status == FvgRetestStatus.INVALIDATED)
                for s in summaries
            )

            row = {
                "price_type": price_type.value,
                "mode_name": mode_name,
                "candle_count": len(raw_candles),
                "fvg_count": fvg_count,
                "ifvg_count": ifvg_count,
                "ge_1_touch": ge_1_touch,
                "ge_1_respect": ge_1_respect,
                "ge_2_respect": ge_2_respect,
                "ge_3_respect": ge_3_respect,
                "ge_4_respect": ge_4_respect,
                "failed_count": failed_count,
                "invalidated_count": invalidated_count,
                "pending_count": pending_count,
                "t_res": round(t_res, 3),
                "t_fvg": round(t_fvg, 4),
                "t_eval": round(t_eval, 4),
            }
            summary_table.append(row)

            # Check for H4 FVGs with high respect count
            if tf == Timeframe.H4:
                for s in summaries:
                    if s.confirmed_respect_count >= 2:
                        h4_multi_respects.append((price_type.value, mode_name, s))

    # Print Table
    print("\n| Fiyat Tipi | Mod / Zaman Dilimi | Mum | FVG | IFVG | >=1 Touch | >=1 Respect | >=2 Respect | >=3 Respect | >=4 Respect | Failed | Invalidated | Pending | Süre (Res/FVG/Eval) |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in summary_table:
        print(
            f"| **{r['price_type']}** | {r['mode_name']} | {r['candle_count']} | {r['fvg_count']} | {r['ifvg_count']} | "
            f"{r['ge_1_touch']} | {r['ge_1_respect']} | {r['ge_2_respect']} | {r['ge_3_respect']} | {r['ge_4_respect']} | "
            f"{r['failed_count']} | {r['invalidated_count']} | {r['pending_count']} | {r['t_res']}s / {r['t_fvg']}s / {r['t_eval']}s |"
        )

    # Print Totals
    tot_candles = sum(r["candle_count"] for r in summary_table)
    tot_fvgs = sum(r["fvg_count"] for r in summary_table)
    tot_ifvgs = sum(r["ifvg_count"] for r in summary_table)
    tot_ge_1_touch = sum(r["ge_1_touch"] for r in summary_table)
    tot_ge_1_resp = sum(r["ge_1_respect"] for r in summary_table)
    tot_ge_2_resp = sum(r["ge_2_respect"] for r in summary_table)
    tot_ge_3_resp = sum(r["ge_3_respect"] for r in summary_table)
    tot_ge_4_resp = sum(r["ge_4_respect"] for r in summary_table)
    tot_failed = sum(r["failed_count"] for r in summary_table)
    tot_inv = sum(r["invalidated_count"] for r in summary_table)
    tot_pend = sum(r["pending_count"] for r in summary_table)

    print(
        f"| **GENEL TOPLAM** | - | **{tot_candles}** | **{tot_fvgs}** | **{tot_ifvgs}** | "
        f"**{tot_ge_1_touch}** | **{tot_ge_1_resp}** | **{tot_ge_2_resp}** | **{tot_ge_3_resp}** | **{tot_ge_4_resp}** | "
        f"**{tot_failed}** | **{tot_inv}** | **{tot_pend}** | - |"
    )

    print("\n" + "=" * 110)
    print("H4 >=2 RESPECT ÖRNEKLERİ VE KRONOLOJİK OLAY DÖKÜMÜ")
    print("=" * 110)
    if not h4_multi_respects:
        print("Gerçek H4 verisinde >=2 respect kriterini sağlayan FVG bulunamadı.")
    else:
        for pt, mode, s in h4_multi_respects:
            print(f"\n[FVG {s.fvg_id}] ({pt} - {mode})")
            print(f"  Yön: {s.fvg_direction.value} | Etkin: {s.effective_direction.value}")
            print(f"  Bölge: [{s.zone_bottom} - {s.zone_top}] (Genişlik: {s.zone_width})")
            print(f"  Oluşum: {s.formation_time.isoformat()}")
            print(f"  Toplam Temas: {s.total_touch_count} | Teyitli Respect: {s.confirmed_respect_count} | Başarısız: {s.failed_reaction_count}")
            print(f"  Maksimum Respect Serisi: {s.maximum_respect_streak} | Son Temas: {s.last_touch_time}")
            print("  Retest Olayları:")
            for ev in s.retest_events:
                print(
                    f"    - Seq #{ev.retest_sequence_number} [{ev.lifecycle_phase}] @ {ev.touch_bar_open_time.isoformat()}: "
                    f"TouchPrice={ev.touch_price}, Pen={ev.penetration_depth} ({ev.penetration_percentage:.1f}%), "
                    f"Status={ev.status.value}, ConfTime={ev.reaction_confirmation_time}, "
                    f"ReactDist={ev.reaction_distance}, Reason={ev.status_reason}"
                )


if __name__ == "__main__":
    run_audit()
