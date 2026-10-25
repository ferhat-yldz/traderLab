"""Configuration for the external Market-Data-Lab repository."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from traderlab.infrastructure.providers.market_data_lab.errors import (
    ExternalDirectoryNotFoundError,
    MissingDatasetFileError,
)

# Project root directory for traderlab (contains src/, tests/, .env)
TRADERLAB_PROJECT_ROOT = Path(__file__).resolve().parents[5]


class MarketDataLabSettings(BaseSettings):
    """Configuration settings for accessing external Market-Data-Lab datasets."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    external_xauusd_data_dir: Path = Field(
        default=Path("../Market-Data-Lab"),
        validation_alias="EXTERNAL_XAUUSD_DATA_DIR",
        description="Path to the external sibling Market-Data-Lab repository",
    )

    def resolve_root_dir(self, base_dir: Path | None = None) -> Path:
        """Resolve the absolute path to the Market-Data-Lab root directory.

        Args:
            base_dir: Optional base path for resolving relative paths (defaults to traderlab project root).

        Returns:
            Resolved absolute Path.

        Raises:
            ExternalDirectoryNotFoundError: If the directory does not exist or is not a directory.
        """
        raw_path = self.external_xauusd_data_dir
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        else:
            anchor = base_dir if base_dir is not None else TRADERLAB_PROJECT_ROOT
            resolved = (anchor / raw_path).resolve()

        if not resolved.exists() or not resolved.is_dir():
            raise ExternalDirectoryNotFoundError(str(resolved))

        return resolved

    def get_bid_csv_path(self, year: int, month: int, base_dir: Path | None = None) -> Path:
        """Return the expected path to the M1 BID CSV file."""
        root = self.resolve_root_dir(base_dir)
        return root / "xauusd" / "bid" / "m1" / f"xauusd_bid_m1_{year}_{month:02d}.csv"

    def get_ask_csv_path(self, year: int, month: int, base_dir: Path | None = None) -> Path:
        """Return the expected path to the M1 ASK CSV file."""
        root = self.resolve_root_dir(base_dir)
        return root / "xauusd" / "ask" / "m1" / f"xauusd_ask_m1_{year}_{month:02d}.csv"

    def validate_dataset_files(
        self, year: int = 2024, month: int = 1, base_dir: Path | None = None
    ) -> tuple[Path, Path]:
        """Validate that the directory and both BID and ASK CSV files exist and are readable.

        Returns:
            Tuple of (bid_csv_path, ask_csv_path).

        Raises:
            ExternalDirectoryNotFoundError: If the repository directory is missing.
            MissingDatasetFileError: If either the BID or ASK CSV file is missing.
        """
        bid_path = self.get_bid_csv_path(year, month, base_dir)
        if not bid_path.is_file():
            raise MissingDatasetFileError(str(bid_path), "bid")

        ask_path = self.get_ask_csv_path(year, month, base_dir)
        if not ask_path.is_file():
            raise MissingDatasetFileError(str(ask_path), "ask")

        return bid_path, ask_path


@lru_cache(maxsize=1)
def get_market_data_lab_settings() -> MarketDataLabSettings:
    """Return a cached singleton of MarketDataLabSettings."""
    return MarketDataLabSettings()
