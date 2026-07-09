"""Cloudflare Turnstile bot protection (optional).

Active only when both ``YTDT_TURNSTILE_SITEKEY`` and
``YTDT_TURNSTILE_SECRET`` are set; without them every check passes, so
local development needs no setup. The keys come from the Cloudflare
dashboard (Turnstile widget for the deployed domain). For testing,
Cloudflare provides fixed keys that always pass: sitekey
``1x00000000000000000000AA`` with secret
``1x0000000000000000000000000000000AA`` (a ``2x...`` pair always fails).
"""

from __future__ import annotations

import os

import requests

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def site_key() -> str:
    return os.environ.get("YTDT_TURNSTILE_SITEKEY", "")


def enabled() -> bool:
    return bool(site_key() and os.environ.get("YTDT_TURNSTILE_SECRET"))


def verify(token: str) -> bool:
    """Check a widget token with Cloudflare; fails closed on any error."""
    if not enabled():
        return True
    if not token:
        return False
    try:
        response = requests.post(
            VERIFY_URL,
            data={"secret": os.environ["YTDT_TURNSTILE_SECRET"], "response": token},
            timeout=10,
        )
        return bool(response.json().get("success"))
    except Exception:
        return False
