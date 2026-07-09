from __future__ import annotations

from ytdt.models import Video
from ytdt.modules import (
    author_counts,
    channel_shorts_ids,
    channel_video_ids,
    detect_shorts,
    cocomment_networks,
    cotag_network,
    shared_tag_network,
    crawl_channel_network,
    fetch_comments,
    fetch_videos,
    interaction_network,
    search_videos,
    trending_videos,
    video_categories,
)
from ytdt.resolve import resolve_channel_id, resolve_channel_ids
from ytdt.utils import sha1_hex


def video_item(vid: str, channel: str = "UCx", tags: list[str] | None = None) -> dict:
    return {
        "id": vid,
        "snippet": {
            "channelId": channel,
            "channelTitle": f"chan {channel}",
            "publishedAt": "2024-01-01T00:00:00Z",
            "title": f"video {vid}",
            "description": "",
            "categoryId": "22",
            "tags": tags or [],
        },
        "contentDetails": {"duration": "PT1M"},
        "statistics": {"viewCount": "10", "likeCount": "1", "commentCount": "2"},
    }


def thread_item(cid: str, author: str, total_replies: int = 0, inline: list | None = None) -> dict:
    thread = {
        "id": cid,
        "snippet": {
            "totalReplyCount": total_replies,
            "topLevelComment": {
                "snippet": {
                    "likeCount": 1,
                    "publishedAt": "2024-01-01T00:00:00Z",
                    "authorDisplayName": author,
                    "authorChannelId": {"value": f"UC_{author}"},
                    "textDisplay": f"comment by {author}",
                }
            },
        },
    }
    if inline is not None:
        thread["replies"] = {"comments": inline}
    return thread


def reply_item(cid: str, author: str) -> dict:
    return {
        "id": cid,
        "snippet": {
            "likeCount": 0,
            "publishedAt": "2024-01-02T00:00:00Z",
            "authorDisplayName": author,
            "authorChannelId": {"value": f"UC_{author}"},
            "textDisplay": f"reply by {author}",
        },
    }


# -- video list ------------------------------------------------------------


def test_fetch_videos_batches_and_preserves_order(make_client):
    ids = [f"v{i}" for i in range(120)]

    def handler(endpoint, params):
        if endpoint == "videos":
            requested = params["id"].split(",")
            assert len(requested) <= 50
            return {"items": [video_item(v) for v in requested]}
        if endpoint == "videoCategories":
            return {"items": [{"id": "22", "snippet": {"title": "People & Blogs"}}]}
        raise AssertionError(endpoint)

    client = make_client(handler)
    videos = fetch_videos(client, ids)
    assert [v.video_id for v in videos] == ids
    assert all(v.category_label == "People & Blogs" for v in videos)
    video_calls = [r for r in client.requests if r[0] == "videos"]
    assert len(video_calls) == 3  # 120 ids -> 3 batches, not 120 calls


def test_fetch_videos_drops_missing_ids(make_client):
    def handler(endpoint, params):
        if endpoint == "videos":
            return {"items": [video_item("v1")]}
        return {"items": []}

    videos = fetch_videos(make_client(handler), ["v1", "deleted"])
    assert [v.video_id for v in videos] == ["v1"]


def test_channel_video_ids_uses_uploads_playlists(make_client):
    def handler(endpoint, params):
        if endpoint == "channels":
            return {
                "items": [
                    {"id": cid, "contentDetails": {"relatedPlaylists": {"uploads": f"UU{cid[2:]}"}}}
                    for cid in params["id"].split(",")
                ]
            }
        if endpoint == "playlistItems":
            plid = params["playlistId"]
            return {"items": [{"contentDetails": {"videoId": f"{plid}_v{i}"}} for i in range(2)]}
        raise AssertionError(endpoint)

    ids = channel_video_ids(make_client(handler), ["UCa", "UCb"])
    assert ids == ["UUa_v0", "UUa_v1", "UUb_v0", "UUb_v1"]


