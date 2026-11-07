"""Tests for `get_workflow_status` (issue #29).

The normalization unit tests matter more than they look: an agent branches on
`state` / `is_terminal`, so mapping an unrecognized Temporal status to
"terminal" would make it report an in-flight run as finished.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import FakeKS
from mcp.server.fastmcp import FastMCP

from ks_mcp.tools.workflows import _elapsed_s, _normalize

WORKFLOW_ID = "agent-wf-7f3a"


@pytest.fixture
def server() -> FastMCP:
    from ks_mcp.server import build_server

    return build_server()


async def call(server: FastMCP, name: str, args: dict[str, Any]) -> Any:
    result = await server.call_tool(name, args)
    if isinstance(result, tuple):
        content, structured = result
        return structured if structured is not None else content
    return result


def _status_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workflow_id": WORKFLOW_ID,
        "temporal_status": "RUNNING",
        "workflow_type": "AgentThreadWorkflow",
        "task_queue": "agent",
        "start_time": "2026-11-07T10:00:00Z",
        "close_time": None,
        "run_id": "run-001",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# status normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "state", "terminal"),
    [
        ("RUNNING", "running", False),
        ("running", "running", False),
        ("CONTINUED_AS_NEW", "running", False),
        ("COMPLETED", "succeeded", True),
        ("FAILED", "failed", True),
        ("TIMED_OUT", "failed", True),
        ("CANCELED", "cancelled", True),
        ("CANCELLED", "cancelled", True),
        ("TERMINATED", "cancelled", True),
    ],
)
def test_normalize_maps_temporal_statuses(raw: str, state: str, terminal: bool) -> None:
    assert _normalize(raw) == (state, terminal)


def test_normalize_treats_unknown_status_as_still_running() -> None:
    """A status name we don't know must not be reported as finished.

    Temporal can add states; claiming `is_terminal` on an unrecognized name
    would make an agent announce a result that hasn't happened yet.
    """
    assert _normalize("SOME_NEW_STATE") == ("unknown", False)
    assert _normalize("") == ("unknown", False)


def test_normalize_tolerates_spacing_and_case() -> None:
    assert _normalize("  Timed Out  ") == ("failed", True)


# ---------------------------------------------------------------------------
# elapsed time
# ---------------------------------------------------------------------------


def test_elapsed_uses_close_time_when_closed() -> None:
    start = datetime(2026, 11, 7, 10, 0, 0, tzinfo=UTC)
    assert _elapsed_s(start, start + timedelta(seconds=90)) == 90.0


def test_elapsed_measures_to_now_while_running() -> None:
    start = datetime.now(UTC) - timedelta(seconds=5)
    elapsed = _elapsed_s(start, None)
    assert elapsed is not None and 4.0 <= elapsed <= 30.0


def test_elapsed_handles_naive_timestamps() -> None:
    """A proxy that strips the tz must not turn into a TypeError mid-tool-call."""
    naive = datetime.now().replace(tzinfo=None)  # noqa: DTZ005 - deliberately naive
    assert _elapsed_s(naive, None) is not None


def test_elapsed_is_none_without_a_start() -> None:
    assert _elapsed_s(None, None) is None


# ---------------------------------------------------------------------------
# end-to-end
# ---------------------------------------------------------------------------


async def test_get_workflow_status_reports_running(server: FastMCP, ks: FakeKS) -> None:
    ks.on("GET", rf"/v1/system-jobs/{WORKFLOW_ID}", json_body=_status_payload())

    out = await call(server, "get_workflow_status", {"workflow_id": WORKFLOW_ID})

    assert out["found"] is True
    assert out["state"] == "running"
    assert out["is_terminal"] is False
    assert out["temporal_status"] == "RUNNING"
    assert out["workflow_type"] == "AgentThreadWorkflow"
    assert out["task_queue"] == "agent"
    assert out["run_id"] == "run-001"
    assert out["closed_at"] is None
    assert out["elapsed_s"] is not None
    assert "still running" in out["summary"]


async def test_get_workflow_status_reports_completed(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "GET",
        rf"/v1/system-jobs/{WORKFLOW_ID}",
        json_body=_status_payload(
            temporal_status="COMPLETED",
            close_time="2026-11-07T10:02:00Z",
        ),
    )

    out = await call(server, "get_workflow_status", {"workflow_id": WORKFLOW_ID})

    assert out["state"] == "succeeded"
    assert out["is_terminal"] is True
    assert out["elapsed_s"] == 120.0
    assert out["closed_at"] is not None
    assert "succeeded after 120.0s" in out["summary"]


async def test_get_workflow_status_reports_failure_as_terminal(
    server: FastMCP, ks: FakeKS
) -> None:
    ks.on(
        "GET",
        rf"/v1/system-jobs/{WORKFLOW_ID}",
        json_body=_status_payload(
            temporal_status="FAILED", close_time="2026-11-07T10:00:30Z"
        ),
    )

    out = await call(server, "get_workflow_status", {"workflow_id": WORKFLOW_ID})

    assert (out["state"], out["is_terminal"]) == ("failed", True)


async def test_get_workflow_status_unknown_status_is_not_terminal(
    server: FastMCP, ks: FakeKS
) -> None:
    ks.on(
        "GET",
        rf"/v1/system-jobs/{WORKFLOW_ID}",
        json_body=_status_payload(temporal_status="RESET"),
    )

    out = await call(server, "get_workflow_status", {"workflow_id": WORKFLOW_ID})

    assert out["state"] == "unknown"
    assert out["is_terminal"] is False
    assert "unrecognized" in out["summary"]
    assert "in flight" in out["summary"]


async def test_get_workflow_status_missing_id_is_not_an_error(
    server: FastMCP, ks: FakeKS
) -> None:
    """A retired workflow is data, not a tool failure — and not a failed run."""
    ks.on(
        "GET",
        rf"/v1/system-jobs/{WORKFLOW_ID}",
        status=404,
        json_body={"detail": "workflow not found", "code": "not_found"},
    )

    out = await call(server, "get_workflow_status", {"workflow_id": WORKFLOW_ID})

    assert out["found"] is False
    assert out["state"] == "unknown"
    assert out["is_terminal"] is False
    assert out["temporal_status"] is None
    assert "not evidence that it failed" in out["summary"]


async def test_get_workflow_status_maps_forbidden_to_mcp_error(
    server: FastMCP, ks: FakeKS
) -> None:
    """Another tenant's workflow is an error, not a `found=false` result."""
    ks.on(
        "GET",
        rf"/v1/system-jobs/{WORKFLOW_ID}",
        status=403,
        json_body={"detail": "not your workflow", "code": "forbidden"},
    )

    with pytest.raises(Exception, match="Forbidden"):
        await call(server, "get_workflow_status", {"workflow_id": WORKFLOW_ID})
