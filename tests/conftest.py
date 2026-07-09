from __future__ import annotations

from typing import Any, Callable

import pytest

from ytdt.client import YouTubeClient

Handler = Callable[[str, dict[str, Any]], dict]


class FakeClient(YouTubeClient):
    """Client whose HTTP layer is replaced by a handler(endpoint, params) -> payload."""

    def __init__(self, handler: Handler, **kwargs):
        kwargs.setdefault("api_key", "test-key")
        kwargs.setdefault("max_workers", 4)
        kwargs.setdefault("session", object())  # never used
        super().__init__(**kwargs)
        self._handler = handler
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def _http_get(self, endpoint: str, params: dict[str, Any]) -> tuple[int, dict]:
        self.requests.append((endpoint, dict(params)))
        return 200, self._handler(endpoint, params)


@pytest.fixture
def make_client():
    def factory(handler: Handler, **kwargs) -> FakeClient:
        return FakeClient(handler, **kwargs)

    return factory
