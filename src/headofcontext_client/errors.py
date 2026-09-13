"""Errors. The reason codes mirror the service's ``reason`` field."""

from __future__ import annotations


class HeadOfContextError(Exception):
    def __init__(self, reason: str, detail: str = "", *, status: int = 0) -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
        self.status = status


class Unauthorized(HeadOfContextError):
    """The bearer token was missing or invalid (HTTP 401)."""


class Forbidden(HeadOfContextError):
    """A token, caller or invariant problem (HTTP 403). Decisions never raise this."""


class Unavailable(HeadOfContextError):
    """The service could not decide: engine, journal or connector unavailable (HTTP 503)."""


class InvalidRequest(HeadOfContextError):
    """The request did not match the contract (HTTP 422)."""
