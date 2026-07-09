"""Exception hierarchy for ytdt.

The YouTube API reports errors with a machine-readable ``reason``. Reasons
fall into three groups: fatal (quota exhausted, bad key), retryable
(transient backend problems), and skippable (a single item is missing,
private, or has comments disabled — the rest of a job can proceed).
Skippable reasons map to :class:`SkippableError` subclasses so callers can
catch one type and move on.
"""

from __future__ import annotations


class YTDTError(Exception):
    """Base class for all ytdt errors."""


class ConfigurationError(YTDTError):
    """The client is misconfigured (e.g. no API key available)."""


class APIError(YTDTError):
    """The YouTube API returned an error that could not be resolved."""

    def __init__(self, message: str, *, reason: str | None = None, status: int | None = None):
        super().__init__(message)
        self.reason = reason
        self.status = status


class QuotaExceededError(APIError):
    """The daily API quota is exhausted; no further requests will succeed."""


class SkippableError(APIError):
    """An error affecting a single item; the surrounding job can continue."""


class NotFoundError(SkippableError):
    """The requested video/channel/playlist does not exist (anymore)."""


class ForbiddenError(SkippableError):
    """The requested resource exists but is not accessible (private, suspended)."""


class CommentsDisabledError(SkippableError):
    """Comments are disabled for the requested video."""


class ProcessingError(SkippableError):
    """YouTube reported a processing failure for this item."""


# API error reasons that abort a run: the quota is gone for the day.
FATAL_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})

# Reasons worth retrying with backoff.
RETRYABLE_REASONS = frozenset({"backendError", "internalError", "rateLimitExceeded", "userRateLimitExceeded"})

# Reasons that only affect the current item.
SKIPPABLE_REASONS: dict[str, type[SkippableError]] = {
    "notFound": NotFoundError,
    "videoNotFound": NotFoundError,
    "channelNotFound": NotFoundError,
    "playlistNotFound": NotFoundError,
    "playlistItemsNotAccessible": ForbiddenError,
    "forbidden": ForbiddenError,
    "subscriptionForbidden": ForbiddenError,
    "channelClosed": ForbiddenError,
    "channelSuspended": ForbiddenError,
    "commentsDisabled": CommentsDisabledError,
    "processingFailure": ProcessingError,
}
