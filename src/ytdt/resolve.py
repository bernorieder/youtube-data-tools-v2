"""Turn channel references (ids, URLs, @handles) into canonical channel ids.

Where possible this uses ``channels.list`` lookups (``forHandle`` /
``forUsername``, 1 quota unit) instead of the search endpoint the PHP
version used (100 units), falling back to search only for opaque URLs.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from .client import YouTubeClient
from .errors import NotFoundError, SkippableError
from .utils import unique

CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")


def _channel_id_by(client: YouTubeClient, **lookup: str) -> str:
    reply = client.get("channels", part="id", **lookup)
    items = reply.get("items") or []
    if not items:
        raise NotFoundError(f"No channel found for {lookup}", reason="channelNotFound")
    return items[0]["id"]


def _channel_id_by_search(client: YouTubeClient, query: str) -> str:
    reply = client.get("search", part="snippet", q=query, type="channel", maxResults=1)
    items = reply.get("items") or []
    if not items:
        raise NotFoundError(f"No channel found for {query!r}", reason="channelNotFound")
    return items[0]["id"]["channelId"]


def resolve_channel_id(client: YouTubeClient, ref: str) -> str:
    """Resolve a channel id, @handle, or any youtube.com channel URL to a UC… id."""
    ref = ref.strip()
    if CHANNEL_ID_RE.match(ref):
        return ref
    if ref.startswith("@"):
        return _channel_id_by(client, forHandle=ref)
    if ref.startswith(("http://", "https://")) or "youtube.com" in ref:
        url = ref if "://" in ref else f"https://{ref}"
        path = unquote(urlparse(url).path).strip("/")
        segments = path.split("/") if path else []
        if segments:
            if segments[0] == "channel" and len(segments) > 1 and CHANNEL_ID_RE.match(segments[1]):
                return segments[1]
            if segments[0].startswith("@"):
                return _channel_id_by(client, forHandle=segments[0])
            if segments[0] == "user" and len(segments) > 1:
                return _channel_id_by(client, forUsername=segments[1])
        # vanity /c/Name URLs and anything else: fall back to search
        return _channel_id_by_search(client, ref)
    # bare handle without @, or legacy username
    return _channel_id_by(client, forHandle=f"@{ref}")


def resolve_channel_ids(
    client: YouTubeClient, refs: list[str], *, missing: list[str] | None = None
) -> list[str]:
    """Resolve a mixed list of channel references (ids, URLs, @handles) to
    UC… ids, in parallel, preserving order and dropping duplicates.

    Plain ids pass through without any API call. An unresolvable
    reference raises :class:`~ytdt.errors.NotFoundError` naming it —
    unless ``missing`` is a list, in which case the failing refs are
    collected there and the remaining ones resolve normally.
    """
    refs = unique(ref.strip() for ref in refs if ref.strip())

    def resolve(ref: str) -> str | None:
        try:
            return resolve_channel_id(client, ref)
        except SkippableError:
            if missing is None:
                raise
            missing.append(ref)
            return None

    ids = client.map(resolve, refs, desc="resolving channels")
    return unique(cid for cid in ids if cid)
