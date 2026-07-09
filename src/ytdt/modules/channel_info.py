"""Channel Info module: full API record for one channel id, URL, or @handle."""

from __future__ import annotations

from typing import Any

from ..client import YouTubeClient
from ..errors import NotFoundError
from ..resolve import resolve_channel_id

PARTS = "brandingSettings,status,id,snippet,contentDetails,statistics,topicDetails"


def channel_info(client: YouTubeClient, ref: str) -> dict[str, Any]:
    """Return the raw ``channels.list`` resource for a channel reference."""
    channel_id = resolve_channel_id(client, ref)
    reply = client.get("channels", part=PARTS, id=channel_id)
    items = reply.get("items") or []
    if not items:
        raise NotFoundError(f"Channel {channel_id} not found", reason="channelNotFound")
    return items[0]
