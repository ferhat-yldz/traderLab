"""Dukascopy market data provider — binary .bi5 decoding and HTTP transport."""

from __future__ import annotations

from traderlab.infrastructure.providers.dukascopy.client import (
    DEFAULT_BASE_URL,
    S3_BUCKET_NAME,
    S3_REGION,
    DukascopyHttpClient,
    build_dukascopy_s3_key,
    build_dukascopy_url,
)
from traderlab.infrastructure.providers.dukascopy.decoder import (
    RECORD_SIZE,
    DukascopyResolution,
    DukascopyTick,
    decode_bi5_ticks,
    encode_bi5_ticks,
)
from traderlab.infrastructure.providers.dukascopy.errors import (
    CorruptedBinaryError,
    DukascopyAccessDeniedError,
    DukascopyDecoderError,
    DukascopyError,
    DukascopyNetworkError,
    DukascopyRateLimitError,
    DukascopyServerError,
    InvalidContentTypeError,
    InvalidRecordSizeError,
    InvalidTimestampError,
    NegativeSpreadError,
    PriceScaleValidationError,
    RegulatoryBlockError,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "RECORD_SIZE",
    "S3_BUCKET_NAME",
    "S3_REGION",
    "CorruptedBinaryError",
    "DukascopyAccessDeniedError",
    "DukascopyDecoderError",
    "DukascopyError",
    "DukascopyHttpClient",
    "DukascopyNetworkError",
    "DukascopyRateLimitError",
    "DukascopyResolution",
    "DukascopyServerError",
    "DukascopyTick",
    "InvalidContentTypeError",
    "InvalidRecordSizeError",
    "InvalidTimestampError",
    "NegativeSpreadError",
    "PriceScaleValidationError",
    "RegulatoryBlockError",
    "build_dukascopy_s3_key",
    "build_dukascopy_url",
    "decode_bi5_ticks",
    "encode_bi5_ticks",
]
