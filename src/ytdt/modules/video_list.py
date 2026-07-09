"""Video List module: video ids from channels/playlists/searches, the
regional most-popular (trending) chart, batched metadata fetching, and the
tag networks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import combinations

from typing import Any

from ..cache import FactCache
from ..client import SEARCH_MAX_RESULTS, YouTubeClient
from ..errors import SkippableError
from ..graph import Graph
from ..models import Video, iso8601_duration_to_seconds
from ..utils import chunked, day_spans, rfc3339, unique
from .channel_list import fetch_channels

VIDEO_PARTS = "statistics,contentDetails,snippet,status,paidProductPlacementDetails,recordingDetails,topicDetails"


def playlist_video_ids(client: YouTubeClient, playlist_id: str) -> list[str]:
    """All video ids in a playlist, in playlist order."""
    items = client.paginate(
        "playlistItems", part="contentDetails", maxResults=50, playlistId=playlist_id
    )
    return [item["contentDetails"]["videoId"] for item in items]


def channel_video_ids(client: YouTubeClient, channel_ids: str | list[str]) -> list[str]:
    """All uploaded video ids for one or more channels (via their uploads playlists)."""
    if isinstance(channel_ids, str):
        channel_ids = [channel_ids]
    channel_ids = unique(channel_ids)

    uploads: list[str] = []
    for batch in chunked(channel_ids):
        reply = client.get("channels", part="contentDetails", id=",".join(batch), maxResults=50)
        for item in reply.get("items", []):
            playlist = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            if playlist:
                uploads.append(playlist)

    def safe_fetch(playlist: str) -> list[str]:
        try:
            return playlist_video_ids(client, playlist)
        except SkippableError:
            # channels without uploads yield a 404 on their uploads playlist
            return []

    id_lists = client.map(safe_fetch, uploads, desc="channel uploads")
    return unique(vid for ids in id_lists for vid in ids)


def search_videos(
    client: YouTubeClient,
    query: str | None = None,
    *,
    order: str = "relevance",
    language: str | None = None,
    region_code: str | None = None,
    published_after: str | datetime | None = None,
    published_before: str | datetime | None = None,
    day_mode: bool = False,
    location: str | None = None,
    location_radius: str | None = None,
) -> list[str]:
    """Search for video ids, following pages to the API's ~500-result
    ceiling (each page of 50 costs 100 quota units).

    ``day_mode`` runs a separate search for every day in the timeframe,
    which can surface many more videos than a single windowed search —
    note each day's search paginates to the ceiling on its own.
    ``location``/``location_radius`` restrict results to geotagged videos
    (e.g. ``"37.42307,-122.08427"`` and ``"10km"``).
    """
    if not query and not location:
        raise ValueError("Provide a query and/or a location.")
    if day_mode:
        if not (published_after and published_before):
            raise ValueError("day_mode requires published_after and published_before.")
        spans = list(day_spans(published_after, published_before))
    elif published_after or published_before:
        spans = [(
            rfc3339(published_after) if published_after else None,
            rfc3339(published_before) if published_before else None,
        )]
    else:
        spans = [(None, None)]

    def search_span(span: tuple[str | None, str | None]) -> list[str]:
        after, before = span
        items = client.paginate(
            "search",
            part="id",
            q=query,
            type="video",
            order=order,
            maxResults=50,
            relevanceLanguage=language,
            regionCode=region_code,
            publishedAfter=after,
            publishedBefore=before,
            location=location,
            locationRadius=location_radius,
            limit=SEARCH_MAX_RESULTS,
        )
        return [item["id"]["videoId"] for item in items]

    id_lists = client.map(search_span, spans, desc="searches")
    return unique(vid for ids in id_lists for vid in ids)


def trending_videos(
    client: YouTubeClient,
    *,
    region_code: str = "US",
    category_id: str | None = None,
    limit: int | None = None,
) -> list[Video]:
    """Videos on YouTube's most-popular ("trending") chart for a region.

    Results come back in chart order (rank 1 first) as fully populated
    :class:`~ytdt.models.Video` records — the chart endpoint returns
    complete video resources, so no separate metadata fetch is needed and
    each page of 50 costs one quota unit. The API caps the chart at
    roughly 200 videos. ``category_id`` restricts the chart to one video
    category (see :func:`video_categories`); not every category has a
    chart in every region — the API answers 404 for those combinations.
    """
    items = client.paginate(
        "videos",
        part=VIDEO_PARTS,
        chart="mostPopular",
        regionCode=region_code,
        videoCategoryId=category_id,
        maxResults=50,
        limit=limit,
    )
    videos = [Video.from_api(item) for item in items]
    labels = _category_labels(client, {v.category_id for v in videos if v.category_id})
    for video in videos:
        video.category_label = labels.get(video.category_id, "")
    return videos


def video_categories(client: YouTubeClient, region_code: str = "US") -> dict[str, str]:
    """Video category id -> label for a region (for :func:`trending_videos`)."""
    reply = client.get("videoCategories", part="snippet", regionCode=region_code)
    return {
        item["id"]: item["snippet"]["title"] for item in reply.get("items", [])
    }


def fetch_videos(client: YouTubeClient, video_ids: list[str]) -> list[Video]:
    """Fetch video records in batches of 50 ids per request, in parallel.

    Results preserve the order of ``video_ids`` (i.e. search rank); ids the
    API does not return (deleted/private videos) are silently dropped.
    Category labels are resolved and filled in.
    """
    video_ids = unique(video_ids)

    def fetch_batch(batch: list[str]) -> list[dict]:
        reply = client.get("videos", part=VIDEO_PARTS, id=",".join(batch), maxResults=50)
        return reply.get("items", [])

    batches = client.map(fetch_batch, chunked(video_ids), desc="video details")
    by_id = {item["id"]: Video.from_api(item) for batch in batches for item in batch}
    videos = [by_id[vid] for vid in video_ids if vid in by_id]

    labels = _category_labels(client, {v.category_id for v in videos if v.category_id})
    for video in videos:
        video.category_label = labels.get(video.category_id, "")
    return videos


def _category_labels(client: YouTubeClient, category_ids: set[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for batch in chunked(sorted(category_ids)):
        reply = client.get("videoCategories", part="snippet", id=",".join(batch))
        for item in reply.get("items", []):
            labels[item["id"]] = item["snippet"]["title"]
    return labels


# Shorts run up to 3 minutes (limit raised from 60s in October 2024), with
# slack for the API's habit of reporting durations a second or two long.
SHORTS_MAX_DURATION = 183


def _shorts_playlist_id(channel_id: str) -> str:
    # Every channel has unlisted system playlists derived from its id:
    # UUSH.. = Shorts, UULF.. = long form, UULV.. = live. Undocumented but
    # served by plain playlistItems.list; they partition the UU.. uploads
    # playlist exactly. Channels without Shorts answer 404.
    return "UUSH" + channel_id[2:]


def channel_shorts_ids(client: YouTubeClient, channel_id: str) -> list[str]:
    """All Shorts video ids of a channel, newest first (empty if none).

    Queries the channel's unlisted Shorts system playlist — API-only, no
    scraping, but the playlist id scheme is undocumented and could stop
    working without notice.
    """
    try:
        items = client.paginate(
            "playlistItems",
            part="contentDetails",
            maxResults=50,
            playlistId=_shorts_playlist_id(channel_id),
        )
        return [item["contentDetails"]["videoId"] for item in items]
    except SkippableError:
        return []


def _days_before(timestamp: str, days: int) -> str:
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (moment - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def detect_shorts(
    client: YouTubeClient, videos: list[Video], *, cache: FactCache | None = None
) -> None:
    """Fill each video's ``is_short`` ("yes"/"no") in place, API-only.

    Videos longer than :data:`SHORTS_MAX_DURATION` seconds or currently
    live/upcoming are "no" without any request. The rest are matched
    against their channel's Shorts system playlist (one paginated
    ``playlistItems.list`` per channel, 1 unit per 50 Shorts), paging
    newest-first and stopping early once every candidate is found or the
    playlist has moved a week past the oldest candidate's publish date.
    Videos without a channel id keep ``is_short = ""`` (undetermined);
    videos whose ``is_short`` is already set keep their value.

    A :class:`~ytdt.cache.FactCache` makes results persistent: a video's
    Shorts status never changes, so cached videos cost no requests on
    later runs. A "no" for a video younger than a week is not persisted,
    because the Shorts playlist can lag behind brand-new uploads.
    """
    undetermined = [video for video in videos if not video.is_short]
    cached = cache.get_shorts([v.video_id for v in undetermined]) if cache else {}
    candidates: dict[str, list[Video]] = {}
    for video in videos:
        if not video.is_short and video.video_id in cached:
            video.is_short = cached[video.video_id]
        if video.is_short:
            continue
        if video.live_broadcast_content in ("live", "upcoming"):
            video.is_short = "no"
        elif iso8601_duration_to_seconds(video.duration) > SHORTS_MAX_DURATION:
            video.is_short = "no"
        elif video.channel_id:
            candidates.setdefault(video.channel_id, []).append(video)

    def shorts_among(channel_id: str) -> set[str]:
        wanted = {video.video_id for video in candidates[channel_id]}
        cutoff = _days_before(min(v.published_at for v in candidates[channel_id]), 7)
        found: set[str] = set()
        try:
            for item in client.paginate(
                "playlistItems",
                part="contentDetails",
                maxResults=50,
                playlistId=_shorts_playlist_id(channel_id),
            ):
                details = item.get("contentDetails", {})
                if details.get("videoId") in wanted:
                    found.add(details["videoId"])
                    if len(found) == len(wanted):
                        break
                published = details.get("videoPublishedAt", "")
                if cutoff and published and published < cutoff:
                    break  # newest-first: nothing older can match
        except SkippableError:  # 404 = the channel has no Shorts
            pass
        return found

    results = client.map(shorts_among, list(candidates), desc="shorts detection")
    for channel_id, found in zip(candidates, results):
        for video in candidates[channel_id]:
            video.is_short = "yes" if video.video_id in found else "no"

    if cache is not None:
        horizon = (datetime.now(timezone.utc) - timedelta(days=7)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        cache.put_shorts({
            video.video_id: video.is_short
            for video in videos
            if video.is_short and video.video_id not in cached
            # a recent (or undatable) "no" may just be playlist lag
            and not (
                video.is_short == "no"
                and (not video.published_at or video.published_at >= horizon)
            )
        })


# Channel fields merged into video rows/nodes by channel_details().
CHANNEL_DETAIL_FIELDS = (
    "publishedAt",
    "daysActive",
    "country",
    "viewCount",
    "subscriberCount",
    "videoCount",
)


def channel_details(client: YouTubeClient, videos: list[Video]) -> dict[str, dict[str, Any]]:
    """Fetch details for every channel in a video list.

    Returns ``channel id -> {"channel_publishedAt": ..., ...}`` with a
    ``channel_`` prefix, ready to merge into video CSV rows or
    :func:`shared_tag_network` nodes. Channels the API does not return
    (deleted/suspended) map to empty values, so merged rows stay uniform.
    """
    ids = unique(video.channel_id for video in videos if video.channel_id)
    details = {
        cid: {f"channel_{field}": "" for field in CHANNEL_DETAIL_FIELDS} for cid in ids
    }
    for channel in fetch_channels(client, ids):
        row = channel.to_row()
        for field in ("viewCount", "subscriberCount", "videoCount"):
            row[field] = int(row[field]) if row[field] != "" else ""
        details[channel.channel_id] = {
            f"channel_{field}": row[field] for field in CHANNEL_DETAIL_FIELDS
        }
    return details


def _video_tags(video: Video) -> list[str]:
    return unique(tag.lower().strip() for tag in (video.tags or []) if tag.strip())


def _as_int(value: int | str) -> int:
    return int(value) if value != "" else 0


def cotag_network(videos: list[Video]) -> Graph:
    """Undirected tag co-occurrence network: tags are nodes, edges count
    how many videos use both tags.

    Each tag node carries ``count`` (videos using the tag) and cumulative
    ``viewCount``/``likeCount``/``commentCount`` summed over those videos.
    """
    graph = Graph(directed=False)
    for video in videos:
        tags = _video_tags(video)
        for tag in tags:
            node = graph.nodes.setdefault(
                tag,
                {"label": tag, "count": 0, "viewCount": 0, "likeCount": 0, "commentCount": 0},
            )
            node["count"] += 1
            node["viewCount"] += _as_int(video.view_count)
            node["likeCount"] += _as_int(video.like_count)
            node["commentCount"] += _as_int(video.comment_count)
        for a, b in combinations(tags, 2):
            graph.add_edge(a, b, weight=1)
    return graph


def shared_tag_network(
    videos: list[Video],
    *,
    channel_details: dict[str, dict[str, Any]] | None = None,
) -> Graph:
    """Undirected video network: the co-tag logic flipped. Videos are
    nodes, an edge's weight is the number of tags two videos share.
    Videos without tags remain as isolated nodes.

    ``channel_details`` (from :func:`channel_details`) adds the
    ``channel_``-prefixed fields to each video node.
    """
    graph = Graph(directed=False)
    tags_by_video = [(video, set(_video_tags(video))) for video in videos]
    for video, tags in tags_by_video:
        graph.add_node(
            video.video_id,
            label=video.title,
            channelId=video.channel_id,
            channelTitle=video.channel_title,
            publishedAt=video.published_at,
            tagCount=len(tags),
            viewCount=_as_int(video.view_count),
            likeCount=_as_int(video.like_count),
            commentCount=_as_int(video.comment_count),
            **(channel_details or {}).get(video.channel_id, {}),
        )
    for (video_a, tags_a), (video_b, tags_b) in combinations(tags_by_video, 2):
        shared = len(tags_a & tags_b)
        if shared:
            graph.add_edge(video_a.video_id, video_b.video_id, weight=shared)
    return graph
