"""Server-level smoke tests for the KS MCP server."""

EXPECTED_TOOLS = {
    "search_knowledge",
    "search_keyword",
    "search_hybrid",
    "extract",
    "read",
    "read_around",
    "cite",
    "ask",
    "list_contents",
    "find",
    "get_info",
    "view_chunk_image",
    "get_organization_info",
    "get_current_datetime",
    # Phase 2 provenance:
    "trace_chunk_lineage",
    "compare_versions",
    # Phase 3 workflows:
    "get_workflow_status",
}


def test_build_server_metadata() -> None:
    from ks_mcp import __version__
    from ks_mcp.server import build_server

    mcp = build_server(host="0.0.0.0", port=9999)
    assert mcp.name == "knowledgestack"
    # Clients read this in `initialize`; FastMCP would otherwise report the
    # MCP library's own version here.
    assert mcp._mcp_server.version == __version__
    instructions = mcp.instructions or ""
    assert "search_knowledge" in instructions
    assert "read_around" in instructions
    assert "cite" in instructions
    assert "ask" in instructions
    assert "search_hybrid" in instructions
    assert "extract" in instructions


async def test_all_tools_registered() -> None:
    from ks_mcp.server import build_server

    mcp = build_server()
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS.issubset(names), f"Missing tools: {EXPECTED_TOOLS - names}"


async def test_every_tool_has_non_empty_description() -> None:
    from ks_mcp.server import build_server

    mcp = build_server()
    tools = await mcp.list_tools()
    missing_desc = [t.name for t in tools if not (t.description or "").strip()]
    assert not missing_desc, f"Tools lacking description: {missing_desc}"


async def test_every_tool_has_input_schema() -> None:
    from ks_mcp.server import build_server

    mcp = build_server()
    tools = await mcp.list_tools()
    for t in tools:
        assert t.inputSchema, f"{t.name} has no inputSchema"


async def test_every_tool_is_async() -> None:
    """Guard the regression that made every tool a no-op.

    ``ksapi`` >= 3 is async-first: its unsuffixed methods are coroutines. A
    sync ``def`` tool that calls them returns an un-awaited coroutine, which
    FastMCP happily serializes — the tool "works" and returns nothing useful.
    Keeping every tool async is what makes `await` the only option.
    """
    import inspect

    from ks_mcp.server import build_server

    mcp = build_server()
    tools = await mcp.list_tools()
    sync_tools = [
        t.name
        for t in tools
        if not inspect.iscoroutinefunction(mcp._tool_manager.get_tool(t.name).fn)  # type: ignore[union-attr]
    ]
    assert not sync_tools, (
        f"These tools are sync and will leak un-awaited coroutines: {sync_tools}"
    )


async def test_every_tool_module_is_registered() -> None:
    """A tool module left out of ``_MODULES`` is invisible with no error anywhere.

    This used to be a hardcoded module count, which flagged the drift but not
    the cause — and had to be bumped by hand every time. Comparing against
    what's actually on disk names the module you forgot.
    """
    import pkgutil

    from ks_mcp import tools
    from ks_mcp.server import _MODULES

    on_disk = {m.name for m in pkgutil.iter_modules(tools.__path__)}
    registered = {m.__name__.rsplit(".", 1)[-1] for m in _MODULES}
    assert on_disk == registered, (
        f"unregistered tool modules: {sorted(on_disk - registered)}; "
        f"registered but missing from disk: {sorted(registered - on_disk)}"
    )


async def test_no_tool_calls_a_sync_sdk_variant() -> None:
    """``*_sync`` SDK methods would block the MCP event loop — none should appear."""
    import pathlib

    tools_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "ks_mcp"
    offenders: list[str] = []
    for path in sorted(tools_dir.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if "_sync(" in line and "def " not in line:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, f"Blocking sync SDK calls found: {offenders}"