def test_search_videos_day_mode_splits_timeframe(make_client):
    def handler(endpoint, params):
        assert endpoint == "search"
        day = params["publishedAfter"][8:10]
        return {"items": [{"id": {"videoId": f"v{day}"}}]}

    client = make_client(handler)
    ids = search_videos(
        client,
        "query",
        day_mode=True,
        published_after="2024-03-01T00:00:00Z",
        published_before="2024-03-04T00:00:00Z",
    )
    assert sorted(ids) == ["v01", "v02", "v03"]
    assert len(client.requests) == 3


def test_detect_shorts_checks_candidates_against_shorts_playlists(make_client):
    videos = [
        Video(video_id="s1", channel_id="UCa", duration="PT30S", published_at="2024-06-01T00:00:00Z"),
        Video(video_id="n1", channel_id="UCa", duration="PT45S", published_at="2024-06-02T00:00:00Z"),
        Video(video_id="long", channel_id="UCa", duration="PT10M", published_at="2024-06-03T00:00:00Z"),
        Video(
            video_id="live",
            channel_id="UCa",
            duration="PT0S",
            live_broadcast_content="live",
            published_at="2024-06-03T00:00:00Z",
        ),
        Video(video_id="s2", channel_id="UCb", duration="PT2M59S", published_at="2024-06-01T00:00:00Z"),
        Video(video_id="nochannel", duration="PT30S", published_at="2024-06-01T00:00:00Z"),
        Video(video_id="preset", channel_id="UCa", duration="PT30S", is_short="yes"),
    ]

    def handler(endpoint, params):
        assert endpoint == "playlistItems"
        items = {
            "UUSHa": [{"contentDetails": {"videoId": "s1", "videoPublishedAt": "2024-06-01T00:00:00Z"}}],
            "UUSHb": [{"contentDetails": {"videoId": "s2", "videoPublishedAt": "2024-06-01T00:00:00Z"}}],
        }
        return {"items": items[params["playlistId"]]}

    client = make_client(handler)
    detect_shorts(client, videos)
    assert [v.is_short for v in videos] == ["yes", "no", "no", "no", "yes", "", "yes"]
    # long/live videos are decided without requests; one request per channel
    assert sorted(p["playlistId"] for _, p in client.requests) == ["UUSHa", "UUSHb"]


def test_detect_shorts_channel_without_shorts_playlist(make_client):
    def handler(endpoint, params):
        return {"error": {"code": 404, "message": "nope", "errors": [{"reason": "playlistNotFound"}]}}

    video = Video(video_id="v1", channel_id="UCa", duration="PT30S", published_at="2024-06-01T00:00:00Z")
    detect_shorts(make_client(handler), [video])
    assert video.is_short == "no"


def test_detect_shorts_stops_paging_past_candidate_age(make_client):
    video = Video(video_id="new", channel_id="UCa", duration="PT30S", published_at="2026-01-01T00:00:00Z")

    def handler(endpoint, params):
        return {
            "items": [
                {"contentDetails": {"videoId": f"old{i}", "videoPublishedAt": "2024-01-01T00:00:00Z"}}
                for i in range(50)
            ],
            "nextPageToken": "more",
        }

    client = make_client(handler)
    detect_shorts(client, [video])
    assert video.is_short == "no"
    assert len(client.requests) == 1  # items are older than the candidate: no second page


def test_channel_shorts_ids_and_missing_playlist(make_client):
    def handler(endpoint, params):
        if params["playlistId"] == "UUSHa":
            return {"items": [{"contentDetails": {"videoId": "s1"}}, {"contentDetails": {"videoId": "s2"}}]}
        return {"error": {"code": 404, "message": "nope", "errors": [{"reason": "playlistNotFound"}]}}

    assert channel_shorts_ids(make_client(handler), "UCa") == ["s1", "s2"]
    assert channel_shorts_ids(make_client(handler), "UCempty") == []


