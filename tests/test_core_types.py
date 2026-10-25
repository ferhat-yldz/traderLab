"""Unit tests for core types and enumerations."""

from __future__ import annotations

from datetime import UTC

import pytest

from traderlab.core.types import (
    AvailabilityConfidence,
    DataSourceId,
    InstrumentId,
    MarketType,
    PriceType,
    ProviderId,
    SessionName,
    Timeframe,
    ensure_utc,
    utc_now,
)


class TestTimeframe:
    def test_ordering(self) -> None:
        assert Timeframe.M1 < Timeframe.M5
        assert Timeframe.M5 < Timeframe.H1
        assert Timeframe.H1 < Timeframe.D1
        assert Timeframe.D1 < Timeframe.W1

    def test_ordering_le(self) -> None:
        assert Timeframe.M1 <= Timeframe.M1
        assert Timeframe.M1 <= Timeframe.H4

    def test_value_is_seconds(self) -> None:
        assert Timeframe.M1.value == 60
        assert Timeframe.H1.value == 3600
        assert Timeframe.D1.value == 86400

    def test_ordering_not_implemented_for_other_types(self) -> None:
        with pytest.raises(TypeError):
            _ = Timeframe.M1 < 60  # type: ignore[operator]


class TestMarketType:
    def test_all_markets_exist(self) -> None:
        markets = {m.value for m in MarketType}
        assert "FOREX" in markets
        assert "CRYPTO" in markets
        assert "EQUITY" in markets
        assert "FUTURES" in markets
        assert "INDEX" in markets


class TestSessionName:
    def test_sessions(self) -> None:
        assert SessionName.ASIAN.value == "ASIAN"
        assert SessionName.OVERLAP_LN_NY.value == "OVERLAP_LN_NY"


class TestPriceType:
    def test_all_price_types_exist(self) -> None:
        types = {p.value for p in PriceType}
        assert types == {"BID", "ASK", "MID", "UNKNOWN"}

    def test_distinct_values(self) -> None:
        assert PriceType.BID != PriceType.ASK
        assert PriceType.BID != PriceType.MID
        assert PriceType.MID != PriceType.UNKNOWN


class TestAvailabilityConfidence:
    def test_confidence_levels(self) -> None:
        levels = {c.value for c in AvailabilityConfidence}
        assert levels == {"VERIFIED", "ESTIMATED", "UNKNOWN"}


class TestNewTypes:
    def test_instrument_id_is_str(self) -> None:
        iid = InstrumentId("XAUUSD")
        assert isinstance(iid, str)
        assert iid == "XAUUSD"

    def test_data_source_id_is_str(self) -> None:
        dsid = DataSourceId("broker-1")
        assert isinstance(dsid, str)

    def test_provider_id_is_str(self) -> None:
        pid = ProviderId("tradingview")
        assert isinstance(pid, str)


class TestUtcNow:
    def test_returns_utc_aware(self) -> None:
        now = utc_now()
        assert now.tzinfo is not None
        assert now.tzinfo == UTC


class TestEnsureUtc:
    def test_valid_utc_preserved(self) -> None:
        from datetime import datetime, timedelta, timezone

        tz_zero = timezone(timedelta(0))
        dt = datetime(2024, 1, 15, 12, 0, tzinfo=tz_zero)
        result = ensure_utc(dt)
        assert result.tzinfo == UTC
        assert result.hour == 12

    def test_naive_datetime_rejected(self) -> None:
        from datetime import datetime

        dt = datetime(2024, 1, 15, 12, 0)
        with pytest.raises(ValueError, match="UTC-aware"):
            ensure_utc(dt)

    def test_non_utc_offset_rejected(self) -> None:
        from datetime import datetime, timedelta, timezone

        tz_plus_3 = timezone(timedelta(hours=3))
        dt = datetime(2024, 1, 15, 15, 0, tzinfo=tz_plus_3)
        with pytest.raises(ValueError, match="must be in UTC timezone"):
            ensure_utc(dt)

    def test_zoneinfo_utc_normalized(self) -> None:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        dt = datetime(2024, 1, 15, 12, 0, tzinfo=ZoneInfo("UTC"))
        result = ensure_utc(dt)
        assert result.tzinfo == UTC
        assert result.hour == 12
