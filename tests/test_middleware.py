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
from typing import Any

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


async def _invoke_via_low_level(
    mcp: FastMCP, name: str, arguments: dict
) -> types.CallToolResult:
    """Drive a tool the way a real MCP client does, returning the result.

    Deliberately *not* `mcp.call_tool(...)`: that is the shortcut that makes a
    broken interception point look like a working one. The result is returned
    so tests can assert on `isError`, not merely on side effects — asserting
    only side effects is what made two tests here pass against broken code.
    """
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    response = await handler(_call_request(name, arguments))
    result = response.root
    assert isinstance(result, types.CallToolResult), f"unexpected result type: {type(result)}"
    return result


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
    """Observability must not be able to fail a working call.

    Was a false green: it asserted only that the tool *body* ran, which it does
    before the hook raises. With the protection removed the test still passed.
    Now it asserts the client sees a successful result.
    """
    mcp, seen = _server_with_ping()

    def explode(call) -> None:
        raise RuntimeError("hook is broken")

    mcp.add_hooks(after=explode)

    result = await _invoke_via_low_level(mcp, "ping", {"value": 1})

    assert seen == ["tool-ran"]
    assert result.isError is False, (
        f"a broken after-hook turned a working call into an error: {result.content}"
    )


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


async def test_contextvars_set_outside_the_call_do_not_reach_a_session_task() -> None:
    """Why credentials are bound inside `call_tool` and never in ASGI middleware.

    Over Streamable HTTP in the default stateful mode, tool calls run in a
    per-session task that `StreamableHTTPSessionManager` starts **once**, with
    `task_group.start(run_server)`. `asyncio.create_task` copies the caller's
    context at creation, so that task permanently inherits the context of the
    request that *initialised the session*.

    Binding credentials in middleware would therefore pin every later call in
    that session to the first caller's credentials — the cross-tenant bleed
    this module exists to prevent. This reproduces the mechanism directly, so
    nobody re-introduces the "just set it in middleware" design.
    """
    seen: list[str | None] = []
    stop = asyncio.Event()

    async def session_task() -> None:
        # Stands in for mcp's run_server: started once, lives for the session.
        for _ in range(3):
            await asyncio.sleep(0)
            creds = current_credentials()
            seen.append(creds.api_key if creds else None)
        stop.set()

    async def initialising_request() -> None:
        with request_credentials("sk-user-session-initialiser", "https://a.example.com"):
            asyncio.create_task(session_task())
            await stop.wait()

    async def later_request() -> None:
        with request_credentials("sk-user-second-caller", "https://b.example.com"):
            await asyncio.sleep(0)

    await asyncio.gather(initialising_request(), later_request())

    assert set(seen) == {"sk-user-session-initialiser"}, (
        "the session task saw a later request's credentials, or none; either way "
        "the inheritance model assumed here has changed and the binding strategy "
        "in middleware.py should be re-checked"
    )


async def test_resolver_binds_credentials_per_call() -> None:
    """The supported #16 path: bind inside call_tool, from the per-message request."""
    mcp, _ = _server_with_ping()
    observed: list[str | None] = []
    mcp.add_hooks(
        before=lambda c: observed.append(c.credentials.api_key if c.credentials else None)
    )

    keys = iter(["sk-user-one", "sk-user-two"])
    mcp.set_credentials_resolver(
        lambda request: RequestCredentials(api_key=next(keys), base_url="https://x.example.com")
    )

    await _invoke_via_low_level(mcp, "ping", {"value": 1})
    await _invoke_via_low_level(mcp, "ping", {"value": 2})

    assert observed == ["sk-user-one", "sk-user-two"], (
        "consecutive calls did not get their own credentials"
    )


async def test_credentials_are_unbound_again_after_the_call() -> None:
    mcp, _ = _server_with_ping()
    mcp.set_credentials_resolver(
        lambda request: RequestCredentials(api_key="sk-user-x", base_url="https://x.example.com")
    )

    await _invoke_via_low_level(mcp, "ping", {"value": 1})

    assert current_credentials() is None


async def test_a_resolver_returning_none_falls_back_to_the_environment() -> None:
    mcp, _ = _server_with_ping()
    observed: list[object] = []
    mcp.add_hooks(before=lambda c: observed.append(c.credentials))
    mcp.set_credentials_resolver(lambda request: None)

    await _invoke_via_low_level(mcp, "ping", {"value": 1})

    assert observed == [None]


