"""Smoke test over the real stdio transport.

Everything else drives tools in-process via ``FastMCP.call_tool``. This spawns
the actual console entry point and speaks MCP JSON-RPC to it, which is the only
thing that catches an import-time failure, a bad CLI wiring, or — the classic
stdio footgun — anything written to stdout that corrupts the protocol frames.
"""

import sys

from conftest import (
    FakeKS,
    chunk_payload,
    current_user_payload,
    document_ref,
    tenant_payload,
)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CHUNK_ID = "00000000-0000-0000-0000-0000000000cc"
DOC_ID = "00000000-0000-0000-0000-0000000000b1"


async def test_server_serves_tools_over_stdio(ks: FakeKS) -> None:
    from ks_mcp import __version__
    from ks_mcp.server import build_server

    expected = {t.name for t in await build_server().list_tools()}

    ks.on(
        "GET",
        rf"/v1/chunks/{CHUNK_ID}",
        json_body=chunk_payload(CHUNK_ID, document=document_ref(DOC_ID, "Handbook")),
    )
    ks.on(
        "GET",
        r"/v1/path-parts/[0-9a-f-]+/ancestry",
        json_body={"ancestors": []},
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ks_mcp.server"],
        # KS_LOG_LEVEL=DEBUG makes the server chatty; all of it must go to
        # stderr, so a passing handshake also proves stdout stays clean.
        env={"KS_API_KEY": "sk-user-test", "KS_BASE_URL": ks.base_url, "KS_LOG_LEVEL": "DEBUG"},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.serverInfo.name == "knowledgestack"
            assert init.serverInfo.version == __version__
            assert init.instructions and "search_hybrid" in init.instructions

            # Compared against the in-process server so adding a tool doesn't
            # require touching a hard-coded count here.
            names = {t.name for t in (await session.list_tools()).tools}
            assert names == expected

            result = await session.call_tool("cite", {"chunk_id": CHUNK_ID})
            assert not result.isError, result.content
            text = getattr(result.content[0], "text", "")
            assert "Handbook" in text
            assert f"[chunk:{CHUNK_ID}]" in text


async def test_check_reports_tenant_and_exits_zero(ks: FakeKS) -> None:
    """`--check` is the documented first command a new user runs."""
    import asyncio

    ks.on("GET", r"/v1/users/me", json_body=current_user_payload())
    ks.on("GET", r"/v1/tenants/[0-9a-f-]+$", json_body=tenant_payload())

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "ks_mcp.server",
        "--check",
        env={"KS_API_KEY": "sk-user-test", "KS_BASE_URL": ks.base_url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    out = stdout.decode()

    assert proc.returncode == 0, out
    assert "OK: authenticated to tenant 'Acme Corp'" in out
    assert "timezone=America/New_York" in out
    assert "search_hybrid" in out


async def test_check_exits_nonzero_when_unreachable() -> None:
    """Non-zero exit is what makes `--check` usable as a CI smoke test."""
    import asyncio

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "ks_mcp.server",
        "--check",
        # Port 9 (discard) is closed, so this fails fast without a real network call.
        env={"KS_API_KEY": "sk-user-test", "KS_BASE_URL": "http://127.0.0.1:9",
             "KS_TIMEOUT_S": "5"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    assert proc.returncode == 1
    assert "FAIL" in stderr.decode()
