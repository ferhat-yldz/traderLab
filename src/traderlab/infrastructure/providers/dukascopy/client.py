"""HTTP client and URL builder for Dukascopy data access."""

from __future__ import annotations

import http.client
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from traderlab.core.types import ensure_utc
from traderlab.infrastructure.providers.dukascopy.decoder import DukascopyResolution
from traderlab.infrastructure.providers.dukascopy.errors import (
    DukascopyAccessDeniedError,
    DukascopyNetworkError,
    DukascopyRateLimitError,
    DukascopyServerError,
    InvalidContentTypeError,
    RegulatoryBlockError,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://datafeed.dukascopy.com/datafeed"
S3_BUCKET_NAME = "cfg-public-proper-wallaby"
S3_REGION = "eu-west-1"


def build_dukascopy_url(
    symbol: str,
    dt_utc: datetime,
    *,
    resolution: DukascopyResolution = DukascopyResolution.HOURLY,
    base_url: str = DEFAULT_BASE_URL,
) -> str:
    """Build a Dukascopy datafeed URL with zero-indexed month convention.

    Args:
        symbol: Financial instrument symbol (e.g. 'XAUUSD').
        dt_utc: UTC datetime representing the archive timestamp.
        resolution: HOURLY (default) or DAILY.
        base_url: Base HTTP endpoint.

    Returns:
        Complete URL string.
    """
    dt = ensure_utc(dt_utc)
    sym = symbol.upper()
    year = dt.year
    month_0 = dt.month - 1  # 00 = January, 11 = December
    day = dt.day

    if resolution == DukascopyResolution.HOURLY:
        hour = dt.hour
        return f"{base_url.rstrip('/')}/{sym}/{year}/{month_0:02d}/{day:02d}/{hour:02d}h_ticks.bi5"

    return f"{base_url.rstrip('/')}/{sym}/{year}/{month_0:02d}/{day:02d}_ticks.bi5"


def build_dukascopy_s3_key(
    symbol: str,
    dt_utc: datetime,
    *,
    resolution: DukascopyResolution = DukascopyResolution.DAILY,
) -> str:
    """Build an AWS S3 object key for the Dukascopy Requester-Pays bucket.

    Args:
        symbol: Financial instrument symbol (e.g. 'XAUUSD').
        dt_utc: UTC datetime.
        resolution: DAILY (standard for S3 archive) or HOURLY.

    Returns:
        S3 key string without leading slash.
    """
    dt = ensure_utc(dt_utc)
    sym = symbol.upper()
    year = dt.year
    month_0 = dt.month - 1
    day = dt.day

    if resolution == DukascopyResolution.HOURLY:
        hour = dt.hour
        return f"{sym}/{year}/{month_0:02d}/{day:02d}/{hour:02d}h_ticks.bi5"

    return f"{sym}/{year}/{month_0:02d}/{day:02d}_ticks.bi5"


class DukascopyHttpClient:
    """Controlled HTTP client for retrieving Dukascopy binary archives.

    Implements strict timeout, exponential backoff, response size limits,
    and content-type verification to prevent ISP block pages from being
    treated as valid market data.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        backoff_factor: float = 0.5,
        max_size_bytes: int = 20 * 1024 * 1024,  # 20 MB max
        user_agent: str = "TraderLab/1.0 (Financial Data Infrastructure)",
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.max_size_bytes = max_size_bytes
        self.user_agent = user_agent

    def fetch_archive(self, url: str) -> bytes | None:
        """Fetch a single .bi5 archive over HTTP.

        Args:
            url: The HTTP/HTTPS URL of the .bi5 file.

        Returns:
            Raw bytes of the file, or None if the resource was not found (HTTP 404).

        Raises:
            DukascopyAccessDeniedError: On HTTP 403.
            DukascopyRateLimitError: On HTTP 429.
            DukascopyServerError: On HTTP 5xx.
            RegulatoryBlockError: If response body contains ISP/regulatory block notices.
            InvalidContentTypeError: If response Content-Type is HTML/text instead of binary.
            DukascopyNetworkError: On network timeouts, connection drops, or retry exhaustions.
        """
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/octet-stream, */*",
        }

        req = urllib.request.Request(url, headers=headers, method="GET")

        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    content_type = resp.headers.get("Content-Type", "")
                    raw_content = resp.read(self.max_size_bytes + 1)
                    body: bytes = (
                        raw_content if isinstance(raw_content, bytes) else bytes(raw_content)
                    )
                    if len(body) > self.max_size_bytes:
                        raise DukascopyNetworkError(
                            f"Response size exceeded limit of {self.max_size_bytes} bytes"
                        )

                    # Strict content validation: prevent HTML block pages
                    ct_lower = content_type.lower()
                    if (
                        "html" in ct_lower
                        or body.startswith(b"<!DOCTYPE")
                        or body.startswith(b"<html")
                    ):
                        preview = body[:300].decode("utf-8", errors="replace")
                        if "erisime_engellenmis" in preview or "Sermaye Piyasas" in preview:
                            raise RegulatoryBlockError(
                                url, "Turkish CMB/SPK judicial access block detected"
                            )
                        raise InvalidContentTypeError(content_type, url, preview)

                    return body

            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    logger.debug(f"HTTP 404 Not Found for {url} (no ticks for this period)")
                    return None
                if exc.code == 403:
                    raise DukascopyAccessDeniedError(url) from exc
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    raise DukascopyRateLimitError(url, retry_after) from exc
                if 500 <= exc.code < 600:
                    if attempt < self.max_retries:
                        sleep_time = self.backoff_factor * (2**attempt)
                        logger.warning(
                            f"HTTP {exc.code} from {url}, retrying in {sleep_time:.1f}s (attempt {attempt + 1})"
                        )
                        time.sleep(sleep_time)
                        continue
                    raise DukascopyServerError(exc.code, url) from exc
                raise DukascopyNetworkError(
                    f"HTTP error {exc.code}: {exc.reason} for {url}"
                ) from exc

            except (TimeoutError, urllib.error.URLError, http.client.HTTPException) as exc:
                if attempt < self.max_retries:
                    sleep_time = self.backoff_factor * (2**attempt)
                    logger.warning(
                        f"Network failure for {url}: {exc}, retrying in {sleep_time:.1f}s"
                    )
                    time.sleep(sleep_time)
                    continue
                raise DukascopyNetworkError(f"Network error accessing {url}: {exc}") from exc

        raise DukascopyNetworkError(f"Failed to fetch {url} after {self.max_retries} retries")
