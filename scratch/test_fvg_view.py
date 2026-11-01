from datetime import UTC, datetime
from decimal import Decimal
from traderlab.application.analysis.resampling import resample_m1_candles, Timeframe
from traderlab.domain.market_structure.fvg import (
    detect_fair_value_gaps,
    track_fvg_lifecycle,
    FvgValidityState,
    FvgFillState,
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

res_h4 = resample_m1_candles(m1_candles, Timeframe.H4)
all_h4 = [rc.candle for rc in res_h4]

t_start = datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
t_end = datetime(2024, 1, 31, 0, 0, tzinfo=UTC)

# Test compute_fvg_records_for_view
eligible = [c for c in all_h4 if c.close_time <= t_end]
all_fvgs = detect_fair_value_gaps(eligible, require_closed=True)
print(f"Total H4 FVGs in month up to t_end: {len(all_fvgs)}")

visible_fvgs = []
for fvg in all_fvgs:
    events = track_fvg_lifecycle(fvg, eligible, as_of_utc=t_end)
    last_ev = events[-1] if events else None
    if fvg.formation_time >= t_start:
        visible_fvgs.append((fvg, last_ev, "FORMED_IN_WINDOW"))
    else:
        if (
            last_ev
            and last_ev.invalidated_by_candle_open
            and last_ev.invalidated_by_candle_open < t_start
            and last_ev.validity_state != FvgValidityState.INVERTED
        ):
            continue
        if (
            last_ev
            and last_ev.fill_state == FvgFillState.FULLY_FILLED
            and last_ev.bar_close_time < t_start
        ):
            continue
        visible_fvgs.append((fvg, last_ev, "PRIOR_ACTIVE_WARMUP"))

print(f"Visible H4 FVGs for [Jan 15 - Jan 31]: {len(visible_fvgs)}")
for fvg, ev, origin in visible_fvgs:
    val = ev.validity_state.value if ev else "VALID"
    fill = ev.fill_state.value if ev else "UNTOUCHED"
    print(
        f"  [{origin}] {fvg.direction.value} {fvg.id[:35]}... formed={fvg.formation_time} -> {val}, {fill}"
    )
