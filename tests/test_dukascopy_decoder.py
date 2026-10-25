"""Unit and regression tests for Dukascopy binary decoding, URL generation, and HTTP client."""

from __future__ import annotations

import io
import lzma
import struct
import urllib.error
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from traderlab.infrastructure.providers.dukascopy.client import (
    DEFAULT_BASE_URL,
    DukascopyHttpClient,
    build_dukascopy_s3_key,
    build_dukascopy_url,
)
from traderlab.infrastructure.providers.dukascopy.decoder import (
    DukascopyResolution,
    DukascopyTick,
    decode_bi5_ticks,
    encode_bi5_ticks,
)
from traderlab.infrastructure.providers.dukascopy.errors import (
    CorruptedBinaryError,
    DukascopyAccessDeniedError,
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

# ── Fixtures & Helpers ────────────────────────────────────────────────────────


@pytest.fixture
def base_hour_utc() -> datetime:
    """Standard base hour for XAUUSD tests (2024-01-16 12:00:00 UTC)."""
    return datetime(2024, 1, 16, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def sample_gold_ticks(base_hour_utc: datetime) -> list[DukascopyTick]:
    """Sample realistic XAUUSD ticks around $2030.50 with 3 decimal precision."""
    divisor = Decimal("1000")
    return [
        DukascopyTick(
            time=base_hour_utc + timedelta(milliseconds=150),
            ask=Decimal("2030.550"),
            bid=Decimal("2030.300"),
            spread=Decimal("0.250"),
            ask_quote_volume=1.5,
            bid_quote_volume=2.0,
            point_divisor=divisor,
        ),
        DukascopyTick(
            time=base_hour_utc + timedelta(milliseconds=2500),
            ask=Decimal("2030.600"),
            bid=Decimal("2030.350"),
            spread=Decimal("0.250"),
            ask_quote_volume=3.25,
            bid_quote_volume=1.80,
            point_divisor=divisor,
        ),
        DukascopyTick(
            time=base_hour_utc + timedelta(milliseconds=3599990),
            ask=Decimal("2031.100"),
            bid=Decimal("2030.900"),
            spread=Decimal("0.200"),
            ask_quote_volume=0.75,
            bid_quote_volume=1.10,
            point_divisor=divisor,
        ),
    ]


# ── Decoder Tests ─────────────────────────────────────────────────────────────


class TestDukascopyDecoder:
    """Tests for binary .bi5 LZMA decoding and record validation."""

    def test_decode_empty_bytes_returns_empty_list(self, base_hour_utc: datetime) -> None:
        """An empty byte stream represents a period with zero ticks."""
        ticks = decode_bi5_ticks(b"", base_hour_utc)
        assert ticks == []

    def test_decode_empty_decompressed_payload(self, base_hour_utc: datetime) -> None:
        """An LZMA archive containing empty decompressed bytes returns an empty list."""
        empty_compressed = lzma.compress(b"")
        ticks = decode_bi5_ticks(empty_compressed, base_hour_utc)
        assert ticks == []

    def test_corrupted_lzma_bytes_raises_error(self, base_hour_utc: datetime) -> None:
        """Corrupted or truncated LZMA archive raises CorruptedBinaryError."""
        garbage = b"NOT_A_VALID_LZMA_STREAM_HEADER"
        with pytest.raises(CorruptedBinaryError, match="LZMA decompression error"):
            decode_bi5_ticks(garbage, base_hour_utc)

    def test_invalid_record_size_not_multiple_of_20_bytes(self, base_hour_utc: datetime) -> None:
        """Decompressed payload with 19 or 21 bytes raises InvalidRecordSizeError."""
        # 19 bytes is not a multiple of 20
        bad_payload = b"\x00" * 19
        compressed = lzma.compress(bad_payload)
        with pytest.raises(InvalidRecordSizeError, match="19 bytes is not a multiple of 20"):
            decode_bi5_ticks(compressed, base_hour_utc)

    def test_naive_base_datetime_rejected(self) -> None:
        """Base datetime must be timezone-aware UTC."""
        naive_dt = datetime(2024, 1, 16, 12, 0, 0)
        with pytest.raises(ValueError, match="must be UTC-aware"):
            decode_bi5_ticks(b"", naive_dt)

    def test_encode_and_decode_roundtrip_gold_ticks(
        self, base_hour_utc: datetime, sample_gold_ticks: list[DukascopyTick]
    ) -> None:
        """Verify perfect roundtrip fidelity: encode -> decode -> identical ticks."""
        divisor = Decimal("1000")
        compressed = encode_bi5_ticks(sample_gold_ticks, base_hour_utc, point_divisor=divisor)

        assert len(compressed) > 0
        decoded = decode_bi5_ticks(compressed, base_hour_utc, point_divisor=divisor)

        assert len(decoded) == len(sample_gold_ticks)
        for original, parsed in zip(sample_gold_ticks, decoded, strict=True):
            assert parsed.time == original.time
            assert parsed.ask == original.ask
            assert parsed.bid == original.bid
            assert parsed.spread == original.spread
            assert pytest.approx(parsed.ask_quote_volume) == original.ask_quote_volume
            assert pytest.approx(parsed.bid_quote_volume) == original.bid_quote_volume
            assert parsed.point_divisor == divisor

    def test_decode_saved_synthetic_fixture(self, base_hour_utc: datetime) -> None:
        """Verify reading and decoding the saved synthetic pilot file from data/raw/."""
        import hashlib
        from pathlib import Path

        fixture_path = Path("data/raw/synthetic/XAUUSD/2024/00/16/12h_ticks.bi5")
        if not fixture_path.exists():
            pytest.skip("Synthetic fixture file does not exist")

        raw_bytes = fixture_path.read_bytes()
        expected_sha256 = "07261b71bb60f1e30693cdfc164a617fd288ad9a64ab7bf77fdc548c07de1733"
        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        assert actual_sha256 == expected_sha256

        ticks = decode_bi5_ticks(
            raw_bytes,
            base_hour_utc,
            point_divisor=Decimal("1000"),
            min_price=Decimal("2000"),
            max_price=Decimal("2100"),
        )
        assert len(ticks) == 100
        assert all(t.spread >= Decimal("0") for t in ticks)
        assert all(t.ask >= t.bid for t in ticks)
        assert all(Decimal("2000") <= t.ask <= Decimal("2100") for t in ticks)

    def test_hourly_timestamp_boundary_exceeded_raises_error(
        self, base_hour_utc: datetime
    ) -> None:
        """Tick offset >= 3,600,000 ms in hourly mode raises InvalidTimestampError."""
        # Construct single 20-byte record with offset 3,600,000 ms (1 hour exactly)
        bad_offset = 3_600_000
        raw_record = struct.pack(">IIIff", bad_offset, 2030500, 2030200, 1.0, 1.0)
        compressed = lzma.compress(raw_record)

        with pytest.raises(InvalidTimestampError, match="3600000 ms exceeds maximum"):
            decode_bi5_ticks(compressed, base_hour_utc, resolution=DukascopyResolution.HOURLY)

    def test_daily_timestamp_boundary_handling(self) -> None:
        """Daily resolution allows offsets up to 86,399,999 ms, rejects 86,400,000 ms."""
        base_day = datetime(2024, 1, 16, 0, 0, 0, tzinfo=UTC)

        # Valid tick at 12 hours (43,200,000 ms)
        valid_offset = 43_200_000
        valid_chunk = struct.pack(">IIIff", valid_offset, 2030500, 2030200, 1.0, 1.0)
        compressed = lzma.compress(valid_chunk)
        ticks = decode_bi5_ticks(compressed, base_day, resolution=DukascopyResolution.DAILY)
        assert len(ticks) == 1
        assert ticks[0].time == datetime(2024, 1, 16, 12, 0, 0, tzinfo=UTC)

        # Invalid tick at 86,400,000 ms (24 hours)
        invalid_chunk = struct.pack(">IIIff", 86_400_000, 2030500, 2030200, 1.0, 1.0)
        compressed_bad = lzma.compress(invalid_chunk)
        with pytest.raises(InvalidTimestampError, match="86400000 ms exceeds maximum"):
            decode_bi5_ticks(compressed_bad, base_day, resolution=DukascopyResolution.DAILY)

    def test_negative_spread_rejection(self, base_hour_utc: datetime) -> None:
        """Decoder strictly rejects inverted book where ASK < BID."""
        # ASK = 2030.000, BID = 2030.500 (inverted spread -0.500)
        raw_record = struct.pack(">IIIff", 1000, 2030000, 2030500, 1.0, 1.0)
        compressed = lzma.compress(raw_record)

        with pytest.raises(
            NegativeSpreadError,
            match=r"Negative spread detected at tick index 0: ASK 2030.* < BID 2030\.5",
        ):
            decode_bi5_ticks(compressed, base_hour_utc, point_divisor=Decimal("1000"))

    def test_negative_spread_rejected_in_dataclass_post_init(self) -> None:
        """DukascopyTick dataclass __post_init__ guarantees invariant ASK >= BID."""
        with pytest.raises(NegativeSpreadError):
            DukascopyTick(
                time=datetime.now(tz=UTC),
                ask=Decimal("2000.00"),
                bid=Decimal("2005.00"),  # bid > ask
                spread=Decimal("-5.00"),
                ask_quote_volume=1.0,
                bid_quote_volume=1.0,
                point_divisor=Decimal("1000"),
            )

    def test_price_scale_sanity_validation(self, base_hour_utc: datetime) -> None:
        """If divisor 100,000 is accidentally used for XAUUSD instead of 1,000, sanity check fails."""
        # Raw price is 2,030,500 ($2030.500). If divided by 100,000, price becomes $20.305!
        raw_record = struct.pack(">IIIff", 1000, 2030500, 2030200, 1.0, 1.0)
        compressed = lzma.compress(raw_record)

        # Expected gold price range for 2024 is [$1500, $3000]
        with pytest.raises(PriceScaleValidationError, match="outside sanity range"):
            decode_bi5_ticks(
                compressed,
                base_hour_utc,
                point_divisor=Decimal("100000"),  # WRONG DIVISOR!
                min_price=Decimal("1500.00"),
                max_price=Decimal("3000.00"),
            )

    def test_invalid_point_divisor_rejected(self, base_hour_utc: datetime) -> None:
        """Zero or negative divisor is rejected."""
        with pytest.raises(ValueError, match="point_divisor must be strictly positive"):
            decode_bi5_ticks(b"", base_hour_utc, point_divisor=Decimal("0"))

    def test_forex_eurusd_point_divisor(self, base_hour_utc: datetime) -> None:
        """Verify standard 5-decimal FX pair (EURUSD) with divisor 100,000."""
        # 1.08550 ask, 1.08535 bid (spread 0.00015 = 1.5 pips)
        raw_record = struct.pack(">IIIff", 500, 108550, 108535, 2.5, 3.0)
        compressed = lzma.compress(raw_record)

        ticks = decode_bi5_ticks(compressed, base_hour_utc, point_divisor=Decimal("100000"))
        assert len(ticks) == 1
        assert ticks[0].ask == Decimal("1.08550")
        assert ticks[0].bid == Decimal("1.08535")
        assert ticks[0].spread == Decimal("0.00015")
        assert ticks[0].ask_quote_volume == 2.5
        assert ticks[0].bid_quote_volume == 3.0


# ── URL and S3 Key Generation Tests ───────────────────────────────────────────


class TestDukascopyUrlBuilder:
    """Tests for URL and S3 path generation with 0-indexed month convention."""

    def test_hourly_url_format(self) -> None:
        """Verify January is month 00, hour is padded, symbol is uppercase."""
        dt = datetime(2024, 1, 16, 12, 30, tzinfo=UTC)
        url = build_dukascopy_url("xauusd", dt, resolution=DukascopyResolution.HOURLY)
        expected = f"{DEFAULT_BASE_URL}/XAUUSD/2024/00/16/12h_ticks.bi5"
        assert url == expected

    def test_december_month_is_11(self) -> None:
        """Verify December is month 11."""
        dt = datetime(2023, 12, 31, 23, 0, tzinfo=UTC)
        url = build_dukascopy_url("EURUSD", dt, resolution=DukascopyResolution.HOURLY)
        expected = f"{DEFAULT_BASE_URL}/EURUSD/2023/11/31/23h_ticks.bi5"
        assert url == expected

    def test_daily_url_format(self) -> None:
        """Verify daily URL path format."""
        dt = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
        url = build_dukascopy_url("XAUUSD", dt, resolution=DukascopyResolution.DAILY)
        expected = f"{DEFAULT_BASE_URL}/XAUUSD/2024/00/16_ticks.bi5"
        assert url == expected

    def test_s3_key_format(self) -> None:
        """Verify S3 key matching Dukascopy Requester-Pays bucket convention."""
        dt = datetime(2024, 1, 16, 0, 0, tzinfo=UTC)
        key = build_dukascopy_s3_key("XAUUSD", dt, resolution=DukascopyResolution.DAILY)
        assert key == "XAUUSD/2024/00/16_ticks.bi5"


# ── HTTP Client Transport Tests ───────────────────────────────────────────────


class DummyHttpResponse:
    """Mock for urllib.request HTTP response."""

    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = io.BytesIO(body)
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        return self._body.read(n)

    def __enter__(self) -> DummyHttpResponse:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class TestDukascopyHttpClient:
    """Tests for HTTP transport, error handling, and regulatory block detection."""

    def test_fetch_success_returns_raw_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 200 with octet-stream returns exact binary payload."""
        expected_bytes = b"BINARY_PAYLOAD_CONTENT"

        def mock_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> DummyHttpResponse:
            return DummyHttpResponse(
                status=200,
                body=expected_bytes,
                headers={"Content-Type": "application/octet-stream"},
            )

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = DukascopyHttpClient()
        data = client.fetch_archive("https://example.com/test.bi5")
        assert data == expected_bytes

    def test_fetch_404_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 404 indicates no tick data for that period and returns None."""

        def mock_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> DummyHttpResponse:
            raise urllib.error.HTTPError("http://example.com", 404, "Not Found", {}, io.BytesIO())

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = DukascopyHttpClient()
        result = client.fetch_archive("https://example.com/missing.bi5")
        assert result is None

    def test_fetch_403_raises_access_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 403 raises DukascopyAccessDeniedError."""

        def mock_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> DummyHttpResponse:
            raise urllib.error.HTTPError("http://example.com", 403, "Forbidden", {}, io.BytesIO())

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = DukascopyHttpClient()
        with pytest.raises(DukascopyAccessDeniedError):
            client.fetch_archive("https://example.com/forbidden.bi5")

    def test_fetch_429_raises_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 429 raises DukascopyRateLimitError."""

        def mock_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> DummyHttpResponse:
            raise urllib.error.HTTPError(
                "http://example.com",
                429,
                "Too Many Requests",
                {"Retry-After": "60"},
                io.BytesIO(),
            )

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = DukascopyHttpClient()
        with pytest.raises(DukascopyRateLimitError, match="Rate limit exceeded"):
            client.fetch_archive("https://example.com/busy.bi5")

    def test_fetch_500_raises_server_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 500 server error exhausts retries and raises DukascopyServerError."""

        def mock_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> DummyHttpResponse:
            raise urllib.error.HTTPError(
                "http://example.com", 500, "Internal Server Error", {}, io.BytesIO()
            )

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = DukascopyHttpClient(max_retries=1, backoff_factor=0.01)
        with pytest.raises(DukascopyServerError):
            client.fetch_archive("https://example.com/error.bi5")

    def test_fetch_html_regulatory_block_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTML body containing Turkish SPK / erisime_engellenmis raises RegulatoryBlockError."""
        spk_html = b"<html><head><title>Access Blocked</title></head><body><div class='erisime_engellenmis'>Sermaye Piyasasi Kurulu karariyla erisime engellenmistir.</div></body></html>"

        def mock_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> DummyHttpResponse:
            return DummyHttpResponse(
                status=200,
                body=spk_html,
                headers={"Content-Type": "text/html; charset=utf-8"},
            )

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = DukascopyHttpClient()
        with pytest.raises(
            RegulatoryBlockError, match="Turkish CMB/SPK judicial access block detected"
        ):
            client.fetch_archive(
                "https://datafeed.dukascopy.com/datafeed/XAUUSD/2024/00/16/12h_ticks.bi5"
            )

    def test_fetch_generic_html_raises_invalid_content_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Arbitrary HTML response raises InvalidContentTypeError."""
        html_page = b"<!DOCTYPE html><html><body>Error 404 Custom Webpage</body></html>"

        def mock_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> DummyHttpResponse:
            return DummyHttpResponse(
                status=200,
                body=html_page,
                headers={"Content-Type": "text/html"},
            )

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = DukascopyHttpClient()
        with pytest.raises(InvalidContentTypeError, match="Invalid Content-Type 'text/html'"):
            client.fetch_archive("https://example.com/not_bi5.bi5")

    def test_fetch_response_exceeds_max_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Payload exceeding max_size_bytes raises DukascopyNetworkError."""
        huge_body = b"\x00" * 105

        def mock_urlopen(req: urllib.request.Request, timeout: float = 10.0) -> DummyHttpResponse:
            return DummyHttpResponse(
                status=200,
                body=huge_body,
                headers={"Content-Type": "application/octet-stream"},
            )

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
        client = DukascopyHttpClient(max_size_bytes=100)
        with pytest.raises(DukascopyNetworkError, match="Response size exceeded limit"):
            client.fetch_archive("https://example.com/huge.bi5")
