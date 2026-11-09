"""Tests for the tool-call interception point and per-request credentials.

This is the substrate #14 (rate limits), #15 (OTel) and #16 (multi-tenant
gateway) all build on, so the properties worth pinning are the ones whose
violation would be *silent*:

- interception must happen on the real low-level request path, not just when a
  test calls `mcp.call_tool(...)`;
- credentials must not leak between concurrent requests.

Both have the same failure signature — everything passes, nothing works.
"""

import asyncio
import dataclasses

import pytest
from mcp import types
from mcp.server.fastmcp import FastMCP

from ks_mcp.middleware import (
    KnowledgeStackMCP,
    RequestCredentials,
    current_credentials,
    request_credentials,
    set_resolved_tenant,
)


def _call_request(name: str, arguments: dict) -> types.CallToolRequest:
    return types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )


async def _invoke_via_low_level(mcp: FastMCP, name: str, arguments: dict) -> None:
    """Drive a tool the way a real MCP client does.

    Deliberately *not* `mcp.call_tool(...)`: that is the shortcut that makes a
    broken interception point look like a working one.
    """
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    await handler(_call_request(name, arguments))


def _server_with_ping() -> tuple[KnowledgeStackMCP, list[str]]:
    mcp = KnowledgeStackMCP(name="test")
    seen: list[str] = []

    @mcp.tool()
    async def ping(value: int) -> int:
        """Return value + 1."""
        seen.append("tool-ran")
        return value + 1

    return mcp, seen


# ---------------------------------------------------------------------------
# interception reaches the real request path
# ---------------------------------------------------------------------------


async def test_hooks_fire_on_the_real_low_level_path() -> None:
    mcp, _ = _server_with_ping()
    before: list[str] = []
    after: list[str] = []
    mcp.add_hooks(before=lambda c: before.append(c.name), after=lambda c: after.append(c.name))

    await _invoke_via_low_level(mcp, "ping", {"value": 1})

    assert before == ["ping"]
    assert after == ["ping"]


async def test_monkeypatching_call_tool_would_not_have_worked() -> None:
    """Pins *why* this is a subclass rather than a wrapper.

    `FastMCP.__init__` runs `_setup_handlers()`, which binds `self.call_tool`
    at construction. Reassigning the attribute later leaves the low-level
    server holding the original, so a patch-based design intercepts nothing in
    production while passing any test that calls `mcp.call_tool` directly.

    If a future mcp release changes that, this test fails and the comment in
    middleware.py stops being true — which is the point.
    """
    plain = FastMCP(name="plain")

    @plain.tool()
    async def pong(value: int) -> int:
        """Return value + 1."""
        return value + 1

    intercepted: list[str] = []
    original = plain.call_tool

    async def patched(name: str, arguments: dict):
        intercepted.append(name)
        return await original(name, arguments)

    plain.call_tool = patched  # type: ignore[method-assign]

    await _invoke_via_low_level(plain, "pong", {"value": 1})

    assert intercepted == [], (
        "a post-construction patch of call_tool reached the request path; "
        "the subclass in middleware.py may no longer be necessary"
    )


async def test_after_hook_runs_and_records_failure() -> None:
    mcp = KnowledgeStackMCP(name="test")

    @mcp.tool()
    async def boom() -> str:
        """Always fails."""
        raise RuntimeError("kaboom")

    calls = []
    mcp.add_hooks(after=lambda c: calls.append(c))

    await _invoke_via_low_level(mcp, "boom", {})

    assert len(calls) == 1
    assert calls[0].failed is True
    assert isinstance(calls[0].error, Exception)
    assert calls[0].duration_s is not None


async def test_after_hook_runs_even_on_success_and_times_the_call() -> None:
    mcp, _ = _server_with_ping()
    calls = []
    mcp.add_hooks(after=lambda c: calls.append(c))

    await _invoke_via_low_level(mcp, "ping", {"value": 1})

    assert calls[0].failed is False
    assert calls[0].duration_s is not None and calls[0].duration_s >= 0.0


async def test_before_hook_can_refuse_a_call() -> None:
    """How #14 rejects an over-limit call: raise from `before`."""
    mcp, seen = _server_with_ping()

    def refuse(call) -> None:
        raise RuntimeError("rate limited")

    mcp.add_hooks(before=refuse)

    await _invoke_via_low_level(mcp, "ping", {"value": 1})

    assert seen == [], "the tool body ran despite the before-hook refusing"


async def test_a_broken_after_hook_does_not_break_the_tool() -> None:
    """Observability must not be able to fail a working call."""
    mcp, seen = _server_with_ping()

    def explode(call) -> None:
        raise RuntimeError("hook is broken")

    mcp.add_hooks(after=explode)

    await _invoke_via_low_level(mcp, "ping", {"value": 1})

    assert seen == ["tool-ran"]


async def test_hooks_nest_outermost_first() -> None:
    """before in order, after in reverse — so a pair brackets like a context manager.

    #15's span has to enclose #14's decision, which only holds if the ordering
    is nested rather than sequential.
    """
    mcp, _ = _server_with_ping()
    order: list[str] = []
    mcp.add_hooks(before=lambda c: order.append("before-1"), after=lambda c: order.append("after-1"))
    mcp.add_hooks(before=lambda c: order.append("before-2"), after=lambda c: order.append("after-2"))

    await _invoke_via_low_level(mcp, "ping", {"value": 1})

    assert order == ["before-1", "before-2", "after-2", "after-1"]