def test_trending_videos_paginates_chart_in_rank_order(make_client):
    def handler(endpoint, params):
        if endpoint == "videos":
            assert params["chart"] == "mostPopular"
            assert params["regionCode"] == "DE"
            assert "id" not in params
            if params.get("pageToken"):
                return {"items": [video_item(f"v{i}") for i in range(50, 60)]}
            return {
                "items": [video_item(f"v{i}") for i in range(50)],
                "nextPageToken": "page2",
            }
        if endpoint == "videoCategories":
            return {"items": [{"id": "22", "snippet": {"title": "People & Blogs"}}]}
        raise AssertionError(endpoint)

    videos = trending_videos(make_client(handler), region_code="DE")
    assert [v.video_id for v in videos] == [f"v{i}" for i in range(60)]
    assert all(v.category_label == "People & Blogs" for v in videos)


def test_trending_videos_passes_category_and_limit(make_client):
    def handler(endpoint, params):
        if endpoint == "videos":
            assert params["videoCategoryId"] == "10"
            return {"items": [video_item("v1")], "nextPageToken": "more"}
        return {"items": [{"id": "22", "snippet": {"title": "People & Blogs"}}]}

    videos = trending_videos(make_client(handler), category_id="10", limit=1)
    assert [v.video_id for v in videos] == ["v1"]


def test_video_categories_labels_for_region(make_client):
    def handler(endpoint, params):
        assert endpoint == "videoCategories"
        assert params["regionCode"] == "NL"
        return {
            "items": [
                {"id": "1", "snippet": {"title": "Film & Animation"}},
                {"id": "10", "snippet": {"title": "Music"}},
            ]
        }

    assert video_categories(make_client(handler), region_code="NL") == {
        "1": "Film & Animation",
        "10": "Music",
    }


def test_cotag_network_counts_cooccurrence_and_metrics():
    videos = [
        Video(video_id="a", tags=["Cats", "dogs"], view_count="100", like_count="10", comment_count="1"),
        Video(video_id="b", tags=["cats", "Dogs", "birds"], view_count="50", like_count="5", comment_count=""),
        Video(video_id="c", tags=["cats"], view_count="7", like_count="", comment_count="2"),
    ]
    graph = cotag_network(videos)
    assert graph.nodes["cats"]["count"] == 3
    assert graph.edges[("cats", "dogs")] == 2
    assert graph.edges[("birds", "cats")] == 1
    # metrics accumulate over the videos carrying the tag; blanks count as 0
    assert graph.nodes["cats"]["viewCount"] == 157
    assert graph.nodes["cats"]["likeCount"] == 15
    assert graph.nodes["cats"]["commentCount"] == 3
    assert graph.nodes["birds"]["viewCount"] == 50


def test_shared_tag_network_links_videos_by_common_tags():
    videos = [
        Video(video_id="a", title="A", channel_id="UC1", tags=["cats", "dogs", "pets"], view_count="9"),
        Video(video_id="b", title="B", channel_id="UC1", tags=["Cats", "Dogs"]),
        Video(video_id="c", title="C", channel_id="UC2", tags=["birds"]),
        Video(video_id="d", title="D", channel_id="UC2", tags=[]),
    ]
    graph = shared_tag_network(videos)
    assert set(graph.nodes) == {"a", "b", "c", "d"}  # tagless d stays as isolate
    assert graph.edges == {("a", "b"): 2}  # cats + dogs, case-insensitive
    assert graph.nodes["a"]["tagCount"] == 3
    assert graph.nodes["a"]["viewCount"] == 9
    assert graph.nodes["d"]["tagCount"] == 0


def channel_list_item(cid: str) -> dict:
    return {
        "id": cid,
        "snippet": {"title": f"chan {cid}", "publishedAt": "2020-01-01T00:00:00Z", "country": "NL"},
        "statistics": {"viewCount": "5000", "subscriberCount": "100", "videoCount": "17"},
    }


def test_channel_details_prefixed_columns(make_client):
    from ytdt.modules import channel_details

    def handler(endpoint, params):
        if endpoint == "channels":
            # UC2 is deleted: the API simply omits it
            return {"items": [channel_list_item("UC1")]}
        raise AssertionError(endpoint)

    videos = [
        Video(video_id="a", channel_id="UC1"),
        Video(video_id="b", channel_id="UC1"),
        Video(video_id="c", channel_id="UC2"),
    ]
    details = channel_details(make_client(handler), videos)
    assert details["UC1"]["channel_country"] == "NL"
    assert details["UC1"]["channel_viewCount"] == 5000  # numeric for GEXF typing
    assert details["UC1"]["channel_publishedAt"] == "2020-01-01T00:00:00Z"
    assert isinstance(details["UC1"]["channel_daysActive"], int)
    # deleted channel still yields uniform (empty) columns
    assert details["UC2"] == {
        "channel_publishedAt": "", "channel_daysActive": "", "channel_country": "",
        "channel_viewCount": "", "channel_subscriberCount": "", "channel_videoCount": "",
    }


