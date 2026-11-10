"""TEMPORARY review probe — delete after running."""

import logging

import pytest
from conftest import FakeKS, current_user_payload
from mcp import types

from ks_mcp import client as client_module
from ks_mcp import writes
from ks_mcp.middleware import KnowledgeStackMCP
from ks_mcp.writes import WritePolicy, install_write_hooks, mark_write_tool


@pytest.fixture(autouse=True)
def _clean_registry():
    writes._reset_write_tools_for_tests()
    yield
    writes._reset_write_tools_for_tests()


async def _invoke(mcp, name, arguments):
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    return await handler(
        types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name=name, arguments=arguments),
        )
    )


def _server_with_write_tool(policy):
    mcp = KnowledgeStackMCP(name="test")
    ran: list[str] = []

    @mcp.tool()
    async def wipe_everything(document_id: str, question: str) -> str:
        """Synthetic mutating tool."""
        ran.append(document_id)
        return "done"

    mark_write_tool("wipe_everything")
    install_write_hooks(mcp, policy)
    return mcp, ran


@pytest.mark.parametrize("level", ["INFO", "WARNING", "ERROR", "CRITICAL"])
async def test_probe_stderr_sink_vs_log_level(
    level, ks: FakeKS, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    ks.on(
        "GET",
        r"/v1/users/me",
        json_body=current_user_payload() | {"current_tenant_role": "ADMIN"},
    )
    monkeypatch.setenv("KS_LOG_LEVEL", level)
    monkeypatch.setattr(client_module, "_LOG_CONFIGURED", False)
    client_module.configure_logging()

    mcp, ran = _server_with_write_tool(WritePolicy(allow_write=True, audit_path=None))
    result = await _invoke(mcp, "wipe_everything", {"document_id": "d1", "question": "secret"})

    err = capsys.readouterr().err
    is_error = result.root.isError
    print(
        f"PROBE level={level} "
        f"warning_enabled={client_module.logger.isEnabledFor(logging.WARNING)} "
        f"tool_ran={ran} isError={is_error} "
        f"audit_lines={err.count('ks-mcp-audit')}"
    )