async def test_async_hooks_are_awaited() -> None:
    mcp, _ = _server_with_ping()
    seen: list[str] = []

    async def before(call) -> None:
        await asyncio.sleep(0)
        seen.append("before")

    async def after(call) -> None:
        await asyncio.sleep(0)
        seen.append("after")

    mcp.add_hooks(before=before, after=after)

    await _invoke_via_low_level(mcp, "ping", {"value": 1})

    assert seen == ["before", "after"]


async def test_hook_sees_the_tool_arguments() -> None:
    mcp, _ = _server_with_ping()
    captured = {}
    mcp.add_hooks(before=lambda c: captured.update(c.arguments))

    await _invoke_via_low_level(mcp, "ping", {"value": 7})

    assert captured == {"value": 7}


# ---------------------------------------------------------------------------
# per-request credentials (#16)
# ---------------------------------------------------------------------------


def test_credentials_are_unset_by_default() -> None:
    """Single-tenant mode: nothing bound, client.py falls back to the env."""
    assert current_credentials() is None


def test_request_credentials_bind_and_unbind() -> None:
    with request_credentials("sk-user-a", "https://a.example.com"):
        creds = current_credentials()
        assert creds is not None
        assert creds.api_key == "sk-user-a"
    assert current_credentials() is None


def test_nested_binds_restore_the_outer_credentials() -> None:
    """Reset-by-token, not set-to-None — else an inner request blanks its caller's."""
    with request_credentials("sk-user-outer", "https://outer.example.com"):
        with request_credentials("sk-user-inner", "https://inner.example.com"):
            inner = current_credentials()
            assert inner is not None and inner.api_key == "sk-user-inner"
        outer = current_credentials()
        assert outer is not None and outer.api_key == "sk-user-outer"


async def test_concurrent_requests_do_not_see_each_others_credentials() -> None:
    """The one failure mode here that is a data breach rather than a bug."""
    observed: dict[str, list[str | None]] = {}

    async def one_request(key: str) -> None:
        with request_credentials(key, f"https://{key}.example.com"):
            seen = []
            for _ in range(5):
                await asyncio.sleep(0)  # yield, inviting interleaving
                creds = current_credentials()
                seen.append(creds.api_key if creds else None)
            observed[key] = seen

    await asyncio.gather(*(one_request(f"sk-user-{i}") for i in range(8)))

    for key, seen in observed.items():
        assert set(seen) == {key}, f"{key} observed another request's credentials: {set(seen)}"


async def test_resolved_tenant_is_scoped_to_its_request() -> None:
    """A tenant resolved for A must never be visible to B — that was the
    `org._cached_tenant` module global's exact failure mode."""
    leaked: dict[str, object] = {}

    async def one_request(name: str) -> None:
        with request_credentials(f"sk-user-{name}", "https://x.example.com"):
            await asyncio.sleep(0)
            set_resolved_tenant(f"tenant-{name}")
            await asyncio.sleep(0)
            creds = current_credentials()
            leaked[name] = creds.tenant if creds else None

    await asyncio.gather(one_request("a"), one_request("b"), one_request("c"))

    assert leaked == {"a": "tenant-a", "b": "tenant-b", "c": "tenant-c"}


def test_set_resolved_tenant_is_a_noop_without_credentials() -> None:
    """So org.py can call it unconditionally in single-tenant mode."""
    set_resolved_tenant("should-be-ignored")
    assert current_credentials() is None


def test_credentials_are_frozen() -> None:
    """A hook must not be able to rewrite who the caller is."""
    creds = RequestCredentials(api_key="k", base_url="u")
    with pytest.raises(dataclasses.FrozenInstanceError):
        creds.api_key = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# client.py honours the request context
# ---------------------------------------------------------------------------


def test_api_client_follows_bound_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from ks_mcp.client import _reset_ambient_client_for_tests, get_api_client

    monkeypatch.setenv("KS_API_KEY", "sk-user-ambient")
    monkeypatch.setenv("KS_BASE_URL", "https://ambient.example.com")
    _reset_ambient_client_for_tests()

    with request_credentials("sk-user-request", "https://request.example.com"):
        client = get_api_client()
        assert client.configuration.host == "https://request.example.com"
        assert client.default_headers["Authorization"] == "Bearer sk-user-request"

    # Outside the request, back to the environment.
    assert get_api_client().configuration.host == "https://ambient.example.com"
    _reset_ambient_client_for_tests()


def test_request_scoped_clients_are_not_pooled_across_tenants() -> None:
    """Deliberately unpooled: a cache keyed coarser than the key is how one
    tenant ends up talking to the API as another."""
    from ks_mcp.client import get_api_client

    with request_credentials("sk-user-a", "https://a.example.com"):
        a = get_api_client()
    with request_credentials("sk-user-b", "https://b.example.com"):
        b = get_api_client()

    assert a is not b
    assert a.default_headers["Authorization"] != b.default_headers["Authorization"]


async def test_build_server_uses_the_intercepting_subclass() -> None:
    """If this regresses, #14/#15/#16 silently lose their interception point."""
    from ks_mcp.server import build_server

    mcp = build_server()
    assert isinstance(mcp, KnowledgeStackMCP)
    # And the low-level handler is bound to the override, not FastMCP's.
    assert mcp._mcp_server.request_handlers[types.CallToolRequest] is not None
