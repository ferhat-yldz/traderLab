"""Zorunlu Faz 4D-K2 Ön Denetim Negatif Test Kanıtı.

Bu betik Aşama 0 kapsamındaki iki kritik PIT ve durum makinesi sınır durumunu test eder:
1. Negatif Test 0.1: Sonradan INVALIDATED olan OB'nin cutoff=None durumunda geçmiş giriş bölgesi olarak seçilememesi (Lookahead sızıntısı).
2. Negatif Test 0.2: Karar anında FULLY_MITIGATED olan (is_active=False) OB'nin aktif giriş bölgesi olarak seçilmesi (Status != INVALIDATED kontrolünün yetersizliği).
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from traderlab.core.types import (
    DataSourceId,
    InstrumentId,
    PriceType,
    Timeframe,
)
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.displacement import (
    DisplacementDirection,
    DisplacementEvent,
)
from traderlab.domain.market_structure.ict_setup import (
    IctSetupConfig,
    detect_ict_setup_candidates,
)
from traderlab.domain.market_structure.market_structure import (
    MarketTrend,
    StructureBreakEvent,
    StructureBreakType,
    StructureDirection,
)
from traderlab.domain.market_structure.order_block import (
    OrderBlockDirection,
    OrderBlockLifecycleEvent,
    OrderBlockRecord,
    OrderBlockStatus,
    OrderBlockZoneMode,
)

INST = InstrumentId("XAUUSD")
SRC = DataSourceId("market_data_lab_m1_derived_h4")


def make_candle(open_t: datetime, o: str, h: str, l: str, c: str) -> Candle:
    close_t = open_t + timedelta(hours=4)
    return Candle(
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        open_time=open_t,
        close_time=close_t,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal("100"),
        tick_volume=Decimal("100"),
        is_closed=True,
    )


def make_structure_break(
    event_id: str,
    open_time: datetime,
    break_type: StructureBreakType = StructureBreakType.BOS,
    direction: StructureDirection = StructureDirection.BEARISH,
    tf: Timeframe = Timeframe.H4,
) -> StructureBreakEvent:
    close_time = open_time + timedelta(seconds=tf.value)
    return StructureBreakEvent(
        event_id=event_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=PriceType.BID,
        structure_direction=direction,
        break_type=break_type,
        source_swing_id=f"SWING_{event_id}",
        protected_level_id=f"PROT_{event_id}",
        reference_level_price=Decimal("4000.00"),
        break_price=Decimal("3960.00"),
        break_bar_open_time=open_time,
        break_bar_close_time=close_time,
        detection_time=close_time,
        trend_before=MarketTrend.UPTREND
        if direction == StructureDirection.BEARISH
        else MarketTrend.DOWNTREND,
        trend_after=MarketTrend.DOWNTREND
        if direction == StructureDirection.BEARISH
        else MarketTrend.UPTREND,
        confirmation_status="CONFIRMED",
        mss_candidate_id=f"MSS_CAND_{event_id}",
    )


def make_disp(
    event_id: str,
    open_time: datetime,
    direction: DisplacementDirection = DisplacementDirection.BEARISH,
    tf: Timeframe = Timeframe.H4,
) -> DisplacementEvent:
    close_time = open_time + timedelta(seconds=tf.value)
    return DisplacementEvent(
        event_id=event_id,
        instrument_id=INST,
        source_id=SRC,
        timeframe=tf,
        price_type=PriceType.BID,
        candle_open_time=open_time,
        candle_close_time=close_time,
        detection_time=close_time,
        direction=direction,
        open_price=Decimal("4000.00"),
        high_price=Decimal("4030.00"),
        low_price=Decimal("3950.00"),
        close_price=Decimal("3960.00"),
        body_size=Decimal("40.00"),
        total_range=Decimal("80.00"),
        body_ratio=Decimal("0.80"),
        volatility_reference=Decimal("15.00"),
        range_atr_multiple=Decimal("2.50"),
        close_location=Decimal("0.125"),
        criteria_passed=("BODY_RATIO", "ATR_EXPANSION"),
        confirmation_status="CONFIRMED",
    )


def run_negative_tests():
    t0 = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
    t_conf = t0 + timedelta(hours=8)
    t_inval = t0 + timedelta(hours=24)

    candles = [
        make_candle(t0, "4000", "4010", "3990", "4005"),
        make_candle(t0 + timedelta(hours=4), "4005", "4020", "3970", "3975"),
        make_candle(t0 + timedelta(hours=8), "3975", "3980", "3950", "3955"),
        make_candle(t0 + timedelta(hours=12), "3955", "3970", "3950", "3960"),
        make_candle(t0 + timedelta(hours=16), "3960", "3975", "3955", "3965"),
        make_candle(t0 + timedelta(hours=20), "3965", "4020", "3960", "4015"),
    ]

    bos = make_structure_break("BOS_01", candles[1].open_time, StructureBreakType.BOS, StructureDirection.BEARISH)
    disp = make_disp("DISP_01", candles[1].open_time, DisplacementDirection.BEARISH)

    # -------------------------------------------------------------------------
    # NEGATİF TEST 0.1: Sonradan INVALIDATED olan OB'nin cutoff=None durumunda seçimi
    # -------------------------------------------------------------------------
    inval_ev = OrderBlockLifecycleEvent(
        event_id="EV_INVAL_01",
        order_block_id="OB_01",
        event_time=t_inval,
        bar_open_time=t_inval - timedelta(hours=4),
        bar_close_time=t_inval,
        status_before=OrderBlockStatus.UNMITIGATED,
        status_after=OrderBlockStatus.INVALIDATED,
        trigger_price=Decimal("4025.00"),
        penetration_depth=Decimal("5.00"),
        penetration_percentage=Decimal("25.00"),
        description="Invalidated",
    )
    ob_inval_later = OrderBlockRecord(
        order_block_id="OB_01",
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        direction=OrderBlockDirection.BEARISH,
        zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE,
        zone_bottom=Decimal("3990.00"),
        zone_top=Decimal("4020.00"),
        midpoint_50=Decimal("4005.00"),
        origin_candle_open_time=t0,
        origin_candle_close_time=t0 + timedelta(hours=4),
        origin_candle_id="C0",
        confirmation_time=t_conf,
        displacement_event_id="DISP_01",
        structure_break_event_id="BOS_01",
        mss_confirmation_id=None,
        associated_fvg_id=None,
        status=OrderBlockStatus.INVALIDATED,  # Dönem sonu statüsü
        invalidation_time=t_inval,            # Gelecekteki iptal anı (t0 + 24h)
        lifecycle_events=(inval_ev,),
    )

    cfg = IctSetupConfig(prefer_order_block_over_fvg=True)
    setups_no_cutoff = detect_ict_setup_candidates(
        candles=candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob_inval_later],
        fvg_zones=[],
        config=cfg,
        as_of_utc=None,  # Tüm geçmiş taraması
    )

    print("\n=======================================================")
    print("KANIT 0.1: Sonradan INVALIDATED Olan OB (as_of_utc=None)")
    print("=======================================================")
    print(f"BOS Tespit Zamanı: {bos.detection_time} (t0 + 8h)")
    print(f"OB Teyit Zamanı:   {ob_inval_later.confirmation_time} (t0 + 8h)")
    print(f"OB İptal Zamanı:   {ob_inval_later.invalidation_time} (t0 + 24h)")
    print(f"BOS anında OB aktif miydi?: EVET (t_inval > t_bos)")
    print(f"Bulunan Setup Sayısı (as_of_utc=None): {len(setups_no_cutoff)}")
    if len(setups_no_cutoff) == 0:
        print("[HATA KANITLANDI 0.1]: Setup bulunamadı! Kök Neden: ict_setup.py satır 1081'deki "
              "'cont_ob.status != OrderBlockStatus.INVALIDATED' kontrolü dönem sonu nihai durumunu "
              "doğrudan incelediği için, setup anında aktif olan OB geçmiş taramasında elenmiştir.")
    else:
        print("[BAŞARILI 0.1]: Setup başarıyla bulundu.")

    # -------------------------------------------------------------------------
    # NEGATİF TEST 0.2: FULLY_MITIGATED OB'nin Karar Anında Seçimi
    # -------------------------------------------------------------------------
    # OB confirmed at t0+4h, fully mitigated at t0+6h, BOS occurs at t0+8h
    ob_fully_mitigated = OrderBlockRecord(
        order_block_id="OB_FULLY_MIT",
        instrument_id=INST,
        source_id=SRC,
        timeframe=Timeframe.H4,
        price_type=PriceType.BID,
        direction=OrderBlockDirection.BEARISH,
        zone_mode=OrderBlockZoneMode.FULL_CANDLE_RANGE,
        zone_bottom=Decimal("3990.00"),
        zone_top=Decimal("4020.00"),
        midpoint_50=Decimal("4005.00"),
        origin_candle_open_time=t0,
        origin_candle_close_time=t0 + timedelta(hours=4),
        origin_candle_id="C0",
        confirmation_time=t0 + timedelta(hours=4),
        displacement_event_id="DISP_01",
        structure_break_event_id="BOS_01",
        mss_confirmation_id=None,
        associated_fvg_id=None,
        status=OrderBlockStatus.FULLY_MITIGATED,  # Setup anında zaten tükenmiş/doymuş bölge
        full_mitigation_time=t0 + timedelta(hours=6),
        lifecycle_events=(),
    )

    setups_fully_mit = detect_ict_setup_candidates(
        candles=candles,
        structure_breaks=[bos],
        displacements=[disp],
        order_blocks=[ob_fully_mitigated],
        fvg_zones=[],
        config=cfg,
        as_of_utc=t0 + timedelta(hours=14),
    )

    print("\n=======================================================")
    print("KANIT 0.2: FULLY_MITIGATED OB Seçimi (is_active=False)")
    print("=======================================================")
    print(f"OB Statüsü:      {ob_fully_mitigated.status.value}")
    print(f"OB is_active:    {ob_fully_mitigated.is_active}")
    print(f"Bulunan Setup:   {len(setups_fully_mit)}")
    if len(setups_fully_mit) > 0:
        print(f"Seçilen Zone ID: {setups_fully_mit[0].order_block_id}")
        print("[HATA KANITLANDI 0.2]: is_active=False olan FULLY_MITIGATED OB aktif giriş bölgesi seçildi! "
              "Kök Neden: ict_setup.py satır 1081'de yalnızca 'status != OrderBlockStatus.INVALIDATED' "
              "kontrol edilmekte, 'is_active' veya 'status not in (INVALIDATED, FULLY_MITIGATED)' "
              "kontrolü yapılmamaktadır.")
    else:
        print("[BAŞARILI 0.2]: FULLY_MITIGATED OB reddedildi.")
    print("=======================================================\n")


if __name__ == "__main__":
    run_negative_tests()
