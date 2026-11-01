"""Deterministic Multi-Timeframe (MTF) Fair Value Gap (FVG) analysis and confluence engine.

Mathematical & Architectural Principles:
    1. **Strict Point-in-Time (PIT) Safety** — All MTF evaluations are bound to a single,
       explicit `as_of_utc` decision timestamp. Zones formed after `as_of_utc` are strictly
       excluded. Lifecycle events occurring after `as_of_utc` are completely masked.
    2. **Pure Descriptive Analytics** — MTF confluence identifies exact mathematical price
       overlap bands between valid active FVGs on different timeframes (e.g. D1, H4, H2).
       It NEVER generates trade signals, buy/sell triggers, execution advice, or win scores.
    3. **Directional Purity with Effective Role** — Confluence requires directional alignment
       based on current effective market role:
       - Normal active Bullish FVG acts as Bullish support.
       - Normal active Bearish FVG acts as Bearish resistance.
       - Inverted Bullish FVG (broken down, holds as resistance) acts as Bearish resistance.
       - Inverted Bearish FVG (broken up, holds as support) acts as Bullish support.
       - Invalidated or non-inverted Fully Filled normal FVGs are strictly excluded.
    4. **Exact Decimal Precision** — All overlap boundaries and widths are computed using
       immutable `Decimal` arithmetic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from traderlab.core.types import Timeframe, ensure_utc
from traderlab.domain.instruments.candle import Candle
from traderlab.domain.market_structure.fvg import (
    FvgDirection,
    FvgFillState,
    FvgLifecycleEvent,
    FvgValidityState,
    FvgZone,
)


@dataclass(frozen=True, slots=True)
class MtfFvgConfluenceBand:
    """An analytical price band where active FVGs from distinct timeframes mathematically intersect.

    Attributes:
        direction: Directional imbalance of the confluent zones (BULLISH or BEARISH).
        timeframes: Tuple of distinct participating timeframes sorted from coarsest to finest.
        overlap_bottom_price: Exact lower boundary of the common price intersection.
        overlap_top_price: Exact upper boundary of the common price intersection.
        overlap_width: Absolute price height (top - bottom) of the overlapping band.
        participating_fvg_ids: Tuple of deterministic IDs of the participating FVG zones.
        participating_zones: Tuple of participating immutable FvgZone objects.
    """

    direction: FvgDirection
    timeframes: tuple[Timeframe, ...]
    overlap_bottom_price: Decimal
    overlap_top_price: Decimal
    overlap_width: Decimal
    participating_fvg_ids: tuple[str, ...]
    participating_zones: tuple[FvgZone, ...]

    def __post_init__(self) -> None:
        if self.overlap_top_price <= self.overlap_bottom_price:
            raise ValueError(
                f"Invalid confluence band: top ({self.overlap_top_price}) must be strictly "
                f"greater than bottom ({self.overlap_bottom_price})"
            )

    @property
    def effective_direction(self) -> FvgDirection:
        """Alias matching the effective directional bias of the confluence band."""
        return self.direction


@dataclass(frozen=True, slots=True)
class MtfConfluenceAnalysisResult:
    """Comprehensive analytical metrics for multi-timeframe FVG confluences."""

    confluent_bands: list[MtfFvgConfluenceBand]
    total_combinations_count: int
    unique_price_bands_count: int
    three_way_confluences_count: int
    two_way_confluences_count: int


@dataclass(frozen=True, slots=True)
class MtfTimeframeSummary:
    """Observational snapshot metrics for a single timeframe panel within the MTF terminal.

    Provides exact, mutually exclusive category counts:
    - Active Normal Bullish / Bearish: structurally VALID and NOT fully filled.
    - Active IFVG: confirmed INVERTED (broken and holding reversed role).
    - Fully Filled: VALID but 100% mitigated by wick (exhausted imbalance).
    - Invalidated: violated by body close without confirmed inversion.
    """

    timeframe: Timeframe
    last_closed_candle_time: datetime | None
    data_completeness: str
    total_detected_fvgs: int
    active_bullish_count: int
    active_bearish_count: int
    inverted_count: int
    drawn_count: int
    total_filtered_count: int
    pit_confidence: str = "RESEARCH_ESTIMATED"

    # Detailed semantic breakdown metrics
    active_normal_bullish_count: int = 0
    active_normal_bearish_count: int = 0
    active_ifvg_bullish_count: int = 0  # Inverted Bearish -> Effective Bullish Support
    active_ifvg_bearish_count: int = 0  # Inverted Bullish -> Effective Bearish Resistance
    fully_filled_count: int = 0
    invalidated_count: int = 0
    visible_formed_count: int = 0
    warmup_carried_count: int = 0


def compute_mtf_fvg_confluences(
    records_by_timeframe: Mapping[Timeframe, Sequence[tuple[FvgZone, FvgLifecycleEvent | None]]],
    *,
    as_of_utc: datetime,
) -> list[MtfFvgConfluenceBand]:
    """Compute exact numerical price overlap bands among active FVGs across different timeframes.

    Rules:
        - Only zones formed on or before `as_of_utc` are evaluated.
        - Only structurally active zones participate:
          * Normal FVG: VALID and not FULLY_FILLED.
          * Inverted FVG: confirmed INVERTED.
        - Invalidated (INVALIDATED) and non-inverted Fully Filled (FULLY_FILLED) normal FVGs
          are strictly excluded as they possess zero remaining imbalance.
        - Confluence requires alignment in current effective directional role:
          * Normal Bullish & Inverted Bearish act as Bullish.
          * Normal Bearish & Inverted Bullish act as Bearish.
        - Intersections between 3 timeframes (e.g. D1 ∩ H4 ∩ H2) and pairs (e.g. D1 ∩ H4,
          D1 ∩ H2, H4 ∩ H2) are identified deterministically without duplicate combinations.
        - Zero lookahead bias: subsequent mitigations/invalidations after `as_of_utc` are ignored.

    Args:
        records_by_timeframe: Mapping of timeframe to its (FvgZone, FvgLifecycleEvent) records.
        as_of_utc: Point-in-Time decision timestamp.

    Returns:
        List of MtfFvgConfluenceBand objects, sorted by number of timeframes descending,
        then by overlap_width descending.
    """
    norm_as_of = ensure_utc(as_of_utc)
    confluent_bands: list[MtfFvgConfluenceBand] = []

    # Sort available timeframes coarsest to finest (e.g. D1, H4, H2)
    available_tfs = sorted(records_by_timeframe.keys(), reverse=True)
    if len(available_tfs) < 2:
        return []

    # Filter active records for each effective direction and timeframe
    active_by_dir_and_tf: dict[FvgDirection, dict[Timeframe, list[FvgZone]]] = {
        FvgDirection.BULLISH: {tf: [] for tf in available_tfs},
        FvgDirection.BEARISH: {tf: [] for tf in available_tfs},
    }

    for tf in available_tfs:
        for fvg, ev in records_by_timeframe[tf]:
            # Formation must be on or before decision time
            if fvg.formation_time > norm_as_of:
                continue

            # Must be active at decision time
            # 1. Invalidated zones possess no structural validity
            if ev is not None and ev.validity_state == FvgValidityState.INVALIDATED:
                continue

            # 2. Normal fully filled zones possess no remaining imbalance
            if (
                ev is not None
                and ev.validity_state == FvgValidityState.VALID
                and ev.fill_state == FvgFillState.FULLY_FILLED
            ):
                continue

            is_inverted = ev is not None and ev.validity_state == FvgValidityState.INVERTED

            # 3. Determine current effective directional role
            if is_inverted:
                # Role reversal: former bullish support broken down -> bearish resistance;
                # former bearish resistance broken up -> bullish support
                eff_dir = FvgDirection.BEARISH if fvg.is_bullish else FvgDirection.BULLISH
            else:
                eff_dir = fvg.direction

            active_by_dir_and_tf[eff_dir][tf].append(fvg)

    # Helper to check combinations for overlaps
    for direction in (FvgDirection.BULLISH, FvgDirection.BEARISH):
        dir_tfs = [tf for tf in available_tfs if active_by_dir_and_tf[direction][tf]]
        if len(dir_tfs) < 2:
            continue

        # 1. Check 3-way combinations if 3 or more timeframes exist
        if len(dir_tfs) >= 3:
            for i in range(len(dir_tfs)):
                for j in range(i + 1, len(dir_tfs)):
                    for k in range(j + 1, len(dir_tfs)):
                        tf1, tf2, tf3 = dir_tfs[i], dir_tfs[j], dir_tfs[k]
                        for z1 in active_by_dir_and_tf[direction][tf1]:
                            for z2 in active_by_dir_and_tf[direction][tf2]:
                                for z3 in active_by_dir_and_tf[direction][tf3]:
                                    b_max = max(z1.bottom_price, z2.bottom_price, z3.bottom_price)
                                    t_min = min(z1.top_price, z2.top_price, z3.top_price)
                                    if b_max < t_min:
                                        confluent_bands.append(
                                            MtfFvgConfluenceBand(
                                                direction=direction,
                                                timeframes=(tf1, tf2, tf3),
                                                overlap_bottom_price=b_max,
                                                overlap_top_price=t_min,
                                                overlap_width=t_min - b_max,
                                                participating_fvg_ids=(z1.id, z2.id, z3.id),
                                                participating_zones=(z1, z2, z3),
                                            )
                                        )

        # 2. Check 2-way combinations
        for i in range(len(dir_tfs)):
            for j in range(i + 1, len(dir_tfs)):
                tf1, tf2 = dir_tfs[i], dir_tfs[j]
                for z1 in active_by_dir_and_tf[direction][tf1]:
                    for z2 in active_by_dir_and_tf[direction][tf2]:
                        b_max = max(z1.bottom_price, z2.bottom_price)
                        t_min = min(z1.top_price, z2.top_price)
                        if b_max < t_min:
                            # Avoid duplicate if an identical 3-way band already covers this exact pair and boundaries
                            already_in_3way = any(
                                band.direction == direction
                                and {tf1, tf2}.issubset(set(band.timeframes))
                                and band.overlap_bottom_price == b_max
                                and band.overlap_top_price == t_min
                                for band in confluent_bands
                                if len(band.timeframes) == 3
                            )
                            if not already_in_3way:
                                confluent_bands.append(
                                    MtfFvgConfluenceBand(
                                        direction=direction,
                                        timeframes=(tf1, tf2),
                                        overlap_bottom_price=b_max,
                                        overlap_top_price=t_min,
                                        overlap_width=t_min - b_max,
                                        participating_fvg_ids=(z1.id, z2.id),
                                        participating_zones=(z1, z2),
                                    )
                                )

    # Sort confluences: 3-way first, then by widest overlap price width
    confluent_bands.sort(key=lambda b: (len(b.timeframes), b.overlap_width), reverse=True)
    return confluent_bands


def analyze_mtf_confluences(
    bands: Sequence[MtfFvgConfluenceBand],
) -> MtfConfluenceAnalysisResult:
    """Analyze a list of computed confluence bands into distinct combinatorial and price metrics."""
    unique_bands = {(b.direction, b.overlap_bottom_price, b.overlap_top_price) for b in bands}
    three_way = sum(1 for b in bands if len(b.timeframes) == 3)
    two_way = sum(1 for b in bands if len(b.timeframes) == 2)
    return MtfConfluenceAnalysisResult(
        confluent_bands=list(bands),
        total_combinations_count=len(bands),
        unique_price_bands_count=len(unique_bands),
        three_way_confluences_count=three_way,
        two_way_confluences_count=two_way,
    )


def summarize_mtf_timeframe(
    timeframe: Timeframe,
    candles: Sequence[Candle],
    fvg_records: Sequence[tuple[FvgZone, FvgLifecycleEvent | None]],
    *,
    drawn_count: int,
    total_filtered_count: int,
    data_completeness: str = "COMPLETE",
    as_of_utc: datetime | None = None,
    visible_start_utc: datetime | None = None,
) -> MtfTimeframeSummary:
    """Compute observational MTF summary metrics for a given timeframe with strict status separation."""
    last_closed: datetime | None = None
    closed_candles = [c for c in candles if c.is_closed]
    if closed_candles:
        last_closed = closed_candles[-1].close_time

    n_total = len(fvg_records)
    n_normal_bull = 0
    n_normal_bear = 0
    n_ifvg_bull = 0
    n_ifvg_bear = 0
    n_fully_filled = 0
    n_invalidated = 0
    n_visible = 0
    n_warmup = 0

    for fvg, ev in fvg_records:
        # 1. State categorization (Mutually exclusive domain partition)
        is_invalidated = ev is not None and ev.validity_state == FvgValidityState.INVALIDATED
        is_inverted = ev is not None and ev.validity_state == FvgValidityState.INVERTED
        is_fully_filled_normal = (
            ev is not None
            and ev.validity_state == FvgValidityState.VALID
            and ev.fill_state == FvgFillState.FULLY_FILLED
        )
        is_active_normal = (
            ev is None or ev.validity_state == FvgValidityState.VALID
        ) and not is_fully_filled_normal

        if is_active_normal:
            if fvg.is_bullish:
                n_normal_bull += 1
            else:
                n_normal_bear += 1
        elif is_inverted:
            if fvg.is_bullish:
                # Inverted bullish -> effective bearish resistance
                n_ifvg_bear += 1
            else:
                # Inverted bearish -> effective bullish support
                n_ifvg_bull += 1
        elif is_fully_filled_normal:
            n_fully_filled += 1
        elif is_invalidated:
            n_invalidated += 1

        # 2. Window partition: Visible formed vs Warm-up carried forward
        if visible_start_utc is not None:
            if fvg.formation_time >= visible_start_utc:
                n_visible += 1
            else:
                n_warmup += 1
        else:
            n_visible += 1

    total_inverted = n_ifvg_bull + n_ifvg_bear

    return MtfTimeframeSummary(
        timeframe=timeframe,
        last_closed_candle_time=last_closed,
        data_completeness=data_completeness,
        total_detected_fvgs=n_total,
        active_bullish_count=n_normal_bull,
        active_bearish_count=n_normal_bear,
        inverted_count=total_inverted,
        drawn_count=drawn_count,
        total_filtered_count=total_filtered_count,
        pit_confidence="RESEARCH_ESTIMATED",
        active_normal_bullish_count=n_normal_bull,
        active_normal_bearish_count=n_normal_bear,
        active_ifvg_bullish_count=n_ifvg_bull,
        active_ifvg_bearish_count=n_ifvg_bear,
        fully_filled_count=n_fully_filled,
        invalidated_count=n_invalidated,
        visible_formed_count=n_visible,
        warmup_carried_count=n_warmup,
    )
