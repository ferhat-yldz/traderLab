"""Core type aliases and lightweight value objects used across the domain.

Design decisions:
    - All timestamps are UTC-aware `datetime` objects.
    - `Decimal` is used for prices and monetary amounts (no float rounding issues).
    - Instrument identifiers are typed strings to prevent misuse at call sites.
    - Enums use UPPER_CASE values following Python conventions.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from decimal import Decimal
from typing import NewType

# ── Branded / NewType identifiers ───────────────────────────────────────────
InstrumentId = NewType("InstrumentId", str)
DataSourceId = NewType("DataSourceId", str)
ProviderId = NewType("ProviderId", str)


# ── Enumerations ────────────────────────────────────────────────────────────
class MarketType(enum.Enum):
    """Broad market classification."""

    FOREX = "FOREX"
    CRYPTO = "CRYPTO"
    EQUITY = "EQUITY"
    FUTURES = "FUTURES"
    INDEX = "INDEX"


class Timeframe(enum.Enum):
    """Candlestick / bar timeframe.

    The *value* is the number of seconds so that arithmetic comparisons
    between timeframes remain trivial and deterministic.
    """

    M1 = 60
    M5 = 300
    M15 = 900
    M30 = 1_800
    H1 = 3_600
    H2 = 7_200
    H4 = 14_400
    D1 = 86_400
    W1 = 604_800

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Timeframe):
            return NotImplemented
        return self.value < other.value

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Timeframe):
            return NotImplemented
        return self.value <= other.value


class SessionName(enum.Enum):
    """Well-known trading sessions (used for session-aware analysis)."""

    ASIAN = "ASIAN"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"
    OVERLAP_LN_NY = "OVERLAP_LN_NY"


class PriceType(enum.Enum):
    """Classification of the price series represented by a candle.

    In Forex and OTC markets, bid and ask prices are distinct streams.
    A candle can represent:
    - BID: constructed from bid quotes (standard broker chart series for longs/shorts)
    - ASK: constructed from ask quotes (critical for short exit / spread evaluation)
    - MID: constructed from mid-quote ((bid + ask) / 2) or single-feed midpoints
    - UNKNOWN: when the upstream provider does not specify or data lineage is unverified.
    """

    BID = "BID"
    ASK = "ASK"
    MID = "MID"
    UNKNOWN = "UNKNOWN"


class AvailabilityConfidence(enum.Enum):
    """Degree of confidence regarding when a bar was knowable/available in the market.

    Critical for avoiding Point-in-Time (PIT) lookahead bias in quantitative research:
    - VERIFIED: Availability timestamp was directly recorded in real-time or verified
      via exchange / broker tick publication logs.
    - ESTIMATED: Availability timestamp is approximated (e.g. close_time + typical network latency).
    - UNKNOWN: Historical bulk backfill where exact publication/arrival latency is unknown.
      Treated as knowable only after close_time with conservative bounds.
    """

    VERIFIED = "VERIFIED"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class PitPolicy(enum.Enum):
    """Point-in-Time availability evaluation policy.

    STRICT_PIT:
        Requires `availability_confidence == AvailabilityConfidence.VERIFIED` and a non-null
        `available_at` timestamp. Rejects bars with UNKNOWN or ESTIMATED confidence, preventing
        unverified historical data from masquerading as verified PIT data.

    RESEARCH_ESTIMATED:
        Permits completed bars with `available_at is None` or `availability_confidence in (ESTIMATED, UNKNOWN)`
        to be used if `as_of >= close_time`. Explicitly documents the caveat that broker network latency
        and retroactive data revisions could cause lookahead bias.
    """

    STRICT_PIT = "STRICT_PIT"
    RESEARCH_ESTIMATED = "RESEARCH_ESTIMATED"


# ── Helper: UTC-aware timestamp & normalization ─────────────────────────────
def utc_now() -> datetime:
    """Return the current UTC-aware timestamp."""
    return datetime.now(tz=UTC)


def ensure_utc(dt: datetime) -> datetime:
    """Validate that datetime is timezone-aware with zero UTC offset, then normalize to UTC.

    Raises:
        ValueError: If `dt` is naive or has a non-zero UTC offset.
    """
    if dt.tzinfo is None:
        raise ValueError("datetime must be UTC-aware (got naive datetime)")
    offset = dt.utcoffset()
    if offset is not None and offset.total_seconds() != 0:
        raise ValueError(f"datetime must be in UTC timezone (offset=0), got offset {offset}")
    return dt.astimezone(UTC)


# ── Public API ──────────────────────────────────────────────────────────────
__all__ = [
    "AvailabilityConfidence",
    "DataSourceId",
    "Decimal",
    "InstrumentId",
    "MarketType",
    "PitPolicy",
    "PriceType",
    "ProviderId",
    "SessionName",
    "Timeframe",
    "ensure_utc",
    "utc_now",
]
