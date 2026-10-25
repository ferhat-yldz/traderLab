"""Market-Data-Lab external historical dataset provider."""

from __future__ import annotations

from traderlab.infrastructure.providers.market_data_lab.bar_classifier import (
    BarQualityClassification,
    ClassifiedBar,
    classify_m1_series,
)
from traderlab.infrastructure.providers.market_data_lab.config import (
    TRADERLAB_PROJECT_ROOT,
    MarketDataLabSettings,
    get_market_data_lab_settings,
)
from traderlab.infrastructure.providers.market_data_lab.csv_reader import (
    EXPECTED_CSV_HEADERS,
    MarketDataLabBar,
    MergedBidAskBar,
    ValidationSummary,
    bar_to_candle,
    merge_bid_ask_m1,
    read_m1_csv,
    validate_month_data,
)
from traderlab.infrastructure.providers.market_data_lab.errors import (
    ExternalDirectoryNotFoundError,
    InvalidCandleDataError,
    InvalidCsvSchemaError,
    MarketDataLabError,
    MissingDatasetFileError,
    NegativeSpreadError,
    TimestampAlignmentError,
)
from traderlab.infrastructure.providers.market_data_lab.inventory import (
    FileInventoryRecord,
    LocalDataInventory,
    LocalInventorySummary,
)
from traderlab.infrastructure.providers.market_data_lab.remote_auditor import (
    RemoteFreshnessAuditor,
    RemoteFreshnessReport,
)

__all__ = [
    "EXPECTED_CSV_HEADERS",
    "TRADERLAB_PROJECT_ROOT",
    "BarQualityClassification",
    "ClassifiedBar",
    "ExternalDirectoryNotFoundError",
    "FileInventoryRecord",
    "InvalidCandleDataError",
    "InvalidCsvSchemaError",
    "LocalDataInventory",
    "LocalInventorySummary",
    "MarketDataLabBar",
    "MarketDataLabError",
    "MarketDataLabSettings",
    "MergedBidAskBar",
    "MissingDatasetFileError",
    "NegativeSpreadError",
    "RemoteFreshnessAuditor",
    "RemoteFreshnessReport",
    "TimestampAlignmentError",
    "ValidationSummary",
    "bar_to_candle",
    "classify_m1_series",
    "get_market_data_lab_settings",
    "merge_bid_ask_m1",
    "read_m1_csv",
    "validate_month_data",
]
