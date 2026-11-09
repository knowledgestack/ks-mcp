"""Write-safety substrate: the opt-in gate, admin scoping, and the audit log.

Foundation for every mutating tool on the roadmap — `ingest_document` (#8),
`ingest_url` (#9), `delete_document` / `delete_folder` (#10), `reembed` (#11)
and `run_document_workflow` (#26) — and it *is* the audit log issue (#12).

Why these five issues are one piece of work
-------------------------------------------
Each of them mutates a tenant's corpus using arguments chosen by a model that
may have just read an untrusted document. That makes them the prompt-injection
surface of this server, and the mitigation is identical for all five, so
building it five times would mean getting it right five times.

Two independent gates, deliberately different in kind:

1. **Registration gate** — static, no network. With writes off, write tools are
   never registered, so they are absent from ``tools/list``. A tool an agent
   cannot see is one it cannot be talked into calling. This is why the gate is
   *registration* rather than a check inside each tool: a refusal still tells
   the model the capability exists and invites it to retry.

2. **Scope gate** — dynamic, per call. Even with writes enabled, the calling
   key must hold ``ADMIN`` or ``OWNER`` in its current tenant. This runs as a
   before-hook on the interception point from ``middleware.py`` rather than at
   startup, because resolving a key's role needs an API call and
   ``build_server`` is synchronous — and because in gateway mode (#16) the key
   differs per request, so a startup check would be answering the wrong
   question.

Audit semantics
---------------
Two records per call, with different failure behaviour, because they answer
different questions:

* ``intent`` is written **before** the mutation and is **fail-closed** — if we
  cannot record that we are about to mutate, we do not mutate.
* ``outcome`` is written **after** and is **fail-open** — the mutation has
  already happened, so refusing it afterwards would not un-mutate anything;
  losing the record is bad but failing the call on top of it is worse.

Arguments are hashed rather than stored: for KS tools they routinely contain
document text and user questions. A hash alone makes an audit log you cannot
act on, though, so an explicit allowlist of non-sensitive identifier arguments
(ids, flags) is recorded in clear. Issue #12 asks only for an args hash; the
allowlist is a deliberate addition, since "someone deleted *something*" is not
an answer anyone can use during an incident.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ksapi
from mcp import McpError
from mcp.types import INVALID_PARAMS, ErrorData

from ks_mcp.client import get_api_client, logger
from ks_mcp.middleware import ToolCall

# Roles that may call a mutating tool. `USER` may not, even with writes
# enabled — the flag is the operator's consent, not the caller's authority.
ADMIN_ROLES = frozenset({"ADMIN", "OWNER"})

# Argument names safe to record in clear in the audit log: identifiers and
# flags, never free text. Anything not listed here is covered only by the hash.
# Add to this when a write tool gains an argument whose value matters during an
# incident *and* cannot carry document content.
AUDITABLE_ARG_NAMES = frozenset(
    {
        "document_id",
        "folder_id",
        "path_part_id",
        "parent_path_part_id",
        "version_id",
        "from_version_id",
        "to_version_id",
        "chunk_id",
        "workflow_id",
        "definition_id",
        "run_id",
        "tag_ids",
        "hard",
        "recursive",
        "dry_run",
        "url",
        "name",
    }
)

# Names of tools that mutate tenant state. Write modules register themselves
# here so the hooks below don't need to know what exists; nothing else should
# be in it.
_write_tool_names: set[str] = set()


def mark_write_tool(name: str) -> None:
    """Declare a tool as mutating, so the gate and audit log cover it."""
    _write_tool_names.add(name)


def is_write_tool(name: str) -> bool:
    return name in _write_tool_names


def write_tool_names() -> frozenset[str]:
    return frozenset(_write_tool_names)


def _reset_write_tools_for_tests() -> None:
    _write_tool_names.clear()


@dataclass(frozen=True)
class WritePolicy:
    """Whether mutating tools are available, and where their audit log goes."""

    allow_write: bool = False
    audit_path: Path | None = None

    @property
    def audit_target(self) -> str:
        return str(self.audit_path) if self.audit_path else "stderr"


def _env_flag(name: str) -> bool:
    """Parse a boolean env var, accepting the usual spellings.

    Anything unrecognised is False: an operator who typo'd `KS_ALLOW_WRITE=ture`
    should get a read-only server, not writes enabled by accident.
    """
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def get_write_policy(*, allow_write: bool = False) -> WritePolicy:
    """Resolve the write policy from the CLI flag and the environment.

    `--allow-write` or `KS_ALLOW_WRITE` enables writes — env as well as flag
    because the container images set behaviour through env, and threading a
    flag through `CMD` for every deployment is how a setting gets dropped.
    Either one is sufficient; neither defaults to on.
    """
    raw_path = os.environ.get("KS_AUDIT_LOG", "").strip()
    return WritePolicy(
        allow_write=allow_write or _env_flag("KS_ALLOW_WRITE"),
        audit_path=Path(raw_path) if raw_path else None,
    )


# ---------------------------------------------------------------------------
# scope gate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """Who is calling, as resolved from the key presented."""

    user_id: str
    email: str | None
    tenant_id: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role.upper() in ADMIN_ROLES


async def resolve_identity() -> Identity:
    """Resolve the calling key's identity via ``GET /v1/users/me``."""
    from ksapi.api.users_api import UsersApi

    from ks_mcp.errors import rest_to_mcp

    try:
        me = await UsersApi(get_api_client()).get_me()
    except ksapi.ApiException as exc:
        raise rest_to_mcp(exc) from exc
    role = getattr(me.current_tenant_role, "value", None) or str(me.current_tenant_role or "")
    return Identity(
        user_id=str(me.id),
        email=getattr(me, "email", None),
        tenant_id=str(me.current_tenant_id),
        role=role,
    )


