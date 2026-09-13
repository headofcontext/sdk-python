"""ToolGuard: wrap tool functions so every call is authorized by the service first."""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from headofcontext_client.client import Session
from headofcontext_client.errors import HeadOfContextError
from headofcontext_client.models import GateResult

OnDeny = Literal["raise", "message"]


class ActionDenied(HeadOfContextError):
    pass


class ApprovalPending(HeadOfContextError):
    def __init__(self, request_id: str, tool: str) -> None:
        super().__init__(
            "approval_required", f"tool {tool!r} requires approval (request {request_id})"
        )
        self.request_id = request_id
        self.tool = tool


class ToolGuard:
    def __init__(
        self,
        session: Session,
        *,
        on_deny: OnDeny = "message",
        tool_map: Mapping[str, str] | None = None,
    ) -> None:
        self._session = session
        self._on_deny = on_deny
        self._tool_map = dict(tool_map or {})

    def resource_for(self, name: str) -> str:
        resource = self._tool_map.get(name, name)
        return resource if resource.startswith("tool:") else f"tool:{resource}"

    async def authorize(self, name: str, args: Mapping[str, Any]) -> GateResult:
        result = await self._session.gate(self.resource_for(name), dict(args))
        if result.allowed:
            return result
        if result.pending and result.approval is not None:
            raise ApprovalPending(result.approval.request_id, name)
        raise ActionDenied(result.decision.reason, f"tool {name!r} denied")

    def wrap_async(
        self, name: str, fn: Callable[..., Awaitable[Any]]
    ) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def guarded(*args: Any, **kwargs: Any) -> Any:
            try:
                await self.authorize(name, _bind(fn, args, kwargs))
            except (ActionDenied, ApprovalPending) as exc:
                return self._refuse(name, exc)
            return await fn(*args, **kwargs)

        return guarded

    def wrap(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            try:
                _run_sync(self.authorize(name, _bind(fn, args, kwargs)))
            except (ActionDenied, ApprovalPending) as exc:
                return self._refuse(name, exc)
            return fn(*args, **kwargs)

        return guarded

    def _refuse(self, name: str, exc: ActionDenied | ApprovalPending) -> str:
        if self._on_deny == "raise":
            raise exc
        if isinstance(exc, ApprovalPending):
            return (
                f"HeadOfContext: tool '{name}' requires approval (request {exc.request_id}). "
                "Do not retry until it is approved."
            )
        return f"HeadOfContext denied tool '{name}': {exc.reason}. Do not retry."


def _bind(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        return dict(inspect.signature(fn).bind_partial(*args, **kwargs).arguments)
    except (TypeError, ValueError):
        return {"args": list(args), **kwargs}


def _run_sync(coro: Awaitable[Any]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await(coro))
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(_await(coro))
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=runner, name="hoc-client-guard", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


async def _await(coro: Awaitable[Any]) -> Any:
    return await coro
