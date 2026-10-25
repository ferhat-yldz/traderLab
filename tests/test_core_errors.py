"""Unit tests for the exception hierarchy."""

from __future__ import annotations

import pytest

from traderlab.core.errors import (
    ConfigurationError,
    DatabaseConnectionError,
    DomainError,
    InfrastructureError,
    InstrumentNotFoundError,
    InvalidCandleError,
    ProviderError,
    TimeframeAlignmentError,
    TraderLabError,
)


class TestExceptionHierarchy:
    """Verify that all exceptions inherit correctly and carry codes."""

    def test_root_exception(self) -> None:
        err = TraderLabError("boom")
        assert str(err) == "boom"
        assert err.code == "TRADERLAB_ERROR"
        assert isinstance(err, Exception)

    def test_domain_error(self) -> None:
        err = DomainError("rule violated")
        assert isinstance(err, TraderLabError)
        assert err.code == "DOMAIN_ERROR"

    def test_instrument_not_found(self) -> None:
        err = InstrumentNotFoundError("XAUUSD")
        assert isinstance(err, DomainError)
        assert err.code == "INSTRUMENT_NOT_FOUND"
        assert "XAUUSD" in str(err)

    def test_invalid_candle(self) -> None:
        err = InvalidCandleError("high < low")
        assert isinstance(err, DomainError)
        assert err.code == "INVALID_CANDLE"

    def test_timeframe_alignment(self) -> None:
        err = TimeframeAlignmentError("misaligned")
        assert isinstance(err, DomainError)
        assert err.code == "TIMEFRAME_ALIGNMENT"

    def test_infrastructure_error(self) -> None:
        err = InfrastructureError("network timeout")
        assert isinstance(err, TraderLabError)
        assert err.code == "INFRA_ERROR"

    def test_database_connection_error(self) -> None:
        err = DatabaseConnectionError("refused")
        assert isinstance(err, InfrastructureError)
        assert err.code == "DB_CONNECTION_FAILED"

    def test_database_connection_error_no_detail(self) -> None:
        err = DatabaseConnectionError()
        assert "Database connection failed" in str(err)

    def test_provider_error(self) -> None:
        err = ProviderError("ccxt", "rate limited")
        assert isinstance(err, InfrastructureError)
        assert err.code == "PROVIDER_ERROR"
        assert "ccxt" in str(err)

    def test_configuration_error(self) -> None:
        err = ConfigurationError("missing DB_HOST")
        assert isinstance(err, TraderLabError)
        assert err.code == "CONFIGURATION_ERROR"

    def test_catch_all_with_root(self) -> None:
        """All custom exceptions can be caught with TraderLabError."""
        exceptions = [
            DomainError("a"),
            InstrumentNotFoundError("b"),
            InvalidCandleError("c"),
            InfrastructureError("d"),
            DatabaseConnectionError("e"),
            ProviderError("f", "g"),
            ConfigurationError("h"),
        ]
        for exc in exceptions:
            with pytest.raises(TraderLabError):
                raise exc
