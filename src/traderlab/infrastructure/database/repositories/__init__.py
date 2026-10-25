"""Database repositories module."""

from traderlab.infrastructure.database.repositories.candle_repository import (
    PostgresCandleRepository,
)

__all__ = ["PostgresCandleRepository"]
