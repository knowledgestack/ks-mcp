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
"""

import argparse
import asyncio
import inspect
import sys

from mcp.server.fastmcp import FastMCP

from ks_mcp import __version__, prompts
from ks_mcp.client import configure_logging, get_base_url, get_log_level
from ks_mcp.tools import ask, browse, cite, extract, org, provenance, read, search, workflows

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
    "the recommended tool sequence for those jobs."
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


def build_server(host: str = "127.0.0.1", port: int = 8765) -> FastMCP:
    mcp = FastMCP(
        name="knowledgestack",
        instructions=INSTRUCTIONS,
        host=host,
        port=port,
        # FastMCP reconfigures root logging in its constructor, so KS_LOG_LEVEL
        # has to reach it or the variable only governs our own namespace.
        log_level=get_log_level(),
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
    _normalize_tool_descriptions(mcp)
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
    parser.add_argument("--version", action="version", version=f"ks-mcp {__version__}")
    args = parser.parse_args()

    configure_logging()

    if args.check:
        raise SystemExit(asyncio.run(_run_check()))

    mcp = build_server(host=args.host, port=args.port)
    if args.http:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
