"""headofcontext-client: thin client for the HeadOfContext service."""

from headofcontext_client.auth import KeycloakClientCredentials, StaticToken, TokenProvider
from headofcontext_client.client import HeadOfContext, Session
from headofcontext_client.errors import (
    Forbidden,
    HeadOfContextError,
    InvalidRequest,
    Unauthorized,
    Unavailable,
)
from headofcontext_client.guard import ActionDenied, ApprovalPending, ToolGuard
from headofcontext_client.models import (
    Approval,
    Chain,
    Decision,
    FilterResult,
    GateResult,
    Inspection,
    IssuedToken,
    Mandate,
    Memory,
    scope,
)

__all__ = [
    "ActionDenied",
    "Approval",
    "ApprovalPending",
    "Chain",
    "Decision",
    "FilterResult",
    "Forbidden",
    "GateResult",
    "HeadOfContext",
    "HeadOfContextError",
    "Inspection",
    "InvalidRequest",
    "IssuedToken",
    "KeycloakClientCredentials",
    "Mandate",
    "Memory",
    "Session",
    "StaticToken",
    "TokenProvider",
    "ToolGuard",
    "Unauthorized",
    "Unavailable",
    "scope",
]
__version__ = "0.2.1"  # x-release-please-version
