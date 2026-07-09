"""Channel Info module: full API record and public playlists for one
channel id, URL, or @handle."""

from __future__ import annotations

from typing import Any

from ..client import YouTubeClient
from ..errors import NotFoundError
from ..resolve import resolve_channel_id
from ..utils import squash_ws

PARTS = "brandingSettings,status,id,snippet,contentDetails,statistics,topicDetails"


def channel_info(client: YouTubeClient, ref: str) -> dict[str, Any]:
    """Return the raw ``channels.list`` resource for a channel reference."""
    channel_id = resolve_channel_id(client, ref)
    reply = client.get("channels", part=PARTS, id=channel_id)
    items = reply.get("items") or []
    if not items:
        raise NotFoundError(f"Channel {channel_id} not found", reason="channelNotFound")
    return items[0]


def channel_playlists(client: YouTubeClient, ref: str) -> list[dict[str, Any]]:
    """All public playlists of a channel, as rows ready for CSV export.

    Uses ``playlists.list`` (1 unit per 50 playlists). System playlists
    (uploads, Shorts, …) are not included — the API only returns the
    playlists the channel curates publicly.
    """
    channel_id = resolve_channel_id(client, ref)
    rows: list[dict[str, Any]] = []
    for item in client.paginate(
        "playlists", part="snippet,contentDetails", channelId=channel_id, maxResults=50
    ):
        snippet = item.get("snippet", {})
        playlist_id = item.get("id", "")
        rows.append(
            {
                "playlistId": playlist_id,
                "playlistUrl": f"https://www.youtube.com/playlist?list={playlist_id}",
                "title": snippet.get("title", ""),
                "itemCount": item.get("contentDetails", {}).get("itemCount", ""),
                "publishedAt": snippet.get("publishedAt", ""),
                "description": squash_ws(snippet.get("description")),
            }
        )
    return rows
