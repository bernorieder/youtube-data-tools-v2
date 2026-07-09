from __future__ import annotations

from ytdt_web import turnstile


def test_disabled_without_keys(monkeypatch):
    monkeypatch.delenv("YTDT_TURNSTILE_SITEKEY", raising=False)
    monkeypatch.delenv("YTDT_TURNSTILE_SECRET", raising=False)
    assert not turnstile.enabled()
    # everything passes so local setups work without any configuration
    assert turnstile.verify("") is True


def test_verify_posts_token(monkeypatch):
    monkeypatch.setenv("YTDT_TURNSTILE_SITEKEY", "site")
    monkeypatch.setenv("YTDT_TURNSTILE_SECRET", "secret")
    calls: dict = {}

    class Response:
        def __init__(self, success: bool):
            self._success = success

        def json(self) -> dict:
            return {"success": self._success}

    def fake_post(url, data=None, timeout=None):
        calls.update(url=url, data=data)
        return Response(data["response"] == "good-token")

    monkeypatch.setattr(turnstile.requests, "post", fake_post)
    assert turnstile.enabled()
    assert turnstile.verify("good-token") is True
    assert calls["url"] == turnstile.VERIFY_URL
    assert calls["data"] == {"secret": "secret", "response": "good-token"}
    assert turnstile.verify("bad-token") is False
    assert turnstile.verify("") is False  # no token, no request


def test_verify_fails_closed_on_network_error(monkeypatch):
    monkeypatch.setenv("YTDT_TURNSTILE_SITEKEY", "site")
    monkeypatch.setenv("YTDT_TURNSTILE_SECRET", "secret")

    def boom(*args, **kwargs):
        raise OSError("cloudflare unreachable")

    monkeypatch.setattr(turnstile.requests, "post", boom)
    assert turnstile.verify("token") is False