async def test_the_request_scoped_client_is_closed_after_the_call() -> None:
    """Request clients aren't pooled, so an unclosed one leaks a connection
    pool and an ssl_context per call."""
    from ks_mcp.middleware import current_request_client

    mcp = KnowledgeStackMCP(name="test")
    captured: list[Any] = []

    @mcp.tool()
    async def uses_client() -> str:
        """Touches the KS client the way a real tool does."""
        from ks_mcp.client import get_api_client

        captured.append(get_api_client())
        # Same client on a second call within the request, not a second one.
        captured.append(get_api_client())
        return "ok"

    mcp.set_credentials_resolver(
        lambda request: RequestCredentials(api_key="sk-user-x", base_url="https://x.example.com")
    )

    await _invoke_via_low_level(mcp, "uses_client", {})

    assert captured[0] is captured[1], "a second get_api_client() built another client"
    assert current_request_client() is None, "the request client was not released"
    # Was a false green: clearing the contextvar happens whether or not the
    # client is actually closed, so the test passed with the close removed.
    # `ApiClient.close()` -> `rest_client.close()` -> `pool_manager.aclose()`,
    # and httpx sets `is_closed`, so that is the real observable.
    pool = captured[0].rest_client.pool_manager
    assert pool is not None and pool.is_closed, (
        "the request-scoped client was released but its connection pool was "
        "never closed — this leaks a pool and an ssl_context per call"
    )


def test_request_scoped_clients_are_not_shared_across_tenants() -> None:
    """One client per request, never reused under different credentials.

    Caught a real bug when written: `request_credentials` reset the credentials
    contextvar but not the cached-client one, so a second binding in the same
    task was handed the *first* tenant's client — the same bleed, by another
    route. Both are now scoped together.
    """
    from ks_mcp.client import get_api_client

    with request_credentials("sk-user-a", "https://a.example.com"):
        a = get_api_client()
    with request_credentials("sk-user-b", "https://b.example.com"):
        b = get_api_client()

    assert a is not b
    assert a.default_headers["Authorization"] != b.default_headers["Authorization"]
    assert b.configuration.host == "https://b.example.com"


def test_effective_credentials_follow_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything talking to KS outside the pooled ApiClient must use these.

    `ask` streams over its own httpx client. Reading the env there made it
    create the thread as the caller and then read the stream back as the
    *process* — in gateway mode a cross-identity mismatch, not merely an
    inconsistency.
    """
    from ks_mcp.client import get_effective_api_key, get_effective_base_url

    monkeypatch.setenv("KS_API_KEY", "sk-user-process")
    monkeypatch.setenv("KS_BASE_URL", "https://process.example.com")

    assert get_effective_api_key() == "sk-user-process"
    assert get_effective_base_url() == "https://process.example.com"

    with request_credentials("sk-user-caller", "https://caller.example.com"):
        assert get_effective_api_key() == "sk-user-caller"
        assert get_effective_base_url() == "https://caller.example.com"

    assert get_effective_api_key() == "sk-user-process"


def test_ask_streams_with_the_effective_credentials() -> None:
    """Pins the call site, since the bug was a plain `get_api_key()` there."""
    import inspect

    from ks_mcp.tools import ask

    source = inspect.getsource(ask)
    assert "get_effective_api_key()" in source
    assert "get_effective_base_url()" in source
    # The env readers must not be reachable from the streaming path again.
    assert "get_api_key()" not in source
    assert "get_base_url()" not in source


async def test_an_already_entered_hook_is_unwound_when_a_later_one_refuses() -> None:
    """A refusal must not leak an earlier hook's span or concurrency slot.

    This is what "behaves like nested context managers" has to mean. Before the
    fix, hook A's `before` ran, hook B's `before` raised, and A's `after` never
    ran — so #15 would leak a span and #14 would leak the slot it had just
    acquired, starving its own limiter.
    """
    mcp, ran = _server_with_ping()
    order: list[str] = []

    mcp.add_hooks(
        before=lambda c: order.append("A.enter"), after=lambda c: order.append("A.exit")
    )

    def refuse(call) -> None:
        raise RuntimeError("rate limited")

    mcp.add_hooks(before=refuse, after=lambda c: order.append("B.exit"))

    await _invoke_via_low_level(mcp, "ping", {"value": 1})

    assert ran == [], "the tool body ran despite a refusal"
    assert order == ["A.enter", "A.exit"], (
        f"expected A to unwind and B never to have entered, got {order}"
    )


async def test_hook_pairs_stay_matched_when_registered_separately() -> None:
    """`add_hooks(before=f)` then `add_hooks(after=g)` must not pair f with g.

    Two independent lists matched them by positional accident, so the unwind
    would have run the wrong partner.
    """
    mcp, _ = _server_with_ping()
    order: list[str] = []

    mcp.add_hooks(before=lambda c: order.append("lonely-before"))
    mcp.add_hooks(after=lambda c: order.append("lonely-after"))

    await _invoke_via_low_level(mcp, "ping", {"value": 1})

    assert order == ["lonely-before", "lonely-after"]
    assert mcp._before_hooks and mcp._after_hooks


async def test_build_server_uses_the_intercepting_subclass() -> None:
    """If this regresses, #14/#15/#16 silently lose their interception point."""
    from ks_mcp.server import build_server

    mcp = build_server()
    assert isinstance(mcp, KnowledgeStackMCP)
    # And the low-level handler is bound to the override, not FastMCP's.
    assert mcp._mcp_server.request_handlers[types.CallToolRequest] is not None
