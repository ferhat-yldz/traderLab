"""Professional FVG and IFVG Visualization and View Filtering Engine for TraderLab.

Separates visual display prioritization from pure domain market structure calculations.
Guarantees:
1. Zero domain loss: visually hidden zones are NEVER deleted or altered in domain models or confluence pools.
2. Deterministic zone selection: identical candle/FVG data + parameters = exact same visual ordering.
3. Strict Point-in-Time compliance: respects formation_time and effective_as_of_utc ceilings.
4. Professional aesthetics: declutters charts by prioritizing active, near-the-money structural levels.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from traderlab.domain.market_structure.fvg import (
    FvgDirection,
    FvgFillState,
    FvgLifecycleEvent,
    FvgValidityState,
    FvgZone,
)


class FvgVisibilityMode(enum.Enum):
    """Visual visibility filtering modes for Fair Value Gaps."""

    PROFESSIONAL = "PROFESYONEL (Varsayılan)"
    ALL_ACTIVE = "TÜM AKTİF BÖLGELER"
    ALL_HISTORICAL = "TÜM TARİHSEL BÖLGELER"
    SELECTED_ONLY = "YALNIZCA SEÇİLİ BÖLGE"


class FvgChartVisibility(enum.Enum):
    """Categorical chart rendering state for an individual FVG zone."""

    VISIBLE = "VISIBLE"
    HIDDEN_BY_FILTER = "HIDDEN_BY_FILTER"
    OUTSIDE_PRICE_VIEW = "OUTSIDE_PRICE_VIEW"
    OUTSIDE_TIME_VIEW = "OUTSIDE_TIME_VIEW"


@dataclass(frozen=True, slots=True)
class FvgViewItem:
    """Decorated FVG item for chart rendering and analytical event table display."""

    fvg: FvgZone
    event: FvgLifecycleEvent | None
    visibility: FvgChartVisibility
    distance_to_price: Decimal | None
    effective_direction: FvgDirection
    is_active: bool

    @property
    def is_visible_on_chart(self) -> bool:
        return self.visibility == FvgChartVisibility.VISIBLE


@dataclass(frozen=True, slots=True)
class FvgViewFilterResult:
    """Result of applying view filtering policies to a set of FVG records."""

    visible_records: list[tuple[FvgZone, FvgLifecycleEvent | None]]
    all_items: list[FvgViewItem]
    total_detected_count: int
    total_active_count: int
    drawn_count: int
    mode: FvgVisibilityMode
    max_zones: int


def calculate_effective_direction(
    fvg: FvgZone,
    event: FvgLifecycleEvent | None,
) -> FvgDirection:
    """Determine the active structural role (effective direction) of an FVG.

    - Normal Bullish FVG -> Bullish (Support)
    - Normal Bearish FVG -> Bearish (Resistance)
    - Inverted Bullish IFVG -> Bearish (Resistance role reversal)
    - Inverted Bearish IFVG -> Bullish (Support role reversal)
    """
    if event is not None and event.validity_state == FvgValidityState.INVERTED:
        return FvgDirection.BEARISH if fvg.is_bullish else FvgDirection.BULLISH
    return fvg.direction


def calculate_distance_to_price(
    fvg: FvgZone,
    current_price: Decimal,
) -> Decimal:
    """Calculate the minimal distance between current market price and the zone boundaries.

    If current price is within [bottom_price, top_price], distance is Decimal(0).
    """
    if fvg.bottom_price <= current_price <= fvg.top_price:
        return Decimal(0)
    if current_price < fvg.bottom_price:
        return fvg.bottom_price - current_price
    return current_price - fvg.top_price


def filter_fvg_records_for_view(
    records: Sequence[tuple[FvgZone, FvgLifecycleEvent | None]],
    *,
    current_price: Decimal | None = None,
    mode: FvgVisibilityMode = FvgVisibilityMode.PROFESSIONAL,
    max_zones: int = 16,
    selected_fvg_id: str | None = None,
    show_bullish: bool = True,
    show_bearish: bool = True,
    show_ifvg: bool = True,
    as_of_utc: datetime | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
) -> FvgViewFilterResult:
    """Filter and prioritize FVG records for clean, professional chart rendering.

    This function never mutates the underlying domain records.
    It returns a prioritized subset for Plotly rendering alongside fully annotated
    items for the analytical table.
    """
    if not records:
        return FvgViewFilterResult(
            visible_records=[],
            all_items=[],
            total_detected_count=0,
            total_active_count=0,
            drawn_count=0,
            mode=mode,
            max_zones=max_zones,
        )

    # Reference price fallback (midpoint of first zone if none provided)
    ref_price = current_price if current_price is not None else records[-1][0].ce_price

    total_detected = len(records)
    total_active = 0

    # Intermediate candidate evaluation
    classified: list[dict[str, Any]] = []

    for fvg, ev in records:
        eff_dir = calculate_effective_direction(fvg, ev)
        dist = calculate_distance_to_price(fvg, ref_price)

        is_inv = ev is not None and ev.validity_state == FvgValidityState.INVERTED
        is_invalid = ev is not None and ev.validity_state == FvgValidityState.INVALIDATED
        is_fully_filled_normal = (
            ev is not None and ev.fill_state == FvgFillState.FULLY_FILLED and not is_inv
        )

        # Active definition in accordance with domain lifecycle contract:
        # Valid unfilled/partially filled zone, or confirmed active IFVG
        is_active = (not is_invalid) and (not is_fully_filled_normal)
        if is_active:
            total_active += 1

        # Check preliminary filter exclusions
        pre_excluded: FvgChartVisibility | None = None

        if as_of_utc is not None and fvg.formation_time > as_of_utc:
            pre_excluded = FvgChartVisibility.OUTSIDE_TIME_VIEW
        elif (
            (fvg.is_bullish and not is_inv and not show_bullish)
            or (fvg.is_bearish and not is_inv and not show_bearish)
            or (is_inv and not show_ifvg)
        ):
            pre_excluded = FvgChartVisibility.HIDDEN_BY_FILTER
        elif price_min is not None and price_max is not None:
            # Check if zone is drastically outside visible vertical price axis (> 2.0x range buffer)
            p_span = price_max - price_min
            buffer = p_span * Decimal(2)
            if fvg.top_price < (price_min - buffer) or fvg.bottom_price > (price_max + buffer):
                pre_excluded = FvgChartVisibility.OUTSIDE_PRICE_VIEW

        classified.append(
            {
                "fvg": fvg,
                "event": ev,
                "distance": dist,
                "effective_direction": eff_dir,
                "is_active": is_active,
                "is_inverted": is_inv,
                "is_invalid": is_invalid,
                "is_fully_filled_normal": is_fully_filled_normal,
                "pre_excluded": pre_excluded,
            }
        )

    visible_ids: set[str] = set()

    # ── Policy Routing ───────────────────────────────────────────────────────
    if mode == FvgVisibilityMode.SELECTED_ONLY:
        # Only the chosen zone is marked visible
        if selected_fvg_id:
            for item in classified:
                if item["fvg"].id == selected_fvg_id:
                    visible_ids.add(item["fvg"].id)
                    break

    elif mode == FvgVisibilityMode.ALL_HISTORICAL:
        # All historical zones (including invalidated / filled) prioritized by recency
        eligible = [c for c in classified if c["pre_excluded"] is None]
        eligible.sort(key=lambda c: (c["fvg"].formation_time, c["fvg"].id), reverse=True)
        for item in eligible[:max_zones]:
            visible_ids.add(item["fvg"].id)

    elif mode == FvgVisibilityMode.ALL_ACTIVE:
        # All active zones (valid normal + confirmed IFVG) prioritized by distance to price
        eligible = [c for c in classified if c["pre_excluded"] is None and c["is_active"]]
        eligible.sort(
            key=lambda c: (c["distance"], -c["fvg"].formation_time.timestamp(), c["fvg"].id)
        )
        for item in eligible[:max_zones]:
            visible_ids.add(item["fvg"].id)

    else:
        # ── PROFESSIONAL MODE (Default) ───────────────────────────────────────
        # Criteria:
        # 1. Must be active (excludes invalidated and non-inverted fully filled).
        # 2. Near-the-money balanced distribution:
        #    - Encompassing zones (distance == 0)
        #    - Nearest supports below current price
        #    - Nearest resistances above current price
        eligible = [c for c in classified if c["pre_excluded"] is None and c["is_active"]]

        encompassing = [c for c in eligible if c["distance"] == Decimal(0)]
        above_resistances = [c for c in eligible if c["fvg"].bottom_price > ref_price]
        below_supports = [c for c in eligible if c["fvg"].top_price < ref_price]

        # Sort each group deterministically: distance ASC, formation_time DESC, fvg.id ASC
        encompassing.sort(key=lambda c: (-c["fvg"].formation_time.timestamp(), c["fvg"].id))
        above_resistances.sort(
            key=lambda c: (c["distance"], -c["fvg"].formation_time.timestamp(), c["fvg"].id)
        )
        below_supports.sort(
            key=lambda c: (c["distance"], -c["fvg"].formation_time.timestamp(), c["fvg"].id)
        )

        chosen: list[dict[str, Any]] = []

        # 1. Take encompassing zones first (up to max_zones)
        for item in encompassing:
            if len(chosen) < max_zones:
                chosen.append(item)

        remaining_capacity = max_zones - len(chosen)
        half_cap = max(1, remaining_capacity // 2)

        # 2. Quota: take up to half_cap from above and below
        taken_above = above_resistances[:half_cap]
        taken_below = below_supports[:half_cap]
        chosen.extend(taken_above)
        chosen.extend(taken_below)

        # 3. If capacity remains due to asymmetry, fill from whichever side has remaining candidates
        rem_after_split = max_zones - len(chosen)
        if rem_after_split > 0:
            surplus_above = above_resistances[len(taken_above) :]
            surplus_below = below_supports[len(taken_below) :]
            surplus = surplus_above + surplus_below
            surplus.sort(
                key=lambda c: (c["distance"], -c["fvg"].formation_time.timestamp(), c["fvg"].id)
            )
            chosen.extend(surplus[:rem_after_split])

        for item in chosen:
            visible_ids.add(item["fvg"].id)

    # ── Final Item Construction ──────────────────────────────────────────────
    all_items: list[FvgViewItem] = []
    visible_records: list[tuple[FvgZone, FvgLifecycleEvent | None]] = []

    for item in classified:
        fvg = item["fvg"]
        ev = item["event"]
        is_vis = fvg.id in visible_ids

        if is_vis:
            vis_state = FvgChartVisibility.VISIBLE
            visible_records.append((fvg, ev))
        elif item["pre_excluded"] is not None:
            vis_state = item["pre_excluded"]
        else:
            vis_state = FvgChartVisibility.HIDDEN_BY_FILTER

        all_items.append(
            FvgViewItem(
                fvg=fvg,
                event=ev,
                visibility=vis_state,
                distance_to_price=item["distance"],
                effective_direction=item["effective_direction"],
                is_active=item["is_active"],
            )
        )

    return FvgViewFilterResult(
        visible_records=visible_records,
        all_items=all_items,
        total_detected_count=total_detected,
        total_active_count=total_active,
        drawn_count=len(visible_records),
        mode=mode,
        max_zones=max_zones,
    )
