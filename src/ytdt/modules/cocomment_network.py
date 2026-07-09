"""Video Co-commenting Network module.

Builds an undirected network of videos where edge weight is the number of
users who commented on both videos (the channel owner is excluded), plus a
derived channel-level network. Comments for all videos are fetched in
parallel.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from ..client import YouTubeClient
from ..errors import CommentsDisabledError, SkippableError
from ..graph import Graph
from ..models import _sql_datetime, _unix
from ..utils import squash_ws, unique
from .video_list import _category_labels


def _video_row(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet", {})
    content = item.get("contentDetails", {})
    stats = item.get("statistics", {})
    return {
        "channelId": snippet.get("channelId", ""),
        "channelTitle": squash_ws(snippet.get("channelTitle")),
        "videoId": item.get("id", ""),
        "publishedAtUnix": _unix(snippet.get("publishedAt")),
        "publishedAtSQL": _sql_datetime(snippet.get("publishedAt")),
        "videoTitle": squash_ws(snippet.get("title")),
        "videoCategoryId": snippet.get("categoryId", ""),
        "videoCategoryLabel": "",
        "defaultLanguage": snippet.get("defaultLanguage", ""),
        "defaultAudioLanguage": snippet.get("defaultAudioLanguage", ""),
        "duration": content.get("duration", ""),
        "viewCount": stats.get("viewCount", ""),
        "likeCount": stats.get("likeCount", ""),
        "commentCount": stats.get("commentCount", ""),
        "commentsDisabled": 0,
    }


def cocomment_networks(
    client: YouTubeClient,
    video_ids: list[str],
    *,
    max_comments: int = 100,
) -> tuple[Graph, Graph]:
    """Return ``(video_network, channel_network)`` for a set of video ids.

    ``max_comments`` caps the relevance-ranked top-level comments examined
    per video (the API serves at most ~1000).
    """
    video_ids = unique(video_ids)

    def fetch_video(video_id: str) -> tuple[dict[str, Any], set[str]] | None:
        reply = client.get(
            "videos", part="statistics,contentDetails,snippet", id=video_id
        )
        items = reply.get("items") or []
        if not items:
            return None
        row = _video_row(items[0])
        authors: set[str] = set()
        try:
            threads = client.paginate(
                "commentThreads",
                part="snippet",
                maxResults=100,
                order="relevance",
                videoId=video_id,
                limit=max_comments,
            )
            for thread in threads:
                snippet = thread["snippet"]["topLevelComment"]["snippet"]
                author = snippet.get("authorChannelId", {}).get("value", "")
                if author and author != row["channelId"]:
                    authors.add(author)
        except CommentsDisabledError:
            row["commentsDisabled"] = 1
        except SkippableError:
            pass
        return row, authors

    results = [r for r in client.map(fetch_video, video_ids, desc="videos") if r]

    labels = _category_labels(
        client, {row["videoCategoryId"] for row, _ in results if row["videoCategoryId"]}
    )

    video_graph = Graph(directed=False)
    channel_graph = Graph(directed=False)
    for rank, (row, _) in enumerate(results, start=1):
        row["videoCategoryLabel"] = labels.get(row["videoCategoryId"], "")
        video_graph.add_node(
            row["videoId"],
            label=row["videoTitle"],
            seedRank=rank,
            publishedAtUnix=row["publishedAtUnix"],
            publishedAtSQL=row["publishedAtSQL"],
            channelTitle=row["channelTitle"],
            channelId=row["channelId"],
            videoCategoryLabel=row["videoCategoryLabel"],
            defaultLanguage=row["defaultLanguage"],
            defaultAudioLanguage=row["defaultAudioLanguage"],
            viewCount=int(row["viewCount"] or 0),
            likeCount=int(row["likeCount"] or 0),
            commentCount=int(row["commentCount"] or 0),
            commentsDisabled=row["commentsDisabled"],
        )
        channel_graph.add_node(row["channelId"], label=row["channelTitle"])

    for (row_a, authors_a), (row_b, authors_b) in combinations(results, 2):
        shared = len(authors_a & authors_b)
        if shared:
            video_graph.add_edge(row_a["videoId"], row_b["videoId"], weight=shared)
            channel_graph.add_edge(row_a["channelId"], row_b["channelId"], weight=1)

    return video_graph, channel_graph
