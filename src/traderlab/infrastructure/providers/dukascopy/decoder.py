"""Binary decoder and encoder for Dukascopy .bi5 tick archives.

Dukascopy publishes historical tick files compressed with raw LZMA.
Each record is a 20-byte big-endian binary struct:
    - 00-03 (uint32): Millisecond offset from start of period (UTC)
    - 04-07 (uint32): Raw ASK price (scaled by point divisor)
    - 08-11 (uint32): Raw BID price (scaled by point divisor)
    - 12-15 (float32): ASK quote liquidity depth (in millions of base currency units)
    - 16-19 (float32): BID quote liquidity depth (in millions of base currency units)

CRITICAL FINANCIAL DATA DISTINCTIONS:
    1. Volume fields represent QUOTE LIQUIDITY DEPTH on the Dukascopy ECN book,
       NEVER exchange-traded transaction volume.
    2. Prices are strictly converted to decimal.Decimal to avoid IEEE-754 floating-point drift.
    3. The invariant ASK >= BID (spread >= 0) is rigorously enforced on every record.
"""

from __future__ import annotations

import dataclasses
import enum
import lzma
import struct
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from traderlab.core.types import ensure_utc
from traderlab.infrastructure.providers.dukascopy.errors import (
    CorruptedBinaryError,
    InvalidRecordSizeError,
    InvalidTimestampError,
    NegativeSpreadError,
    PriceScaleValidationError,
)

# Struct format: big-endian uint32, uint32, uint32, float32, float32
TICK_STRUCT = struct.Struct(">IIIff")
RECORD_SIZE = TICK_STRUCT.size  # Exactly 20 bytes


class DukascopyResolution(enum.Enum):
    """Time resolution of the .bi5 archive."""

    HOURLY = "HOURLY"  # Time offset is relative to hour start (0 to 3,599,999 ms)
    DAILY = "DAILY"  # Time offset is relative to day start (0 to 86,399,999 ms)

    @property
    def max_offset_ms(self) -> int:
        if self == DukascopyResolution.HOURLY:
            return 3_600_000
        return 86_400_000


@dataclasses.dataclass(frozen=True, slots=True)
class DukascopyTick:
    """Represents a validated, normalized market tick from a Dukascopy archive.

    Attributes:
        time: UTC-aware timestamp of the tick.
        ask: Best offer price (Decimal).
        bid: Best bid price (Decimal).
        spread: Difference between ask and bid (ask - bid >= 0).
        ask_quote_volume: Quoted ask liquidity depth in millions of base units.
        bid_quote_volume: Quoted bid liquidity depth in millions of base units.
        point_divisor: Divisor used to convert raw integer to real price.
    """

    time: datetime
    ask: Decimal
    bid: Decimal
    spread: Decimal
    ask_quote_volume: float
    bid_quote_volume: float
    point_divisor: Decimal

    def __post_init__(self) -> None:
        ensure_utc(self.time)
        if self.ask < self.bid:
            raise NegativeSpreadError(str(self.ask), str(self.bid), 0)