def test_shared_tag_network_merges_channel_details():
    videos = [Video(video_id="a", channel_id="UC1", tags=["x"])]
    details = {"UC1": {"channel_country": "NL", "channel_subscriberCount": 100}}
    graph = shared_tag_network(videos, channel_details=details)
    assert graph.nodes["a"]["channel_country"] == "NL"
    assert graph.nodes["a"]["channel_subscriberCount"] == 100


# -- comments ----------------------------------------------------------------


def owner_lookup(vid: str = "vid", channel: str = "UC_owner") -> dict:
    return {"items": [{"id": vid, "snippet": {"channelId": channel}}]}


def test_fetch_comments_uses_inline_replies_when_complete(make_client):
    def handler(endpoint, params):
        if endpoint == "videos":
            return owner_lookup(channel="UC_Alice")
        if endpoint == "commentThreads":
            return {
                "items": [
                    thread_item("t1", "Alice", total_replies=1, inline=[reply_item("r1", "Bob")]),
                    thread_item("t2", "Carol"),
                ]
            }
        raise AssertionError(f"unexpected call to {endpoint}")

    comments = fetch_comments(make_client(handler), "vid")
    assert [c.comment_id for c in comments] == ["t1", "r1", "t2"]
    assert comments[1].parent_author_name == "Alice"
    # Alice owns the video's channel (UC_Alice), Bob and Carol do not
    assert [c.is_channel_owner for c in comments] == ["yes", "no", "no"]


def test_fetch_comments_fetches_missing_replies(make_client):
    def handler(endpoint, params):
        if endpoint == "videos":
            return owner_lookup()
        if endpoint == "commentThreads":
            return {"items": [thread_item("t1", "Alice", total_replies=2, inline=[reply_item("r1", "Bob")])]}
        if endpoint == "comments":
            assert params["parentId"] == "t1"
            return {"items": [reply_item("r1", "Bob"), reply_item("r2", "Dave")]}
        raise AssertionError(endpoint)

    comments = fetch_comments(make_client(handler), "vid")
    assert [c.comment_id for c in comments] == ["t1", "r1", "r2"]


def test_fetch_comments_limit_uses_relevance(make_client):
    def handler(endpoint, params):
        if endpoint == "videos":
            return owner_lookup()
        assert params.get("order") == "relevance"
        return {"items": [thread_item(f"t{i}", f"A{i}") for i in range(5)], "nextPageToken": "x"}

    comments = fetch_comments(make_client(handler), "vid", limit=3)
    assert len(comments) == 3


def test_author_counts_and_interaction_network():
    def handler(endpoint, params):
        return {}

    from ytdt.models import Comment

    comments = [
        Comment(comment_id="t1", author_name="Alice", like_count=7),
        Comment(comment_id="r1", author_name="Bob", like_count=2, is_reply=1, parent_id="t1", parent_author_name="Alice"),
        Comment(comment_id="r2", author_name="Bob", like_count="", is_reply=1, parent_id="t1", parent_author_name="Alice"),
    ]
    counts = author_counts(comments)
    assert counts["Bob"] == 2
    graph = interaction_network(comments)
    assert graph.nodes[sha1_hex("Alice")]["commentCount"] == 1
    assert graph.edges[(sha1_hex("Bob"), sha1_hex("Alice"))] == 2
    # cumulative likes received per user; blanks count as 0
    assert graph.nodes[sha1_hex("Alice")]["likeCount"] == 7
    assert graph.nodes[sha1_hex("Bob")]["likeCount"] == 2


# -- co-comment network -------------------------------------------------------


