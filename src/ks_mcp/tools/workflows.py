"""Workflow status polling.

``ask`` and ``extract`` both hand back a ``workflow_id`` for the Temporal
workflow that served the call, and the KS backend runs ingest / re-embed /
reclassify work on the same machinery. This exposes the read-only status
route so an agent can tell "still running" from "failed" instead of guessing
from a timeout.

Read-only: this is a status GET, so it is *not* gated behind ``--allow-write``.
Triggering workflows is a separate, write-scoped concern (see issue #26).
"""

from datetime import UTC, datetime
from typing import Annotated

import ksapi
from ksapi.api.system_jobs_api import SystemJobsApi
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from ks_mcp.client import get_api_client
from ks_mcp.errors import is_not_found, rest_to_mcp

# Temporal's own `WorkflowExecutionStatus` names, lowercased, mapped onto the
# four things an agent can actually *do* next. Anything Temporal adds later
# falls through to "unknown", which we treat as non-terminal so a caller polls
# again rather than reporting a run as finished on a name we don't recognize.
_TERMINAL_STATES = {
    "completed": "succeeded",
    "failed": "failed",
    "canceled": "cancelled",
    "cancelled": "cancelled",
    "terminated": "cancelled",
    "timed_out": "failed",
}
_RUNNING_STATES = {"running", "continued_as_new"}


class WorkflowStatus(BaseModel):
    """Point-in-time state of one Temporal workflow owned by the caller's tenant."""

    workflow_id: str = Field(..., description="The workflow id you asked about.")
    found: bool = Field(
        default=True,
        description=(
            "False when no workflow with this id is visible to the tenant — either a "
            "bad id, or a run old enough to have aged out of Temporal's retention. "
            "Every other field is then unset; do not read absence as failure."
        ),
    )
    state: str = Field(
        ...,
        description=(
            "Normalized outcome: `running` | `succeeded` | `failed` | `cancelled` | "
            "`unknown`. Branch on this rather than on ``temporal_status``."
        ),
    )
    is_terminal: bool = Field(
        ...,
        description=(
            "True once the run has stopped for good. While false the result may still "
            "change, so a later poll can return something different."
        ),
    )
    temporal_status: str | None = Field(
        default=None,
        description="Raw Temporal status name (e.g. `RUNNING`, `COMPLETED`), for logs.",
    )
    workflow_type: str | None = Field(
        default=None, description="Which workflow ran (e.g. the ingest or agent workflow)."
    )
    task_queue: str | None = Field(default=None, description="Temporal task queue it ran on.")
    run_id: str | None = Field(
        default=None, description="Temporal run id — the specific attempt, distinct from the id."
    )
    started_at: str | None = Field(default=None, description="ISO-8601 start time.")
    closed_at: str | None = Field(
        default=None, description="ISO-8601 close time; null while still running."
    )
    elapsed_s: float | None = Field(
        default=None,
        description=(
            "Seconds from start to close, or to now if still running. Use it to pace "
            "polling instead of retrying in a tight loop."
        ),
    )
    summary: str = Field(..., description="One-line plain-English status.")


def _normalize(temporal_status: str) -> tuple[str, bool]:
    """Map a raw Temporal status onto (state, is_terminal)."""
    key = temporal_status.strip().lower().replace(" ", "_")
    if key in _TERMINAL_STATES:
        return _TERMINAL_STATES[key], True
    if key in _RUNNING_STATES:
        return "running", False
    # Unrecognized: report it, but don't claim the run is over.
    return "unknown", False


def _elapsed_s(start: datetime | None, close: datetime | None) -> float | None:
    if start is None:
        return None
    # Temporal timestamps are tz-aware; be defensive in case a proxy strips it,
    # or subtracting from `now` raises on naive/aware mixing.
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    end = close or datetime.now(UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return round((end - start).total_seconds(), 3)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_workflow_status(
        workflow_id: Annotated[
            str,
            Field(
                description=(
                    "Workflow id to look up — e.g. the `workflow_id` returned by `ask` "
                    "or by an ingest call."
                ),
                min_length=1,
                max_length=512,
            ),
        ],
    ) -> WorkflowStatus:
        """Check whether a Knowledge Stack background workflow is still running.

        `ask` and `extract` return a `workflow_id`; so do the backend's ingest,
        re-embed and reclassify jobs. Pass one here to find out whether it
        succeeded, failed, or is still going — useful when a call timed out and
        you need to know whether the work is still in flight before retrying
        and doing it twice.

        Read `state` (`running` / `succeeded` / `failed` / `cancelled` /
        `unknown`) rather than the raw `temporal_status`, and `is_terminal` to
        decide whether polling again can change the answer.

        This is a single point-in-time read, not a wait. Do not call it in a
        tight loop: if `is_terminal` is false, tell the user the work is still
        running and let them ask again, or leave a gap proportional to
        `elapsed_s` before the next check.

        An unknown id comes back with `found=false` rather than as an error —
        workflows age out of Temporal's retention window, so "not there" does
        not mean "failed".
        """
        api = SystemJobsApi(get_api_client())
        try:
            status = await api.get_temporal_workflow_status(workflow_id=workflow_id)
        except ksapi.ApiException as exc:
            if is_not_found(exc):
                return WorkflowStatus(
                    workflow_id=workflow_id,
                    found=False,
                    state="unknown",
                    is_terminal=False,
                    summary=(
                        f"No workflow {workflow_id!r} is visible to this tenant. It may "
                        "never have existed, or it may have aged out of Temporal's "
                        "retention window — this is not evidence that it failed."
                    ),
                )
            raise rest_to_mcp(exc) from exc

        state, is_terminal = _normalize(status.temporal_status)
        elapsed = _elapsed_s(status.start_time, status.close_time)
        took = f" after {elapsed}s" if elapsed is not None else ""
        if state == "running":
            summary = f"Workflow {status.workflow_type} is still running{took}."
        elif state == "unknown":
            summary = (
                f"Workflow {status.workflow_type} reported an unrecognized Temporal "
                f"status {status.temporal_status!r}{took}; treat it as still in flight."
            )
        else:
            summary = f"Workflow {status.workflow_type} {state}{took}."

        return WorkflowStatus(
            workflow_id=status.workflow_id,
            found=True,
            state=state,
            is_terminal=is_terminal,
            temporal_status=status.temporal_status,
            workflow_type=status.workflow_type,
            task_queue=status.task_queue,
            run_id=status.run_id,
            started_at=status.start_time.isoformat() if status.start_time else None,
            closed_at=status.close_time.isoformat() if status.close_time else None,
            elapsed_s=elapsed,
            summary=summary,
        )
