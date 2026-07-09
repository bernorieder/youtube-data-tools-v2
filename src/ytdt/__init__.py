"""YouTube Data Tools — a Python library for extracting research data from
the YouTube Data API v3.

Quick start::

    from ytdt import YouTubeClient, search_videos, fetch_videos, write_table

    client = YouTubeClient(api_key="...")           # or YTDT_API_KEY env var
    ids = search_videos(client, "climate change")
    videos = fetch_videos(client, ids)
    write_table(videos, "videolist.csv")
"""

from .cache import FactCache, default_cache_path
from .client import YouTubeClient
from .errors import (
    APIError,
    CommentsDisabledError,
    ConfigurationError,
    ForbiddenError,
    NotFoundError,
    ProcessingError,
    QuotaExceededError,
    SkippableError,
    YTDTError,
)
from .graph import Graph
from .models import Channel, Comment, Video
from .modules import (
    author_counts,
    channel_info,
    channel_details,
    channel_shorts_ids,
    channel_video_ids,
    cocomment_networks,
    cotag_network,
    crawl_channel_network,
    detect_shorts,
    fetch_channels,
    fetch_comments,
    fetch_comments_bulk,
    fetch_videos,
    interaction_network,
    resolve_reply_targets,
    playlist_video_ids,
    pseudonymize,
    search_channels,
    search_videos,
    shared_tag_network,
    trending_videos,
    video_categories,
    video_info,
)
from .resolve import resolve_channel_id, resolve_channel_ids
from .tabular import write_key_values, write_table

__version__ = "2.0.0a1"

__all__ = [
    "APIError",
    "Channel",
    "Comment",
    "CommentsDisabledError",
    "ConfigurationError",
    "FactCache",
    "ForbiddenError",
    "Graph",
    "NotFoundError",
    "ProcessingError",
    "QuotaExceededError",
    "SkippableError",
    "Video",
    "YTDTError",
    "YouTubeClient",
    "author_counts",
    "channel_info",
    "channel_details",
    "channel_shorts_ids",
    "channel_video_ids",
    "cocomment_networks",
    "cotag_network",
    "crawl_channel_network",
    "default_cache_path",
    "detect_shorts",
    "fetch_channels",
    "fetch_comments",
    "fetch_comments_bulk",
    "fetch_videos",
    "interaction_network",
    "resolve_reply_targets",
    "playlist_video_ids",
    "pseudonymize",
    "resolve_channel_id",
    "resolve_channel_ids",
    "search_channels",
    "search_videos",
    "shared_tag_network",
    "trending_videos",
    "video_categories",
    "video_info",
    "write_key_values",
    "write_table",
]