def test_cocomment_networks_edges_from_shared_authors(make_client):
    commenters = {
        "v1": ["Ann", "Ben", "Cid"],
        "v2": ["Ben", "Cid", "Eva"],
        "v3": ["Zoe"],
    }

    def handler(endpoint, params):
        if endpoint == "videos":
            return {"items": [video_item(params["id"], channel=f"UC_{params['id']}")]}
        if endpoint == "commentThreads":
            vid = params["videoId"]
            return {"items": [thread_item(f"{vid}_{a}", a) for a in commenters[vid]]}
        if endpoint == "videoCategories":
            return {"items": [{"id": "22", "snippet": {"title": "People & Blogs"}}]}
        raise AssertionError(endpoint)

    video_graph, channel_graph = cocomment_networks(make_client(handler), ["v1", "v2", "v3"])
    assert set(video_graph.nodes) == {"v1", "v2", "v3"}
    assert video_graph.edges == {("v1", "v2"): 2}  # Ben + Cid
    assert channel_graph.edges == {("UC_v1", "UC_v2"): 1}
    assert video_graph.nodes["v1"]["seedRank"] == 1


# -- channel network ----------------------------------------------------------


def channel_item(cid: str) -> dict:
    return {
        "id": cid,
        "snippet": {"title": f"chan {cid}", "publishedAt": "2020-01-01T00:00:00Z", "country": "NL"},
        "statistics": {"subscriberCount": "10", "videoCount": "5", "viewCount": "12345"},
    }


def test_crawl_channel_network_depth_semantics(make_client):
    featured = {"UCa": ["UCb"], "UCb": ["UCa"], "UCc": ["UCd"], "UCd": []}
    subscribed = {"UCa": ["UCc"]}  # subscriptions are always followed too

    def handler(endpoint, params):
        if endpoint == "channels":
            return {"items": [channel_item(cid) for cid in params["id"].split(",")]}
        if endpoint == "channelSections":
            channels = featured[params["channelId"]]
            return {"items": [{"contentDetails": {"channels": channels}}]}
        if endpoint == "subscriptions":
            return {
                "items": [
                    {"snippet": {"resourceId": {"channelId": cid}}}
                    for cid in subscribed.get(params["channelId"], [])
                ]
            }
        raise AssertionError(endpoint)

    graph = crawl_channel_network(make_client(handler), ["UCa"], depth=1)
    # depth 1: seeds + their targets; UCd (linked from level-1 UCc) is not fetched
    assert set(graph.nodes) == {"UCa", "UCb", "UCc"}
    assert ("UCa", "UCc") in graph.edges  # via subscription
    assert ("UCa", "UCb") in graph.edges
    assert ("UCb", "UCa") in graph.edges  # backlink from level 1 to known node
    assert graph.nodes["UCa"]["isSeed"] == "yes"
    assert graph.nodes["UCb"]["isSeed"] == "no"
    # nodes carry the channel-list fields (converted to numbers where needed)
    assert graph.nodes["UCa"]["viewCount"] == 12345
    assert graph.nodes["UCa"]["subscriberCount"] == 10
    expected = {"label", "isSeed", "seedRank", "customUrl", "channelUrl", "description", "publishedAt",
                "daysActive", "defaultLanguage", "country", "madeForKids",
                "viewCount", "subscriberCount", "videoCount", "thumbnail",
                "keywords", "topicDetails"}
    assert set(graph.nodes["UCa"]) == expected


def test_crawl_depth_zero_only_seed_relations(make_client):
    featured = {"UCa": ["UCb", "UCx"], "UCb": []}

    def handler(endpoint, params):
        if endpoint == "channels":
            return {"items": [channel_item(cid) for cid in params["id"].split(",")]}
        if endpoint == "channelSections":
            return {"items": [{"contentDetails": {"channels": featured[params["channelId"]]}}]}
        if endpoint == "subscriptions":
            return {"items": []}  # private subscriptions
        raise AssertionError(endpoint)

    graph = crawl_channel_network(make_client(handler), ["UCa", "UCb"], depth=0)
    assert set(graph.nodes) == {"UCa", "UCb"}
    assert list(graph.edges) == [("UCa", "UCb")]


