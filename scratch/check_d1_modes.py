from traderlab.infrastructure.providers.market_data_lab.config import get_market_data_lab_settings
from traderlab.infrastructure.providers.market_data_lab.csv_reader import (
    read_m1_csv,
    bar_to_candle,
)
from traderlab.core.types import PriceType, Timeframe
from traderlab.application.analysis.resampling import resample_m1_candles, DailyCloseMode
from traderlab.domain.market_structure.fvg import detect_fair_value_gaps

settings = get_market_data_lab_settings()
bid_path, _ = settings.validate_dataset_files(2024, 1)
bars = read_m1_csv(bid_path, PriceType.BID)
# Filter only non-synthetic (as clean data would be)
candles = [bar_to_candle(b) for b in bars]

res_utc = resample_m1_candles(candles, Timeframe.D1, daily_close_mode=DailyCloseMode.UTC_MIDNIGHT)
res_ny = resample_m1_candles(candles, Timeframe.D1, daily_close_mode=DailyCloseMode.NEW_YORK_1700)

print(f"UTC_MIDNIGHT total: {len(res_utc)}")
for r in res_utc[:8]:
    c = r.candle
    print(
        f"  {c.open_time} ({c.open_time.strftime('%a')}) -> {c.close_time} | pres={r.present_m1_count} act={r.active_m1_count} synth={r.synthetic_m1_count} O={c.open} H={c.high} L={c.low} C={c.close} is_closed={c.is_closed}"
    )

print(f"\nNEW_YORK_1700 total: {len(res_ny)}")
for r in res_ny[:8]:
    c = r.candle
    print(
        f"  {c.open_time} ({c.open_time.strftime('%a')}) -> {c.close_time} | pres={r.present_m1_count} act={r.active_m1_count} synth={r.synthetic_m1_count} O={c.open} H={c.high} L={c.low} C={c.close} is_closed={c.is_closed}"
    )

# Check clean candles (only active bars, no synthetic)
clean_candles = [c for c in candles if not c.is_synthetic]
res_utc_clean = resample_m1_candles(
    clean_candles, Timeframe.D1, daily_close_mode=DailyCloseMode.UTC_MIDNIGHT
)
res_ny_clean = resample_m1_candles(
    clean_candles, Timeframe.D1, daily_close_mode=DailyCloseMode.NEW_YORK_1700
)

print(f"\nClean candles count: {len(clean_candles)}")
print(f"UTC_MIDNIGHT clean bars:")
for r in res_utc_clean[:8]:
    c = r.candle
    print(
        f"  {c.open_time} ({c.open_time.strftime('%a')}) -> pres={r.present_m1_count} comp={r.completeness.value} is_closed={c.is_closed}"
    )

print(f"NEW_YORK_1700 clean bars:")
for r in res_ny_clean[:8]:
    c = r.candle
    print(
        f"  {c.open_time} ({c.open_time.strftime('%a')}) -> pres={r.present_m1_count} comp={r.completeness.value} is_closed={c.is_closed}"
    )
