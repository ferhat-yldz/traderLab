"""Comprehensive Session-Aware 6-Pilot Audit for PostgreSQL vs CSV (January 2024).

Covers:
- D1 BID
- D1 ASK
- H4 BID
- H4 ASK
- H2 BID
- H2 ASK
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    ResampledCandle,
    WindowCompleteness,
    resample_m1_candles,
)
from traderlab.core.types import DataSourceId, InstrumentId, PriceType, Timeframe
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.fvg import FvgZone, detect_fair_value_gaps
from traderlab.infrastructure.database.repositories.candle_repository import (
    PostgresCandleRepository,
)
from traderlab.infrastructure.database.session import create_db_engine, get_session_factory
from traderlab.infrastructure.providers.market_data_lab.bar_classifier import (
    BarQualityClassification,
    classify_m1_series,
)
from traderlab.infrastructure.providers.market_data_lab.config import get_market_data_lab_settings
from traderlab.infrastructure.providers.market_data_lab.csv_reader import (
    bar_to_candle,
    read_m1_csv,
)


async def load_postgres_m1(price_type: PriceType) -> list[Candle]:
    engine = create_db_engine()
    session_factory = get_session_factory(engine)
    repo = PostgresCandleRepository(session_factory)
    candles = await repo.get_candles(
        source_id=DataSourceId("market_data_lab_m1"),
        timeframe=Timeframe.M1,
        start_utc=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
        end_utc=datetime(2024, 2, 1, 0, 0, tzinfo=timezone.utc),
        instrument_id=InstrumentId("XAUUSD"),
        price_type=price_type,
        only_closed=True,
    )
    await engine.dispose()
    return list(candles)


def load_csv_active_m1(price_type: PriceType) -> list[Candle]:
    settings = get_market_data_lab_settings()
    bid_path, ask_path = settings.validate_dataset_files(2024, 1)
    csv_path = bid_path if price_type == PriceType.BID else ask_path
    raw_bars = read_m1_csv(csv_path, price_type)
    classified = classify_m1_series(raw_bars)
    active_candles = [
        bar_to_candle(cb.bar, is_synthetic=False)
        for cb in classified
        if cb.classification != BarQualityClassification.SYNTHETIC_CARRY_FORWARD
    ]
    return active_candles


def run_single_combination(
    candles: list[Candle],
    tf: Timeframe,
    pt: PriceType,
    source_name: str,
    daily_mode: DailyCloseMode = DailyCloseMode.UTC_MIDNIGHT,
) -> dict[str, Any]:
    res = resample_m1_candles(candles, tf, daily_close_mode=daily_mode)

    # Metrics
    total_candles = len(res)
    valid_candles = [r for r in res if r.candle.is_closed and not r.candle.is_synthetic]
    complete_count = sum(1 for r in res if r.completeness == WindowCompleteness.COMPLETE)
    partial_count = sum(1 for r in res if r.completeness == WindowCompleteness.PARTIAL)
    all_synth_count = sum(
        1 for r in res if r.completeness == WindowCompleteness.ALL_SYNTHETIC_CLOSED
    )

    total_market_closed_mins = sum(r.market_closed_minutes for r in res)
    total_missing_trading_mins = sum(r.missing_trading_minutes for r in res)

    # FVG detection on valid domain candles
    domain_candles = [r.candle for r in res]
    fvgs = detect_fair_value_gaps(domain_candles, require_closed=True)
    bullish_fvgs = [f for f in fvgs if f.is_bullish]
    bearish_fvgs = [f for f in fvgs if f.is_bearish]

    return {
        "source": source_name,
        "timeframe": tf.name,
        "price_type": pt.value,
        "total_resampled": total_candles,
        "valid_candles": len(valid_candles),
        "complete_count": complete_count,
        "partial_count": partial_count,
        "all_synth_count": all_synth_count,
        "market_closed_minutes": total_market_closed_mins,
        "missing_trading_minutes": total_missing_trading_mins,
        "total_fvgs": len(fvgs),
        "bullish_fvgs": len(bullish_fvgs),
        "bearish_fvgs": len(bearish_fvgs),
        "fvgs": fvgs,
        "resampled": res,
    }


async def main() -> None:
    print("=" * 100)
    print("TRADERLAB FAZ 2B-K2: REAL PILOT DATA QUALITY & FVG AUDIT (JANUARY 2024)")
    print("=" * 100)

    print("\nLoading clean PostgreSQL M1 records...")
    pg_bid = await load_postgres_m1(PriceType.BID)
    pg_ask = await load_postgres_m1(PriceType.ASK)
    print(f"Loaded {len(pg_bid):,} BID bars, {len(pg_ask):,} ASK bars from PostgreSQL.")

    print("\nLoading CSV active M1 records (synthetics eliminated)...")
    csv_bid = load_csv_active_m1(PriceType.BID)
    csv_ask = load_csv_active_m1(PriceType.ASK)
    print(f"Loaded {len(csv_bid):,} BID bars, {len(csv_ask):,} ASK bars from CSV.")

    assert len(pg_bid) == len(csv_bid), "PostgreSQL and CSV BID counts do not match!"
    assert len(pg_ask) == len(csv_ask), "PostgreSQL and CSV ASK counts do not match!"
    print("M1 Raw Records Match: 100% Exact Count Equality.")

    pilots = [
        (Timeframe.D1, PriceType.BID, "D1 BID"),
        (Timeframe.D1, PriceType.ASK, "D1 ASK"),
        (Timeframe.H4, PriceType.BID, "H4 BID"),
        (Timeframe.H4, PriceType.ASK, "H4 ASK"),
        (Timeframe.H2, PriceType.BID, "H2 BID"),
        (Timeframe.H2, PriceType.ASK, "H2 ASK"),
    ]

    print("\n" + "=" * 100)
    print("SIX PILOT COMPARISON: POSTGRESQL VS CSV (UTC_MIDNIGHT)")
    print("=" * 100)

    for tf, pt, name in pilots:
        data_pg = pg_bid if pt == PriceType.BID else pg_ask
        data_csv = csv_bid if pt == PriceType.BID else csv_ask

        res_pg = run_single_combination(data_pg, tf, pt, "PostgreSQL", DailyCloseMode.UTC_MIDNIGHT)
        res_csv = run_single_combination(
            data_csv, tf, pt, "CSV-Clean", DailyCloseMode.UTC_MIDNIGHT
        )

        print(f"\n>>> PILOT: {name} <<<")
        print(
            f"  [PG]  Total Bars: {res_pg['total_resampled']} | Valid Closed: {res_pg['valid_candles']} | Complete: {res_pg['complete_count']} | Partial: {res_pg['partial_count']}"
        )
        print(
            f"        Market Closed Mins: {res_pg['market_closed_minutes']:,} | Missing Trading Mins: {res_pg['missing_trading_minutes']}"
        )
        print(
            f"        Bullish FVGs: {res_pg['bullish_fvgs']} | Bearish FVGs: {res_pg['bearish_fvgs']} | Total FVGs: {res_pg['total_fvgs']}"
        )

        print(
            f"  [CSV] Total Bars: {res_csv['total_resampled']} | Valid Closed: {res_csv['valid_candles']} | Complete: {res_csv['complete_count']} | Partial: {res_csv['partial_count']}"
        )
        print(
            f"        Market Closed Mins: {res_csv['market_closed_minutes']:,} | Missing Trading Mins: {res_csv['missing_trading_minutes']}"
        )
        print(
            f"        Bullish FVGs: {res_csv['bullish_fvgs']} | Bearish FVGs: {res_csv['bearish_fvgs']} | Total FVGs: {res_csv['total_fvgs']}"
        )

        # Verify 100% OHLC identity between PostgreSQL and CSV
        assert res_pg["total_resampled"] == res_csv["total_resampled"]
        assert res_pg["valid_candles"] == res_csv["valid_candles"]
        assert res_pg["market_closed_minutes"] == res_csv["market_closed_minutes"]
        assert res_pg["missing_trading_minutes"] == res_csv["missing_trading_minutes"]
        assert res_pg["total_fvgs"] == res_csv["total_fvgs"]
        assert res_pg["bullish_fvgs"] == res_csv["bullish_fvgs"]
        assert res_pg["bearish_fvgs"] == res_csv["bearish_fvgs"]

        # Check candle by candle
        for r_p, r_c in zip(res_pg["resampled"], res_csv["resampled"]):
            c_p, c_c = r_p.candle, r_c.candle
            assert c_p.open_time == c_c.open_time
            assert c_p.close_time == c_c.close_time
            assert c_p.open == c_c.open
            assert c_p.high == c_c.high
            assert c_p.low == c_c.low
            assert c_p.close == c_c.close
            assert c_p.is_closed == c_c.is_closed
            assert r_p.expected_trading_minutes == r_c.expected_trading_minutes
            assert r_p.missing_trading_minutes == r_c.missing_trading_minutes

        # Check FVG by FVG
        for f_p, f_c in zip(res_pg["fvgs"], res_csv["fvgs"]):
            assert f_p.id == f_c.id
            assert f_p.formation_time == f_c.formation_time
            assert f_p.direction == f_c.direction
            assert f_p.top_price == f_c.top_price
            assert f_p.bottom_price == f_c.bottom_price
            assert f_p.ce_price == f_c.ce_price
            assert f_p.width == f_c.width

        print(
            "  -> BİREBİR POSTGRESQL - CSV MUTABAKATI: %100 BAŞARILI (OHLC + FVG + ZAMAN DAMGASI)"
        )

    # Also evaluate NEW_YORK_1700 for D1 as required by Section 5
    print("\n" + "=" * 100)
    print("D1 EVALUATION: UTC_MIDNIGHT VS NEW_YORK_1700 CLOSING POLICIES")
    print("=" * 100)

    for pt in [PriceType.BID, PriceType.ASK]:
        data = pg_bid if pt == PriceType.BID else pg_ask
        res_utc = run_single_combination(
            data, Timeframe.D1, pt, "UTC", DailyCloseMode.UTC_MIDNIGHT
        )
        res_ny = run_single_combination(data, Timeframe.D1, pt, "NY", DailyCloseMode.NEW_YORK_1700)

        print(f"\n[D1 {pt.value}]")
        print(
            f"  UTC_MIDNIGHT  : Total Barlar={res_utc['total_resampled']}, Valid={res_utc['valid_candles']}, FVGs={res_utc['total_fvgs']} (Bullish={res_utc['bullish_fvgs']}, Bearish={res_utc['bearish_fvgs']})"
        )
        for idx, f in enumerate(res_utc["fvgs"], 1):
            print(
                f"    UTC #{idx}: {f.direction.value} formed={f.formation_time} [{f.bottom_price}, {f.top_price}] CE={f.ce_price} c1={f.candle_1_open.strftime('%Y-%m-%d')} c2={f.candle_2_open.strftime('%Y-%m-%d')} c3={f.candle_3_open.strftime('%Y-%m-%d')}"
            )

        print(
            f"  NEW_YORK_1700 : Total Barlar={res_ny['total_resampled']}, Valid={res_ny['valid_candles']}, FVGs={res_ny['total_fvgs']} (Bullish={res_ny['bullish_fvgs']}, Bearish={res_ny['bearish_fvgs']})"
        )
        for idx, f in enumerate(res_ny["fvgs"], 1):
            print(
                f"    NY  #{idx}: {f.direction.value} formed={f.formation_time} [{f.bottom_price}, {f.top_price}] CE={f.ce_price} c1={f.candle_1_open.strftime('%Y-%m-%d %H:%M')} c2={f.candle_2_open.strftime('%Y-%m-%d %H:%M')} c3={f.candle_3_open.strftime('%Y-%m-%d %H:%M')}"
            )


if __name__ == "__main__":
    asyncio.run(main())
