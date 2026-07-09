from __future__ import annotations

import pytest

from ytdt.errors import APIError, NotFoundError, QuotaExceededError


def error_payload(reason: str, message: str = "boom") -> dict:
    return {"error": {"message": message, "errors": [{"reason": reason}]}}


def test_get_returns_payload(make_client):
    client = make_client(lambda endpoint, params: {"items": [{"id": "x"}]})
    assert client.get("videos", id="x")["items"] == [{"id": "x"}]
    assert client.call_count == 1


def test_get_drops_empty_params_and_adds_key(make_client):
    client = make_client(lambda endpoint, params: {})
    client.get("videos", id="x", regionCode=None, q="")
    endpoint, params = client.requests[0]
    assert params == {"id": "x", "key": "test-key"}


def test_retry_on_backend_error(make_client, monkeypatch):
    monkeypatch.setattr("ytdt.client.time.sleep", lambda s: None)
    calls = {"n": 0}

    def handler(endpoint, params):
        calls["n"] += 1
        if calls["n"] < 3:
            return error_payload("backendError")
        return {"items": []}

    client = make_client(handler)
    assert client.get("videos", id="x") == {"items": []}
    assert calls["n"] == 3


def test_retries_exhausted_raises(make_client, monkeypatch):
    monkeypatch.setattr("ytdt.client.time.sleep", lambda s: None)
    client = make_client(lambda e, p: error_payload("backendError"), max_retries=2)
    with pytest.raises(APIError):
        client.get("videos", id="x")
    assert client.call_count == 3


def test_quota_exceeded_is_fatal(make_client):
    client = make_client(lambda e, p: error_payload("quotaExceeded"))
    with pytest.raises(QuotaExceededError):
        client.get("videos", id="x")
    assert client.call_count == 1  # no retries


def test_skippable_reason_maps_to_typed_error(make_client):
    client = make_client(lambda e, p: error_payload("videoNotFound"))
    with pytest.raises(NotFoundError):
        client.get("videos", id="x")


def test_quota_accounting(make_client):
    client = make_client(lambda e, p: {})
    client.get("search", q="x")
    client.get("videos", id="x")
    assert client.quota_used == 101


def test_paginate_follows_tokens(make_client):
    pages = {
        None: {"items": [{"n": 1}, {"n": 2}], "nextPageToken": "t2"},
        "t2": {"items": [{"n": 3}], "nextPageToken": "t3"},
        "t3": {"items": [{"n": 4}]},
    }
    client = make_client(lambda e, p: pages[p.get("pageToken")])
    assert [i["n"] for i in client.paginate("videos")] == [1, 2, 3, 4]


def test_paginate_respects_limit(make_client):
    pages = {
        None: {"items": [{"n": 1}, {"n": 2}], "nextPageToken": "t2"},
        "t2": {"items": [{"n": 3}, {"n": 4}]},
    }
    client = make_client(lambda e, p: pages[p.get("pageToken")])
    assert [i["n"] for i in client.paginate("videos", limit=3)] == [1, 2, 3]


def test_map_preserves_order_and_reports_progress(make_client):
    client = make_client(lambda e, p: {})
    events = []
    client.on_progress = lambda desc, done, total: events.append((desc, done, total))
    result = client.map(lambda x: x * 2, [3, 1, 2], desc="doubling")
    assert result == [6, 2, 4]
    assert events[-1] == ("doubling", 3, 3)


def test_map_propagates_exceptions(make_client):
    client = make_client(lambda e, p: {})

    def boom(x):
        raise ValueError("nope")

    with pytest.raises(ValueError):
        client.map(boom, [1, 2, 3])
