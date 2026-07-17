"""Video Comments module: comment threads, replies, author counts, and the
reply-interaction network for a single video.

Reply fetching is optimized two ways over the original tool: the
``commentThreads`` request asks for the ``replies`` part, so threads whose
replies fit in the inline payload need no extra request at all, and the
remaining threads are fetched in parallel.

YouTube supports nested reply chains (replies to replies), but the API
flattens every reply under its top-level comment. The only trace of the
real structure is the @mention YouTube inserts at the start of a nested
reply's text; :func:`resolve_reply_targets` recovers it so that
``isReplyToId``/``isReplyToName`` and the interaction network reflect who
actually answered whom.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..client import YouTubeClient
from ..errors import CommentsDisabledError, NotFoundError, SkippableError
from ..graph import Graph
from ..models import Comment, _sql_datetime, _unix
from ..utils import chunked, sha1_hex, squash_ws, unique


def video_info(client: YouTubeClient, video_id: str) -> dict[str, Any]:
    """Basic info/statistics for one video (the ``basicinfo`` export)."""
    reply = client.get(
        "videos", part="statistics,contentDetails,snippet,status", id=video_id
    )
    items = reply.get("items") or []
    if not items:
        raise NotFoundError(f"Video {video_id} not found", reason="videoNotFound")
    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    return {
        "id": item.get("id", ""),
        "published": _sql_datetime(snippet.get("publishedAt")),
        "published_unix": _unix(snippet.get("publishedAt")),
        "title": squash_ws(snippet.get("title")),
        "description": squash_ws(snippet.get("description")),
        "channelId": snippet.get("channelId", ""),
        "channelTitle": snippet.get("channelTitle", ""),
        "duration": item.get("contentDetails", {}).get("duration", ""),
        "viewCount": stats.get("viewCount", 0),
        "likeCount": stats.get("likeCount", 0),
        "commentCount": stats.get("commentCount", 0),
    }


# Invisible characters YouTube places before inserted @mentions
# (zero-width space/non-joiner/joiner, BOM, word joiner), plus space.
_INVISIBLE = "\u200b\u200c\u200d\ufeff\u2060 "

# Cap for inferredLevel; None = unlimited (follow mention chains as deep
# as they go). YouTube's interface indents four levels visually and has
# been observed to record threading only to about level 6, so set this to
# an int to saturate levels at the platform's depth instead.
MAX_INFERRED_LEVEL: int | None = None

# Characters allowed in YouTube handles; used to reject partial matches
# (participant @abc must not match a mention of @abcdef).
_HANDLE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def resolve_reply_targets(top: Comment, replies: list[Comment]) -> None:
    """Point each reply at the comment it actually answers (in place).

    ``replies`` must be in chronological order. A reply that starts with an
    @mention of an earlier participant in the thread is attributed to that
    participant's most recent prior comment; everything else keeps the
    top-level comment as parent. Best effort: mentions of users whose
    comment was deleted (or of non-participants) fall back to the top level.

    The API often stores inserted mentions glued to the comment text with
    the separating space missing ("@someoneNo, I disagree"), in every text
    format — the UI re-adds it visually. A participant handle that is a
    strict prefix of the mention run is therefore accepted too, though a
    match with a proper boundary always wins. On any match the text is
    normalized to what the UI shows: leading invisible characters dropped
    and the missing space restored.

    Finally, ``inferred_reply_count`` (direct replies among the retrieved
    comments) and ``inferred_level`` (depth in the reconstructed tree,
    0 = top level, saturating at :data:`MAX_INFERRED_LEVEL` when that is
    set) are filled for the top-level comment and every reply.
    ``total_reply_count`` is left fully in line with the API (thread-wide
    count on top-level comments, empty on replies).
    """
    seen: list[Comment] = [top]
    for reply in replies:
        text = reply.text.lstrip(_INVISIBLE)
        if text.startswith("@"):
            clean: Comment | None = None
            glued: Comment | None = None
            clean_len = glued_len = 0
            for prior in seen:
                name = prior.author_name
                if not name:
                    continue
                handle = name if name.startswith("@") else "@" + name
                if not text.startswith(handle):
                    continue
                following = text[len(handle) : len(handle) + 1]
                # >=: on equal length, the later comment wins
                if following and following in _HANDLE_CHARS:
                    if len(handle) >= glued_len:
                        glued, glued_len = prior, len(handle)
                elif len(handle) >= clean_len:
                    clean, clean_len = prior, len(handle)
            best, best_len = (clean, clean_len) if clean else (glued, glued_len)
            if best is not None:
                reply.parent_id = best.comment_id
                reply.parent_author_name = best.author_name
                rest = text[best_len:].lstrip(" ")
                reply.text = text[:best_len] + (" " + rest if rest else "")
        seen.append(reply)

    child_counts = Counter(reply.parent_id for reply in replies)
    top.inferred_reply_count = child_counts.get(top.comment_id, 0)
    top.inferred_level = 0
    levels = {top.comment_id: 0}
    for reply in replies:  # parents always precede their replies
        reply.inferred_reply_count = child_counts.get(reply.comment_id, 0)
        level = levels[reply.parent_id] + 1
        if MAX_INFERRED_LEVEL is not None:
            level = min(level, MAX_INFERRED_LEVEL)
        reply.inferred_level = level
        levels[reply.comment_id] = level


def fetch_comments(
    client: YouTubeClient,
    video_id: str,
    *,
    limit: int | None = None,
    include_replies: bool = True,
    owner_channel_id: str | None = None,
) -> list[Comment]:
    """All retrievable comments for a video, top level and replies interleaved.

    ``limit`` caps the number of *top-level* comments; when set, YouTube's
    relevance ranking is used (mirroring the original tool's behaviour).
    Raises :class:`~ytdt.errors.CommentsDisabledError` when the video has
    comments turned off.

    Every comment gets ``is_channel_owner`` ("yes"/"no"): whether its
    author is the channel the video belongs to. Pass ``owner_channel_id``
    to skip the lookup (one ``videos.list`` call) when it is already known.
    """
    if owner_channel_id is None:
        reply = client.get("videos", part="snippet", id=video_id)
        items = reply.get("items") or []
        owner_channel_id = items[0]["snippet"].get("channelId", "") if items else ""
    threads = list(
        client.paginate(
            "commentThreads",
            part="snippet,replies" if include_replies else "snippet",
            textFormat="plainText",
            maxResults=100,
            videoId=video_id,
            order="relevance" if limit else None,
            limit=limit,
        )
    )
    tops = [Comment.from_thread(thread) for thread in threads]
    if not include_replies:
        return _finalize(tops, video_id, owner_channel_id)

    def replies_for(pair: tuple[dict, Comment]) -> list[Comment]:
        thread, top = pair
        total = thread["snippet"].get("totalReplyCount", 0)
        if total == 0:
            top.inferred_reply_count = 0
            top.inferred_level = 0
            return []
        inline = thread.get("replies", {}).get("comments", [])
        if len(inline) >= total:
            # inline replies arrive newest-first; flip to chronological
            replies = [Comment.from_reply(item, top) for item in reversed(inline)]
        else:
            items = client.paginate(
                "comments",
                part="snippet",
                textFormat="plainText",
                maxResults=100,
                parentId=top.comment_id,
            )
            replies = [Comment.from_reply(item, top) for item in items]
        # the API guarantees no reply ordering; resolution needs chronological
        replies.sort(key=lambda c: c.published_at)
        resolve_reply_targets(top, replies)
        return replies

    reply_lists = client.map(replies_for, zip(threads, tops), desc="comment replies")
    comments: list[Comment] = []
    for top, replies in zip(tops, reply_lists):
        comments.append(top)
        comments.extend(replies)
    return _finalize(comments, video_id, owner_channel_id)


def _finalize(
    comments: list[Comment], video_id: str, owner_channel_id: str
) -> list[Comment]:
    """Stamp the video id and the channel-owner flag on every comment."""
    for comment in comments:
        comment.video_id = video_id
        if owner_channel_id:
            comment.is_channel_owner = (
                "yes" if comment.author_channel_id == owner_channel_id else "no"
            )
    return comments


# Cap on videos per bulk comment download.
DEFAULT_MAX_VIDEOS = 100


def skip_reason(exc: SkippableError) -> str:
    """Short label for why a video's comments could not be retrieved."""
    if isinstance(exc, CommentsDisabledError):
        return "comments disabled"
    if isinstance(exc, NotFoundError):
        return "video not found"
    return "unavailable"


def fetch_comments_bulk(
    client: YouTubeClient,
    video_ids: list[str],
    *,
    limit: int | None = None,
    max_videos: int | None = DEFAULT_MAX_VIDEOS,
    missing: list[tuple[str, str]] | None = None,
) -> list[Comment]:
    """Comments for several videos in parallel, as one combined list.

    The result is **always pseudonymized**: bulk collection of user-level
    data across videos must not carry plain author names. Hashes are
    consistent across videos, so the same user remains one node in the
    combined interaction network. Videos that are missing or have comments
    disabled are skipped; pass a ``missing`` list to collect
    ``(video_id, reason)`` pairs for them. ``limit`` caps top-level
    comments per video; ``max_videos`` (None = unlimited) rejects
    oversized requests.
    """
    video_ids = unique(video_ids)
    if max_videos is not None and len(video_ids) > max_videos:
        raise ValueError(
            f"Bulk comment download is limited to {max_videos} videos "
            f"(got {len(video_ids)})."
        )

    def owner_batch(batch: list[str]) -> list[dict]:
        reply = client.get("videos", part="snippet", id=",".join(batch), maxResults=50)
        return reply.get("items", [])

    batches = client.map(owner_batch, chunked(video_ids), desc="video owners")
    owners = {
        item["id"]: item.get("snippet", {}).get("channelId", "")
        for batch in batches
        for item in batch
    }

    def per_video(video_id: str) -> list[Comment]:
        try:
            return fetch_comments(
                client, video_id, limit=limit, owner_channel_id=owners.get(video_id, "")
            )
        except SkippableError as exc:
            if missing is not None:
                missing.append((video_id, skip_reason(exc)))
            return []

    comment_lists = client.map(per_video, video_ids, desc="videos")
    return pseudonymize([c for lst in comment_lists for c in lst])


def pseudonymize(comments: list[Comment]) -> list[Comment]:
    """Replace author names and comment ids with irreversible hashes."""
    return [comment.pseudonymized() for comment in comments]


def author_counts(comments: list[Comment]) -> "Counter[str]":
    """Comment count per author, most active first."""
    counts: Counter[str] = Counter(comment.author_name for comment in comments)
    return counts


def interaction_network(comments: list[Comment]) -> Graph:
    """Directed reply network between commenters: A replied to B.

    Nodes carry ``commentCount``, the cumulative ``likeCount`` received
    across the user's comments, and ``isChannelOwner`` ("yes" when the
    user owns a channel one of their commented videos belongs to).
    """
    graph = Graph(directed=True)
    counts = author_counts(comments)
    likes: Counter[str] = Counter()
    owners: set[str] = set()
    for comment in comments:
        likes[comment.author_name] += int(comment.like_count or 0)
        if comment.is_channel_owner == "yes":
            owners.add(comment.author_name)
    for author, count in counts.items():
        graph.add_node(
            sha1_hex(author),
            label=author,
            commentCount=count,
            likeCount=likes[author],
            isChannelOwner="yes" if author in owners else "no",
        )
    for comment in comments:
        if comment.is_reply and comment.parent_author_name:
            graph.add_node(
                sha1_hex(comment.parent_author_name),
                label=comment.parent_author_name,
            )
            graph.add_edge(
                sha1_hex(comment.author_name),
                sha1_hex(comment.parent_author_name),
                weight=1,
            )
    for attrs in graph.nodes.values():
        attrs.setdefault("commentCount", 0)
        attrs.setdefault("likeCount", 0)
        attrs.setdefault("isChannelOwner", "no")
    return graph
