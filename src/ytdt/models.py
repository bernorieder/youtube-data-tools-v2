"""Typed records for videos, channels, and comments.

Each model has a ``from_api()`` constructor that flattens the raw API
resource and a ``to_row()`` method that produces the tabular row used in
exports. Column names match the historical YTDT (PHP) output so existing
analysis pipelines keep working, with two deliberate fixes: the misspelled
``defaultLAudioLanguage`` column is now ``defaultAudioLanguage``, and
``durationSec`` correctly includes hours and days (the PHP version dropped
them).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from typing import Any

from .utils import sha1_hex, squash_ws

_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def iso8601_duration_to_seconds(duration: str | None) -> int:
    """Convert an ISO 8601 duration like ``PT1H2M3S`` to seconds."""
    if not duration:
        return 0
    match = _DURATION.match(duration)
    if not match:
        return 0
    parts = {k: int(v) for k, v in match.groupdict().items() if v}
    return (
        parts.get("days", 0) * 86400
        + parts.get("hours", 0) * 3600
        + parts.get("minutes", 0) * 60
        + parts.get("seconds", 0)
    )


def _sql_datetime(rfc3339_value: str | None) -> str:
    if not rfc3339_value:
        return ""
    value = datetime.fromisoformat(rfc3339_value.replace("Z", "+00:00"))
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def days_active(published_at: str | None) -> int | str:
    """Days elapsed since publication (UTC, rounded); empty for missing dates."""
    if not published_at:
        return ""
    published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    return round((datetime.now(timezone.utc) - published).total_seconds() / 86400)


def _unix(rfc3339_value: str | None) -> int | str:
    if not rfc3339_value:
        return ""
    value = datetime.fromisoformat(rfc3339_value.replace("Z", "+00:00"))
    return int(value.timestamp())


@lru_cache(maxsize=1)
def topic_labels() -> dict[str, str]:
    """Freebase topic id -> human-readable label (channel topicDetails)."""
    with resources.files("ytdt.data").joinpath("topic_ids.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


# An @mention inside comment text: an @ not preceded by a handle character
# (so email addresses stay untouched) followed by a run of handle characters.
_MENTION = re.compile(r"(?<![0-9A-Za-z._-])@[0-9A-Za-z._-]{3,}")


def hash_text_mentions(text: str) -> str:
    """Replace @mentions in comment text with ``@<sha1 of the mention>``.

    The hash covers the mention token including the ``@``, matching how
    author names (which the API returns as ``@handle``) are hashed in
    pseudonymized comments — so a hashed in-text mention equals the hashed
    ``authorName``/``isReplyToName`` of that user. Trailing periods are
    treated as sentence punctuation, not part of the handle. Best effort:
    mentions of legacy multi-word display names cannot be delimited and are
    left as they are.
    """

    def replace(match: re.Match[str]) -> str:
        token = match.group(0).rstrip(".")
        punctuation = match.group(0)[len(token) :]
        return "@" + sha1_hex(token) + punctuation

    return _MENTION.sub(replace, text)


def parse_channel_keywords(raw: str | None) -> list[str]:
    """Split brandingSettings keywords: quoted phrases plus single words."""
    if not raw:
        return []
    quoted = re.findall(r'"(.+?)"', raw)
    rest = re.sub(r'".+?"', " ", raw)
    return [w for w in rest.split() if w] + quoted


@dataclass
class Video:
    channel_id: str = ""
    channel_title: str = ""
    video_id: str = ""
    published_at: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = None  # type: ignore[assignment]
    category_id: str = ""
    category_label: str = ""
    topic_categories: list[str] = None  # type: ignore[assignment]
    duration: str = ""
    is_short: str = ""  # "yes"/"no" via detect_shorts(); "" = not determined
    dimension: str = ""
    definition: str = ""
    caption: str = ""
    live_broadcast_content: str = ""
    default_language: str = ""
    default_audio_language: str = ""
    thumbnail: str = ""
    licensed_content: bool | str = ""
    region_restriction_allowed: list[str] = None  # type: ignore[assignment]
    region_restriction_blocked: list[str] = None  # type: ignore[assignment]
    made_for_kids: bool | str = ""
    has_paid_product_placement: bool | str = ""
    location_description: str = ""
    latitude: float | str = ""
    longitude: float | str = ""
    view_count: int | str = ""
    like_count: int | str = ""
    comment_count: int | str = ""

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "Video":
        snippet = item.get("snippet", {})
        content = item.get("contentDetails", {})
        restriction = content.get("regionRestriction", {})
        stats = item.get("statistics", {})
        recording = item.get("recordingDetails", {})
        location = recording.get("location", {})
        topics = item.get("topicDetails", {}).get("topicCategories", [])
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = (thumbnails.get("maxres") or thumbnails.get("high") or {}).get("url", "")
        return cls(
            channel_id=snippet.get("channelId", ""),
            channel_title=squash_ws(snippet.get("channelTitle")),
            video_id=item.get("id", ""),
            published_at=snippet.get("publishedAt", ""),
            title=squash_ws(snippet.get("title")),
            description=squash_ws(snippet.get("description")),
            tags=snippet.get("tags", []),
            category_id=snippet.get("categoryId", ""),
            topic_categories=[t.rsplit("/", 1)[-1] for t in topics],
            duration=content.get("duration", ""),
            dimension=content.get("dimension", ""),
            definition=content.get("definition", ""),
            caption=content.get("caption", ""),
            live_broadcast_content=snippet.get("liveBroadcastContent", ""),
            default_language=snippet.get("defaultLanguage", ""),
            default_audio_language=snippet.get("defaultAudioLanguage", ""),
            thumbnail=thumbnail,
            licensed_content=content.get("licensedContent", ""),
            region_restriction_allowed=restriction.get("allowed", []),
            region_restriction_blocked=restriction.get("blocked", []),
            made_for_kids=item.get("status", {}).get("madeForKids", ""),
            has_paid_product_placement=item.get("paidProductPlacementDetails", {}).get(
                "hasPaidProductPlacement", ""
            ),
            location_description=recording.get("locationDescription", ""),
            latitude=location.get("latitude", ""),
            longitude=location.get("longitude", ""),
            view_count=stats.get("viewCount", ""),
            like_count=stats.get("likeCount", ""),
            comment_count=stats.get("commentCount", ""),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "channelId": self.channel_id,
            "channelTitle": self.channel_title,
            "videoId": self.video_id,
            "videoUrl": f"https://www.youtube.com/watch?v={self.video_id}" if self.video_id else "",
            "publishedAt": self.published_at,
            "publishedAtSQL": _sql_datetime(self.published_at),
            "videoTitle": self.title,
            "videoDescription": self.description,
            "tags": ",".join(self.tags or []),
            "videoCategoryId": self.category_id,
            "videoCategoryLabel": self.category_label,
            "topicCategories": ",".join(self.topic_categories or []),
            "duration": self.duration,
            "durationSec": iso8601_duration_to_seconds(self.duration),
            "isShort": self.is_short,
            "dimension": self.dimension,
            "definition": self.definition,
            "caption": self.caption,
            "liveBroadcastContent": self.live_broadcast_content,
            "defaultLanguage": self.default_language,
            "defaultAudioLanguage": self.default_audio_language,
            "thumbnail_maxres": self.thumbnail,
            "licensedContent": self.licensed_content,
            "regionRestrictionAllowed": ",".join(self.region_restriction_allowed or []),
            "regionRestrictionBlocked": ",".join(self.region_restriction_blocked or []),
            "madeForKids": self.made_for_kids,
            "hasPaidProductPlacement": self.has_paid_product_placement,
            "locationDescription": self.location_description,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "viewCount": self.view_count,
            "likeCount": self.like_count,
            "commentCount": self.comment_count,
        }


@dataclass
class Channel:
    channel_id: str = ""
    title: str = ""
    custom_url: str = ""  # the channel's @handle
    description: str = ""
    published_at: str = ""
    default_language: str = ""
    country: str = ""
    view_count: int | str = ""
    subscriber_count: int | str = ""
    video_count: int | str = ""
    thumbnail: str = ""
    keywords: list[str] = None  # type: ignore[assignment]
    topics: list[str] = None  # type: ignore[assignment]
    made_for_kids: bool | str = ""

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "Channel":
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        branding = item.get("brandingSettings", {}).get("channel", {})
        topic_ids = item.get("topicDetails", {}).get("topicIds", [])
        labels = topic_labels()
        thumbnails = snippet.get("thumbnails", {})
        thumbnail = (thumbnails.get("high") or thumbnails.get("default") or {}).get("url", "")
        return cls(
            channel_id=item.get("id", ""),
            title=squash_ws(snippet.get("title")),
            custom_url=snippet.get("customUrl", ""),
            description=squash_ws(snippet.get("description")),
            published_at=snippet.get("publishedAt", ""),
            default_language=snippet.get("defaultLanguage", ""),
            country=snippet.get("country", ""),
            view_count=stats.get("viewCount", ""),
            subscriber_count=stats.get("subscriberCount", ""),
            video_count=stats.get("videoCount", ""),
            thumbnail=thumbnail,
            keywords=parse_channel_keywords(branding.get("keywords")),
            topics=[labels.get(t, t) for t in topic_ids],
            made_for_kids=item.get("status", {}).get("madeForKids", ""),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.channel_id,
            "title": self.title,
            "customUrl": self.custom_url,
            "channelUrl": f"https://www.youtube.com/channel/{self.channel_id}" if self.channel_id else "",
            "description": self.description,
            "publishedAt": self.published_at,
            "daysActive": days_active(self.published_at),
            "defaultLanguage": self.default_language,
            "country": self.country,
            "madeForKids": self.made_for_kids,
            "viewCount": self.view_count,
            "subscriberCount": self.subscriber_count,
            "videoCount": self.video_count,
            "thumbnail": self.thumbnail,
            "keywords": "|".join(self.keywords or []),
            "topicDetails": "|".join(self.topics or []),
        }


@dataclass
class Comment:
    """A top-level comment or reply.

    ``thread_id`` is the top-level comment the API groups a reply under.
    ``parent_id``/``parent_author_name`` point at the comment a reply
    actually answers: YouTube now supports nested reply chains but the API
    flattens them, so the true target is recovered from the @mention
    YouTube inserts into the reply text (see
    :func:`ytdt.modules.video_comments.resolve_reply_targets`); when no
    target can be resolved, they point at the top-level comment.

    ``total_reply_count`` mirrors the API verbatim: the thread-wide reply
    count for top-level comments (nested and non-retrievable replies
    included), empty for replies. ``inferred_reply_count`` and
    ``inferred_level`` are this library's reconstruction: the comment's
    direct replies among the retrieved comments, and its depth in the
    reconstructed tree (0 = top level, 1 = direct reply, 2 = reply to a
    reply, …). Both are empty when replies were not fetched.
    """

    comment_id: str = ""
    video_id: str = ""
    total_reply_count: int | str = ""
    inferred_reply_count: int | str = ""
    inferred_level: int | str = ""
    like_count: int | str = ""
    published_at: str = ""
    author_name: str = ""
    author_channel_id: str = ""
    is_channel_owner: str = ""  # "yes"/"no" vs the video's channel; "" = unknown
    text: str = ""
    is_reply: int = 0
    thread_id: str = ""
    parent_id: str = ""
    parent_author_name: str = ""

    @classmethod
    def from_thread(cls, thread: dict[str, Any]) -> "Comment":
        """Build a top-level comment from a commentThreads resource."""
        snippet = thread["snippet"]["topLevelComment"]["snippet"]
        return cls(
            comment_id=thread["id"],
            total_reply_count=thread["snippet"].get("totalReplyCount", 0),
            like_count=snippet.get("likeCount", 0),
            published_at=snippet.get("publishedAt", ""),
            author_name=squash_ws(snippet.get("authorDisplayName")),
            author_channel_id=snippet.get("authorChannelId", {}).get("value", ""),
            text=squash_ws(snippet.get("textDisplay")),
            is_reply=0,
            thread_id=thread["id"],
        )

    @classmethod
    def from_reply(cls, item: dict[str, Any], top: "Comment") -> "Comment":
        """Build a reply comment from a comments resource; the parent
        defaults to the thread's top-level comment until resolved."""
        snippet = item["snippet"]
        return cls(
            comment_id=item["id"],
            total_reply_count="",
            like_count=snippet.get("likeCount", 0),
            published_at=snippet.get("publishedAt", ""),
            author_name=squash_ws(snippet.get("authorDisplayName")),
            author_channel_id=snippet.get("authorChannelId", {}).get("value", ""),
            text=squash_ws(snippet.get("textDisplay")),
            is_reply=1,
            thread_id=top.comment_id,
            parent_id=top.comment_id,
            parent_author_name=top.author_name,
        )

    def pseudonymized(self) -> "Comment":
        """Replace identifying fields with irreversible hashes.

        This covers the id/author/parent fields and @mentions inside the
        comment text (see :func:`hash_text_mentions`); the rest of the text
        is kept verbatim.
        """
        return replace(
            self,
            comment_id=sha1_hex(self.comment_id),
            author_name=sha1_hex(self.author_name),
            text=hash_text_mentions(self.text),
            author_channel_id=sha1_hex(self.author_channel_id) if self.author_channel_id else "",
            thread_id=sha1_hex(self.thread_id) if self.thread_id else "",
            parent_id=sha1_hex(self.parent_id) if self.parent_id else "",
            parent_author_name=sha1_hex(self.parent_author_name) if self.parent_author_name else "",
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "videoId": self.video_id,
            "id": self.comment_id,
            "totalReplyCount": self.total_reply_count,
            "inferredReplyCount": self.inferred_reply_count,
            "inferredLevel": self.inferred_level,
            "likeCount": self.like_count,
            "publishedAt": _sql_datetime(self.published_at),
            "authorName": self.author_name,
            "authorChannelId": self.author_channel_id,
            "isChannelOwner": self.is_channel_owner,
            "text": self.text,
            "isReply": self.is_reply,
            "threadId": self.thread_id,
            "isReplyToId": self.parent_id,
            "isReplyToName": self.parent_author_name,
        }
