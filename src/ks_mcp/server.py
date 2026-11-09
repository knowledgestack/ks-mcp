"""Knowledge Stack MCP server entry point.

Run it via:

    # stdio (Claude Desktop, Cursor, pydantic-ai MCPServerStdio, LangGraph)
    uvx knowledgestack-mcp

    # Streamable HTTP (remote agents / ngrok tunnels)
    uvx knowledgestack-mcp --http --port 8765

    # verify your key + connectivity before wiring up a client
    uvx knowledgestack-mcp --check

Environment:
    KS_API_KEY    (required)  A ``sk-user-...`` key issued from the KS dashboard.
    KS_BASE_URL   (optional)  Override the KS API host (default: production).
    KS_TIMEOUT_S  (optional)  Upstream HTTP timeout in seconds (default: 30).
    KS_LOG_LEVEL  (optional)  DEBUG/INFO/WARNING/ERROR/CRITICAL; logs go to stderr.
    KS_ALLOWED_HOSTS   (optional) Comma-separated Host header allowlist for
                                  --http, e.g. "ks-mcp.fly.dev". Set this when
                                  hosting: binding 0.0.0.0 otherwise disables
                                  DNS-rebinding protection entirely.
    KS_ALLOWED_ORIGINS (optional) Comma-separated Origin allowlist, used only
                                  alongside KS_ALLOWED_HOSTS.
    KS_ALLOW_WRITE     (optional) Set to 1/true to expose mutating tools, the
                                  same as passing --allow-write.
    KS_AUDIT_LOG       (optional) Path for the write audit log (JSON lines).
                                  Defaults to stderr.
"""

import argparse
import asyncio
import inspect
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from ks_mcp import __version__, prompts, resources
from ks_mcp.client import (
    configure_logging,
    get_allowed_hosts,
    get_allowed_origins,
    get_base_url,
    get_log_level,
    logger,
)
from ks_mcp.middleware import KnowledgeStackMCP
from ks_mcp.tools import ask, browse, cite, extract, org, provenance, read, search, workflows
from ks_mcp.writes import WritePolicy, get_write_policy, install_write_hooks

INSTRUCTIONS = (
    "Knowledge Stack: ground every answer in the tenant's knowledge base.\n"
    "Three ways to use this server:\n"
    "  • Quick path — `ask(question)` for a one-shot grounded answer from the KS "
    "agent (streamed reply assembled into one result, with inline citations).\n"
    "  • Structured path — `extract(prompt, output_schema=...)` when you need "
    "schema-conforming JSON rather than prose.\n"
    "  • Custom path — `search_hybrid` (best default), `search_knowledge` "
    "(concepts) or `search_keyword` (exact terms) → `read_around` / `read` for "
    "context → `cite` once per chunk used. Append the returned `[chunk:UUID]` "
    "tag to the supporting sentence in your answer.\n"
    "Use `list_contents` / `find` / `get_info` to navigate the folder tree, and "
    "`view_chunk_image` for IMAGE-type chunks. `trace_chunk_lineage` and "
    "`compare_versions` answer provenance questions. `get_workflow_status` "
    "tells you whether a background job (an `ask` that timed out, an ingest) is "
    "still running — read it once, don't poll in a loop. The only state-changing "
    "tools are `ask` (creates a thread + assistant message) and `extract`; both "
    "consume one message of tenant quota. Everything else only reads — though "
    "`compare_versions` is a privileged read and is audit-logged upstream, like "
    "a download.\n"
    "If your client supports MCP prompts, `summarize-with-citations`, "
    "`compare-documents`, `expand-around-chunk` and `answer-from-folder` carry "
    "the recommended tool sequence for those jobs.\n"
    "Folders and documents are also addressable as MCP resources: read "
    "`ks://folders` for the tenant root, then follow each child's `ks://folder/…` "
    "or `ks://document/…` URI."
)

_MODULES = (search, read, browse, cite, ask, extract, org, provenance, workflows)


def _normalize_tool_descriptions(mcp: FastMCP) -> None:
    """Dedent tool descriptions so they are identical on every Python version.

    Tool descriptions are taken from docstrings, and Python 3.13 strips common
    leading indentation at compile time (gh-81283) while 3.11 and 3.12 do not.
    Left alone that costs us twice:

    * a server running on 3.11/3.12 sends every tool description with eight
      spaces on each body line — wasted tokens in the tool list, and ragged
      rendering in clients that display descriptions verbatim;
    * `schemas/` could only ever match the interpreter that generated it,
      which is what broke CI: the files were exported on 3.13 and the matrix
      runs 3.11 and 3.12.

    ``cleandoc`` converges both forms on the same string and is idempotent, so
    this is safe to apply regardless of the running interpreter.

    Prompt and resource descriptions are passed as explicit keyword arguments
    rather than read from docstrings, so they need no normalization.
    """
    for tool in mcp._tool_manager.list_tools():
        if tool.description:
            tool.description = inspect.cleandoc(tool.description)


def _transport_security() -> TransportSecuritySettings | None:
    """Build Host/Origin validation settings from the environment.

    FastMCP auto-enables DNS-rebinding protection only when bound to localhost.
    Bind ``0.0.0.0`` — which every container deployment does — and it passes
    ``None``, which turns Host and Origin validation *off*. Setting
    ``KS_ALLOWED_HOSTS`` is how an operator keeps it on with their real
    hostname.

    Returns ``None`` when unset, preserving FastMCP's own default behaviour.
    """
    hosts = get_allowed_hosts()
    if not hosts:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=get_allowed_origins(),
    )


