from __future__ import annotations

import pytest

from ytdt.modules import fetch_comments, fetch_comments_bulk, interaction_network
from ytdt.utils import sha1_hex


def thread_item(cid: str, author: str) -> dict:
    return {
        "id": cid,
        "snippet": {
            "totalReplyCount": 0,
            "topLevelComment": {
                "snippet": {
                    "authorDisplayName": author,
                    "authorChannelId": {"value": f"UC_{author}"},
                    "textDisplay": f"text by {author}",
                    "publishedAt": "2024-01-01T00:00:00Z",
                    "likeCount": 0,
                }
            },
        },
        "replies": {"comments": []},
    }


COMMENTERS = {"v1": ["@alice", "@bob"], "v2": ["@alice"], "v3": ["@carol"]}

# @alice owns v1's channel and also comments on v2
OWNERS = {"v1": "UC_@alice", "v2": "UC_other", "v3": "UC_other"}


def handler(endpoint, params):
    if endpoint == "videos":
        return {
            "items": [
                {"id": vid, "snippet": {"channelId": OWNERS[vid]}}
                for vid in params["id"].split(",")
                if vid in OWNERS
            ]
        }
    if endpoint == "commentThreads":
        vid = params["videoId"]
        if vid == "disabled":
            return {"error": {"message": "off", "errors": [{"reason": "commentsDisabled"}]}}
        return {"items": [thread_item(f"{vid}_{a}", a) for a in COMMENTERS[vid]]}
    raise AssertionError(endpoint)


def test_single_fetch_stamps_video_id_and_owner(make_client):
    comments = fetch_comments(make_client(handler), "v1")
    assert all(c.video_id == "v1" for c in comments)
    assert comments[0].to_row()["videoId"] == "v1"
    flags = {c.author_name: c.is_channel_owner for c in comments}
    assert flags == {"@alice": "yes", "@bob": "no"}


def test_bulk_combines_videos_and_always_pseudonymizes(make_client):
    comments = fetch_comments_bulk(make_client(handler), ["v1", "v2", "v3"])
    assert len(comments) == 4
    # video ids stay readable, everything identifying is hashed
    assert {c.video_id for c in comments} == {"v1", "v2", "v3"}
    assert all(len(c.author_name) == 40 for c in comments)
    assert all(len(c.comment_id) == 40 for c in comments)
    # the same user hashes identically across videos -> one node in the network
    alice_hash = sha1_hex("@alice")
    alice_comments = [c for c in comments if c.author_name == alice_hash]
    assert {c.video_id for c in alice_comments} == {"v1", "v2"}
    # the owner flag survives pseudonymization: alice owns v1's channel
    assert {c.video_id: c.is_channel_owner for c in alice_comments} == {"v1": "yes", "v2": "no"}
    graph = interaction_network(comments)
    assert graph.nodes[sha1_hex(alice_hash)]["commentCount"] == 2
    assert graph.nodes[sha1_hex(alice_hash)]["isChannelOwner"] == "yes"
    assert graph.nodes[sha1_hex(sha1_hex("@carol"))]["isChannelOwner"] == "no"


def test_bulk_skips_disabled_comments(make_client):
    comments = fetch_comments_bulk(make_client(handler), ["v1", "disabled"])
    assert {c.video_id for c in comments} == {"v1"}


def test_bulk_enforces_max_videos(make_client):
    ids = [f"v{i}" for i in range(6)]
    with pytest.raises(ValueError, match="limited to 5 videos"):
        fetch_comments_bulk(make_client(handler), ids, max_videos=5)
    # duplicates don't count against the cap
    fetch_comments_bulk(make_client(handler), ["v1", "v1", "v2"], max_videos=2)


def test_bulk_collects_skipped_videos(make_client):
    from ytdt.errors import CommentsDisabledError, NotFoundError

    def handler(endpoint, params):
        if endpoint == "videos":
            return {"items": [{"id": v, "snippet": {"channelId": "UCx"}}
                              for v in params["id"].split(",")]}
        if endpoint == "commentThreads":
            vid = params["videoId"]
            if vid == "gone":
                raise NotFoundError("video gone", reason="videoNotFound")
            if vid == "quiet":
                raise CommentsDisabledError("disabled", reason="commentsDisabled")
            return {"items": []}
        raise AssertionError(endpoint)

    missing: list = []
    comments = fetch_comments_bulk(
        make_client(handler), ["gone", "quiet", "empty"], missing=missing
    )
    assert comments == []
    assert sorted(missing) == [("gone", "video not found"), ("quiet", "comments disabled")]


def test_missing_marker_rows():
    from ytdt.models import Comment, Video

    row = Comment.missing("abc123def45", "comments disabled").to_row()
    assert row["videoId"] == "abc123def45"
    assert row["text"] == "[comments disabled: abc123def45]"

    row = Video.missing("abc123def45").to_row()
    assert row["videoId"] == "abc123def45"
    assert row["videoTitle"] == "[not found: abc123def45]"
    assert Video.missing("garbage").to_row()["videoId"] == ""
