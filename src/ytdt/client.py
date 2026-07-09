"""HTTP client for the YouTube Data API v3.

One :class:`YouTubeClient` instance is shared by all modules. It provides:

- ``get()``      — a single API call with retry/backoff and typed errors
- ``paginate()`` — a generator that follows ``nextPageToken``
- ``map()``      — order-preserving threaded map for parallel fetching

The client keeps a running count of HTTP calls and an estimate of quota
units spent (search costs 100 units, everything else 1), so frontends can
show usage and users can budget their daily quota.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, Iterator, TypeVar

import requests

from .errors import (
    APIError,
    ConfigurationError,
    FATAL_REASONS,
    QuotaExceededError,
    RETRYABLE_REASONS,
    SKIPPABLE_REASONS,
)

logger = logging.getLogger("ytdt")

T = TypeVar("T")
R = TypeVar("R")

# Quota units per call, per endpoint (default 1).
QUOTA_COSTS = {"search": 100}

# search.list stops serving pages after roughly 500 results; searches
# always paginate to this ceiling (10 pages = up to 1000 quota units).
SEARCH_MAX_RESULTS = 500

ProgressCallback = Callable[[str, int, int], None]


class YouTubeClient:
    BASE_URL = "https://www.googleapis.com/youtube/v3/"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        max_workers: int = 8,
        max_retries: int = 5,
        timeout: float = 30.0,
        session: requests.Session | None = None,
        on_progress: ProgressCallback | None = None,
    ):
        self.api_key = api_key or os.environ.get("YTDT_API_KEY") or os.environ.get("YOUTUBE_API_KEY")
        if not self.api_key:
            raise ConfigurationError(
                "No API key. Pass api_key= or set the YTDT_API_KEY environment variable."
            )
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.timeout = timeout
        self.on_progress = on_progress
        if session is None:
            session = requests.Session()
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=max_workers, pool_maxsize=max_workers
            )
            session.mount("https://", adapter)
        self.session = session
        self._lock = threading.Lock()
        # caps in-flight HTTP requests to max_workers even when map() calls
        # nest (e.g. bulk comment fetching parallelizes per video and per
        # reply thread)
        self._request_slots = threading.BoundedSemaphore(max_workers)
        self.call_count = 0
        self.quota_used = 0

    # -- transport ---------------------------------------------------------

    def _http_get(self, endpoint: str, params: dict[str, Any]) -> tuple[int, dict]:
        """Perform one HTTP GET; returns (status_code, decoded JSON). Test seam."""
        response = self.session.get(self.BASE_URL + endpoint, params=params, timeout=self.timeout)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return response.status_code, payload

    def get(self, endpoint: str, **params: Any) -> dict:
        """Call an API endpoint, retrying transient failures with backoff.

        Raises :class:`QuotaExceededError` when the daily quota is gone,
        a :class:`SkippableError` subclass for per-item problems, and
        :class:`APIError` for anything else.
        """
        params = {k: v for k, v in params.items() if v is not None and v != ""}
        params["key"] = self.api_key

        delay = 1.0
        last_error: APIError | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(delay + random.uniform(0, delay / 2))
                delay = min(delay * 2, 30.0)

            with self._lock:
                self.call_count += 1
                self.quota_used += QUOTA_COSTS.get(endpoint, 1)

            try:
                with self._request_slots:
                    status, payload = self._http_get(endpoint, params)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = APIError(f"Connection error calling {endpoint}: {exc}")
                logger.warning("%s (attempt %d/%d)", last_error, attempt + 1, self.max_retries + 1)
                continue

            error = payload.get("error")
            if not error:
                return payload

            details = (error.get("errors") or [{}])[0]
            reason = details.get("reason", "")
            message = error.get("message") or details.get("message") or reason

            if reason in FATAL_REASONS:
                raise QuotaExceededError(
                    f"YouTube API quota exceeded: {message}", reason=reason, status=status
                )
            if reason in SKIPPABLE_REASONS:
                raise SKIPPABLE_REASONS[reason](
                    f"{endpoint}: {message}", reason=reason, status=status
                )
            if reason in RETRYABLE_REASONS or status >= 500:
                last_error = APIError(
                    f"{endpoint}: {message}", reason=reason, status=status
                )
                logger.warning(
                    "Retryable API error %r (attempt %d/%d)",
                    reason or status,
                    attempt + 1,
                    self.max_retries + 1,
                )
                continue
            raise APIError(f"{endpoint}: {message}", reason=reason, status=status)

        raise last_error if last_error else APIError(f"{endpoint}: request failed")

    # -- helpers -----------------------------------------------------------

    def paginate(self, endpoint: str, *, limit: int | None = None, **params: Any) -> Iterator[dict]:
        """Yield ``items`` across pages, following nextPageToken up to ``limit`` items."""
        token: str | None = None
        yielded = 0
        while True:
            page = self.get(endpoint, pageToken=token, **params)
            for item in page.get("items", []):
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            token = page.get("nextPageToken")
            if not token:
                return

    def map(self, fn: Callable[[T], R], items: Iterable[T], *, desc: str = "") -> list[R]:
        """Apply ``fn`` to items in parallel threads, preserving input order.

        The first exception raised by ``fn`` propagates; remaining work is
        cancelled where possible.
        """
        items = list(items)
        if not items:
            return []
        results: list[R] = [None] * len(items)  # type: ignore[list-item]
        workers = min(self.max_workers, len(items))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fn, item): i for i, item in enumerate(items)}
            done = 0
            try:
                for future in as_completed(futures):
                    results[futures[future]] = future.result()
                    done += 1
                    self.notify(desc, done, len(items))
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        return results

    def notify(self, desc: str, done: int, total: int) -> None:
        """Report progress to the registered callback (used by CLI/web frontends)."""
        if desc:
            logger.debug("%s: %d/%d", desc, done, total)
            if self.on_progress:
                self.on_progress(desc, done, total)
