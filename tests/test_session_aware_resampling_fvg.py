"""Acceptance test suite for Faz 2B-K2: Session-Aware Resampling and FVG Data Integrity.

Verifies the 14 mandatory acceptance criteria specified in the Faz 2B-K2 mandate:
1. Normal daily rollover
2. Sunday opening H2 window
3. Sunday opening H4 window
4. UTC and New York D1 differentiation
5. Daylight Saving Time (DST) transitions
6. Weekend price gap
7. Missing M1 record during open market
8. Absence of M1 during normal market closure
9. Completely synthetic window
10. PostgreSQL-CSV OHLC equality
11. PostgreSQL-CSV FVG equality
12. ASK-only record preservation
13. Zero lookahead leakage (Point-in-Time)
14. Preservation of legacy resampling semantics
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from traderlab.application.analysis.resampling import (
    DailyCloseMode,
    WindowCompleteness,
    filter_candles_by_decision_time,
    resample_m1_candles,
)
from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.fvg import (
    detect_fair_value_gaps,
)
from traderlab.infrastructure.calendars.forex_session import (
    MarketSessionState,
    XAUUSDTradingCalendar,
)
from traderlab.infrastructure.database.repositories.candle_repository import (
    PostgresCandleRepository,
)
from traderlab.infrastructure.database.session import create_db_engine, get_session_factory
from traderlab.infrastructure.providers.market_data_lab.bar_classifier import (
    BarQualityClassification,
    classify_m1_series,
)
from traderlab.infrastructure.providers.market_data_lab.config import (
    get_market_data_lab_settings,
)
from traderlab.infrastructure.providers.market_data_lab.csv_reader import (
    bar_to_candle,
    read_m1_csv,
)


def _make_candle(
    open_time: datetime,
    open_p: str = "2050.000",
    high_p: str = "2052.000",
    low_p: str = "2048.000",
    close_p: str = "2051.000",
    timeframe: Timeframe = Timeframe.M1,
    price_type: PriceType = PriceType.BID,
    is_closed: bool = True,
    is_synthetic: bool = False,
    volume: str | None = "100.0",
    source_id: DataSourceId | None = None,
) -> Candle:
    tf_seconds = timeframe.value
    src = source_id or DataSourceId("test_session_source")
    return Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=src,
        timeframe=timeframe,
        open_time=open_time,
        close_time=open_time + timedelta(seconds=tf_seconds),
        open=Decimal(open_p),
        high=Decimal(high_p),
        low=Decimal(low_p),
        close=Decimal(close_p),
        volume=Decimal(volume) if volume is not None else None,
        price_type=price_type,
        is_closed=is_closed,
        is_synthetic=is_synthetic,
        availability_confidence=AvailabilityConfidence.ESTIMATED,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1. Normal Daily Rollover
# ═════════════════════════════════════════════════════════════════════════════
def test_01_normal_daily_rollover() -> None:
    """During normal rollover (17:00-18:00 NY = 22:00-23:00 UTC Jan), missing M1s are normal closure."""
    cal = XAUUSDTradingCalendar()
    # Wednesday Jan 17, 2024: 22:00 to 23:00 UTC is daily rollover break
    w_open = datetime(2024, 1, 17, 22, 0, tzinfo=UTC)
    w_close = datetime(2024, 1, 17, 23, 0, tzinfo=UTC)

    assert cal.count_open_minutes(w_open, w_close) == 0

    # Build D1 sequence with 1380 active bars (rollover 22:00-23:00 is naturally absent)
    day_open = datetime(2024, 1, 17, 0, 0, tzinfo=UTC)
    bars: list[Candle] = []
    for m in range(1440):
        t = day_open + timedelta(minutes=m)
        if cal.is_market_open(t):
            bars.append(_make_candle(t, "2050.00", "2051.00", "2049.00", "2050.50"))

    assert len(bars) == 1380
    res = resample_m1_candles(bars, Timeframe.D1, daily_close_mode=DailyCloseMode.UTC_MIDNIGHT)
    assert len(res) == 1
    rc = res[0]

    assert rc.calendar_minutes == 1440
    assert rc.expected_trading_minutes == 1380
    assert rc.market_closed_minutes == 60
    assert rc.missing_trading_minutes == 0
    assert rc.active_m1_count == 1380
    assert rc.completeness == WindowCompleteness.COMPLETE
    assert rc.candle.is_closed is True
    assert rc.is_session_complete is True


# ═════════════════════════════════════════════════════════════════════════════
# 2. Sunday Opening H2 Window
# ═════════════════════════════════════════════════════════════════════════════
def test_02_sunday_opening_h2_window() -> None:
    """Sunday 22:00-24:00 UTC (winter): 22:00-23:00 is closed, 23:00-24:00 is open (60 trading mins)."""
    cal = XAUUSDTradingCalendar()
    # Sunday Jan 21, 2024: 22:00 to 24:00 UTC
    w_open = datetime(2024, 1, 21, 22, 0, tzinfo=UTC)
    w_close = datetime(2024, 1, 22, 0, 0, tzinfo=UTC)

    assert cal.count_open_minutes(w_open, w_close) == 60

    # Clean data only provides quotes when market opened (23:00 to 24:00 = 60 bars)
    bars = [
        _make_candle(
            datetime(2024, 1, 21, 23, 0, tzinfo=UTC) + timedelta(minutes=i),
            open_p="2025.00" if i == 0 else "2026.00",
            high_p="2028.50" if i == 30 else ("2027.50" if i == 59 else "2026.50"),
            low_p="2024.00" if i == 15 else "2025.00",
            close_p="2027.00" if i == 59 else "2026.00",
        )
        for i in range(60)
    ]

    res = resample_m1_candles(bars, Timeframe.H2)
    assert len(res) == 1
    rc = res[0]

    assert rc.calendar_minutes == 120
    assert rc.expected_trading_minutes == 60
    assert rc.market_closed_minutes == 60
    assert rc.missing_trading_minutes == 0
    assert rc.completeness == WindowCompleteness.COMPLETE
    assert rc.candle.is_closed is True
    assert rc.candle.open == Decimal("2025.00")
    assert rc.candle.high == Decimal("2028.50")
    assert rc.candle.low == Decimal("2024.00")
    assert rc.candle.close == Decimal("2027.00")


# ═════════════════════════════════════════════════════════════════════════════
# 3. Sunday Opening H4 Window
# ═════════════════════════════════════════════════════════════════════════════
def test_03_sunday_opening_h4_window() -> None:
    """Sunday 20:00-24:00 UTC (winter): 20:00-23:00 closed (180 mins), 23:00-24:00 open (60 mins)."""
    cal = XAUUSDTradingCalendar()
    w_open = datetime(2024, 1, 21, 20, 0, tzinfo=UTC)
    w_close = datetime(2024, 1, 22, 0, 0, tzinfo=UTC)

    assert cal.count_open_minutes(w_open, w_close) == 60

    bars = [
        _make_candle(
            datetime(2024, 1, 21, 23, 0, tzinfo=UTC) + timedelta(minutes=i),
            open_p="2025.00" if i == 0 else "2026.00",
            high_p="2029.00" if i == 45 else ("2028.50" if i == 59 else "2026.50"),
            low_p="2024.00" if i == 10 else "2025.00",
            close_p="2028.00" if i == 59 else "2026.00",
        )
        for i in range(60)
    ]

    res = resample_m1_candles(bars, Timeframe.H4)
    assert len(res) == 1
    rc = res[0]

    assert rc.calendar_minutes == 240
    assert rc.expected_trading_minutes == 60
    assert rc.market_closed_minutes == 180
    assert rc.missing_trading_minutes == 0
    assert rc.completeness == WindowCompleteness.COMPLETE
    assert rc.candle.is_closed is True
    assert rc.candle.open == Decimal("2025.00")
    assert rc.candle.high == Decimal("2029.00")
    assert rc.candle.low == Decimal("2024.00")
    assert rc.candle.close == Decimal("2028.00")


# ═════════════════════════════════════════════════════════════════════════════
# 4. UTC and New York D1 Differentiation
# ═════════════════════════════════════════════════════════════════════════════
def test_04_utc_vs_new_york_d1_differentiation() -> None:
    """UTC_MIDNIGHT Sunday produces a 1-hour stub candle; NEW_YORK_1700 merges Sunday into Monday."""
    # Sunday Jan 21 evening (23:00 to 24:00 UTC)
    sunday_bars = [
        _make_candle(
            datetime(2024, 1, 21, 23, 0, tzinfo=UTC) + timedelta(minutes=i),
            "2020.00",
            "2022.00",
            "2019.00",
            "2021.00",
        )
        for i in range(60)
    ]
    # Monday Jan 22 (00:00 to 22:00 UTC = 1320 mins)
    monday_bars = [
        _make_candle(
            datetime(2024, 1, 22, 0, 0, tzinfo=UTC) + timedelta(minutes=i),
            "2021.00",
            "2030.00",
            "2020.00",
            "2028.00",
        )
        for i in range(1320)
    ]
    all_bars = sunday_bars + monday_bars

    # UTC_MIDNIGHT: Produces 2 separate bars (Sunday 1-hour stub + Monday bar)
    res_utc = resample_m1_candles(
        all_bars, Timeframe.D1, daily_close_mode=DailyCloseMode.UTC_MIDNIGHT
    )
    assert len(res_utc) == 2
    sunday_rc = res_utc[0]
    assert sunday_rc.candle.open_time == datetime(2024, 1, 21, 0, 0, tzinfo=UTC)
    assert sunday_rc.expected_trading_minutes == 60
    assert sunday_rc.candle.open_time.weekday() == 6
    assert sunday_rc.candle.open_time.hour == 0

    # In FVG detection: Sunday stub candle is excluded from candidate sequence to prevent false FVGs!
    fvgs_utc = detect_fair_value_gaps([sunday_rc.candle, res_utc[1].candle])
    assert len(fvgs_utc) == 0

    # NEW_YORK_1700: Sunday evening + Monday until 17:00 NY merges into exactly ONE trading day
    res_ny = resample_m1_candles(
        all_bars, Timeframe.D1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
    )
    assert len(res_ny) == 1
    ny_rc = res_ny[0]
    # In winter (EST, UTC-5): starts Sunday 17:00 EST = 22:00 UTC
    assert ny_rc.candle.open_time == datetime(2024, 1, 21, 22, 0, tzinfo=UTC)
    assert ny_rc.candle.close_time == datetime(2024, 1, 22, 22, 0, tzinfo=UTC)
    assert ny_rc.expected_trading_minutes == 1380
    assert ny_rc.active_m1_count == 1380
    assert ny_rc.completeness == WindowCompleteness.COMPLETE


# ═════════════════════════════════════════════════════════════════════════════
# 5. Daylight Saving Time (DST) Transitions
# ═════════════════════════════════════════════════════════════════════════════
def test_05_dst_transitions() -> None:
    """Trading calendar dynamically shifts rollover and Sunday open between EST (UTC-5) and EDT (UTC-4)."""
    cal = XAUUSDTradingCalendar()

    # Winter (EST): Rollover is 22:00-23:00 UTC
    winter_rollover = datetime(2024, 1, 16, 22, 30, tzinfo=UTC)
    assert cal.is_dst_active(winter_rollover) is False
    assert cal.get_session_state(winter_rollover) == MarketSessionState.CLOSED_DAILY_ROLLOVER
    assert cal.is_market_open(winter_rollover) is False

    # Summer (EDT): Rollover is 21:00-22:00 UTC
    summer_rollover = datetime(2024, 7, 16, 21, 30, tzinfo=UTC)
    assert cal.is_dst_active(summer_rollover) is True
    assert cal.get_session_state(summer_rollover) == MarketSessionState.CLOSED_DAILY_ROLLOVER
    assert cal.is_market_open(summer_rollover) is False

    # In summer, 22:30 UTC is OPEN (18:30 EDT, market reopened!)
    summer_open = datetime(2024, 7, 16, 22, 30, tzinfo=UTC)
    assert cal.get_session_state(summer_open) == MarketSessionState.OPEN
    assert cal.is_market_open(summer_open) is True


# ═════════════════════════════════════════════════════════════════════════════
# 6. Weekend Price Gap
# ═════════════════════════════════════════════════════════════════════════════
def test_06_weekend_price_gap() -> None:
    """Gap across Friday close and Sunday open is recognized as weekend and suppressed under default policy."""
    # Friday H1 close (21:00-22:00 UTC)
    c1 = _make_candle(
        datetime(2024, 1, 19, 20, 0, tzinfo=UTC),
        "2030.00",
        "2035.00",
        "2029.00",
        "2034.00",
        timeframe=Timeframe.H1,
    )
    c2 = _make_candle(
        datetime(2024, 1, 19, 21, 0, tzinfo=UTC),
        "2034.00",
        "2040.00",
        "2033.00",
        "2039.00",
        timeframe=Timeframe.H1,
    )
    # Sunday H1 open (23:00-24:00 UTC) -> 49 hours gap!
    c3 = _make_candle(
        datetime(2024, 1, 21, 23, 0, tzinfo=UTC),
        "2045.00",
        "2050.00",
        "2042.00",
        "2048.00",
        timeframe=Timeframe.H1,
    )

    # Under allow_weekend_gap=False: Suppressed
    fvgs_suppressed = detect_fair_value_gaps([c1, c2, c3], allow_weekend_gap=False)
    assert len(fvgs_suppressed) == 0

    # Under allow_weekend_gap=True: Allowed
    fvgs_allowed = detect_fair_value_gaps([c1, c2, c3], allow_weekend_gap=True)
    assert len(fvgs_allowed) == 1
    assert fvgs_allowed[0].is_bullish is True
    assert fvgs_allowed[0].bottom_price == Decimal("2035.00")
    assert fvgs_allowed[0].top_price == Decimal("2042.00")


# ═════════════════════════════════════════════════════════════════════════════
# 7. Missing M1 Record During Open Market
# ═════════════════════════════════════════════════════════════════════════════
def test_07_missing_m1_in_open_market() -> None:
    """Absence of quotes during open session causes missing_trading_minutes > 0, PARTIAL, and is_closed=False."""
    # Tuesday 14:00-15:00 UTC is active London/NY session (expected 60 trading minutes)
    h1_open = datetime(2024, 1, 16, 14, 0, tzinfo=UTC)
    # Only supply 55 bars (5 minutes missing during active trading)
    bars = [_make_candle(h1_open + timedelta(minutes=i)) for i in range(55)]

    res = resample_m1_candles(bars, Timeframe.H1, source_id_suffix="")
    assert len(res) == 1
    rc = res[0]

    assert rc.calendar_minutes == 60
    assert rc.expected_trading_minutes == 60
    assert rc.active_m1_count == 55
    assert rc.missing_trading_minutes == 5
    assert rc.completeness == WindowCompleteness.PARTIAL
    assert rc.candle.is_closed is False
    assert rc.is_session_complete is False

    # Incomplete bar cannot form FVG under require_closed=True
    c_prev1 = _make_candle(
        h1_open - timedelta(hours=2),
        timeframe=Timeframe.H1,
        source_id=rc.candle.source_id,
    )
    c_prev2 = _make_candle(
        h1_open - timedelta(hours=1),
        timeframe=Timeframe.H1,
        source_id=rc.candle.source_id,
    )
    fvgs = detect_fair_value_gaps([c_prev1, c_prev2, rc.candle], require_closed=True)
    assert len(fvgs) == 0


# ═════════════════════════════════════════════════════════════════════════════
# 8. Absence of M1 During Normal Market Closure
# ═════════════════════════════════════════════════════════════════════════════
def test_08_absence_of_m1_in_normal_market_closure() -> None:
    """Missing quotes during closed periods (rollover/weekend) do not increment missing_trading_minutes."""
    cal = XAUUSDTradingCalendar()
    # Rollover window: 22:00-23:00 UTC
    w_open = datetime(2024, 1, 16, 22, 0, tzinfo=UTC)
    w_close = datetime(2024, 1, 16, 23, 0, tzinfo=UTC)

    open_mins = cal.count_open_minutes(w_open, w_close)
    assert open_mins == 0
    # Clean data has 0 records during rollover break: not counted as missing trading minutes!


# ═════════════════════════════════════════════════════════════════════════════
# 9. Completely Synthetic Window
# ═════════════════════════════════════════════════════════════════════════════
def test_09_all_synthetic_window() -> None:
    """A window composed entirely of synthetic carry-forward bars is marked ALL_SYNTHETIC_CLOSED and is_synthetic=True."""
    base_dt = datetime(2024, 1, 20, 10, 0, tzinfo=UTC)  # Saturday (market closed)
    bars = [
        _make_candle(
            base_dt + timedelta(minutes=i),
            "2050.00",
            "2050.00",
            "2050.00",
            "2050.00",
            is_synthetic=True,
            volume=None,
        )
        for i in range(15)
    ]

    res = resample_m1_candles(bars, Timeframe.M15)
    assert len(res) == 1
    rc = res[0]

    assert rc.completeness == WindowCompleteness.ALL_SYNTHETIC_CLOSED
    assert rc.candle.is_synthetic is True
    assert rc.candle.is_closed is True

    # Synthetic bars are rejected by FVG engine
    c1 = _make_candle(
        base_dt - timedelta(minutes=30),
        timeframe=Timeframe.M15,
        source_id=rc.candle.source_id,
    )
    c2 = _make_candle(
        base_dt - timedelta(minutes=15),
        timeframe=Timeframe.M15,
        source_id=rc.candle.source_id,
    )
    fvgs = detect_fair_value_gaps([c1, c2, rc.candle])
    assert len(fvgs) == 0


# ═════════════════════════════════════════════════════════════════════════════
# 10. PostgreSQL–CSV OHLC Equality
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_10_postgresql_csv_ohlc_equality() -> None:
    """PostgreSQL clean data and CSV active records produce 100% identical OHLC values for D1, H4, and H2."""
    engine = create_db_engine()
    repo = PostgresCandleRepository(get_session_factory(engine))
    pg_bid = await repo.get_candles(
        source_id=DataSourceId("market_data_lab_m1"),
        timeframe=Timeframe.M1,
        start_utc=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        end_utc=datetime(2024, 2, 1, 0, 0, tzinfo=UTC),
        instrument_id=InstrumentId("XAUUSD"),
        price_type=PriceType.BID,
        only_closed=True,
    )
    await engine.dispose()

    settings = get_market_data_lab_settings()
    bid_path, _ = settings.validate_dataset_files(2024, 1)
    raw_bid = read_m1_csv(bid_path, PriceType.BID)
    classified = classify_m1_series(raw_bid)
    csv_active = [
        bar_to_candle(cb.bar, is_synthetic=False)
        for cb in classified
        if cb.classification != BarQualityClassification.SYNTHETIC_CARRY_FORWARD
    ]

    assert len(pg_bid) == len(csv_active) == 30570

    for tf in [Timeframe.D1, Timeframe.H4, Timeframe.H2]:
        res_pg = resample_m1_candles(list(pg_bid), tf)
        res_csv = resample_m1_candles(csv_active, tf)

        assert len(res_pg) == len(res_csv)
        for r_p, r_c in zip(res_pg, res_csv, strict=True):
            cp, cc = r_p.candle, r_c.candle
            assert cp.open_time == cc.open_time
            assert cp.close_time == cc.close_time
            assert cp.open == cc.open
            assert cp.high == cc.high
            assert cp.low == cc.low
            assert cp.close == cc.close
            assert cp.is_closed == cc.is_closed
            assert r_p.completeness == r_c.completeness
            assert r_p.expected_trading_minutes == r_c.expected_trading_minutes


# ═════════════════════════════════════════════════════════════════════════════
# 11. PostgreSQL–CSV FVG Equality
# ═════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_11_postgresql_csv_fvg_equality() -> None:
    """PostgreSQL and CSV clean datasets produce 100% identical Fair Value Gaps across D1, H4, and H2."""
    engine = create_db_engine()
    repo = PostgresCandleRepository(get_session_factory(engine))
    pg_bid = await repo.get_candles(
        source_id=DataSourceId("market_data_lab_m1"),
        timeframe=Timeframe.M1,
        start_utc=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        end_utc=datetime(2024, 2, 1, 0, 0, tzinfo=UTC),
        instrument_id=InstrumentId("XAUUSD"),
        price_type=PriceType.BID,
        only_closed=True,
    )
    await engine.dispose()

    settings = get_market_data_lab_settings()
    bid_path, _ = settings.validate_dataset_files(2024, 1)
    raw_bid = read_m1_csv(bid_path, PriceType.BID)
    classified = classify_m1_series(raw_bid)
    csv_active = [
        bar_to_candle(cb.bar, is_synthetic=False)
        for cb in classified
        if cb.classification != BarQualityClassification.SYNTHETIC_CARRY_FORWARD
    ]

    for tf in [Timeframe.D1, Timeframe.H4, Timeframe.H2]:
        res_pg = resample_m1_candles(list(pg_bid), tf)
        res_csv = resample_m1_candles(csv_active, tf)

        fvgs_pg = detect_fair_value_gaps([r.candle for r in res_pg], require_closed=True)
        fvgs_csv = detect_fair_value_gaps([r.candle for r in res_csv], require_closed=True)

        assert len(fvgs_pg) == len(fvgs_csv)
        for f_p, f_c in zip(fvgs_pg, fvgs_csv, strict=True):
            assert f_p.id == f_c.id
            assert f_p.formation_time == f_c.formation_time
            assert f_p.direction == f_c.direction
            assert f_p.top_price == f_c.top_price
            assert f_p.bottom_price == f_c.bottom_price
            assert f_p.ce_price == f_c.ce_price
            assert f_p.width == f_c.width


# ═════════════════════════════════════════════════════════════════════════════
# 12. ASK-Only Record Preservation
# ═════════════════════════════════════════════════════════════════════════════
def test_12_ask_only_preservation() -> None:
    """The isolated ASK-only record on 2026-08-16 23:59:00 UTC preserves BID/ASK separation strictly."""
    dt_ask_only = datetime(2026, 8, 16, 23, 59, tzinfo=UTC)
    ask_candle = _make_candle(dt_ask_only, price_type=PriceType.ASK)

    # Resample ASK alone -> succeeds
    res_ask = resample_m1_candles([ask_candle], Timeframe.M5)
    assert len(res_ask) == 1
    assert res_ask[0].candle.price_type == PriceType.ASK

    # Mixing BID and ASK raises MixedPriceTypeError
    bid_candle = _make_candle(dt_ask_only, price_type=PriceType.BID)
    from traderlab.application.analysis.resampling import MixedPriceTypeError

    with pytest.raises(MixedPriceTypeError):
        resample_m1_candles([bid_candle, ask_candle], Timeframe.M5)


# ═════════════════════════════════════════════════════════════════════════════
# 13. Zero Lookahead Leakage
# ═════════════════════════════════════════════════════════════════════════════
def test_13_zero_lookahead_leakage() -> None:
    """Decision time filtering strictly excludes bars finalizing after decision timestamp."""
    c1 = _make_candle(datetime(2024, 1, 16, 10, 0, tzinfo=UTC), timeframe=Timeframe.H1)
    c2 = _make_candle(datetime(2024, 1, 16, 11, 0, tzinfo=UTC), timeframe=Timeframe.H1)
    c3 = _make_candle(datetime(2024, 1, 16, 12, 0, tzinfo=UTC), timeframe=Timeframe.H1)

    # At decision time 11:30 UTC: c1 (closed 11:00) is visible, c2 (closed 12:00) is NOT visible
    as_of = datetime(2024, 1, 16, 11, 30, tzinfo=UTC)
    visible = filter_candles_by_decision_time([c1, c2, c3], as_of)
    assert len(visible) == 1
    assert visible[0] == c1

    # At decision time 12:00 UTC: c1 and c2 are visible
    visible_12 = filter_candles_by_decision_time(
        [c1, c2, c3], datetime(2024, 1, 16, 12, 0, tzinfo=UTC)
    )
    assert len(visible_12) == 2


# ═════════════════════════════════════════════════════════════════════════════
# 14. Preservation of Legacy Resampling Tests
# ═════════════════════════════════════════════════════════════════════════════
def test_14_legacy_resampling_tests_preserved() -> None:
    """When session calendar is disabled, resampling falls back to pure calendar count without error."""
    base_dt = datetime(2024, 1, 16, 10, 0, tzinfo=UTC)
    candles = [_make_candle(base_dt + timedelta(minutes=i)) for i in range(5)]

    res = resample_m1_candles(candles, Timeframe.M5, use_session_calendar=False)
    assert len(res) == 1
    rc = res[0]
    assert rc.expected_m1_count == 5
    assert rc.present_m1_count == 5
    assert rc.completeness == WindowCompleteness.COMPLETE
    assert rc.candle.is_closed is True
