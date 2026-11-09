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

Credentials now live in a :class:`RequestCredentials` contextvar, which
``asyncio`` copies per task, so concurrent requests cannot observe each
other's. Nothing sets it yet — ``client.py`` falls back to the environment when
it is unset, which is exactly today's single-tenant behaviour. #16 becomes
"populate the contextvar from the request" rather than a refactor.
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
    """Bind credentials for the duration of one request.

    #16's entry point: a Streamable HTTP handler wraps each request in this and
    everything downstream — the API client, the tenant lookup — follows,
    without any tool knowing about it.
    """
    token = _credentials.set(RequestCredentials(api_key=api_key, base_url=base_url))
    try:
        yield
    finally:
        # Reset rather than set-to-None: nested binds must restore the outer
        # value, or an inner request would blank its caller's credentials.
        _credentials.reset(token)


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
        super().__init__(*args, **kwargs)

    def add_hooks(self, *, before: BeforeHook | None = None, after: AfterHook | None = None) -> None:
        """Register a before/after pair. Either may be omitted."""
        if before is not None:
            self._before_hooks.append(before)
        if after is not None:
            self._after_hooks.append(after)

    async def call_tool(
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

        try:
            return await super().call_tool(name, arguments)
        except BaseException as exc:
            call.failed = True
            call.error = exc
            raise
        finally:
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
