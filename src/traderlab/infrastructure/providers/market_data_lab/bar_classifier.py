"""Classification of Market-Data-Lab M1 bars into active, flat, synthetic, and stagnant categories."""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass

from traderlab.infrastructure.calendars.forex_session import (
    MarketSessionState,
    XAUUSDTradingCalendar,
)
from traderlab.infrastructure.providers.market_data_lab.csv_reader import MarketDataLabBar

# Maximum consecutive flat minutes during an open market before flagging as uncertain/stagnant
MAX_LEGITIMATE_OPEN_FLAT_RUN: int = 5


class BarQualityClassification(enum.Enum):
    """Refined classification of individual M1 bar quality and market state."""

    ACTIVE_NORMAL = "ACTIVE_NORMAL"
    """Normal price movement (open != high or open != low or open != close) during open market."""

    SYNTHETIC_CARRY_FORWARD = "SYNTHETIC_CARRY_FORWARD"
    """Flat bar (O=H=L=C) during verified closed market (weekend, holiday, rollover).
    Proven synthetic carry-forward artifact produced by downloader. is_synthetic = True."""

    LEGITIMATE_FLAT_OPEN = "LEGITIMATE_FLAT_OPEN"
    """Flat bar (O=H=L=C) during verified open market hours (isolated low-volatility minute).
    Real market price, not synthetic artifact. is_synthetic = False."""

    UNCERTAIN_STAGNANT = "UNCERTAIN_STAGNANT"
    """Prolonged flat prices (> 5 consecutive minutes) during open market or ambiguous boundaries.
    Not proven synthetic, but flagged as suspicious/stagnant. is_synthetic = False."""


@dataclass(frozen=True, slots=True)
class ClassifiedBar:
    """An M1 bar paired with its quality classification."""

    bar: MarketDataLabBar
    classification: BarQualityClassification
    session_state: MarketSessionState
    consecutive_flat_count: int


def classify_m1_series(
    bars: Sequence[MarketDataLabBar],
    calendar: XAUUSDTradingCalendar | None = None,
    *,
    max_legitimate_flat_run: int = MAX_LEGITIMATE_OPEN_FLAT_RUN,
) -> list[ClassifiedBar]:
    """Classify a chronological sequence of M1 bars into distinct quality categories.

    Distinguishes:
    1. Verified closed market carry-forward (is_synthetic = True)
    2. Legitimate open market flat bars (is_synthetic = False)
    3. Uncertain prolonged stagnant runs (is_synthetic = False, flagged)
    4. Normal active market bars (is_synthetic = False)
    """
    cal = calendar if calendar is not None else XAUUSDTradingCalendar()
    classified: list[ClassifiedBar] = []

    consecutive_open_flat_count = 0

    for bar in bars:
        session_state = cal.get_session_state(bar.open_time)

        if not bar.is_flat:
            consecutive_open_flat_count = 0
            classified.append(
                ClassifiedBar(
                    bar=bar,
                    classification=BarQualityClassification.ACTIVE_NORMAL,
                    session_state=session_state,
                    consecutive_flat_count=0,
                )
            )
            continue

        # If flat (O == H == L == C):
        if cal.is_verified_closed(bar.open_time):
            consecutive_open_flat_count = 0
            classified.append(
                ClassifiedBar(
                    bar=bar,
                    classification=BarQualityClassification.SYNTHETIC_CARRY_FORWARD,
                    session_state=session_state,
                    consecutive_flat_count=0,
                )
            )
        else:
            # Flat bar during active / open market hours
            consecutive_open_flat_count += 1
            if consecutive_open_flat_count <= max_legitimate_flat_run:
                classification = BarQualityClassification.LEGITIMATE_FLAT_OPEN
            else:
                classification = BarQualityClassification.UNCERTAIN_STAGNANT

            classified.append(
                ClassifiedBar(
                    bar=bar,
                    classification=classification,
                    session_state=session_state,
                    consecutive_flat_count=consecutive_open_flat_count,
                )
            )

    return classified
