from __future__ import annotations

from ytdt.models import (
    Channel,
    Comment,
    Video,
    iso8601_duration_to_seconds,
    parse_channel_keywords,
)


def test_duration_parsing_includes_hours_and_days():
    assert iso8601_duration_to_seconds("PT4M13S") == 253
    assert iso8601_duration_to_seconds("PT1H2M3S") == 3723  # PHP version dropped the hour
    assert iso8601_duration_to_seconds("P1DT2H") == 93600
    assert iso8601_duration_to_seconds("PT30S") == 30
    assert iso8601_duration_to_seconds("") == 0
    assert iso8601_duration_to_seconds(None) == 0


def test_channel_keywords_quoted_phrases():
    assert parse_channel_keywords('news "digital methods" research "data critique"') == [
        "news",
        "research",
        "digital methods",
        "data critique",
    ]
    assert parse_channel_keywords("") == []
    assert parse_channel_keywords(None) == []


def test_video_from_api_row():
    item = {
        "id": "vid1",
        "snippet": {
            "channelId": "UCx",
            "channelTitle": "A  Channel",
            "publishedAt": "2024-01-02T03:04:05Z",
            "title": "Hello\nWorld",
            "description": "desc",
            "tags": ["Tag1", "tag2"],
            "categoryId": "22",
            "thumbnails": {"maxres": {"url": "http://img"}},
        },
        "contentDetails": {
            "duration": "PT1H0M10S",
            "dimension": "2d",
            "definition": "hd",
            "caption": "false",
            "licensedContent": True,
            "regionRestriction": {"allowed": ["DE", "NL"], "blocked": ["US"]},
        },
        "statistics": {"viewCount": "100", "likeCount": "10", "commentCount": "5"},
        "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Music"]},
        "paidProductPlacementDetails": {"hasPaidProductPlacement": False},
        "status": {"madeForKids": False},
    }
    row = Video.from_api(item).to_row()
    assert row["videoId"] == "vid1"
    assert row["videoTitle"] == "Hello World"
    assert row["publishedAtSQL"] == "2024-01-02 03:04:05"
    assert row["tags"] == "Tag1,tag2"
    assert row["topicCategories"] == "Music"
    assert row["durationSec"] == 3610
    assert row["thumbnail_maxres"] == "http://img"
    assert row["hasPaidProductPlacement"] is False
    assert row["regionRestrictionAllowed"] == "DE,NL"
    assert row["regionRestrictionBlocked"] == "US"
    assert row["madeForKids"] is False


def test_video_new_fields_default_empty():
    row = Video.from_api({"id": "v", "snippet": {"liveBroadcastContent": "none"}}).to_row()
    assert row["liveBroadcastContent"] == "none"
    assert row["regionRestrictionAllowed"] == ""
    assert row["regionRestrictionBlocked"] == ""
    assert row["madeForKids"] == ""


def test_channel_from_api_row():
    item = {
        "id": "UCy",
        "snippet": {
            "title": "Chan",
            "description": "d",
            "publishedAt": "2020-05-01T00:00:00Z",
            "country": "NL",
            "thumbnails": {"high": {"url": "http://t"}},
        },
        "statistics": {"viewCount": "1", "subscriberCount": "2", "videoCount": "3"},
        "brandingSettings": {"channel": {"keywords": 'a "b c"'}},
        "topicDetails": {"topicIds": ["/m/04rlf"]},
        "status": {"madeForKids": True},
    }
    row = Channel.from_api(item).to_row()
    assert row["id"] == "UCy"
    assert row["keywords"] == "a|b c"
    assert row["topicDetails"] == "Music (parent topic)"
    assert row["madeForKids"] is True
    # published 2020-05-01; daysActive grows with time but is well past 2000 by now
    assert isinstance(row["daysActive"], int) and row["daysActive"] > 2000


def test_channel_missing_status_and_date():
    row = Channel.from_api({"id": "UCz"}).to_row()
    assert row["madeForKids"] == ""
    assert row["daysActive"] == ""


def test_comment_pseudonymization_is_consistent():
    top = Comment(comment_id="c1", author_name="Alice", author_channel_id="UCa")
    reply = Comment(
        comment_id="c2",
        author_name="Bob",
        is_reply=1,
        parent_id="c1",
        parent_author_name="Alice",
    )
    ptop, preply = top.pseudonymized(), reply.pseudonymized()
    assert ptop.author_name != "Alice"
    assert len(ptop.author_name) == 40
    assert preply.parent_author_name == ptop.author_name  # same hash for same name
    assert preply.parent_id == ptop.comment_id
