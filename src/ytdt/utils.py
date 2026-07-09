"""Small shared helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator, Sequence, TypeVar

T = TypeVar("T")

_WS = re.compile(r"\s+")


def squash_ws(text: str | None) -> str:
    """Collapse all whitespace runs (incl. newlines) to single spaces."""
    if not text:
        return ""
    return _WS.sub(" ", text).strip()


def chunked(items: Sequence[T], size: int = 50) -> list[Sequence[T]]:
    """Split a sequence into chunks of at most ``size`` (the API's id-batch limit)."""
    return [items[i : i + size] for i in range(0, len(items), size)]


def unique(items: Iterable[T]) -> list[T]:
    """Deduplicate while preserving first-seen order."""
    return list(dict.fromkeys(items))


def sha1_hex(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def rfc3339(value: str | datetime) -> str:
    """Format a datetime (or pass through a string) as the RFC 3339 UTC form the API expects.

    A bare date string ("2024-01-01") is expanded to midnight UTC, so
    users don't have to remember the full timestamp syntax."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value + "T00:00:00Z"
    return value


def parse_rfc3339(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def day_spans(after: str | datetime, before: str | datetime) -> Iterator[tuple[str, str]]:
    """Yield consecutive 24h (after, before) RFC 3339 spans covering the timeframe."""
    start = parse_rfc3339(rfc3339(after))
    end = parse_rfc3339(rfc3339(before))
    while start < end:
        nxt = start + timedelta(days=1)
        yield rfc3339(start), rfc3339(nxt)
        start = nxt