async def require_admin_scope(tool_name: str) -> Identity:
    """Raise unless the calling key holds ADMIN or OWNER in its tenant."""
    identity = await resolve_identity()
    if not identity.is_admin:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=(
                    f"`{tool_name}` needs an admin-scoped key. This key has role "
                    f"{identity.role or 'unknown'!r} in tenant {identity.tenant_id}. "
                    "Writes are enabled on this server, but this key may not perform "
                    "them — tell the user rather than retrying."
                ),
            )
        )
    return identity


# ---------------------------------------------------------------------------
# audit log (#12)
# ---------------------------------------------------------------------------


def hash_arguments(arguments: dict[str, Any]) -> str:
    """Stable hash of a tool's arguments, for correlating without storing them."""
    canonical = json.dumps(arguments, sort_keys=True, default=str, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """The subset of arguments recordable in clear — identifiers and flags only."""
    return {k: v for k, v in arguments.items() if k in AUDITABLE_ARG_NAMES}


def _emit(policy: WritePolicy, record: dict[str, Any]) -> None:
    """Append one JSON record to the sink. Raises if the sink is unusable."""
    line = json.dumps(record, sort_keys=True, default=str, ensure_ascii=False)
    if policy.audit_path is None:
        # stderr, never stdout: stdout is the stdio transport's frame channel.
        logger.warning("ks-mcp-audit %s", line)
        return
    policy.audit_path.parent.mkdir(parents=True, exist_ok=True)
    with policy.audit_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _record(call: ToolCall, event: str, identity: Identity | None) -> dict[str, Any]:
    creds = call.credentials
    return {
        "event": event,
        "tool": call.name,
        "args_hash": hash_arguments(call.arguments),
        "args": _safe_arguments(call.arguments),
        "user_id": identity.user_id if identity else None,
        "email": identity.email if identity else None,
        "tenant_id": identity.tenant_id if identity else None,
        "role": identity.role if identity else None,
        # Present only in gateway mode; in single-tenant mode the key comes
        # from the environment and there is nothing per-request to record.
        "request_scoped": creds is not None,
    }


def audit_intent(policy: WritePolicy, call: ToolCall, identity: Identity | None) -> None:
    """Record that a mutation is about to happen. Fail-closed by design."""
    _emit(policy, _record(call, "intent", identity))


def audit_outcome(policy: WritePolicy, call: ToolCall, identity: Identity | None) -> None:
    """Record how the mutation ended. Fail-open: the write already happened."""
    record = _record(call, "outcome", identity)
    record["failed"] = call.failed
    record["duration_s"] = round(call.duration_s, 3) if call.duration_s is not None else None
    if call.error is not None:
        record["error"] = f"{type(call.error).__name__}: {call.error}"[:300]
    # #12 names "upstream workflow id" as a required field, and most KS writes
    # are asynchronous: ingest/reembed return a Temporal workflow id and the
    # mutation completes later. Without it an audit entry says a write started
    # but gives nothing to correlate against the pipeline that performed it.
    #
    # Nothing populates this yet because no write tool exists. The contract for
    # #8-#11 and #26 is: set `call.state["workflow_id"]` once the upstream call
    # returns. `report_workflow_id` is the supported way to do that; a tool that
    # forgets leaves the field absent rather than wrong.
    workflow_id = call.state.get("workflow_id")
    if workflow_id:
        record["workflow_id"] = str(workflow_id)
    try:
        _emit(policy, record)
    except Exception:
        logger.exception("audit sink unusable while recording outcome for %r", call.name)


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------


def report_workflow_id(call: ToolCall, workflow_id: str | None) -> None:
    """Attach the upstream workflow id to this call's audit outcome record.

    Write tools call this after the KS API returns. Separate function rather
    than having tools poke ``call.state`` directly so the key name is in one
    place and the audit log can't quietly lose the field to a typo.
    """
    if workflow_id:
        call.state["workflow_id"] = workflow_id


def install_write_hooks(mcp: Any, policy: WritePolicy) -> None:
    """Attach the scope gate and audit log to every mutating tool call.

    Registered unconditionally. With writes disabled no write tool exists, so
    the hooks are inert — but installing them anyway means enabling writes
    cannot accidentally enable them *without* the gate.
    """

    async def before(call: ToolCall) -> None:
        if not is_write_tool(call.name):
            return
        identity = await require_admin_scope(call.name)
        call.state["identity"] = identity
        # Fail-closed: an unusable audit sink must stop the mutation, not be
        # swallowed, or writes proceed unrecorded.
        try:
            audit_intent(policy, call, identity)
        except Exception as exc:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=(
                        f"Refusing `{call.name}`: the audit log at {policy.audit_target} "
                        f"could not be written ({exc}). Mutating tools are not run "
                        "unless the call can be recorded."
                    ),
                )
            ) from exc

    def after(call: ToolCall) -> None:
        if not is_write_tool(call.name):
            return
        audit_outcome(policy, call, call.state.get("identity"))

    mcp.add_hooks(before=before, after=after)
