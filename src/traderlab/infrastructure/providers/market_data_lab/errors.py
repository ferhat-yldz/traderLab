"""Exceptions for Market-Data-Lab external CSV dataset access and validation."""

from __future__ import annotations

from traderlab.core.errors import InfrastructureError


class MarketDataLabError(InfrastructureError):
    """Base exception for external Market-Data-Lab dataset operations."""

    def __init__(self, message: str, *, code: str = "MARKET_DATA_LAB_ERROR") -> None:
        super().__init__(message, code=code)


class ExternalDirectoryNotFoundError(MarketDataLabError):
    """Raised when the external Market-Data-Lab directory does not exist."""

    def __init__(self, path: str) -> None:
        super().__init__(
            f"External Market-Data-Lab directory not found at: {path}",
            code="EXTERNAL_DIR_NOT_FOUND",
        )


class MissingDatasetFileError(MarketDataLabError):
    """Raised when expected CSV files (bid/ask) are missing in the external repository."""

    def __init__(self, file_path: str, side: str) -> None:
        super().__init__(
            f"Missing required {side.upper()} dataset file at: {file_path}",
            code="MISSING_DATASET_FILE",
        )


class InvalidCsvSchemaError(MarketDataLabError):
    """Raised when CSV headers or columns do not match expected schema."""

    def __init__(self, actual_headers: list[str], expected_headers: list[str]) -> None:
        super().__init__(
            f"Invalid CSV headers: got {actual_headers}, expected {expected_headers}",
            code="INVALID_CSV_SCHEMA",
        )


class InvalidCandleDataError(MarketDataLabError):
    """Raised when candle data violates financial consistency (OHLC logic, non-positive price)."""

    def __init__(self, reason: str, line_num: int | None = None) -> None:
        msg = f"Invalid candle data: {reason}"
        if line_num is not None:
            msg += f" (line {line_num})"
        super().__init__(msg, code="INVALID_CANDLE_DATA")


class TimestampAlignmentError(MarketDataLabError):
    """Raised when timestamp is not aligned to minute boundary or is non-monotonic."""

    def __init__(self, detail: str, line_num: int | None = None) -> None:
        msg = f"Timestamp alignment error: {detail}"
        if line_num is not None:
            msg += f" (line {line_num})"
        super().__init__(msg, code="TIMESTAMP_ALIGNMENT_ERROR")


class NegativeSpreadError(MarketDataLabError):
    """Raised when ASK price is strictly lower than BID price on matching timestamp."""

    def __init__(self, timestamp: str, ask_close: str, bid_close: str) -> None:
        super().__init__(
            f"Negative spread at timestamp {timestamp}: ASK close {ask_close} < BID close {bid_close}",
            code="NEGATIVE_SPREAD_ERROR",
        )
