from __future__ import annotations

from ytdt.models import Comment
from ytdt.modules import fetch_comments, interaction_network, resolve_reply_targets
from ytdt.utils import sha1_hex


def comment(cid: str, author: str, text: str, top: Comment | None = None) -> Comment:
    if top is None:
        return Comment(comment_id=cid, author_name=author, text=text, thread_id=cid)
    return Comment(
        comment_id=cid,
        author_name=author,
        text=text,
        is_reply=1,
        thread_id=top.comment_id,
        parent_id=top.comment_id,
        parent_author_name=top.author_name,
    )


def test_nested_reply_resolved_via_mention():
    top = comment("t1", "@alice", "original comment")
    r1 = comment("r1", "@bob", "no mention here", top)
    r2 = comment("r2", "@carol", "@bob I disagree", top)
    resolve_reply_targets(top, [r1, r2])
    assert (r1.parent_id, r1.parent_author_name) == ("t1", "@alice")  # fallback
    assert (r2.parent_id, r2.parent_author_name) == ("r1", "@bob")  # resolved


def test_reply_counts_reconstructed_from_resolved_tree():
    top = comment("t1", "@alice", "original comment")
    top.total_reply_count = 3  # API total for the whole thread
    r1 = comment("r1", "@bob", "no mention here", top)
    r2 = comment("r2", "@carol", "@bob I disagree", top)
    r3 = comment("r3", "@bob", "@carol fair point", top)
    resolve_reply_targets(top, [r1, r2, r3])
    # totalReplyCount stays fully in line with the API
    assert top.total_reply_count == 3
    assert (r1.total_reply_count, r2.total_reply_count, r3.total_reply_count) == ("", "", "")
    # inferredReplyCount holds the reconstructed direct-reply counts
    assert top.inferred_reply_count == 1  # only r1 answers the top level
    assert r1.inferred_reply_count == 1  # r2 answers r1
    assert r2.inferred_reply_count == 1  # r3 answers r2
    assert r3.inferred_reply_count == 0  # leaf
    # inferredLevel is the depth in the reconstructed tree
    assert top.inferred_level == 0
    assert r1.inferred_level == 1
    assert r2.inferred_level == 2
    assert r3.inferred_level == 3
    row = r1.to_row()
    assert row["totalReplyCount"] == ""
    assert row["inferredReplyCount"] == 1
    assert row["inferredLevel"] == 1


def test_mention_behind_invisible_characters():
    top = comment("t1", "@alice", "original")
    r1 = comment("r1", "@bob", "some answer", top)
    # YouTube inserts zero-width spaces before the mention, and squash_ws
    # turns the newline after it into a space
    r2 = comment("r2", "@carol", "​​@bob genau!", top)
    resolve_reply_targets(top, [r1, r2])
    assert r2.parent_author_name == "@bob"


def test_glued_mention_matches_participant_and_restores_space():
    # the API often drops the space after inserted mentions, in all formats
    top = comment("t1", "@alice", "original")
    r1 = comment("r1", "@Helfried_B", "first", top)
    r2 = comment("r2", "@dave", "​@Helfried_BNein das sieht er genau richtig.", top)
    resolve_reply_targets(top, [r1, r2])
    assert r2.parent_id == "r1"
    assert r2.text == "@Helfried_B Nein das sieht er genau richtig."


def test_clean_boundary_match_beats_glued_prefix():
    top = comment("t1", "@alice", "original")
    r1 = comment("r1", "@abc", "first", top)
    r2 = comment("r2", "@abcdef", "second", top)
    r3 = comment("r3", "@dave", "@abcdef you are right", top)
    resolve_reply_targets(top, [r1, r2, r3])
    assert r3.parent_id == "r2"  # clean match on @abcdef, not glued @abc + "def"
    assert r3.text == "@abcdef you are right"  # already-clean text unchanged


def test_longest_handle_wins():
    top = comment("t1", "@alice", "original")
    r1 = comment("r1", "@abc", "first", top)
    r2 = comment("r2", "@abc.def", "second", top)
    r3 = comment("r3", "@eve", "@abc.def well said", top)
    resolve_reply_targets(top, [r1, r2, r3])
    assert r3.parent_id == "r2"


