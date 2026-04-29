"""Knowledge Stack MCP server entry point.

Run it via:

    # stdio (Claude Desktop, Cursor, pydantic-ai MCPServerStdio, LangGraph)
    uvx knowledgestack-mcp

    # Streamable HTTP (remote agents / ngrok tunnels)
    uvx knowledgestack-mcp --http --port 8765

Environment:
    KS_API_KEY   (required)  A ``sk-user-...`` key issued from the KS dashboard.
    KS_BASE_URL  (optional)  Override the KS API host (default: production).
"""

import argparse

from mcp.server.fastmcp import FastMCP

from ks_mcp.tools import ask, browse, cite, org, provenance, read, search


def build_server(host: str = "127.0.0.1", port: int = 8765) -> FastMCP:
    mcp = FastMCP(
        name="knowledgestack",
        instructions=(
            "Knowledge Stack: ground every answer in the tenant's knowledge base.\n"
            "Two ways to use this server:\n"
            "  • Quick path — call `ask(question)` for a one-shot grounded answer "
            "from the KS agent (streamed assistant reply assembled into one result, "
            "with inline citations).\n"
            "  • Custom path — `search_knowledge` (concepts) or `search_keyword` "
            "(exact terms) → `read_around` / `read` for context → `cite` once per "
            "chunk used. Append the returned `[chunk:UUID]` tag to the supporting "
            "sentence in your answer.\n"
            "Use `list_contents` / `find` / `get_info` to navigate the folder tree, "
            "and `view_chunk_image` for IMAGE-type chunks. The only state-changing "
            "tool is `ask`, which creates a thread and assistant message; everything "
            "else is read-only."
        ),
        host=host,
        port=port,
    )
    for module in (search, read, browse, cite, ask, org, provenance):
        module.register(mcp)
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--http", action="store_true", help="Serve over Streamable HTTP instead of stdio."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    mcp = build_server(host=args.host, port=args.port)
    if args.http:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
