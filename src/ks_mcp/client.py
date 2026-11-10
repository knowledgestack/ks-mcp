"""Shared ksapi client factory.

One ``ApiClient`` per server process, pooled across tool calls to avoid
re-doing the TLS handshake for every tool invocation.

``ksapi`` >= 3 is async-first (it ships an httpx2 ``AsyncClient`` under the
hood), so every tool in this package is an ``async def`` that awaits the
unsuffixed SDK methods. The sync ``*_sync`` variants exist but would block
the MCP event loop, so we don't use them.
"""

import logging
import os
import sys
from typing import Literal, cast

import ksapi

DEFAULT_BASE_URL = "https://api.knowledgestack.ai"
# The SDK's own httpx2 default is 5s, which is too tight for search and far too
# tight for the blocking agent routes. 30s matches the documented default.
DEFAULT_TIMEOUT_S = 30.0
# `POST /v1/agent/extract` runs a Temporal workflow to completion; the API docs
# tell clients to set a generous timeout for it.
DEFAULT_AGENT_TIMEOUT_S = 120.0

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
_VALID_LOG_LEVELS: tuple[LogLevel, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_LOG_CONFIGURED = False
logger = logging.getLogger("ks_mcp")


def _env(key: str, *, required: bool = True, default: str | None = None) -> str:
    value = os.environ.get(key, default)
    if required and not value:
        raise RuntimeError(f"{key} is not set. Export it or add it to your MCP client config.")
    return value or ""


def get_api_key() -> str:
    """Return ``KS_API_KEY``, raising a setup-oriented error when missing."""
    return _env("KS_API_KEY")


def get_base_url() -> str:
    """Return the KS API host, defaulting to production."""
    return _env("KS_BASE_URL", required=False, default=DEFAULT_BASE_URL)


def get_effective_api_key() -> str:
    """The API key for the in-flight request — bound credentials, else the env.

    Anything that talks to KS *outside* the pooled ``ApiClient`` must use this
    rather than :func:`get_api_key`, or it authenticates as the process instead
    of as the caller. ``ask`` streams its SSE response over its own httpx
    client and so has to: without this it created the thread as the caller and
    then read the stream as the process, which in gateway mode (#16) is a
    cross-identity mismatch, not just an inconsistency.
    """
    from ks_mcp.middleware import current_credentials

    creds = current_credentials()
    return creds.api_key if creds is not None else get_api_key()


def get_effective_base_url() -> str:
    """The KS host for the in-flight request — bound credentials, else the env."""
    from ks_mcp.middleware import current_credentials

    creds = current_credentials()
    return creds.base_url if creds is not None else get_base_url()


def get_timeout_s() -> float:
    """Return the upstream HTTP timeout from ``KS_TIMEOUT_S`` (seconds)."""
    raw = os.environ.get("KS_TIMEOUT_S")
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        timeout = float(raw)
    except ValueError:
        raise RuntimeError(f"KS_TIMEOUT_S must be a number of seconds, got {raw!r}.") from None
    if timeout <= 0:
        raise RuntimeError(f"KS_TIMEOUT_S must be greater than 0, got {timeout}.")
    return timeout


def _csv_env(key: str) -> list[str]:
    """Parse a comma-separated env var into a stripped, non-empty list."""
    return [item.strip() for item in os.environ.get(key, "").split(",") if item.strip()]


def get_allowed_hosts() -> list[str]:
    """Host header values the Streamable HTTP transport will accept.

    From ``KS_ALLOWED_HOSTS`` (comma-separated, e.g.
    ``ks-mcp.fly.dev,ks-mcp.internal:*``). Empty means "not configured".

    This exists because FastMCP only auto-enables DNS-rebinding protection when
    bound to localhost — a server bound to ``0.0.0.0`` (which is every container
    deployment) gets *no* Host/Origin validation at all unless we pass settings
    in ourselves.
    """
    return _csv_env("KS_ALLOWED_HOSTS")


def get_allowed_origins() -> list[str]:
    """Origin header values to accept, from ``KS_ALLOWED_ORIGINS``.

    Only consulted when ``KS_ALLOWED_HOSTS`` is set. Leaving it empty permits
    requests with no ``Origin`` header (ordinary non-browser MCP clients) while
    still rejecting a browser origin that isn't listed.
    """
    return _csv_env("KS_ALLOWED_ORIGINS")


def get_log_level() -> LogLevel:
    """Return a valid ``KS_LOG_LEVEL`` name, falling back to INFO.

    Also handed to ``FastMCP(log_level=...)`` so one variable controls the
    whole server's verbosity, not just this package's namespace.
    """
    level_name = cast(LogLevel, os.environ.get("KS_LOG_LEVEL", "INFO").strip().upper())
    return level_name if level_name in _VALID_LOG_LEVELS else "INFO"


def configure_logging() -> None:
    """Send this package's logs to stderr at ``KS_LOG_LEVEL``.

    stdout is the stdio transport's MCP frame channel — writing anything else
    there corrupts the protocol, so the handler is pinned to stderr and kept off
    the root logger (``propagate = False``).
    """
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.handlers = [handler]
    logger.setLevel(getattr(logging, get_log_level()))
    logger.propagate = False
    _LOG_CONFIGURED = True


def _build_api_client(base_url: str, api_key: str) -> ksapi.ApiClient:
    """Construct an authenticated KS API client for one (host, key) pair.

    The KS backend's ``get_current_identity`` accepts
    ``Authorization: Bearer sk-user-...`` on every v1 route, so a single
    header injection is enough.
    """
    configuration = ksapi.Configuration(host=base_url)
    client = ksapi.ApiClient(configuration)
    client.set_default_header("Authorization", f"Bearer {api_key}")

    # `Configuration` exposes no timeout, and the SDK builds its httpx2 client
    # lazily with a 5s default. Build it up front via the SDK's own factory so
    # limits/TLS/proxy handling is preserved, then apply KS_TIMEOUT_S.
    # (httpx coerces the float to its own Timeout, so we need no httpx2 import.)
    rest = client.rest_client
    pool_manager = rest._create_pool_manager()
    pool_manager.timeout = get_timeout_s()
    rest.pool_manager = pool_manager

    # The two settings most likely to be wrong when a user reports "it doesn't
    # work". Never log the key itself.
    logger.debug("KS client ready: base_url=%s timeout=%ss", base_url, get_timeout_s())
    return client


# Process-wide client, used when no request credentials are bound — i.e. stdio
# and today's single-key HTTP mode. Pooled so repeated tool calls don't redo the
# TLS handshake.
_ambient_client: ksapi.ApiClient | None = None
_ambient_identity: tuple[str, str] | None = None


def get_api_client() -> ksapi.ApiClient:
    """Return the KS API client for the in-flight request.

    When request credentials are bound (multi-tenant gateway mode, #16) this
    builds a client for *those* credentials and never consults the environment.
    Such clients are deliberately **not** pooled: a cache keyed on anything
    coarser than the key is how one tenant ends up talking to the API as
    another, and that is a data breach rather than a performance problem.

    With no credentials bound, behaviour is as before: one pooled client built
    from ``KS_API_KEY`` / ``KS_BASE_URL``.
    """
    # Imported here: middleware imports `logger` from this module, so a
    # top-level import would be circular.
    from ks_mcp.middleware import (
        current_credentials,
        current_request_client,
        set_request_client,
    )

    creds = current_credentials()
    if creds is not None:
        # One client per request, not per call. `_build_api_client` eagerly
        # constructs an httpx2.AsyncClient with its own connection pool and
        # ssl_context, so building one per `get_api_client()` leaked both —
        # and a single gated write call makes at least two calls (the identity
        # lookup plus the tool body). `call_tool` closes this in a finally.
        existing = current_request_client()
        if existing is not None:
            return existing
        client = _build_api_client(creds.base_url, creds.api_key)
        set_request_client(client)
        return client

    global _ambient_client, _ambient_identity
    identity = (get_base_url(), get_api_key())
    # Rebuild if the environment changed under us — tests monkeypatch
    # KS_BASE_URL between cases, and silently serving a stale host made those
    # failures look like network flakes.
    if _ambient_client is None or _ambient_identity != identity:
        if _ambient_client is not None:
            # The outgoing client owns an httpx connection pool. `get_api_client`
            # is sync so it cannot await `aclose()`; drop the pool explicitly
            # rather than stranding it until GC, which may never run it.
            stranded = _ambient_client.rest_client.pool_manager
            if stranded is not None:
                _ambient_client.rest_client.pool_manager = None
                logger.debug("dropped the pool of the superseded ambient KS client")
        _ambient_client = _build_api_client(*identity)
        _ambient_identity = identity
    return _ambient_client


async def close_api_client() -> None:
    """Close the pooled ambient client (used on shutdown / in tests).

    Request-scoped clients are not tracked here; they are closed by whatever
    bound the credentials.
    """
    global _ambient_client, _ambient_identity
    if _ambient_client is not None:
        await _ambient_client.close()
    _ambient_client = None
    _ambient_identity = None


def _reset_ambient_client_for_tests() -> None:
    """Drop the pooled client without awaiting a close (test teardown only)."""
    global _ambient_client, _ambient_identity
    _ambient_client = None
    _ambient_identity = None