def test_latest_prior_comment_of_author_wins():
    top = comment("t1", "@alice", "original")
    r1 = comment("r1", "@bob", "early take", top)
    r2 = comment("r2", "@bob", "revised take", top)
    r3 = comment("r3", "@carol", "@bob which one?", top)
    resolve_reply_targets(top, [r1, r2, r3])
    assert r3.parent_id == "r2"


def make_ping_pong(depth: int = 9) -> tuple[Comment, list[Comment]]:
    top = comment("t1", "@alice", "original comment")
    replies = []
    authors = ["@bob", "@carol"]
    for i in range(depth):
        target = authors[(i + 1) % 2] if i else "@alice"
        replies.append(comment(f"r{i + 1}", authors[i % 2], f"{target} point {i}", top))
    return top, replies


def test_inferred_level_unlimited_by_default():
    top, replies = make_ping_pong()
    resolve_reply_targets(top, replies)
    assert [r.inferred_level for r in replies] == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert replies[8].parent_id == "r8"


def test_inferred_level_saturates_when_cap_set(monkeypatch):
    monkeypatch.setattr("ytdt.modules.video_comments.MAX_INFERRED_LEVEL", 6)
    top, replies = make_ping_pong()
    resolve_reply_targets(top, replies)
    assert [r.inferred_level for r in replies] == [1, 2, 3, 4, 5, 6, 6, 6, 6]
    # mention-resolved parents still follow the full chain
    assert replies[8].parent_id == "r8"


def test_cannot_reply_to_later_comment():
    top = comment("t1", "@alice", "original")
    r1 = comment("r1", "@carol", "@bob hello?", top)  # bob only appears later
    r2 = comment("r2", "@bob", "here now", top)
    resolve_reply_targets(top, [r1, r2])
    assert r1.parent_author_name == "@alice"


def test_legacy_display_name_without_at_prefix():
    top = comment("t1", "Old School User", "original")
    r1 = comment("r1", "@bob", "@Old School User thanks!", top)
    resolve_reply_targets(top, [r1])
    assert r1.parent_id == "t1"
    assert r1.parent_author_name == "Old School User"


def thread_item(cid: str, author: str, total: int, inline: list) -> dict:
    return {
        "id": cid,
        "snippet": {
            "totalReplyCount": total,
            "topLevelComment": {
                "snippet": {
                    "authorDisplayName": author,
                    "authorChannelId": {"value": f"UC_{author}"},
                    "textDisplay": "top text",
                    "publishedAt": "2024-01-01T00:00:00Z",
                    "likeCount": 0,
                }
            },
        },
        "replies": {"comments": inline},
    }


def reply_item(cid: str, author: str, text: str) -> dict:
    return {
        "id": cid,
        "snippet": {
            "authorDisplayName": author,
            "authorChannelId": {"value": f"UC_{author}"},
            "textDisplay": text,
            "publishedAt": "2024-01-02T00:00:00Z",
            "likeCount": 0,
        },
    }


def test_fetch_comments_resolves_nested_replies_and_network(make_client):
    # inline replies arrive newest-first, as in the real API
    inline = [
        reply_item("r2", "@carol", "@bob nested answer"),
        reply_item("r1", "@bob", "top-level answer"),
    ]

    def handler(endpoint, params):
        if endpoint == "videos":
            return {"items": [{"id": "vid", "snippet": {"channelId": "UC_owner"}}]}
        if endpoint == "commentThreads":
            return {"items": [thread_item("t1", "@alice", 2, inline)]}
        raise AssertionError(endpoint)

    comments = fetch_comments(make_client(handler), "vid")
    by_id = {c.comment_id: c for c in comments}
    assert by_id["r2"].parent_id == "r1"
    assert by_id["r2"].thread_id == "t1"
    assert by_id["r1"].parent_id == "t1"

    graph = interaction_network(comments)
    assert graph.edges == {
        (sha1_hex("@bob"), sha1_hex("@alice")): 1,
        (sha1_hex("@carol"), sha1_hex("@bob")): 1,
    }
