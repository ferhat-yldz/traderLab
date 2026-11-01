"""Scratch test for FVG detection + lifecycle pipeline on resampled candles."""

from datetime import UTC, datetime
from traderlab.application.analysis.resampling import (
    resample_m1_candles,
    Timeframe,
    DailyCloseMode,
)
from traderlab.domain.market_structure.fvg import (
    detect_fair_value_gaps,
    track_fvg_lifecycle,
    FvgDirection,
)
from traderlab.infrastructure.providers.market_data_lab import (
    get_market_data_lab_settings,
    read_m1_csv,
    classify_m1_series,
    bar_to_candle,
    BarQualityClassification,
)
from traderlab.core.types import PriceType

s = get_market_data_lab_settings()
bp, _ = s.validate_dataset_files(2024, 1)
raw = read_m1_csv(bp, PriceType.BID)
cl = classify_m1_series(raw)
m1_candles = [
    bar_to_candle(
        cb.bar,
        is_synthetic=(cb.classification == BarQualityClassification.SYNTHETIC_CARRY_FORWARD),
    )
    for cb in cl
]

# Test H4
res_h4 = resample_m1_candles(m1_candles, Timeframe.H4)
candles_h4 = [rc.candle for rc in res_h4]
fvgs_h4 = detect_fair_value_gaps(candles_h4)
print(f"H4 detected FVGs: {len(fvgs_h4)}")

tracked_h4 = []
for fvg in fvgs_h4:
    events = track_fvg_lifecycle(fvg, candles_h4)
    last_event = events[-1] if events else None
    tracked_h4.append((fvg, last_event))

print("First 3 H4 tracked FVGs:")
for fvg, event in tracked_h4[:3]:
    fill = event.fill_state.value if event else "UNTOUCHED"
    val = event.validity_state.value if event else "VALID"
    print(
        f"  {fvg.id}: {fvg.direction.value} [{fvg.bottom_price} - {fvg.top_price}] CE={fvg.ce_price} formed={fvg.formation_time} -> {val}, {fill}"
    )