def decode_bi5_ticks(
    compressed_data: bytes,
    base_time_utc: datetime,
    *,
    resolution: DukascopyResolution = DukascopyResolution.HOURLY,
    point_divisor: Decimal = Decimal("1000"),
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
) -> list[DukascopyTick]:
    """Decode an LZMA-compressed .bi5 archive into validated DukascopyTick objects.

    Args:
        compressed_data: Raw bytes of the .bi5 file.
        base_time_utc: Starting timestamp of the archive period (hour or day) in UTC.
        resolution: HOURLY (default) or DAILY time bounds.
        point_divisor: Price scaling divisor (e.g. 1000 for XAUUSD, 100000 for EURUSD).
        min_price: Optional lower bound for sanity checking the decoded price.
        max_price: Optional upper bound for sanity checking the decoded price.

    Returns:
        List of decoded DukascopyTick records in chronological order.

    Raises:
        CorruptedBinaryError: If LZMA decompression fails.
        InvalidRecordSizeError: If decompressed length is not a multiple of 20 bytes.
        InvalidTimestampError: If a tick offset exceeds the maximum resolution bound.
        NegativeSpreadError: If ASK < BID on any record.
        PriceScaleValidationError: If decoded prices fall outside sanity bounds.
    """
    if point_divisor <= Decimal("0"):
        raise ValueError(f"point_divisor must be strictly positive, got {point_divisor}")

    base_utc = ensure_utc(base_time_utc)

    if not compressed_data:
        return []

    try:
        raw_bytes = lzma.decompress(compressed_data)
    except Exception as exc:
        raise CorruptedBinaryError(str(exc)) from exc

    if not raw_bytes:
        return []

    total_bytes = len(raw_bytes)
    if total_bytes % RECORD_SIZE != 0:
        raise InvalidRecordSizeError(total_bytes, RECORD_SIZE)

    max_offset_ms = resolution.max_offset_ms
    ticks: list[DukascopyTick] = []

    for idx, offset in enumerate(range(0, total_bytes, RECORD_SIZE)):
        chunk = raw_bytes[offset : offset + RECORD_SIZE]
        ms_offset, raw_ask, raw_bid, ask_vol, bid_vol = TICK_STRUCT.unpack(chunk)

        if ms_offset >= max_offset_ms:
            raise InvalidTimestampError(ms_offset, max_offset_ms)

        ask = Decimal(raw_ask) / point_divisor
        bid = Decimal(raw_bid) / point_divisor

        if ask < bid:
            raise NegativeSpreadError(str(ask), str(bid), idx)

        if min_price is not None and (ask < min_price or bid < min_price):
            bad_price = str(min(ask, bid))
            raise PriceScaleValidationError(
                bad_price, str(min_price), str(max_price or "inf"), str(point_divisor)
            )

        if max_price is not None and (ask > max_price or bid > max_price):
            bad_price = str(max(ask, bid))
            raise PriceScaleValidationError(
                bad_price, str(min_price or "0"), str(max_price), str(point_divisor)
            )

        tick_time = base_utc + timedelta(milliseconds=ms_offset)
        spread = ask - bid

        ticks.append(
            DukascopyTick(
                time=tick_time,
                ask=ask,
                bid=bid,
                spread=spread,
                ask_quote_volume=ask_vol,
                bid_quote_volume=bid_vol,
                point_divisor=point_divisor,
            )
        )

    return ticks


def encode_bi5_ticks(
    ticks: Sequence[DukascopyTick],
    base_time_utc: datetime,
    *,
    point_divisor: Decimal = Decimal("1000"),
) -> bytes:
    """Encode DukascopyTick objects into an LZMA-compressed .bi5 binary archive.

    This utility is primarily used for generating synthetic binary fixtures and roundtrip testing.

    Args:
        ticks: Sequence of ticks to pack and compress.
        base_time_utc: Base period timestamp (hour or day) in UTC.
        point_divisor: Divisor to scale Decimal prices back to raw uint32 integers.

    Returns:
        LZMA-compressed bytes compatible with Dukascopy .bi5 format.
    """
    base_utc = ensure_utc(base_time_utc)
    raw_chunks: list[bytes] = []

    for tick in ticks:
        tick_utc = ensure_utc(tick.time)
        delta = tick_utc - base_utc
        ms_offset = int(delta.total_seconds() * 1000)
        if ms_offset < 0:
            raise ValueError(f"Tick timestamp {tick_utc} is before base time {base_utc}")

        raw_ask = round(tick.ask * point_divisor)
        raw_bid = round(tick.bid * point_divisor)

        chunk = TICK_STRUCT.pack(
            ms_offset,
            raw_ask,
            raw_bid,
            tick.ask_quote_volume,
            tick.bid_quote_volume,
        )
        raw_chunks.append(chunk)

    payload = b"".join(raw_chunks)
    return lzma.compress(payload)
