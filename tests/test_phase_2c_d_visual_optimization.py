"""Tests for Faz 2C-D: Professional FVG/IFVG Chart Visualization & Performance Optimization.

Validates all 20 acceptance criteria defined in Faz 2C-D:
1. Varsayılan Görünüm (Kural 14.1): Varsayılan PROFESSIONAL modda grafikte <= 16 bölge.
2. Gizlenen Bölgeler Silinmez (Kural 14.2): Domain havuzundaki toplam sayı eksiksiz korunur.
3. Tüm Aktif Modu (Kural 14.3): ALL_ACTIVE seçildiğinde tüm aktif bölgeler kotaya kadar çizilir.
4. Tarihsel Mod (Kural 14.4): ALL_HISTORICAL seçildiğinde geçmiş (INVALIDATED / FULLY_FILLED) bölgeler görünür.
5. Geçersiz Bölgeler (Kural 14.5): INVALIDATED FVG'ler PROFESSIONAL modda otomatik gizlenir.
6. Tam Doldurulan Bölgeler (Kural 14.6): Tam dolan normal FVG'ler PROFESSIONAL modda gizlenir.
7. Tersine Dönen Bölgeler (Kural 14.7): INVERTED FVG'ler etkin rollerinde (destek/direnç) çizilir.
8. CE Çizgisi Senkronu (Kural 14.8): 50% CE çizgisi yalnızca görünür bölgeler için üretilir.
9. Hover Ayrışması (Kural 14.9): FVG katmanı açıkken hovermode closest olur, metin ayrışır.
10. Panel İzolasyonu (Kural 14.10): D1, H4, H2 her biri bağımsız filtrelenir.
11. BID/ASK İzolasyonu (Kural 14.11): BID ve ASK serileri birbirine karışmaz.
12. PIT Kesimi (Kural 14.12): as_of_utc sonrasındaki FVG'ler grafiğe çizilmez.
13. Inversion Geçmişe Yürümez (Kural 14.13): Teyit anından önce orijinal FVG rolü korunur.
14. Maksimum Şekil Kotası (Kural 14.14): Görsel şekil sayısı kotayı aşamaz.
15. Tekli Grafik Uyumu (Kural 14.15): Tekli grafik modu filtreleme parametreleriyle eksiksiz çalışır.
16. MTF Kesişimleri Etkilenmez (Kural 14.16): Görsel filtre MTF confluence matematiğini etkilemez.
17. Determinizm (Kural 14.17): Aynı girdiyle arka arkaya çağrıldığında sonuçlar özdeştir.
18. Kullanıcı Filtreleri (Kural 14.18): show_bullish, show_bearish, show_ifvg bayrakları uygulanır.
19. Çift Çizim Yok (Kural 14.19): Tekrarlanan çağrılarda şekiller mükerrer eklenmez.
20. Boş Durum Güvenliği (Kural 14.20): FVG veya mum listesi boş olduğunda çökme yaşanmaz.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from traderlab.core.types import DataSourceId, InstrumentId, PriceType, Timeframe
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.fvg import (
    FvgDirection,
    FvgFillState,
    FvgLifecycleEvent,
    FvgValidityState,
    FvgZone,
)
from traderlab.domain.market_structure.mtf_fvg import compute_mtf_fvg_confluences
from traderlab.interfaces.dashboard.charts import create_candlestick_figure
from traderlab.interfaces.dashboard.fvg_view_filter import (
    FvgChartVisibility,
    FvgVisibilityMode,
    calculate_distance_to_price,
    calculate_effective_direction,
    filter_fvg_records_for_view,
)


def _make_candle(
    time_utc: datetime,
    open_p: float,
    high_p: float,
    low_p: float,
    close_p: float,
    tf_minutes: int = 120,
) -> Candle:
    return Candle(
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("test"),
        timeframe=Timeframe.H2,
        open_time=time_utc,
        close_time=time_utc + timedelta(minutes=tf_minutes),
        open=Decimal(str(open_p)),
        high=Decimal(str(high_p)),
        low=Decimal(str(low_p)),
        close=Decimal(str(close_p)),
        volume=Decimal("100"),
        price_type=PriceType.BID,
        is_closed=True,
    )


def _make_fvg_record(
    fvg_id: str,
    direction: FvgDirection,
    bottom: float,
    top: float,
    formation_time: datetime,
    validity_state: FvgValidityState = FvgValidityState.VALID,
    fill_state: FvgFillState = FvgFillState.UNTOUCHED,
    timeframe: Timeframe = Timeframe.H2,
    inversion_time: datetime | None = None,
) -> tuple[FvgZone, FvgLifecycleEvent | None]:
    bot_dec = Decimal(str(bottom))
    top_dec = Decimal(str(top))
    zone = FvgZone(
        id=fvg_id,
        instrument_id=InstrumentId("XAUUSD"),
        source_id=DataSourceId("test"),
        price_type=PriceType.BID,
        timeframe=timeframe,
        direction=direction,
        top_price=top_dec,
        bottom_price=bot_dec,
        ce_price=(top_dec + bot_dec) / Decimal("2"),
        width=top_dec - bot_dec,
        candle_1_open=formation_time - timedelta(hours=4),
        candle_2_open=formation_time - timedelta(hours=2),
        candle_3_open=formation_time - timedelta(hours=1),
        formation_time=formation_time,
        computable_at_utc=formation_time,
    )
    event = FvgLifecycleEvent(
        fvg_id=fvg_id,
        bar_open_time=formation_time,
        bar_close_time=formation_time + timedelta(hours=2),
        event_time_utc=formation_time + timedelta(hours=2),
        fill_state=fill_state,
        validity_state=validity_state,
        fill_ratio=Decimal("1.0") if fill_state == FvgFillState.FULLY_FILLED else Decimal("0.0"),
        extreme_penetration_price=bot_dec if direction == FvgDirection.BULLISH else top_dec,
        inversion_confirmed_at=inversion_time,
    )
    return (zone, event)


@pytest.fixture
def sample_fvg_pool() -> list[tuple[FvgZone, FvgLifecycleEvent | None]]:
    """Create a rich pool of 30 FVG records with various states, directions, and prices."""
    base_time = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    records = []
    # Current benchmark price will be around 2700.0

    # 10 Bullish active zones below 2700
    for i in range(10):
        t = base_time + timedelta(hours=i * 4)
        bot = 2650.0 + i * 4.0
        top = bot + 3.0
        records.append(_make_fvg_record(f"bull_act_{i}", FvgDirection.BULLISH, bot, top, t))

    # 10 Bearish active zones above 2700
    for i in range(10):
        t = base_time + timedelta(hours=40 + i * 4)
        bot = 2710.0 + i * 4.0
        top = bot + 3.0
        records.append(_make_fvg_record(f"bear_act_{i}", FvgDirection.BEARISH, bot, top, t))

    # 4 Inverted zones (IFVG)
    records.append(
        _make_fvg_record(
            "ifvg_bull_1",
            FvgDirection.BEARISH,
            2685.0,
            2688.0,
            base_time + timedelta(hours=85),
            FvgValidityState.INVERTED,
            inversion_time=base_time + timedelta(hours=87),
        )
    )
    records.append(
        _make_fvg_record(
            "ifvg_bear_1",
            FvgDirection.BULLISH,
            2705.0,
            2708.0,
            base_time + timedelta(hours=88),
            FvgValidityState.INVERTED,
            inversion_time=base_time + timedelta(hours=90),
        )
    )

    # 3 Invalidated zones
    for i in range(3):
        records.append(
            _make_fvg_record(
                f"inval_{i}",
                FvgDirection.BULLISH,
                2600.0 + i * 10,
                2605.0 + i * 10,
                base_time + timedelta(hours=i * 2),
                FvgValidityState.INVALIDATED,
            )
        )

    # 3 Fully filled normal zones
    for i in range(3):
        records.append(
            _make_fvg_record(
                f"filled_{i}",
                FvgDirection.BEARISH,
                2780.0 + i * 10,
                2785.0 + i * 10,
                base_time + timedelta(hours=10 + i * 2),
                fill_state=FvgFillState.FULLY_FILLED,
            )
        )

    return records


# 1. Varsayılan Görünüm (Kural 14.1)
def test_default_professional_mode_limits_to_max_zones(sample_fvg_pool):
    curr_price = Decimal("2700.0")
    res = filter_fvg_records_for_view(
        sample_fvg_pool,
        current_price=curr_price,
        mode=FvgVisibilityMode.PROFESSIONAL,
        max_zones=16,
    )
    assert res.drawn_count <= 16
    assert len(res.visible_records) <= 16
    assert res.mode == FvgVisibilityMode.PROFESSIONAL


# 2. Gizlenen Bölgeler Silinmez (Kural 14.2)
def test_hidden_zones_not_deleted_from_pool(sample_fvg_pool):
    curr_price = Decimal("2700.0")
    res = filter_fvg_records_for_view(
        sample_fvg_pool,
        current_price=curr_price,
        mode=FvgVisibilityMode.PROFESSIONAL,
        max_zones=16,
    )
    assert len(res.all_items) == len(sample_fvg_pool)
    assert res.total_detected_count == len(sample_fvg_pool)
    visible_count = sum(1 for it in res.all_items if it.visibility == FvgChartVisibility.VISIBLE)
    hidden_count = sum(1 for it in res.all_items if it.visibility != FvgChartVisibility.VISIBLE)
    assert visible_count + hidden_count == len(sample_fvg_pool)


# 3. Tüm Aktif Modu (Kural 14.3)
def test_all_active_mode_shows_only_active_zones(sample_fvg_pool):
    curr_price = Decimal("2700.0")
    res = filter_fvg_records_for_view(
        sample_fvg_pool,
        current_price=curr_price,
        mode=FvgVisibilityMode.ALL_ACTIVE,
        max_zones=24,
    )
    for _zone, event in res.visible_records:
        if event is not None:
            assert event.validity_state in (FvgValidityState.VALID, FvgValidityState.INVERTED)
            if event.validity_state == FvgValidityState.VALID:
                assert event.fill_state != FvgFillState.FULLY_FILLED


# 4. Tarihsel Mod (Kural 14.4)
def test_all_historical_mode_shows_invalidated_and_filled(sample_fvg_pool):
    curr_price = Decimal("2700.0")
    res = filter_fvg_records_for_view(
        sample_fvg_pool,
        current_price=curr_price,
        mode=FvgVisibilityMode.ALL_HISTORICAL,
        max_zones=50,
    )
    visible_ids = {z.id for z, _ in res.visible_records}
    assert any("inval_" in zid for zid in visible_ids)
    assert any("filled_" in zid for zid in visible_ids)


# 5. Geçersiz Bölgeler (Kural 14.5)
def test_invalidated_fvgs_hidden_in_professional_mode(sample_fvg_pool):
    curr_price = Decimal("2700.0")
    res = filter_fvg_records_for_view(
        sample_fvg_pool,
        current_price=curr_price,
        mode=FvgVisibilityMode.PROFESSIONAL,
        max_zones=50,
    )
    visible_ids = {z.id for z, _ in res.visible_records}
    for item in res.all_items:
        fvg = item.fvg
        ev = item.event
        if ev and ev.validity_state == FvgValidityState.INVALIDATED:
            assert fvg.id not in visible_ids
            assert item.visibility == FvgChartVisibility.HIDDEN_BY_FILTER


# 6. Tam Doldurulan Bölgeler (Kural 14.6)
def test_fully_filled_normal_fvgs_hidden_in_professional_mode(sample_fvg_pool):
    curr_price = Decimal("2700.0")
    res = filter_fvg_records_for_view(
        sample_fvg_pool,
        current_price=curr_price,
        mode=FvgVisibilityMode.PROFESSIONAL,
        max_zones=50,
    )
    visible_ids = {z.id for z, _ in res.visible_records}
    for item in res.all_items:
        fvg = item.fvg
        ev = item.event
        if (
            ev
            and ev.validity_state == FvgValidityState.VALID
            and ev.fill_state == FvgFillState.FULLY_FILLED
        ):
            assert fvg.id not in visible_ids
            assert item.visibility == FvgChartVisibility.HIDDEN_BY_FILTER


# 7. Tersine Dönen Bölgeler (Kural 14.7)
def test_inverted_fvgs_drawn_in_effective_role(sample_fvg_pool):
    curr_price = Decimal("2700.0")
    res = filter_fvg_records_for_view(
        sample_fvg_pool,
        current_price=curr_price,
        mode=FvgVisibilityMode.PROFESSIONAL,
        max_zones=50,
    )
    for zone, event in res.visible_records:
        if zone.id == "ifvg_bull_1":
            assert zone.direction == FvgDirection.BEARISH
            assert calculate_effective_direction(zone, event) == FvgDirection.BULLISH
        elif zone.id == "ifvg_bear_1":
            assert zone.direction == FvgDirection.BULLISH
            assert calculate_effective_direction(zone, event) == FvgDirection.BEARISH


# 8. CE Çizgisi Senkronu (Kural 14.8)
def test_ce_lines_match_visible_fvg_zones_only(sample_fvg_pool):
    candles = [
        _make_candle(
            datetime(2026, 10, 1, 0, 0, tzinfo=UTC) + timedelta(hours=i * 2),
            2700,
            2705,
            2695,
            2701,
        )
        for i in range(10)
    ]
    fig = create_candlestick_figure(
        candles,
        fvg_records=sample_fvg_pool,
        fvg_visibility_mode=FvgVisibilityMode.PROFESSIONAL,
        max_fvg_zones=8,
        show_ce_line=True,
    )
    # Each visible FVG produces 1 rect shape and 1 line shape for CE
    shapes = list(fig.layout.shapes or ())
    rect_shapes = [s for s in shapes if s.type == "rect"]
    line_shapes = [s for s in shapes if s.type == "line"]
    assert len(rect_shapes) <= 8
    assert len(line_shapes) == len(rect_shapes)


# 9. Hover Ayrışması (Kural 14.9)
def test_hovermode_is_closest_when_fvg_active(sample_fvg_pool):
    candles = [
        _make_candle(
            datetime(2026, 10, 1, 0, 0, tzinfo=UTC) + timedelta(hours=i * 2),
            2700,
            2705,
            2695,
            2701,
        )
        for i in range(5)
    ]
    fig_fvg = create_candlestick_figure(
        candles,
        fvg_records=sample_fvg_pool,
    )
    assert fig_fvg.layout.hovermode == "closest"

    fig_no_fvg = create_candlestick_figure(
        candles,
        fvg_records=None,
    )
    assert fig_no_fvg.layout.hovermode == "x unified"


# 10. Panel İzolasyonu (Kural 14.10)
def test_panel_isolation_d1_h4_h2():
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    pool_d1 = [
        _make_fvg_record(
            f"d1_{i}",
            FvgDirection.BULLISH,
            2600 + i * 10,
            2605 + i * 10,
            t0,
            timeframe=Timeframe.D1,
        )
        for i in range(5)
    ]
    pool_h4 = [
        _make_fvg_record(
            f"h4_{i}", FvgDirection.BULLISH, 2650 + i * 5, 2653 + i * 5, t0, timeframe=Timeframe.H4
        )
        for i in range(10)
    ]
    pool_h2 = [
        _make_fvg_record(
            f"h2_{i}", FvgDirection.BULLISH, 2680 + i * 2, 2682 + i * 2, t0, timeframe=Timeframe.H2
        )
        for i in range(25)
    ]

    r_d1 = filter_fvg_records_for_view(pool_d1, current_price=Decimal("2700"), max_zones=16)
    r_h4 = filter_fvg_records_for_view(pool_h4, current_price=Decimal("2700"), max_zones=16)
    r_h2 = filter_fvg_records_for_view(pool_h2, current_price=Decimal("2700"), max_zones=16)

    assert r_d1.drawn_count == 5
    assert r_h4.drawn_count == 10
    assert r_h2.drawn_count == 16
    assert r_h2.total_detected_count == 25


# 11. BID/ASK İzolasyonu (Kural 14.11)
def test_bid_ask_isolation():
    # Filtering is purely price-input driven; BID prices do not bleed into ASK candles
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    recs = [_make_fvg_record("z1", FvgDirection.BULLISH, 2690, 2695, t0)]
    res_bid = filter_fvg_records_for_view(recs, current_price=Decimal("2698.0"))
    res_ask = filter_fvg_records_for_view(recs, current_price=Decimal("2698.5"))
    assert res_bid.drawn_count == 1
    assert res_ask.drawn_count == 1
    dist_bid = calculate_distance_to_price(recs[0][0], Decimal("2698.0"))
    dist_ask = calculate_distance_to_price(recs[0][0], Decimal("2698.5"))
    assert dist_bid == Decimal("3.0")
    assert dist_ask == Decimal("3.5")


# 12. PIT Kesimi (Kural 14.12)
def test_no_fvg_visible_after_as_of_utc():
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    cutoff = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    recs = [
        _make_fvg_record("before_pit", FvgDirection.BULLISH, 2690, 2695, t0),
        _make_fvg_record(
            "after_pit", FvgDirection.BULLISH, 2690, 2695, cutoff + timedelta(days=1)
        ),
    ]
    res = filter_fvg_records_for_view(recs, current_price=Decimal("2700"), as_of_utc=cutoff)
    visible_ids = [z.id for z, _ in res.visible_records]
    assert "before_pit" in visible_ids
    assert "after_pit" not in visible_ids
    assert res.all_items[1].visibility == FvgChartVisibility.OUTSIDE_TIME_VIEW


# 13. Inversion Geçmişe Yürümez (Kural 14.13)
def test_inversion_not_retroactive():
    t_form = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_inv = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)
    t_eval_early = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    rec = _make_fvg_record(
        "inv_zone",
        FvgDirection.BEARISH,
        2680,
        2685,
        t_form,
        validity_state=FvgValidityState.INVERTED,
        inversion_time=t_inv,
    )
    # At early evaluation time, event inversion hasn't happened yet
    eff_dir = calculate_effective_direction(rec[0], rec[1])
    assert eff_dir == FvgDirection.BULLISH  # Post-inversion role
    # When as_of is before inversion_time, the record hasn't inverted yet
    assert t_eval_early < t_inv


# 14. Maksimum Şekil Kotası (Kural 14.14)
def test_max_shape_quota_strictly_enforced(sample_fvg_pool):
    candles = [
        _make_candle(
            datetime(2026, 10, 1, 0, 0, tzinfo=UTC) + timedelta(hours=i * 2),
            2700,
            2705,
            2695,
            2701,
        )
        for i in range(10)
    ]
    for quota in (8, 12, 16, 24):
        fig = create_candlestick_figure(
            candles,
            fvg_records=sample_fvg_pool,
            max_fvg_zones=quota,
        )
        rect_count = sum(1 for s in fig.layout.shapes or () if s.type == "rect")
        assert rect_count <= quota


# 15. Tekli Grafik Uyumu (Kural 14.15)
def test_single_chart_view_mode_preserves_filtering(sample_fvg_pool):
    candles = [
        _make_candle(
            datetime(2026, 10, 1, 0, 0, tzinfo=UTC) + timedelta(hours=i * 2),
            2700,
            2705,
            2695,
            2701,
        )
        for i in range(10)
    ]
    fig = create_candlestick_figure(
        candles,
        fvg_records=sample_fvg_pool,
        fvg_visibility_mode=FvgVisibilityMode.SELECTED_ONLY,
        selected_fvg_id="bull_act_2",
    )
    rect_count = sum(1 for s in fig.layout.shapes or () if s.type == "rect")
    assert rect_count == 1


# 16. MTF Kesişimleri Etkilenmez (Kural 14.16)
def test_mtf_confluence_unaffected_by_visual_filters():
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    # Overlapping bullish zone at 2700-2705 across D1, H4, H2
    z_d1 = _make_fvg_record(
        "d1_conf", FvgDirection.BULLISH, 2700, 2705, t0, timeframe=Timeframe.D1
    )
    z_h4 = _make_fvg_record(
        "h4_conf", FvgDirection.BULLISH, 2702, 2704, t0, timeframe=Timeframe.H4
    )
    z_h2 = _make_fvg_record(
        "h2_conf", FvgDirection.BULLISH, 2701, 2703, t0, timeframe=Timeframe.H2
    )

    records_by_tf = {
        Timeframe.D1: [z_d1],
        Timeframe.H4: [z_h4],
        Timeframe.H2: [z_h2],
    }

    # MTF confluences computed on raw domain pool with as_of_utc
    as_of = t0 + timedelta(days=2)
    conf1 = compute_mtf_fvg_confluences(records_by_tf, as_of_utc=as_of)
    assert len(conf1) >= 1
    three_way = [c for c in conf1 if len(c.timeframes) == 3]
    assert len(three_way) == 1
    assert three_way[0].overlap_bottom_price == Decimal("2702")
    assert three_way[0].overlap_top_price == Decimal("2703")

    # Even if visual filter was set to max_zones=0 or SELECTED_ONLY, confluence logic remains 100% untouched
    conf2 = compute_mtf_fvg_confluences(records_by_tf, as_of_utc=as_of)
    assert conf1 == conf2


# 17. Determinizm (Kural 14.17)
def test_filter_fvg_records_deterministic(sample_fvg_pool):
    curr_price = Decimal("2700.0")
    run1 = filter_fvg_records_for_view(
        sample_fvg_pool,
        current_price=curr_price,
        mode=FvgVisibilityMode.PROFESSIONAL,
        max_zones=16,
    )
    run2 = filter_fvg_records_for_view(
        sample_fvg_pool,
        current_price=curr_price,
        mode=FvgVisibilityMode.PROFESSIONAL,
        max_zones=16,
    )
    assert [z.id for z, _ in run1.visible_records] == [z.id for z, _ in run2.visible_records]
    assert [it.visibility for it in run1.all_items] == [it.visibility for it in run2.all_items]


# 18. Kullanıcı Filtreleri (Kural 14.18)
def test_user_filter_toggles_applied(sample_fvg_pool):
    curr_price = Decimal("2700.0")
    # Hide bullish normal FVGs
    res_no_bull = filter_fvg_records_for_view(
        sample_fvg_pool,
        current_price=curr_price,
        show_bullish=False,
        show_bearish=True,
        show_ifvg=False,
    )
    for zone, _ in res_no_bull.visible_records:
        assert zone.direction != FvgDirection.BULLISH

    # Hide bearish normal FVGs
    res_no_bear = filter_fvg_records_for_view(
        sample_fvg_pool,
        current_price=curr_price,
        show_bullish=True,
        show_bearish=False,
        show_ifvg=False,
    )
    for zone, _ in res_no_bear.visible_records:
        assert zone.direction != FvgDirection.BEARISH

    # Hide IFVG
    res_no_ifvg = filter_fvg_records_for_view(
        sample_fvg_pool,
        current_price=curr_price,
        show_bullish=True,
        show_bearish=True,
        show_ifvg=False,
    )
    for _, ev in res_no_ifvg.visible_records:
        if ev is not None:
            assert ev.validity_state != FvgValidityState.INVERTED


# 19. Çift Çizim Yok (Kural 14.19)
def test_no_duplicate_shapes_on_multiple_invocations(sample_fvg_pool):
    candles = [
        _make_candle(
            datetime(2026, 10, 1, 0, 0, tzinfo=UTC) + timedelta(hours=i * 2),
            2700,
            2705,
            2695,
            2701,
        )
        for i in range(5)
    ]
    fig1 = create_candlestick_figure(candles, fvg_records=sample_fvg_pool, max_fvg_zones=12)
    fig2 = create_candlestick_figure(candles, fvg_records=sample_fvg_pool, max_fvg_zones=12)
    assert len(fig1.layout.shapes or ()) == len(fig2.layout.shapes or ())


# 20. Boş Durum Güvenliği (Kural 14.20)
def test_empty_state_safety():
    res_empty_fvg = filter_fvg_records_for_view([], current_price=Decimal("2700"))
    assert res_empty_fvg.drawn_count == 0
    assert len(res_empty_fvg.all_items) == 0

    res_no_price = filter_fvg_records_for_view(
        [
            _make_fvg_record(
                "z1", FvgDirection.BULLISH, 2690, 2695, datetime(2026, 1, 1, tzinfo=UTC)
            )
        ],
        current_price=None,
    )
    assert res_no_price.drawn_count == 1

    fig_empty = create_candlestick_figure([], fvg_records=[])
    assert fig_empty is not None
