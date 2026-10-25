"""Domain-specific exception hierarchy.

All custom exceptions inherit from ``TraderLabError`` so that callers can
catch the entire family with a single ``except`` clause when appropriate.

Subclasses are organised by bounded context:
    - ``DomainError``       — invariant violations inside domain logic
    - ``InfrastructureError`` — failures in external adapters (DB, network)
    - ``ConfigurationError``  — missing or invalid configuration

Each concrete exception carries a human-readable ``message`` **and** a
machine-friendly ``code`` string (e.g. ``"INSTRUMENT_NOT_FOUND"``) that
can be used in API responses or logging without leaking implementation
details.
"""

from __future__ import annotations


class TraderLabError(Exception):
    """Root of the TraderLab exception hierarchy."""

    def __init__(self, message: str, *, code: str = "TRADERLAB_ERROR") -> None:
        super().__init__(message)
        self.code = code


# ── Domain ──────────────────────────────────────────────────────────────────
class DomainError(TraderLabError):
    """A business-rule or invariant violation."""

    def __init__(self, message: str, *, code: str = "DOMAIN_ERROR") -> None:
        super().__init__(message, code=code)


class InstrumentNotFoundError(DomainError):
    def __init__(self, identifier: str) -> None:
        super().__init__(
            f"Instrument not found: {identifier}",
            code="INSTRUMENT_NOT_FOUND",
        )


class InvalidCandleError(DomainError):
    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Invalid candle data: {reason}",
            code="INVALID_CANDLE",
        )


class TimeframeAlignmentError(DomainError):
    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Timeframe alignment error: {reason}",
            code="TIMEFRAME_ALIGNMENT",
        )


# ── Infrastructure ─────────────────────────────────────────────────────────
class InfrastructureError(TraderLabError):
    """Failure in an external adapter (database, network, file-system)."""

    def __init__(self, message: str, *, code: str = "INFRA_ERROR") -> None:
        super().__init__(message, code=code)


class DatabaseConnectionError(InfrastructureError):
    def __init__(self, detail: str = "") -> None:
        msg = "Database connection failed"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg, code="DB_CONNECTION_FAILED")


class ProviderError(InfrastructureError):
    def __init__(self, provider: str, detail: str) -> None:
        super().__init__(
            f"Provider '{provider}' error: {detail}",
            code="PROVIDER_ERROR",
        )


# ── Configuration ──────────────────────────────────────────────────────────
class ConfigurationError(TraderLabError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="CONFIGURATION_ERROR")
