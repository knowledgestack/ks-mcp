"""The single interception point for every tool call, and per-request credentials.

This is the substrate three roadmap issues need and none of them should have to
build:

* per-tool rate limits and concurrency caps (#14)
* OpenTelemetry spans on every tool call (#15)
* multi-tenant gateway mode, one process serving many API keys (#16)

All three want to do something *around* every call. Without a choke point each
would have to touch all 17 tool registrations, and the three would collide.

Why a FastMCP subclass and not a wrapper
----------------------------------------
``FastMCP.__init__`` runs ``_setup_handlers()``, which does::

    self._mcp_server.call_tool(validate_input=False)(self.call_tool)

That captures ``self.call_tool`` as a **bound method, at construction time**.
So assigning ``mcp.call_tool = wrapper`` afterwards does nothing to real
traffic: the low-level server still holds the original. Tests that invoke
``mcp.call_tool(...)`` directly would see the wrapper and pass, while
production intercepted nothing — green tests, zero coverage.

Overriding ``call_tool`` in a subclass works because ``self.call_tool``
resolves through the MRO during ``__init__``, so the low-level server binds the
override. Both behaviours are pinned by tests in ``tests/test_middleware.py``.

Why contextvars for credentials
-------------------------------
``get_api_client()`` used to be an ``@lru_cache(maxsize=1)`` process singleton
and ``org.py`` kept its resolved tenant in a module global. One process, one
key, one tenant — fine for stdio, fatal for #16: over Streamable HTTP two
tenants share the process, and a module-global tenant means tenant A's identity
is served to tenant B. That is a data breach, not a bug.

Credentials live in a :class:`RequestCredentials` contextvar. **How that
contextvar gets set is load-bearing, and the obvious way is wrong.**

Over Streamable HTTP in the default stateful mode, tool calls do *not* run in
the HTTP request's task. ``StreamableHTTPSessionManager`` starts its
``run_server`` task **once per session**, via ``task_group.start(run_server)``,
and ``asyncio.create_task`` copies the caller's context at creation. So the
session task permanently inherits the context of the request that *initialised
the session*. Binding credentials in ASGI middleware — the natural reading of
"populate it from the request" — would pin every later tool call in that
session to the first caller's credentials: the exact cross-tenant bleed this
module exists to prevent. Verified by reproduction; see
``tests/test_middleware.py::test_contextvars_set_outside_the_call_do_not_reach_a_session_task``.

So binding happens **inside** ``call_tool`` instead, from the *per-message*
request context (``request_ctx`` is set per message in the low-level server,
before our override runs). Same task as the tool, one message, no inheritance.
Supply a resolver with :meth:`KnowledgeStackMCP.set_credentials_resolver`;
without one, ``client.py`` falls back to the environment, which is exactly
today's single-tenant behaviour.
"""

import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ContentBlock

from ks_mcp.client import logger


@dataclass(frozen=True)
class RequestCredentials:
    """The credentials and resolved identity for one in-flight request.

    Frozen so a hook cannot mutate another hook's view of who is calling.
    ``tenant`` is filled in lazily by ``org.py`` once resolved, via
    :func:`set_resolved_tenant`, so the lookup is cached per request instead of
    per process.
    """

    api_key: str
    base_url: str
    # Opaque to this module — `OrganizationInfo`, but importing it here would
    # make schema.py depend on middleware and back.
    tenant: Any | None = None


_credentials: ContextVar[RequestCredentials | None] = ContextVar(
    "ks_mcp_credentials", default=None
)

# The ApiClient built for the in-flight request. Held separately from
# RequestCredentials (which is frozen) so `get_api_client()` can build it
# lazily and `call_tool` can close it afterwards. Without this, every
# `get_api_client()` call under bound credentials built a fresh
# httpx2.AsyncClient — its own connection pool and ssl_context — and nothing
# ever closed them: an FD-exhaustion path in exactly the hosted mode #16
# targets. A single gated write call builds at least two (the identity lookup
# in writes.py plus the tool body).
_request_client: ContextVar[Any | None] = ContextVar("ks_mcp_request_client", default=None)


