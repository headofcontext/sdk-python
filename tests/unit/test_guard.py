import httpx

from headofcontext_client import HeadOfContext, ToolGuard, scope
from tests.unit.conftest import DECISION, FakeService


async def test_guard_allows_and_denies(service: FakeService, client: HeadOfContext) -> None:
    session = await client.issue("user-jwt", scope(act=["tool:*"]))
    guard = ToolGuard(session, tool_map={"send_mail": "mail.send"})

    async def send_mail(to: str) -> str:
        return f"sent to {to}"

    wrapped = guard.wrap_async("send_mail", send_mail)
    assert await wrapped(to="bob") == "sent to bob"
    assert service.requests[-1][2]["tool"] == "tool:mail.send" and service.requests[-1][2][
        "args"
    ] == {"to": "bob"}

    service.responses["/v1/actions/gate"] = (
        200,
        {"decision": {**DECISION, "outcome": "DENY", "reason": "not_related"}, "approval": None},
    )
    out = await wrapped(to="bob")
    assert out.startswith("HeadOfContext denied") and "not_related" in out


def test_guard_sync_wrapper() -> None:
    service = FakeService()
    hoc = HeadOfContext(
        "http://hoc.test", agent_token="t", transport=httpx.MockTransport(service.handler)
    )
    import asyncio

    session = asyncio.run(hoc.issue("user-jwt", scope(act=["tool:*"])))
    guard = ToolGuard(session)

    def export(year: int) -> str:
        return f"exported {year}"

    assert guard.wrap("hr.export", export)(2026) == "exported 2026"
    assert service.requests[-1][2]["args"] == {"year": 2026}
