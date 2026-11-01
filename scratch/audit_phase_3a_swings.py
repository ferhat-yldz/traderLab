"""Real XAUUSD market data audit script for Phase 3A Swing Detection Engine.

Performs:
1. Loads real XAUUSD BID and ASK M1 candles for Sep-Oct 2026.
2. Resamples to D1 (both UTC_MIDNIGHT and NEW_YORK_1700), H4, and H2.
3. Detects Swings with left_bars=3, right_bars=3, TiePolicy.STRICT.
4. Generates comprehensive audit metrics:
   - Swing High count
   - Swing Low count
   - Candidate count
   - Confirmed count
   - First and last pivot time
   - First and last confirmation time
5. Mathematically validates 10 individual swings (5 high, 5 low across timeframes)
   showing all left bars and right bars.
6. Proves Point-in-Time safety: verifies no confirmation before the right-side candle closes.
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
from traderlab.domain.market_structure.swing import (
    SwingConfirmationStatus,
    SwingPoint,
    SwingType,
    TiePolicy,
    detect_swings,
    filter_confirmed_swings,
)
from traderlab.interfaces.dashboard.app import (
    load_month_m1_candles_cached,
)


def run_phase_3a_audit() -> None:
    print("=" * 80)
    print("TRADERLAB — FAZ 3A: REAL XAUUSD SWING DETECTION AUDIT")
    print("=" * 80)

    start_utc = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 15, 23, 59, tzinfo=UTC)

    all_audit_swings: list[tuple[PriceType, str, list[Candle], list[SwingPoint]]] = []

    for p_type in (PriceType.BID, PriceType.ASK):
        print(f"\nLoading real M1 data for {p_type.value}...")
        m1_sep = load_month_m1_candles_cached(2026, 9, p_type)
        m1_oct = load_month_m1_candles_cached(2026, 10, p_type)
        all_m1 = m1_sep + m1_oct

        # Resample to D1 (NY 17:00), D1 (UTC 00:00), H4, H2
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

        print(f"\n>>> [{p_type.value}] AUDIT SUMMARY (2026-09-15 to 2026-10-15):")
        print(
            f"{'Timeframe':<18} | {'Total':<6} | {'Highs':<6} | {'Lows':<6} | {'Conf':<6} | {'Cand':<6} | {'First Pivot (UTC)':<20} | {'Last Conf (UTC)':<20}"
        )
        print("-" * 105)

        for name, c_list in configs:
            swings = detect_swings(c_list, left_bars=3, right_bars=3, tie_policy=TiePolicy.STRICT)
            all_audit_swings.append((p_type, name, c_list, swings))

            highs = [s for s in swings if s.is_swing_high]
            lows = [s for s in swings if s.is_swing_low]
            conf = [s for s in swings if s.is_confirmed]
            cand = [s for s in swings if s.is_candidate]

            first_piv = swings[0].pivot_time.strftime("%Y-%m-%d %H:%M") if swings else "N/A"
            last_conf = (
                conf[-1].confirmation_time.strftime("%Y-%m-%d %H:%M")
                if conf and conf[-1].confirmation_time
                else "N/A"
            )

            print(
                f"{name:<18} | {len(swings):<6} | {len(highs):<6} | {len(lows):<6} | {len(conf):<6} | {len(cand):<6} | {first_piv:<20} | {last_conf:<20}"
            )

    # ─────────────────────────────────────────────────────────────────────────
    # DETAILED MATHEMATICAL AUDIT OF 10 SWING EXAMPLES
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("DETAILED MATHEMATICAL STEP-BY-STEP PROOF FOR 10 SAMPLE SWINGS")
    print("=" * 80)

    # Pick samples from BID H4, BID H2, ASK H4, BID D1
    samples_found = 0
    for p_type, name, c_list, swings in all_audit_swings:
        if samples_found >= 10:
            break
        conf_swings = [s for s in swings if s.is_confirmed]
        if not conf_swings:
            continue

        c_map = {c.open_time: idx for idx, c in enumerate(c_list)}

        for s in conf_swings[:3]:
            if samples_found >= 10:
                break
            piv_idx = c_map.get(s.pivot_time)
            if piv_idx is None or piv_idx < 3 or (piv_idx + 3) >= len(c_list):
                continue

            samples_found += 1
            left_candles = c_list[piv_idx - 3 : piv_idx]
            pivot_c = c_list[piv_idx]
            right_candles = c_list[piv_idx + 1 : piv_idx + 4]

            print(
                f"\n--- [SAMPLE #{samples_found}] {p_type.value} {name} {s.swing_type.value} ---"
            )
            print(f"Swing ID: {s.id}")
            print(
                f"Pivot Price: {s.pivot_price} | Pivot Time (open): {s.pivot_time} | Pivot Close: {s.pivot_close_time}"
            )
            print(f"Confirmation Time (close of right[3]): {s.confirmation_time}")
            print(f"Status: {s.confirmation_status.value}")

            if s.is_swing_high:
                print("Left 3 bars comparison (must all be < pivot_high):")
                for offset, c in enumerate(left_candles, start=-3):
                    diff = s.pivot_price - c.high
                    assert c.high < s.pivot_price, (
                        f"Violation: left bar {c.open_time} high {c.high} >= pivot {s.pivot_price}"
                    )
                    print(
                        f"  Bar [{offset}] {c.open_time.strftime('%Y-%m-%d %H:%M')}: High = {c.high} (Pivot - High = +{diff}) -> PASS"
                    )

                print(
                    f"Pivot bar [0] {pivot_c.open_time.strftime('%Y-%m-%d %H:%M')}: High = {pivot_c.high}"
                )

                print("Right 3 bars comparison (must all be < pivot_high):")
                for offset, c in enumerate(right_candles, start=1):
                    diff = s.pivot_price - c.high
                    assert c.high < s.pivot_price, (
                        f"Violation: right bar {c.open_time} high {c.high} >= pivot {s.pivot_price}"
                    )
                    print(
                        f"  Bar [+{offset}] {c.open_time.strftime('%Y-%m-%d %H:%M')} (Close: {c.close_time.strftime('%Y-%m-%d %H:%M')}): High = {c.high} (Pivot - High = +{diff}) -> PASS"
                    )

            else:
                print("Left 3 bars comparison (must all be > pivot_low):")
                for offset, c in enumerate(left_candles, start=-3):
                    diff = c.low - s.pivot_price
                    assert c.low > s.pivot_price, (
                        f"Violation: left bar {c.open_time} low {c.low} <= pivot {s.pivot_price}"
                    )
                    print(
                        f"  Bar [{offset}] {c.open_time.strftime('%Y-%m-%d %H:%M')}: Low = {c.low} (Low - Pivot = +{diff}) -> PASS"
                    )

                print(
                    f"Pivot bar [0] {pivot_c.open_time.strftime('%Y-%m-%d %H:%M')}: Low = {pivot_c.low}"
                )

                print("Right 3 bars comparison (must all be > pivot_low):")
                for offset, c in enumerate(right_candles, start=1):
                    diff = c.low - s.pivot_price
                    assert c.low > s.pivot_price, (
                        f"Violation: right bar {c.open_time} low {c.low} <= pivot {s.pivot_price}"
                    )
                    print(
                        f"  Bar [+{offset}] {c.open_time.strftime('%Y-%m-%d %H:%M')} (Close: {c.close_time.strftime('%Y-%m-%d %H:%M')}): Low = {c.low} (Low - Pivot = +{diff}) -> PASS"
                    )

            # Point-in-Time proof: evaluate at right[2] close time vs right[3] close time
            as_of_before = right_candles[1].close_time  # Only 2 right bars closed
            as_of_at_conf = right_candles[2].close_time  # Exactly 3rd right bar closed

            swings_before = filter_confirmed_swings(
                detect_swings(c_list, left_bars=3, right_bars=3, as_of_utc=as_of_before),
                as_of_utc=as_of_before,
            )
            swings_at_conf = filter_confirmed_swings(
                detect_swings(c_list, left_bars=3, right_bars=3, as_of_utc=as_of_at_conf),
                as_of_utc=as_of_at_conf,
            )

            found_before = any(sw.id == s.id for sw in swings_before)
            found_at_conf = any(sw.id == s.id for sw in swings_at_conf)

            assert not found_before, (
                f"PIT VIOLATION: Swing {s.id} confirmed before right[3] close!"
            )
            assert found_at_conf, f"PIT ERROR: Swing {s.id} not confirmed at right[3] close!"
            print(
                f"PIT Verification: Visible as of {as_of_before.strftime('%Y-%m-%d %H:%M')}: {found_before} (False expected) | As of {as_of_at_conf.strftime('%Y-%m-%d %H:%M')}: {found_at_conf} (True expected) -> PROVEN"
            )

    print(
        f"\nSuccessfully verified {samples_found} sample swings with 100% mathematical accuracy and PIT safety."
    )


if __name__ == "__main__":
    run_phase_3a_audit()