# The ToolCall for the in-flight invocation, so a tool body can reach its own
# call without the framework threading it through. FastMCP passes a tool only
# its declared arguments, so there is no other way for a tool to annotate its
# own audit record. Set and read within one awaited chain in the same task, so
# unlike credentials this needs no per-message machinery.
_current_call: ContextVar["ToolCall | None"] = ContextVar("ks_mcp_current_call", default=None)


def current_tool_call() -> "ToolCall | None":
    """The ToolCall being handled, or None outside a tool call."""
    return _current_call.get()


def current_request_client() -> Any | None:
    """The ApiClient already built for this request, if any."""
    return _request_client.get()


def set_request_client(client: Any) -> None:
    """Cache the ApiClient for the remainder of this request."""
    _request_client.set(client)


def current_credentials() -> RequestCredentials | None:
    """Credentials for the in-flight request, or None in single-tenant mode."""
    return _credentials.get()


def set_resolved_tenant(tenant: Any) -> None:
    """Attach a resolved tenant to the current request's credentials.

    A no-op when no credentials are bound (single-tenant mode), so ``org.py``
    can call it unconditionally.
    """
    creds = _credentials.get()
    if creds is None:
        return
    _credentials.set(
        RequestCredentials(api_key=creds.api_key, base_url=creds.base_url, tenant=tenant)
    )


@contextmanager
def request_credentials(api_key: str, base_url: str):
    """Bind credentials for the current task.

    **Do not call this from ASGI middleware or an HTTP handler.** Over
    Streamable HTTP the tool runs in the session task, not the request task, so
    a binding made there is invisible to the tool — and worse, a binding made
    during session initialisation is inherited by every later call in that
    session. See the module docstring.

    The supported path is :meth:`KnowledgeStackMCP.set_credentials_resolver`,
    which binds inside ``call_tool``. This context manager is for that, for
    stdio, and for tests.
    """
    token = _credentials.set(RequestCredentials(api_key=api_key, base_url=base_url))
    # The cached client belongs to the *previous* credentials, so it must not
    # be visible under these. Without this reset, a second binding in the same
    # task would be handed the first tenant's client — which is the bleed this
    # whole module is about, arriving by a different route.
    client_token = _request_client.set(None)
    try:
        yield
    finally:
        # Reset rather than set-to-None: nested binds must restore the outer
        # value, or an inner request would blank its caller's credentials.
        _credentials.reset(token)
        _request_client.reset(client_token)


@dataclass
class ToolCall:
    """One tool invocation, as seen by the hooks.

    ``arguments`` is the raw argument dict. Treat it as sensitive: for KS tools
    it routinely contains document text and user questions. The audit log
    hashes it rather than storing it (see ``writes.py``), and hooks that log
    should do the same.
    """

    name: str
    arguments: dict[str, Any]
    credentials: RequestCredentials | None = None
    started_at: float = 0.0
    duration_s: float | None = None
    failed: bool = False
    error: BaseException | None = None
    # Scratch space so a hook can pass state from before to after the call
    # without reaching for another contextvar.
    state: dict[str, Any] = field(default_factory=dict)


# A hook is called twice: once before the tool runs, once after (even on
# failure). Returning a value from `before` is not supported on purpose —
# short-circuiting a call is rate limiting's job (#14) and it should do that by
# raising an McpError, which already maps to a proper MCP error response.
BeforeHook = Callable[[ToolCall], None | Awaitable[None]]
AfterHook = Callable[[ToolCall], None | Awaitable[None]]
# Receives the per-message request (or None under stdio).
CredentialsResolver = Callable[[Any | None], "RequestCredentials | None"]


