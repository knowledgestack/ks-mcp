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
from functools import lru_cache
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


@lru_cache(maxsize=1)
def get_api_client() -> ksapi.ApiClient:
    """Return a process-singleton authenticated KS API client.

    The KS backend's ``get_current_identity`` accepts
    ``Authorization: Bearer sk-user-...`` on every v1 route, so a single
    header injection is enough.
    """
    configuration = ksapi.Configuration(host=get_base_url())
    client = ksapi.ApiClient(configuration)
    client.set_default_header("Authorization", f"Bearer {get_api_key()}")

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
    logger.debug("KS client ready: base_url=%s timeout=%ss", get_base_url(), get_timeout_s())
    return client


async def close_api_client() -> None:
    """Close the pooled client and clear the cache (used on shutdown / in tests)."""
    if get_api_client.cache_info().currsize:
        await get_api_client().close()
    get_api_client.cache_clear()
