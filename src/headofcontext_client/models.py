"""Plain dataclasses for the contract's response shapes. No validation library needed."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, TypeVar

T = TypeVar("T")


def scope(
    *,
    read: list[str] | None = None,
    act: list[str] | None = None,
    remember: list[str] | None = None,
) -> dict[str, Any]:
    """Build the ``scope`` body: ``scope(read=["document:*"], act=["tool:mail.*"])``."""
    caps = [{"kind": "read", "resource": r} for r in read or []]
    caps += [{"kind": "act", "resource": r} for r in act or []]
    caps += [{"kind": "remember", "resource": r} for r in remember or []]
    return {"capabilities": caps}


def _dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


@dataclass(frozen=True)
class Chain:
    subject: str
    actor: str
    root_actor: str
    depth: int
    scope: dict[str, Any]
    delegation: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Chain:
        return cls(
            subject=str(data["subject"]),
            actor=str(data["actor"]),
            root_actor=str(data["root_actor"]),
            depth=int(data["depth"]),
            scope=dict(data["scope"]),
            delegation=list(data.get("delegation", [])),
        )


@dataclass(frozen=True)
class IssuedToken:
    token: str = field(repr=False)
    chain: Chain
    expires_at: datetime | None
    revocation_ids: list[str]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> IssuedToken:
        return cls(
            token=str(data["token"]),
            chain=Chain.from_json(data["chain"]),
            expires_at=_dt(data.get("expires_at")),
            revocation_ids=[str(r) for r in data.get("revocation_ids", [])],
        )


@dataclass(frozen=True)
class Inspection:
    chain: Chain
    expires_at: datetime | None
    revocation_ids: list[str]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Inspection:
        return cls(
            chain=Chain.from_json(data["chain"]),
            expires_at=_dt(data.get("expires_at")),
            revocation_ids=[str(r) for r in data.get("revocation_ids", [])],
        )


@dataclass(frozen=True)
class Decision:
    outcome: str
    reason: str
    decision_id: str
    resource: str
    timestamp: datetime | None

    @property
    def allowed(self) -> bool:
        return self.outcome == "ALLOW"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Decision:
        return cls(
            outcome=str(data["outcome"]),
            reason=str(data["reason"]),
            decision_id=str(data["decision_id"]),
            resource=str(data["resource"]),
            timestamp=_dt(data.get("timestamp")),
        )


@dataclass(frozen=True)
class Approval:
    request_id: str
    subject: str
    actor: str
    tool: str
    status: str
    created_at: datetime | None
    expires_at: datetime | None
    resolved_by: str | None = None
    resolution_reason: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Approval:
        return cls(
            request_id=str(data["request_id"]),
            subject=str(data["subject"]),
            actor=str(data["actor"]),
            tool=str(data["tool"]),
            status=str(data["status"]),
            created_at=_dt(data.get("created_at")),
            expires_at=_dt(data.get("expires_at")),
            resolved_by=data.get("resolved_by"),
            resolution_reason=data.get("resolution_reason"),
        )


@dataclass(frozen=True)
class GateResult:
    decision: Decision
    approval: Approval | None = None

    @property
    def allowed(self) -> bool:
        return self.decision.allowed

    @property
    def pending(self) -> bool:
        return self.decision.outcome == "REQUIRE_APPROVAL" and self.approval is not None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> GateResult:
        approval = data.get("approval")
        return cls(
            decision=Decision.from_json(data["decision"]),
            approval=Approval.from_json(approval) if approval else None,
        )


@dataclass(frozen=True)
class FilterResult(Generic[T]):
    kept: list[T]
    dropped: list[str]
    strategy: str


@dataclass(frozen=True)
class Memory:
    memory_id: str
    content: str = field(repr=False)
    derived_from: list[str] = field(default_factory=list)
    written_for: str = ""
    written_by: str = ""
    created_at: datetime | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Memory:
        return cls(
            memory_id=str(data["memory_id"]),
            content=str(data.get("content", "")),
            derived_from=[str(d) for d in data.get("derived_from", [])],
            written_for=str(data.get("written_for", "")),
            written_by=str(data.get("written_by", "")),
            created_at=_dt(data.get("created_at")),
        )


@dataclass(frozen=True)
class Mandate:
    """A standing authorization from a human to an agent (ADR 0016)."""

    mandate_id: str
    subject: str
    agent: str
    scope: dict[str, Any]
    status: str
    created_at: datetime | None
    expires_at: datetime | None
    max_token_ttl_minutes: int
    created_by: str = ""
    revoked_at: datetime | None = None
    revocation_reason: str | None = None

    @property
    def active(self) -> bool:
        return self.status == "active"

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Mandate:
        return cls(
            mandate_id=str(data["mandate_id"]),
            subject=str(data["subject"]),
            agent=str(data["agent"]),
            scope=dict(data.get("scope", {})),
            status=str(data["status"]),
            created_at=_dt(data.get("created_at")),
            expires_at=_dt(data.get("expires_at")),
            max_token_ttl_minutes=int(data.get("max_token_ttl_minutes", 0)),
            created_by=str(data.get("created_by", "")),
            revoked_at=_dt(data.get("revoked_at")),
            revocation_reason=data.get("revocation_reason"),
        )