def build_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    policy: WritePolicy | None = None,
) -> KnowledgeStackMCP:
    # KnowledgeStackMCP, not FastMCP: it overrides `call_tool` to give every
    # tool call one interception point (see middleware.py). It must be the
    # class we *construct* — FastMCP binds `self.call_tool` during __init__, so
    # patching the attribute afterwards would never reach real traffic.
    mcp = KnowledgeStackMCP(
        name="knowledgestack",
        instructions=INSTRUCTIONS,
        host=host,
        port=port,
        # FastMCP reconfigures root logging in its constructor, so KS_LOG_LEVEL
        # has to reach it or the variable only governs our own namespace.
        log_level=get_log_level(),
        transport_security=_transport_security(),
    )
    # FastMCP doesn't forward a version to the low-level Server, so clients see
    # the MCP library version in `serverInfo` instead of ours. Set it directly so
    # bug reports and registry listings name the right release.
    mcp._mcp_server.version = __version__
    for module in _MODULES:
        module.register(mcp)
    # Prompts are pure text — no API calls, no tenant quota — so they're always
    # registered; a client that ignores prompts/ simply never lists them.
    prompts.register(mcp)
    resources.register(mcp)

    # Mutating tools are registered only when writes are enabled, so they are
    # absent from `tools/list` otherwise — a tool an agent cannot see is one it
    # cannot be talked into calling by an injected document. The hooks go on
    # unconditionally so enabling writes can't enable them ungated.
    write_policy = policy if policy is not None else get_write_policy()
    install_write_hooks(mcp, write_policy)
    if write_policy.allow_write:
        logger.warning(
            "Writes are ENABLED (--allow-write / KS_ALLOW_WRITE). Mutating tools "
            "require an admin-scoped key and every call is audit-logged to %s.",
            write_policy.audit_target,
        )
        # No write tools exist yet; #8-#11 and #26 register here, each calling
        # writes.mark_write_tool(...) so the gate and audit log cover them.

    _normalize_tool_descriptions(mcp)

    # The MCP endpoint itself can't serve as a health check: `GET /mcp` returns
    # 406 without an SSE Accept header and 400 without a session id, so every
    # platform's HTTP probe would read the service as down. This is a liveness
    # probe only — it deliberately does *not* call the KS API, so a probe can't
    # burn tenant quota or fail the container over an upstream blip. Use
    # `--check` to validate credentials.
    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": "knowledgestack", "version": __version__})

    return mcp


async def _run_check() -> int:
    """Validate the env + key against the live API and print a human summary."""
    from ks_mcp.client import close_api_client
    from ks_mcp.tools.org import _fetch_tenant

    # Flushed so the header still precedes the stderr FAIL block in a terminal.
    print(f"ks-mcp {__version__}")
    print(f"KS_BASE_URL: {get_base_url()}", flush=True)
    try:
        info = await _fetch_tenant()
    except Exception as exc:  # noqa: BLE001 - this is a diagnostic, report anything
        print(f"FAIL: {exc}", file=sys.stderr)
        print(
            "\nCheck that KS_API_KEY is a `sk-user-…` key for this host and "
            "that KS_BASE_URL points at the right deployment.",
            file=sys.stderr,
        )
        return 1
    finally:
        await close_api_client()

    tools = await build_server().list_tools()
    print(f"OK: authenticated to tenant {info.name!r} ({info.tenant_id})")
    print(f"    language={info.default_language} timezone={info.timezone}")
    print(f"    {len(tools)} tools registered: {', '.join(sorted(t.name for t in tools))}")
    return 0


# Hosts for which FastMCP enables DNS-rebinding protection by itself.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _warn_if_unprotected(host: str) -> None:
    """Warn when serving HTTP on a public interface with no Host allowlist.

    This is the silent-by-default case: bound to 0.0.0.0 with no
    ``KS_ALLOWED_HOSTS``, the transport accepts any Host and any Origin. A
    warning rather than a hard failure, because refusing to start would break
    anyone already running behind a proxy that handles this.
    """
    if host in _LOCAL_HOSTS or get_allowed_hosts():
        return
    logger.warning(
        "Serving Streamable HTTP on %s with no KS_ALLOWED_HOSTS: Host and Origin "
        "validation are disabled, so any hostname pointed at this server is "
        "accepted. Set KS_ALLOWED_HOSTS to your public hostname (e.g. "
        "KS_ALLOWED_HOSTS=mcp.example.com) unless an upstream proxy already "
        "validates it.",
        host,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ks-mcp",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--http", action="store_true", help="Serve over Streamable HTTP instead of stdio."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify KS_API_KEY and connectivity, print the tool list, then exit.",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help=(
            "Expose mutating tools (ingest / delete / reembed). Off by default. "
            "Each call additionally requires an admin-scoped key and is "
            "audit-logged to KS_AUDIT_LOG, or stderr if unset."
        ),
    )
    parser.add_argument("--version", action="version", version=f"ks-mcp {__version__}")
    args = parser.parse_args()

    configure_logging()

    if args.check:
        raise SystemExit(asyncio.run(_run_check()))

    mcp = build_server(
        host=args.host,
        port=args.port,
        policy=get_write_policy(allow_write=args.allow_write),
    )
    if args.http:
        _warn_if_unprotected(args.host)
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