class KnowledgeStackMCP(FastMCP):
    """FastMCP with one interception point around every tool call.

    Hooks run in registration order before the call and in **reverse** order
    after it, so a pair behaves like nested context managers: the first hook
    registered is the outermost, and its `after` sees the innermost hooks'
    effects. That ordering is what lets a tracing span (#15) enclose a rate
    limiter's (#14) decision rather than the other way round.

    A hook raising from `before` aborts the call — that is how #14 refuses one.
    A hook raising from `after` must never mask the tool's own result or error,
    so those are caught and logged.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Set before super().__init__ — it calls _setup_handlers, which binds
        # self.call_tool, and an incoming call could in principle arrive before
        # we return.
        self._before_hooks: list[BeforeHook] = []
        self._after_hooks: list[AfterHook] = []
        self._credentials_resolver: CredentialsResolver | None = None
        super().__init__(*args, **kwargs)

    def set_credentials_resolver(self, resolver: CredentialsResolver | None) -> None:
        """Derive per-call credentials from the incoming request (#16).

        The resolver is handed the *per-message* request — a Starlette
        ``Request`` over Streamable HTTP, or ``None`` for stdio — and returns
        :class:`RequestCredentials` or ``None`` to fall back to the
        environment.

        It is called inside ``call_tool``, in the same task as the tool, once
        per message. That is the only safe place: see the module docstring for
        why binding in ASGI middleware silently pins a whole session to its
        first caller.
        """
        self._credentials_resolver = resolver

    def _per_message_request(self) -> Any | None:
        """The HTTP request for the message being handled, if there is one.

        `request_ctx` is set per message by the low-level server before our
        override runs, so this is per-call and not inherited. Returns None
        under stdio, or outside a request.
        """
        try:
            return self._mcp_server.request_context.request
        except (LookupError, AttributeError):
            return None

    def add_hooks(self, *, before: BeforeHook | None = None, after: AfterHook | None = None) -> None:
        """Register a before/after pair. Either may be omitted."""
        if before is not None:
            self._before_hooks.append(before)
        if after is not None:
            self._after_hooks.append(after)

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        resolved = None
        if self._credentials_resolver is not None:
            resolved = self._credentials_resolver(self._per_message_request())
        if resolved is None:
            return await self._call_tool_with_hooks(name, arguments)
        # Bound here, in the tool's own task, for this message only.
        with request_credentials(resolved.api_key, resolved.base_url):
            try:
                return await self._call_tool_with_hooks(name, arguments)
            finally:
                await self._close_request_client()

    async def _close_request_client(self) -> None:
        """Close the ApiClient built for this request, if one was.

        Request-scoped clients are not pooled, so without this each call leaks
        a connection pool and an ssl_context.
        """
        client = current_request_client()
        if client is None:
            return
        _request_client.set(None)
        try:
            await client.close()
        except Exception:
            logger.exception("failed closing the request-scoped KS client")

    async def _call_tool_with_hooks(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        call = ToolCall(
            name=name,
            arguments=arguments,
            credentials=current_credentials(),
            # time.monotonic, not time.time: durations must not go backwards
            # when the host clock is adjusted mid-call.
            started_at=time.monotonic(),
        )

        for hook in self._before_hooks:
            result = hook(call)
            if result is not None:
                await result

        call_token = _current_call.set(call)
        try:
            return await super().call_tool(name, arguments)
        except BaseException as exc:
            call.failed = True
            call.error = exc
            raise
        finally:
            _current_call.reset(call_token)
            call.duration_s = time.monotonic() - call.started_at
            for hook in reversed(self._after_hooks):
                try:
                    outcome = hook(call)
                    if outcome is not None:
                        await outcome
                except Exception:
                    # An observability hook must never turn a working tool call
                    # into a failure, nor replace the tool's own exception with
                    # its own while unwinding.
                    logger.exception("after-hook failed for tool %r", name)
