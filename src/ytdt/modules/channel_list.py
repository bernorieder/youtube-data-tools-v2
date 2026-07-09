"""Channel List module: channel records from a search or a list of ids."""

from __future__ import annotations

from datetime import datetime

from ..client import SEARCH_MAX_RESULTS, YouTubeClient
from ..models import Channel
from ..utils import chunked, rfc3339, unique

PARTS = "id,snippet,topicDetails,statistics,brandingSettings,status"


def search_channels(
    client: YouTubeClient,
    query: str,
    *,
    order: str = "relevance",
    language: str | None = None,
    region_code: str | None = None,
    published_after: str | datetime | None = None,
    published_before: str | datetime | None = None,
) -> list[str]:
    """Search for channels, following pages to the API's ~500-result
    ceiling (each page of 50 costs 100 quota units)."""
    items = client.paginate(
        "search",
        part="id",
        q=query,
        type="channel",
        order=order,
        maxResults=50,
        relevanceLanguage=language,
        regionCode=region_code,
        publishedAfter=rfc3339(published_after) if published_after else None,
        publishedBefore=rfc3339(published_before) if published_before else None,
        limit=SEARCH_MAX_RESULTS,
    )
    return unique(item["id"]["channelId"] for item in items)


def fetch_channels(client: YouTubeClient, channel_ids: list[str]) -> list[Channel]:
    """Fetch channel records in batches of 50 ids per request, in parallel.

    Results preserve the order of ``channel_ids``; ids the API does not
    return (deleted/suspended channels) are silently dropped.
    """
    channel_ids = unique(channel_ids)

    def fetch_batch(batch: list[str]) -> list[dict]:
        reply = client.get("channels", part=PARTS, id=",".join(batch), maxResults=50)
        return reply.get("items", [])

    batches = client.map(fetch_batch, chunked(channel_ids), desc="channel details")
    by_id = {item["id"]: Channel.from_api(item) for batch in batches for item in batch}
    return [by_id[cid] for cid in channel_ids if cid in by_id]
