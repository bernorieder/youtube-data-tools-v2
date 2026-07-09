from __future__ import annotations

from datetime import datetime, timezone

from ytdt.cache import FactCache
from ytdt.models import Video
from ytdt.modules import detect_shorts

OLD = "2020-01-01T00:00:00Z"


def test_fact_cache_roundtrip(tmp_path):
    cache = FactCache(tmp_path / "cache.db")
    cache.put_shorts({"a": "yes", "b": "no"})
    assert cache.get_shorts(["a", "b", "unknown"]) == {"a": "yes", "b": "no"}
    cache.put_shorts({"a": "no"})  # overwrite wins
    assert cache.get_shorts(["a"]) == {"a": "no"}
    cache.close()


def test_fact_cache_batches_large_lookups(tmp_path):
    cache = FactCache(tmp_path / "cache.db")
    cache.put_shorts({f"v{i}": "yes" for i in range(1200)})
    assert len(cache.get_shorts([f"v{i}" for i in range(1500)])) == 1200
    cache.close()


def test_detect_shorts_fills_and_reuses_cache(tmp_path, make_client):
    cache = FactCache(tmp_path / "cache.db")

    def handler(endpoint, params):
        return {"items": [{"contentDetails": {"videoId": "s1", "videoPublishedAt": OLD}}]}

    videos = [
        Video(video_id="s1", channel_id="UCa", duration="PT30S", published_at=OLD),
        Video(video_id="n1", channel_id="UCa", duration="PT45S", published_at=OLD),
        Video(video_id="long", channel_id="UCa", duration="PT10M", published_at=OLD),
    ]
    client = make_client(handler)
    detect_shorts(client, videos, cache=cache)
    assert [v.is_short for v in videos] == ["yes", "no", "no"]
    assert len(client.requests) == 1

    # second run resolves entirely from the cache: no requests at all
    rerun = [
        Video(video_id="s1", channel_id="UCa", duration="PT30S", published_at=OLD),
        Video(video_id="n1", channel_id="UCa", duration="PT45S", published_at=OLD),
        Video(video_id="long", channel_id="UCa", duration="PT10M", published_at=OLD),
    ]
    quiet = make_client(lambda e, p: {"items": []})
    detect_shorts(quiet, rerun, cache=cache)
    assert [v.is_short for v in rerun] == ["yes", "no", "no"]
    assert quiet.requests == []
    cache.close()


def test_detect_shorts_does_not_persist_recent_no(tmp_path, make_client):
    recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache = FactCache(tmp_path / "cache.db")

    # a fresh video missing from the Shorts playlist may just be indexing
    # lag: reported "no" but rechecked on the next run
    video = Video(video_id="v", channel_id="UCa", duration="PT30S", published_at=recent)
    detect_shorts(make_client(lambda e, p: {"items": []}), [video], cache=cache)
    assert video.is_short == "no"
    assert cache.get_shorts(["v"]) == {}

    # a recent "yes" is definitive and persisted
    fresh_yes = Video(video_id="w", channel_id="UCa", duration="PT30S", published_at=recent)
    handler = lambda e, p: {"items": [{"contentDetails": {"videoId": "w", "videoPublishedAt": recent}}]}
    detect_shorts(make_client(handler), [fresh_yes], cache=cache)
    assert cache.get_shorts(["w"]) == {"w": "yes"}
    cache.close()
