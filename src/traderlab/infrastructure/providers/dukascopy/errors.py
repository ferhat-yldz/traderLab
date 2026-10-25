"""Exceptions for Dukascopy data access and binary decoding."""

from __future__ import annotations

from traderlab.core.errors import InfrastructureError


class DukascopyError(InfrastructureError):
    """Base exception for all Dukascopy provider operations."""

    def __init__(self, message: str, *, code: str = "DUKASCOPY_ERROR") -> None:
        super().__init__(message, code=code)


# ── Decoder Exceptions ────────────────────────────────────────────────────────


class DukascopyDecoderError(DukascopyError):
    """Base exception for decoding failures."""

    def __init__(self, message: str, *, code: str = "DUKASCOPY_DECODER_ERROR") -> None:
        super().__init__(message, code=code)


class CorruptedBinaryError(DukascopyDecoderError):
    """Raised when binary archive is corrupted or cannot be decompressed via LZMA."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Corrupted binary archive or LZMA decompression error: {detail}",
            code="DUKASCOPY_CORRUPTED_BINARY",
        )


class InvalidRecordSizeError(DukascopyDecoderError):
    """Raised when decompressed byte length is not a valid multiple of the 20-byte record size."""

    def __init__(self, total_bytes: int, record_size: int = 20) -> None:
        remainder = total_bytes % record_size
        super().__init__(
            f"Invalid payload size: {total_bytes} bytes is not a multiple of {record_size} bytes (remainder: {remainder})",
            code="DUKASCOPY_INVALID_RECORD_SIZE",
        )


class InvalidTimestampError(DukascopyDecoderError):
    """Raised when tick millisecond offset exceeds boundary or is non-monotonic."""

    def __init__(self, offset_ms: int, max_allowed_ms: int) -> None:
        super().__init__(
            f"Invalid timestamp offset {offset_ms} ms exceeds maximum allowed limit {max_allowed_ms} ms",
            code="DUKASCOPY_INVALID_TIMESTAMP",
        )


class NegativeSpreadError(DukascopyDecoderError):
    """Raised when ASK price is strictly lower than BID price."""

    def __init__(self, ask: str, bid: str, tick_index: int) -> None:
        super().__init__(
            f"Negative spread detected at tick index {tick_index}: ASK {ask} < BID {bid}",
            code="DUKASCOPY_NEGATIVE_SPREAD",
        )


class PriceScaleValidationError(DukascopyDecoderError):
    """Raised when prices fall outside sanity bounds due to incorrect point divisor."""

    def __init__(self, price: str, min_price: str, max_price: str, divisor: str) -> None:
        super().__init__(
            f"Price {price} is outside sanity range [{min_price}, {max_price}] with divisor {divisor}",
            code="DUKASCOPY_PRICE_SCALE_ERROR",
        )


# ── Network and Access Exceptions ─────────────────────────────────────────────


class DukascopyNetworkError(DukascopyError):
    """Base exception for network transport and HTTP errors."""

    def __init__(self, message: str, *, code: str = "DUKASCOPY_NETWORK_ERROR") -> None:
        super().__init__(message, code=code)


class DukascopyAccessDeniedError(DukascopyNetworkError):
    """Raised on HTTP 403 Forbidden (e.g. S3 Requester Pays unauthenticated access)."""

    def __init__(self, url: str) -> None:
        super().__init__(
            f"Access denied (HTTP 403) for URL: {url}",
            code="DUKASCOPY_ACCESS_DENIED",
        )


class DukascopyRateLimitError(DukascopyNetworkError):
    """Raised on HTTP 429 Too Many Requests."""

    def __init__(self, url: str, retry_after: str | None = None) -> None:
        msg = f"Rate limit exceeded (HTTP 429) for URL: {url}"
        if retry_after:
            msg += f" (Retry-After: {retry_after})"
        super().__init__(msg, code="DUKASCOPY_RATE_LIMIT")


class DukascopyServerError(DukascopyNetworkError):
    """Raised on HTTP 5xx Server Errors."""

    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(
            f"Dukascopy server error (HTTP {status_code}) for URL: {url}",
            code="DUKASCOPY_SERVER_ERROR",
        )


class InvalidContentTypeError(DukascopyNetworkError):
    """Raised when server returns an unexpected content type (e.g. HTML instead of binary)."""

    def __init__(self, content_type: str, url: str, preview: str = "") -> None:
        msg = f"Invalid Content-Type '{content_type}' received from {url}"
        if preview:
            msg += f" (Body preview: {preview[:150]})"
        super().__init__(msg, code="DUKASCOPY_INVALID_CONTENT_TYPE")


class RegulatoryBlockError(InvalidContentTypeError):
    """Raised when response indicates regulatory or ISP level blocking (e.g. Turkish SPK notice)."""

    def __init__(self, url: str, block_reason: str) -> None:
        super().__init__(
            content_type="text/html",
            url=url,
            preview=f"Regulatory/ISP Block: {block_reason}",
        )
        self.code = "DUKASCOPY_REGULATORY_BLOCK"
