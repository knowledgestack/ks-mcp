"""Tests for the write-safety substrate (#8-#11, #26 foundation; #12 itself).

No mutating tool exists yet, so these exercise the substrate with a synthetic
one. That is the point: the gate has to be proven correct *before* the first
real write tool leans on it, not after.

The properties here are security properties. The failure mode for most of them
is not a crash but a quiet widening — a write tool visible when it shouldn't
be, a non-admin key that gets through, a mutation with no audit record.
"""

import json

import pytest
from conftest import FakeKS, current_user_payload
from mcp import types

from ks_mcp import writes
from ks_mcp.middleware import KnowledgeStackMCP, ToolCall
from ks_mcp.writes import (
    AUDITABLE_ARG_NAMES,
    Identity,
    WritePolicy,
    get_write_policy,
    hash_arguments,
    install_write_hooks,
    mark_write_tool,
)


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


def _server_with_write_tool(policy: WritePolicy) -> tuple[KnowledgeStackMCP, list[str]]:
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


# ---------------------------------------------------------------------------
# policy resolution
# ---------------------------------------------------------------------------


def test_writes_are_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KS_ALLOW_WRITE", raising=False)
    assert get_write_policy().allow_write is False


def test_cli_flag_enables_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KS_ALLOW_WRITE", raising=False)
    assert get_write_policy(allow_write=True).allow_write is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_var_enables_writes(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env as well as flag: the container images configure through env, and
    threading a flag through CMD per deployment is how a setting gets lost."""
    monkeypatch.setenv("KS_ALLOW_WRITE", value)
    assert get_write_policy().allow_write is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "ture", "maybe"])
def test_unrecognised_env_values_leave_writes_off(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo must fail safe. `KS_ALLOW_WRITE=ture` is a read-only server."""
    monkeypatch.setenv("KS_ALLOW_WRITE", value)
    assert get_write_policy().allow_write is False


# ---------------------------------------------------------------------------
# registration gate
# ---------------------------------------------------------------------------


async def test_no_write_tools_are_registered_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real server must expose nothing mutating unless asked."""
    from ks_mcp.server import build_server

    monkeypatch.delenv("KS_ALLOW_WRITE", raising=False)
    tools = {t.name for t in await build_server().list_tools()}

    # Was vacuous: `write_tool_names()` is empty until a write tool registers,
    # so intersecting against it passed no matter what the server exposed.
    # Assert against the actual names #8-#11/#26 will use instead.
    planned = {
        "ingest_document",
        "ingest_url",
        "delete_document",
        "delete_folder",
        "reembed",
        "run_document_workflow",
    }
    assert not (tools & planned), f"mutating tools exposed with writes off: {tools & planned}"
    # And nothing that merely *looks* mutating slipped in either.
    suspicious = {t for t in tools if t.split("_")[0] in {"delete", "ingest", "create", "update", "reembed"}}
    assert not suspicious, f"unexpected mutating-looking tools: {suspicious}"


async def test_enabling_writes_does_not_by_itself_add_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guards the state this lands in: the gate exists, no write tool does yet.

    When #8 lands this should start failing, which is the reminder to update
    it rather than a surprise.
    """
    from ks_mcp.server import build_server

    monkeypatch.setenv("KS_ALLOW_WRITE", "1")
    tools = {t.name for t in await build_server().list_tools()}
    assert "ingest_document" not in tools


async def test_write_hooks_are_installed_even_with_writes_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So turning writes on can't turn them on *ungated*."""
    from ks_mcp.server import build_server

    monkeypatch.delenv("KS_ALLOW_WRITE", raising=False)
    mcp = build_server()
    assert len(mcp._before_hooks) >= 1
    assert len(mcp._after_hooks) >= 1


# ---------------------------------------------------------------------------
# scope gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("role", "is_admin"), [("ADMIN", True), ("OWNER", True), ("USER", False)])
def test_admin_roles(role: str, is_admin: bool) -> None:
    identity = Identity(user_id="u", email=None, tenant_id="t", role=role)
    assert identity.is_admin is is_admin


async def test_non_admin_key_cannot_call_a_write_tool(
    ks: FakeKS, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag is the operator's consent; it is not the caller's authority."""
    ks.on("GET", r"/v1/users/me", json_body=current_user_payload())  # role USER
    policy = WritePolicy(allow_write=True, audit_path=tmp_path / "audit.jsonl")
    mcp, ran = _server_with_write_tool(policy)

    result = await _invoke(mcp, "wipe_everything", {"document_id": "d1", "question": "q"})

    assert ran == [], "the mutating tool body ran for a non-admin key"
    assert "admin-scoped key" in json.dumps(result.model_dump(), default=str)


async def test_admin_key_may_call_a_write_tool(
    ks: FakeKS, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = current_user_payload() | {"current_tenant_role": "ADMIN"}
    ks.on("GET", r"/v1/users/me", json_body=payload)
    policy = WritePolicy(allow_write=True, audit_path=tmp_path / "audit.jsonl")
    mcp, ran = _server_with_write_tool(policy)

    await _invoke(mcp, "wipe_everything", {"document_id": "d1", "question": "q"})

    assert ran == ["d1"]


async def test_read_tools_are_not_gated(ks: FakeKS, tmp_path) -> None:
    """The gate must not touch the read surface — no identity call, no audit."""
    policy = WritePolicy(allow_write=True, audit_path=tmp_path / "audit.jsonl")
    mcp = KnowledgeStackMCP(name="test")
    ran = []

    @mcp.tool()
    async def just_reading() -> str:
        """Read-only."""
        ran.append(True)
        return "ok"

    install_write_hooks(mcp, policy)
    await _invoke(mcp, "just_reading", {})

    assert ran == [True]
    assert not (tmp_path / "audit.jsonl").exists()
    assert ks.paths() == [], "a read tool triggered an identity lookup"


# ---------------------------------------------------------------------------
# audit log (#12)
# ---------------------------------------------------------------------------


def test_arguments_are_hashed_stably_regardless_of_key_order() -> None:
    a = hash_arguments({"x": 1, "y": "two"})
    b = hash_arguments({"y": "two", "x": 1})
    assert a == b and a.startswith("sha256:")


def test_argument_hash_changes_with_content() -> None:
    assert hash_arguments({"x": 1}) != hash_arguments({"x": 2})


async def test_audit_records_intent_and_outcome(ks: FakeKS, tmp_path) -> None:
    ks.on("GET", r"/v1/users/me", json_body=current_user_payload() | {"current_tenant_role": "ADMIN"})
    audit = tmp_path / "audit.jsonl"
    mcp, _ = _server_with_write_tool(WritePolicy(allow_write=True, audit_path=audit))

    await _invoke(mcp, "wipe_everything", {"document_id": "d1", "question": "secret text"})

    records = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [r["event"] for r in records] == ["intent", "outcome"]
    for r in records:
        assert r["tool"] == "wipe_everything"
        assert r["role"] == "ADMIN"
        assert r["tenant_id"]
        assert r["user_id"]
    assert records[1]["failed"] is False
    assert records[1]["duration_s"] is not None


async def test_audit_never_stores_free_text_arguments(ks: FakeKS, tmp_path) -> None:
    """Arguments routinely contain document text and user questions."""
    ks.on("GET", r"/v1/users/me", json_body=current_user_payload() | {"current_tenant_role": "ADMIN"})
    audit = tmp_path / "audit.jsonl"
    mcp, _ = _server_with_write_tool(WritePolicy(allow_write=True, audit_path=audit))

    secret = "patient name and diagnosis"
    await _invoke(mcp, "wipe_everything", {"document_id": "d1", "question": secret})

    body = audit.read_text()
    assert secret not in body
    # ...but the identifier you'd need during an incident IS there.
    records = [json.loads(line) for line in body.splitlines()]
    assert records[0]["args"] == {"document_id": "d1"}
    assert records[0]["args_hash"].startswith("sha256:")


def test_auditable_arg_allowlist_holds_no_free_text_names() -> None:
    """A regression guard on the allowlist itself: adding `question` or
    `prompt` here would silently start logging document content."""
    forbidden = {"question", "prompt", "text", "content", "body", "output_schema", "query"}
    assert not (AUDITABLE_ARG_NAMES & forbidden)


async def test_audit_records_failure_outcome(ks: FakeKS, tmp_path) -> None:
    ks.on("GET", r"/v1/users/me", json_body=current_user_payload() | {"current_tenant_role": "ADMIN"})
    audit = tmp_path / "audit.jsonl"
    mcp = KnowledgeStackMCP(name="test")

    @mcp.tool()
    async def failing_write(document_id: str) -> str:
        """Always fails."""
        raise RuntimeError("upstream exploded")

    mark_write_tool("failing_write")
    install_write_hooks(mcp, WritePolicy(allow_write=True, audit_path=audit))

    await _invoke(mcp, "failing_write", {"document_id": "d1"})

    records = [json.loads(line) for line in audit.read_text().splitlines()]
    assert records[1]["failed"] is True
    assert "upstream exploded" in records[1]["error"]


async def test_an_unwritable_audit_sink_refuses_the_write(ks: FakeKS, tmp_path) -> None:
    """Fail-closed on intent: if we cannot record that we are about to mutate,
    we do not mutate."""
    ks.on("GET", r"/v1/users/me", json_body=current_user_payload() | {"current_tenant_role": "ADMIN"})
    # A path whose parent is a *file*, so mkdir/open must fail.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    mcp, ran = _server_with_write_tool(
        WritePolicy(allow_write=True, audit_path=blocker / "audit.jsonl")
    )

    result = await _invoke(mcp, "wipe_everything", {"document_id": "d1", "question": "q"})

    assert ran == [], "mutated without being able to record it"
    assert "audit log" in json.dumps(result.model_dump(), default=str)


def test_outcome_audit_is_fail_open(tmp_path) -> None:
    """The mutation already happened; refusing it afterwards un-mutates nothing,
    so a broken sink must not also raise."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    policy = WritePolicy(allow_write=True, audit_path=blocker / "audit.jsonl")
    call = ToolCall(name="wipe_everything", arguments={"document_id": "d1"}, duration_s=0.1)

    writes.audit_outcome(policy, call, None)  # must not raise


async def test_audit_falls_back_to_stderr_without_a_path(ks: FakeKS, capsys) -> None:
    """Was a no-op: it took `caplog` and asserted nothing about it, so it only
    proved the tool ran. Now it asserts the record actually reaches stderr."""
    ks.on("GET", r"/v1/users/me", json_body=current_user_payload() | {"current_tenant_role": "ADMIN"})
    mcp, ran = _server_with_write_tool(WritePolicy(allow_write=True, audit_path=None))

    await _invoke(mcp, "wipe_everything", {"document_id": "d1", "question": "q"})

    assert ran == ["d1"]
    err = capsys.readouterr().err
    assert "ks-mcp-audit" in err, "no audit record reached stderr"
    assert '"event": "intent"' in err and '"event": "outcome"' in err
    # stdout is the stdio transport's frame channel; writing there corrupts MCP.
    assert "ks-mcp-audit" not in capsys.readouterr().out


async def test_audit_survives_a_quiet_log_level(ks: FakeKS, capsys, monkeypatch) -> None:
    """An audit trail a log level can switch off is not an audit trail.

    The default sink used the package logger, so `KS_LOG_LEVEL=ERROR` — an
    ordinary production setting — discarded every record while writes carried
    on. It now has its own logger at INFO with `propagate = False`.
    """
    import logging

    from ks_mcp.client import logger as package_logger

    monkeypatch.setenv("KS_LOG_LEVEL", "ERROR")
    package_logger.setLevel(logging.ERROR)
    ks.on("GET", r"/v1/users/me", json_body=current_user_payload() | {"current_tenant_role": "ADMIN"})
    mcp, ran = _server_with_write_tool(WritePolicy(allow_write=True, audit_path=None))

    await _invoke(mcp, "wipe_everything", {"document_id": "d1", "question": "q"})

    assert ran == ["d1"]
    assert "ks-mcp-audit" in capsys.readouterr().err, (
        "KS_LOG_LEVEL silenced the audit log"
    )


def test_policy_reports_its_audit_target(tmp_path) -> None:
    assert WritePolicy().audit_target == "stderr"
    assert WritePolicy(audit_path=tmp_path / "a.jsonl").audit_target.endswith("a.jsonl")


async def test_audit_records_the_upstream_workflow_id_when_reported(
    ks: FakeKS, tmp_path
) -> None:
    """#12 names "upstream workflow id" as a field, and most KS writes are
    async — ingest/reembed return a Temporal id and finish later, so without it
    an entry says a write started and gives nothing to correlate against.

    Nothing populates it yet (no write tool exists). This pins the contract
    #8-#11/#26 must honour, so the field isn't dead code by the time they land.
    """
    ks.on("GET", r"/v1/users/me", json_body=current_user_payload() | {"current_tenant_role": "ADMIN"})
    audit = tmp_path / "audit.jsonl"
    mcp = KnowledgeStackMCP(name="test")

    @mcp.tool()
    async def async_write(document_id: str) -> str:
        """Mutating tool that kicks off a Temporal workflow."""
        # Exactly what a real write tool writes — no reference to its ToolCall,
        # because FastMCP never gives a tool one. An earlier signature took the
        # call explicitly and this test smuggled it out through a hook, which
        # made an unusable API look proven.
        writes.report_workflow_id("wf-abc-123")
        return "started"

    mark_write_tool("async_write")
    install_write_hooks(mcp, WritePolicy(allow_write=True, audit_path=audit))

    await _invoke(mcp, "async_write", {"document_id": "d1"})

    records = [json.loads(line) for line in audit.read_text().splitlines()]
    outcome = records[-1]
    assert outcome["event"] == "outcome"
    assert outcome["workflow_id"] == "wf-abc-123"


def test_report_workflow_id_ignores_none() -> None:
    """A synchronous write has no workflow id; the field must be absent, not null."""
    from ks_mcp.middleware import _current_call

    call = ToolCall(name="x", arguments={})
    token = _current_call.set(call)
    try:
        writes.report_workflow_id(None)
    finally:
        _current_call.reset(token)
    assert "workflow_id" not in call.state


def test_report_workflow_id_outside_a_tool_call_is_a_noop() -> None:
    """A helper shared between a tool and a script must not explode."""
    from ks_mcp.middleware import current_tool_call

    assert current_tool_call() is None
    writes.report_workflow_id("wf-orphan")  # must not raise


async def test_the_tool_call_is_unbound_after_the_call(ks: FakeKS, tmp_path) -> None:
    """Otherwise a later write would annotate a previous call's audit record."""
    from ks_mcp.middleware import current_tool_call

    ks.on("GET", r"/v1/users/me", json_body=current_user_payload() | {"current_tenant_role": "ADMIN"})
    mcp, _ = _server_with_write_tool(WritePolicy(allow_write=True, audit_path=tmp_path / "a.jsonl"))

    await _invoke(mcp, "wipe_everything", {"document_id": "d1", "question": "q"})

    assert current_tool_call() is None


async def test_an_operator_installed_audit_handler_survives(ks: FakeKS, tmp_path) -> None:
    """A SIEM/syslog handler attached to `ks_mcp.audit` must not be removed.

    An earlier version assigned `handlers = [ours]`, which silently dropped it
    — and with `propagate = False` that left no route off the box at all, in
    exactly the deployments that care most about auditing.
    """
    import logging

    from ks_mcp.writes import audit_logger

    captured: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    operator_handler = Capture()
    audit_logger.addHandler(operator_handler)
    try:
        ks.on(
            "GET",
            r"/v1/users/me",
            json_body=current_user_payload() | {"current_tenant_role": "ADMIN"},
        )
        mcp, ran = _server_with_write_tool(WritePolicy(allow_write=True, audit_path=None))

        await _invoke(mcp, "wipe_everything", {"document_id": "d1", "question": "q"})

        assert ran == ["d1"]
        assert operator_handler in audit_logger.handlers, "our handler replaced the operator's"
        assert len(captured) == 2, f"operator handler saw {len(captured)} records, expected 2"
    finally:
        audit_logger.removeHandler(operator_handler)


def test_the_audit_log_path_comes_from_the_environment(monkeypatch, tmp_path) -> None:
    """`KS_AUDIT_LOG` had zero coverage, and it selects the only sink that
    honours the fail-closed contract. Mutating it to `""` left all tests green.
    """
    monkeypatch.setenv("KS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    assert get_write_policy().audit_path == tmp_path / "audit.jsonl"

    monkeypatch.delenv("KS_AUDIT_LOG", raising=False)
    assert get_write_policy().audit_path is None, "unset must mean stderr, not a path"

    monkeypatch.setenv("KS_AUDIT_LOG", "   ")
    assert get_write_policy().audit_path is None, "whitespace must not become a path"