# -- resolve -------------------------------------------------------------------


def test_resolve_channel_references(make_client):
    def handler(endpoint, params):
        if endpoint == "channels" and "forHandle" in params:
            return {"items": [{"id": "UC_handle_result_0000000"}]}
        if endpoint == "channels" and "forUsername" in params:
            return {"items": [{"id": "UC_username_result_00000"}]}
        if endpoint == "search":
            return {"items": [{"id": {"channelId": "UC_search_result_0000000"}}]}
        raise AssertionError(endpoint)

    client = make_client(handler)
    cid = "UCtxGqPJPPi8ptAzB029jpYA"
    assert resolve_channel_id(client, cid) == cid
    assert resolve_channel_id(client, f"https://www.youtube.com/channel/{cid}") == cid
    assert resolve_channel_id(client, "@SomeHandle") == "UC_handle_result_0000000"
    assert (
        resolve_channel_id(client, "https://www.youtube.com/@SomeHandle/videos")
        == "UC_handle_result_0000000"
    )
    assert (
        resolve_channel_id(client, "https://www.youtube.com/user/oldname")
        == "UC_username_result_00000"
    )
    assert (
        resolve_channel_id(client, "https://www.youtube.com/c/VanityName")
        == "UC_search_result_0000000"
    )


def test_resolve_channel_ids_mixed_refs(make_client):
    def handler(endpoint, params):
        if endpoint == "channels" and "forHandle" in params:
            return {"items": [{"id": "UC_handle_result_0000000"}]}
        raise AssertionError(endpoint)

    client = make_client(handler)
    cid = "UCtxGqPJPPi8ptAzB029jpYA"
    refs = [
        cid,
        "@SomeHandle",
        f"https://www.youtube.com/channel/{cid}",  # duplicate of the plain id
        " ",
    ]
    assert resolve_channel_ids(client, refs) == [cid, "UC_handle_result_0000000"]
    # plain ids and channel URLs resolve without any API call
    assert len(client.requests) == 1


def test_rfc3339_expands_bare_dates():
    from ytdt.utils import rfc3339

    assert rfc3339("2024-01-01") == "2024-01-01T00:00:00Z"
    assert rfc3339(" 2024-01-01 ") == "2024-01-01T00:00:00Z"
    assert rfc3339("2024-01-01T12:30:00Z") == "2024-01-01T12:30:00Z"


def test_search_accepts_bare_dates(make_client):
    def handler(endpoint, params):
        assert endpoint == "search"
        assert params["publishedAfter"] == "2024-01-01T00:00:00Z"
        assert params["publishedBefore"] == "2024-02-01T00:00:00Z"
        return {"items": [{"id": {"videoId": "v1"}}]}

    client = make_client(handler)
    assert search_videos(
        client, "query", published_after="2024-01-01", published_before="2024-02-01"
    ) == ["v1"]


def test_channel_playlists_rows(make_client):
    channel_id = "UCtxGqPJPPi8ptAzB029jpYA"

    def handler(endpoint, params):
        assert endpoint == "playlists"
        assert params["channelId"] == channel_id
        if params.get("pageToken"):
            return {"items": [{
                "id": "PL2",
                "snippet": {"title": "Second", "publishedAt": "2022-01-01T00:00:00Z"},
                "contentDetails": {"itemCount": 2},
            }]}
        return {
            "items": [{
                "id": "PL1",
                "snippet": {
                    "title": "First",
                    "publishedAt": "2021-01-01T00:00:00Z",
                    "description": "line1\nline2",
                },
                "contentDetails": {"itemCount": 4},
            }],
            "nextPageToken": "page2",
        }

    from ytdt.modules import channel_playlists

    rows = channel_playlists(make_client(handler), channel_id)
    assert [r["playlistId"] for r in rows] == ["PL1", "PL2"]
    assert rows[0]["playlistUrl"] == "https://www.youtube.com/playlist?list=PL1"
    assert rows[0]["itemCount"] == 4
    assert rows[0]["description"] == "line1 line2"  # newlines squashed for CSV
