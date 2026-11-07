"""End-to-end tool tests against a local KS stand-in.

These drive the registered MCP tools through ``FastMCP.call_tool`` and the real
``ksapi`` client, so they cover the whole path: argument schema → SDK request
serialization → HTTP → response parsing → MCP result model. This is the layer
that was missing when every tool was silently returning an un-awaited coroutine.
"""

import json
from typing import Any

import pytest
from conftest import (
    PARENT_PATH_ID,
    TENANT_ID,
    FakeKS,
    chunk_payload,
    current_user_payload,
    document_ref,
    folder_payload,
    path_part_payload,
    scored_chunk_payload,
    section_payload,
    tenant_payload,
)
from mcp.server.fastmcp import FastMCP

CHUNK_ID = "00000000-0000-0000-0000-0000000000cc"
PATH_PART_ID = "00000000-0000-0000-0000-0000000000dd"
SECTION_ID = "00000000-0000-0000-0000-0000000000a1"
DOC_ID = "00000000-0000-0000-0000-0000000000b1"


@pytest.fixture
def server() -> FastMCP:
    from ks_mcp.server import build_server

    return build_server()


async def call(server: FastMCP, name: str, args: dict[str, Any]) -> Any:
    """Invoke a tool and return its structured payload (or text)."""
    result = await server.call_tool(name, args)
    # FastMCP returns (content_blocks, structured_payload) in current releases.
    if isinstance(result, tuple):
        content, structured = result
        return structured if structured is not None else content
    return result


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


async def test_search_knowledge_round_trip(server: FastMCP, ks: FakeKS) -> None:
    ks.on("POST", r"/v1/chunks/search", json_body=[scored_chunk_payload(CHUNK_ID)])

    out = await call(server, "search_knowledge", {"query": "onboarding", "top_k": 3})

    hits = out["hits"] if isinstance(out, dict) else out.hits
    assert len(hits) == 1
    hit = hits[0]
    assert str(hit["chunk_id"]) == CHUNK_ID
    assert hit["text"] == "The onboarding flow has three steps."
    assert hit["score"] == 0.87
    assert hit["materialized_path"] == "HR/Handbooks/Onboarding"

    sent = ks.last().json()
    assert sent["query"] == "onboarding"
    assert sent["top_k"] == 3
    assert sent["search_type"] == "dense_only"
    assert sent["with_document"] is True


async def test_search_keyword_uses_full_text(server: FastMCP, ks: FakeKS) -> None:
    ks.on("POST", r"/v1/chunks/search", json_body=[])

    await call(server, "search_keyword", {"query": "SKU-4417"})

    assert ks.last().json()["search_type"] == "full_text"


async def test_search_hybrid_sends_profile(server: FastMCP, ks: FakeKS) -> None:
    ks.on("POST", r"/v1/chunks/search", json_body=[])

    await call(server, "search_hybrid", {"query": "refund policy for SKU-4417"})

    sent = ks.last().json()
    assert sent["search_type"] == "hybrid"
    assert sent["hybrid_profile"] == "hybrid"


async def test_search_forwards_chunk_type_filter(server: FastMCP, ks: FakeKS) -> None:
    ks.on("POST", r"/v1/chunks/search", json_body=[])

    await call(server, "search_knowledge", {"query": "q1 revenue", "chunk_types": ["TABLE"]})

    assert ks.last().json()["chunk_types"] == ["TABLE"]


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


async def test_read_resolves_chunk_path_part(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "GET",
        rf"/v1/path-parts/{PATH_PART_ID}$",
        json_body=path_part_payload(PATH_PART_ID, "CHUNK", CHUNK_ID),
    )
    ks.on("GET", rf"/v1/chunks/{CHUNK_ID}", json_body=chunk_payload(CHUNK_ID))

    out = await call(server, "read", {"path_part_id": PATH_PART_ID})

    text = out if isinstance(out, str) else json.dumps(out)
    assert "three steps" in text
    assert f"[chunk:{CHUNK_ID}]" in text


async def test_read_falls_back_to_chunk_on_404(server: FastMCP, ks: FakeKS) -> None:
    # No path-part stub -> the harness 404s, which should trigger the chunk retry.
    ks.on("GET", rf"/v1/chunks/{CHUNK_ID}", json_body=chunk_payload(CHUNK_ID))

    out = await call(server, "read", {"path_part_id": CHUNK_ID})

    text = out if isinstance(out, str) else json.dumps(out)
    assert "three steps" in text
    assert ks.paths("GET")[0].startswith("/v1/path-parts/")


async def test_read_section_reports_page_number(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "GET",
        rf"/v1/path-parts/{PATH_PART_ID}$",
        json_body=path_part_payload(PATH_PART_ID, "SECTION", SECTION_ID),
    )
    ks.on("GET", rf"/v1/sections/{SECTION_ID}", json_body=section_payload(SECTION_ID, 4))

    out = await call(server, "read", {"path_part_id": PATH_PART_ID})

    text = out if isinstance(out, str) else json.dumps(out)
    assert "page 4" in text


async def test_read_around_requests_chunk_content_type(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "GET",
        rf"/v1/chunks/{CHUNK_ID}/neighbors",
        json_body={
            "items": [
                {
                    "part_type": "CHUNK",
                    "path_part_id": PATH_PART_ID,
                    "name": "chunk",
                    "parent_path_id": PARENT_PATH_ID,
                    "metadata_obj_id": CHUNK_ID,
                    "depth": 1,
                    "materialized_path": "HR/Handbooks/Onboarding",
                    "system_managed": False,
                    "approval_state": "not_required",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "content": "anchor body",
                    "chunk_type": "TEXT",
                    "chunk_metadata": {},
                }
            ],
            "anchor_index": 0,
            "anchor_offset": 0,
            "total": 1,
        },
    )

    out = await call(server, "read_around", {"chunk_id": CHUNK_ID, "radius": 1})

    text = out if isinstance(out, str) else json.dumps(out)
    assert "[ANCHOR]" in text
    assert "anchor body" in text
    # `chunks_only` was removed in backend 3.x; `content_type=CHUNK` replaced it.
    path = ks.paths("GET")[-1]
    assert "content_type=CHUNK" in path
    assert "chunks_only" not in path
    assert "prev=1" in path and "next=1" in path


# ---------------------------------------------------------------------------
# cite
# ---------------------------------------------------------------------------


async def test_cite_builds_citation_with_page(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "GET",
        rf"/v1/chunks/{CHUNK_ID}",
        json_body=chunk_payload(CHUNK_ID, document=document_ref(DOC_ID, "The Onboarding Handbook")),
    )
    ks.on(
        "GET",
        rf"/v1/path-parts/{PATH_PART_ID}/ancestry",
        json_body={"ancestors": [path_part_payload(PATH_PART_ID, "SECTION", SECTION_ID)]},
    )
    ks.on("GET", rf"/v1/sections/{SECTION_ID}", json_body=section_payload(SECTION_ID, 7))

    out = await call(server, "cite", {"chunk_id": CHUNK_ID})

    assert out["document_name"] == "The Onboarding Handbook"
    assert out["page_number"] == 7
    assert out["tag"] == f"[chunk:{CHUNK_ID}]"
    # `with_document=true` is what populates document_name.
    assert "with_document=true" in ks.paths("GET")[0].lower()


# ---------------------------------------------------------------------------
# browse
# ---------------------------------------------------------------------------


async def test_list_contents_lists_root(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "GET",
        r"/v1/folders(\?|$)",
        json_body={
            "items": [folder_payload(PATH_PART_ID, DOC_ID)],
            "total": 1,
            "limit": 100,
            "offset": 0,
        },
    )

    out = await call(server, "list_contents", {})

    items = out["result"] if isinstance(out, dict) and "result" in out else out
    assert len(items) == 1
    assert items[0]["name"] == "HR"
    assert items[0]["part_type"] == "FOLDER"
    assert str(items[0]["path_part_id"]) == PATH_PART_ID


# ---------------------------------------------------------------------------
# org
# ---------------------------------------------------------------------------


async def test_get_organization_info_resolves_via_users_me(server: FastMCP, ks: FakeKS) -> None:
    ks.on("GET", r"/v1/users/me", json_body=current_user_payload())
    ks.on("GET", r"/v1/tenants/[0-9a-f-]+$", json_body=tenant_payload())

    out = await call(server, "get_organization_info", {})

    assert out["name"] == "Acme Corp"
    assert out["timezone"] == "America/New_York"
    assert out["default_language"] == "en"
    # There is no /v1/tenants/me route; identity comes from /v1/users/me.
    assert any(p.endswith("/v1/users/me") for p in ks.paths("GET"))
    assert not any(p.endswith("/v1/tenants/me") for p in ks.paths("GET"))


async def test_get_current_datetime_uses_tenant_timezone(server: FastMCP, ks: FakeKS) -> None:
    ks.on("GET", r"/v1/users/me", json_body=current_user_payload())
    ks.on("GET", r"/v1/tenants/[0-9a-f-]+$", json_body=tenant_payload())

    out = await call(server, "get_current_datetime", {})

    assert out["timezone"] == "America/New_York"
    assert out["iso_utc"].endswith("+00:00")


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


async def test_trace_chunk_lineage_reads_graph_shape(server: FastMCP, ks: FakeKS) -> None:
    parent = "00000000-0000-0000-0000-0000000000aa"
    ks.on(
        "GET",
        rf"/v1/chunk-lineages/{CHUNK_ID}",
        json_body={
            "nodes": [
                {
                    "id": CHUNK_ID,
                    "content": "merged body",
                    "chunk_type": "TEXT",
                    "chunk_metadata": {},
                    "tenant_id": TENANT_ID,
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                }
            ],
            "edges": [{"parent_chunk_id": parent, "chunk_id": CHUNK_ID}],
        },
    )

    out = await call(server, "trace_chunk_lineage", {"chunk_id": CHUNK_ID})

    assert len(out["edges"]) == 1
    assert str(out["edges"][0]["parent_chunk_id"]) == parent
    assert str(out["edges"][0]["chunk_id"]) == CHUNK_ID
    assert out["nodes"][0]["preview"] == "merged body"
    assert "1 lineage edge" in out["summary"]
    assert "max_depth=5" in ks.paths("GET")[-1]


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


async def test_extract_posts_inline_schema(server: FastMCP, ks: FakeKS) -> None:
    ks.on("POST", r"/v1/agent/extract", json_body={"structured": {"renewal_date": "2027-01-01"}})

    schema = {"type": "object", "properties": {"renewal_date": {"type": "string"}}}
    out = await call(server, "extract", {"prompt": "renewal date", "output_schema": schema})

    assert out["structured"] == {"renewal_date": "2027-01-01"}
    sent = ks.last().json()
    assert sent["prompt"] == "renewal date"
    assert sent["output_schema"] == schema


async def test_extract_rejects_both_schema_sources(server: FastMCP, ks: FakeKS) -> None:
    with pytest.raises(Exception, match="exactly one"):
        await call(
            server,
            "extract",
            {
                "prompt": "x",
                "output_schema": {"type": "object"},
                "schema_path_part_id": PATH_PART_ID,
            },
        )
    assert ks.requests == []


async def test_extract_rejects_neither_schema_source(server: FastMCP, ks: FakeKS) -> None:
    with pytest.raises(Exception, match="exactly one"):
        await call(server, "extract", {"prompt": "x"})
    assert ks.requests == []


# ---------------------------------------------------------------------------
# error propagation
# ---------------------------------------------------------------------------


async def test_quota_429_surfaces_as_stop_looping(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "POST",
        r"/v1/chunks/search",
        status=429,
        json_body={"detail": "daily cap", "code": "quota_exceeded", "request_id": "req-1"},
    )

    with pytest.raises(Exception) as excinfo:
        await call(server, "search_knowledge", {"query": "x"})

    message = str(excinfo.value)
    assert "quota exhausted" in message
    assert "req-1" in message


async def test_401_surfaces_key_hint(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "POST",
        r"/v1/chunks/search",
        status=401,
        json_body={"detail": "bad key", "code": "unauthorized"},
    )

    with pytest.raises(Exception, match="KS_API_KEY"):
        await call(server, "search_knowledge", {"query": "x"})


# ---------------------------------------------------------------------------
# ask — the full three-leg flow: create thread → POST message → SSE → enrich
# ---------------------------------------------------------------------------

THREAD_ID = "00000000-0000-0000-0000-0000000000e1"
MESSAGE_ID = "00000000-0000-0000-0000-0000000000e2"


def _wire_thread_creation(ks: FakeKS) -> None:
    # create_thread is 201 and send_user_message is 202 — returning 200 makes the
    # SDK parse the body as ErrorResponse instead.
    ks.on(
        "POST",
        r"/v1/threads$",
        status=201,
        json_body={
            "id": THREAD_ID,
            "path_part_id": PATH_PART_ID,
            "title": "Refunds",
            "materialized_path": "Threads/Refunds",
            "tenant_id": TENANT_ID,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )
    ks.on(
        "POST",
        rf"/v1/threads/{THREAD_ID}/user_message",
        status=202,
        json_body={"workflow_id": "wf-77", "user_message_id": MESSAGE_ID},
    )


def _wire_ask(ks: FakeKS, *, citations: list[dict[str, Any]] | None = None) -> None:
    _wire_thread_creation(ks)
    ks.on_sse(
        rf"/v1/threads/{THREAD_ID}/stream",
        [
            ("message_start", f'{{"id":"{MESSAGE_ID}"}}'),
            ("text_start", f'{{"id":"{MESSAGE_ID}","part_id":"p1"}}'),
            ("text_delta", '{"delta":"Refunds take "}'),
            ("text_delta", '{"delta":"30 days."}'),
            ("text_end", f'{{"id":"{MESSAGE_ID}","part_id":"p1"}}'),
            ("message_end", f'{{"id":"{MESSAGE_ID}"}}'),
            (None, "[DONE]"),
        ],
    )
    ks.on(
        "GET",
        rf"/v1/threads/{THREAD_ID}/messages/{MESSAGE_ID}",
        json_body={
            "id": MESSAGE_ID,
            "path_part_id": PATH_PART_ID,
            "parent_path_id": PARENT_PATH_ID,
            "materialized_path": "Threads/t1",
            "tenant_id": TENANT_ID,
            "role": "ASSISTANT",
            "sequence": 2,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "content": {
                "text": "Refunds take 30 days. [chunk:resolved]",
                "is_error": False,
                "citations": citations if citations is not None else [],
            },
        },
    )


async def test_ask_assembles_stream_and_enriches_citations(server: FastMCP, ks: FakeKS) -> None:
    _wire_ask(
        ks,
        citations=[
            {
                "chunk_id": CHUNK_ID,
                "quote": "30 days",
                "document_name": "Refund Policy",
                "materialized_path": "Legal/Refund Policy",
                "path_part_id": PATH_PART_ID,
                "version_number": 3,
            }
        ],
    )

    out = await call(server, "ask", {"question": "How long do refunds take?"})

    # The persisted (citation-resolved) text wins over the raw stream deltas.
    assert out["answer"] == "Refunds take 30 days. [chunk:resolved]"
    assert str(out["thread_id"]) == THREAD_ID
    assert str(out["message_id"]) == MESSAGE_ID
    assert out["workflow_id"] == "wf-77"
    assert out["is_error"] is False
    assert len(out["citations"]) == 1
    assert out["citations"][0]["quote"] == "30 days"
    assert out["citations"][0]["document_name"] == "Refund Policy"
    assert out["citations"][0]["version_number"] == 3

    posts = ks.paths("POST")
    assert posts[0].endswith("/v1/threads")
    assert posts[1].endswith("/user_message")
    assert ks.requests[1].json()["input_text"] == "How long do refunds take?"


async def test_ask_falls_back_to_stream_text_when_enrich_fails(
    server: FastMCP, ks: FakeKS
) -> None:
    _wire_thread_creation(ks)
    ks.on_sse(
        rf"/v1/threads/{THREAD_ID}/stream",
        [
            ("message_start", f'{{"id":"{MESSAGE_ID}"}}'),
            ("text_delta", '{"delta":"partial but useful"}'),
            ("message_end", f'{{"id":"{MESSAGE_ID}"}}'),
        ],
    )
    # No stub for the message fetch -> 404; the tool must still answer.
    out = await call(server, "ask", {"question": "q"})

    assert out["answer"] == "partial but useful"
    assert out["citations"] == []


async def test_ask_reuses_supplied_thread_without_creating_one(
    server: FastMCP, ks: FakeKS
) -> None:
    _wire_ask(ks)

    await call(server, "ask", {"question": "follow up", "thread_id": THREAD_ID})

    assert not any(p.endswith("/v1/threads") for p in ks.paths("POST"))


async def test_ask_forwards_output_language(server: FastMCP, ks: FakeKS) -> None:
    _wire_ask(ks)

    await call(server, "ask", {"question": "q", "output_language": "zh"})

    user_message = next(r for r in ks.requests if r.path.endswith("/user_message"))
    assert user_message.json()["output_language"] == "zh"


async def test_ask_surfaces_stream_error_event(server: FastMCP, ks: FakeKS) -> None:
    _wire_thread_creation(ks)
    ks.on_sse(
        rf"/v1/threads/{THREAD_ID}/stream",
        [
            ("error", '{"code":"quota_exceeded","message":"out of messages"}'),
            (None, "[DONE]"),
        ],
    )

    out = await call(server, "ask", {"question": "q"})

    assert out["is_error"] is True
    assert "quota_exceeded" in out["answer"]


async def test_ask_recovers_full_answer_when_stream_times_out(
    server: FastMCP, ks: FakeKS
) -> None:
    """A timeout is not an error if the final message can still be fetched.

    The agent frequently finishes just after we stop reading the stream, so the
    persisted message is authoritative — flagging is_error would make callers
    discard a complete answer.
    """
    _wire_thread_creation(ks)
    ks.on_sse(
        rf"/v1/threads/{THREAD_ID}/stream",
        [
            ("message_start", f'{{"id":"{MESSAGE_ID}"}}'),
            ("text_delta", '{"delta":"Refunds take "}'),
        ],
        hang_s=12.0,  # never sends message_end -> the client must time out
    )
    ks.on(
        "GET",
        rf"/v1/threads/{THREAD_ID}/messages/{MESSAGE_ID}",
        json_body={
            "id": MESSAGE_ID,
            "path_part_id": PATH_PART_ID,
            "parent_path_id": PARENT_PATH_ID,
            "materialized_path": "Threads/t1",
            "tenant_id": TENANT_ID,
            "role": "ASSISTANT",
            "sequence": 2,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "content": {"text": "Refunds take 30 days.", "is_error": False, "citations": []},
        },
    )

    out = await call(server, "ask", {"question": "q", "timeout_s": 10.0})

    assert out["answer"] == "Refunds take 30 days."
    assert out["is_error"] is False
    assert "timed out" not in out["answer"]


async def test_ask_flags_error_when_timeout_and_no_message(server: FastMCP, ks: FakeKS) -> None:
    """With nothing recoverable, a timeout must surface as a degraded result."""
    _wire_thread_creation(ks)
    ks.on_sse(
        rf"/v1/threads/{THREAD_ID}/stream",
        [("text_delta", '{"delta":"partial"}')],
        hang_s=12.0,
    )
    # No message_start -> no message_id -> nothing to fetch.
    out = await call(server, "ask", {"question": "q", "timeout_s": 10.0})

    assert out["is_error"] is True
    assert "timed out" in out["answer"]
    assert "partial" in out["answer"]


async def test_ask_handles_stream_closing_without_terminator(
    server: FastMCP, ks: FakeKS
) -> None:
    """A truncated stream (no message_end / [DONE]) must not hang or error."""
    _wire_ask(ks)
    ks.on_sse(
        rf"/v1/threads/{THREAD_ID}/stream",
        [
            ("message_start", f'{{"id":"{MESSAGE_ID}"}}'),
            ("text_delta", '{"delta":"truncated"}'),
        ],
    )

    out = await call(server, "ask", {"question": "q"})

    assert out["answer"] == "Refunds take 30 days. [chunk:resolved]"
    assert out["is_error"] is False


# ---------------------------------------------------------------------------
# read: DOCUMENT dispatch (section headings, chunk tags, pagination)
# ---------------------------------------------------------------------------

VERSION_ID = "00000000-0000-0000-0000-0000000000b2"


def _content_item(part_type: str, name: str, *, content: str = "", meta: str = "") -> dict[str, Any]:
    item: dict[str, Any] = {
        "part_type": part_type,
        "path_part_id": PATH_PART_ID,
        "name": name,
        "parent_path_id": PARENT_PATH_ID,
        "metadata_obj_id": meta or CHUNK_ID,
        "depth": 1,
        "materialized_path": "HR/Handbooks/Onboarding",
        "system_managed": False,
        "approval_state": "not_required",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    if part_type == "CHUNK":
        item |= {"content": content, "chunk_type": "TEXT", "chunk_metadata": {}}
    else:
        item |= {"page_number": 1, "chunk_start_index": 0, "chunk_end_index": 1}
    return item


def _version_payload() -> dict[str, Any]:
    return {
        "id": VERSION_ID,
        "path_part_id": PATH_PART_ID,
        "version": 3,
        "name": "v3",
        "parent_path_id": PARENT_PATH_ID,
        "materialized_path": "HR/Handbooks/Onboarding/v3",
        "system_managed": False,
        "tenant_id": TENANT_ID,
        "uploader": {
            "user_id": "00000000-0000-0000-0000-000000001001",
            "first_name": "A",
            "last_name": "B",
        },
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _wire_document(ks: FakeKS, version_pages: list[list[dict[str, Any]]]) -> None:
    ks.on(
        "GET",
        rf"/v1/path-parts/{PATH_PART_ID}$",
        json_body=path_part_payload(PATH_PART_ID, "DOCUMENT", DOC_ID),
    )
    ks.on(
        "GET",
        rf"/v1/documents/{DOC_ID}",
        json_body={
            "part_type": "DOCUMENT",
            "id": DOC_ID,
            "path_part_id": PATH_PART_ID,
            "name": "The Onboarding Handbook",
            "parent_path_part_id": None,
            "document_type": "PDF",
            "document_origin": "SOURCE",
            "active_version_id": VERSION_ID,
            "active_version": _version_payload(),
            "materialized_path": "HR/Handbooks/Onboarding",
            "system_managed": False,
            "approval_state": "not_required",
            "exclude_from_qdrant": False,
            "tenant_id": TENANT_ID,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )

    def responder(path: str) -> tuple[int, Any]:
        offset = 0
        for part in path.split("&"):
            if part.split("=")[0].endswith("offset"):
                offset = int(part.split("=")[1])
        index = offset // 100
        items = version_pages[index] if index < len(version_pages) else []
        return 200, {"items": items, "total": 999, "limit": 100, "offset": offset}

    ks.on("GET", rf"/v1/document_versions/{VERSION_ID}/contents", responder=responder)


async def test_read_document_flattens_sections_and_chunks(server: FastMCP, ks: FakeKS) -> None:
    _wire_document(
        ks,
        [
            [
                _content_item("SECTION", "Getting Started"),
                _content_item("CHUNK", "", content="Step one is paperwork."),
                _content_item("SECTION", "Benefits"),
                _content_item("CHUNK", "", content="Dental is included."),
            ]
        ],
    )

    out = await call(server, "read", {"path_part_id": PATH_PART_ID})
    text = out if isinstance(out, str) else json.dumps(out)

    assert "# The Onboarding Handbook" in text
    assert "## Getting Started" in text
    assert "## Benefits" in text
    assert "Step one is paperwork." in text
    assert "Dental is included." in text
    # Every chunk must carry a citable tag.
    assert text.count(f"[chunk:{CHUNK_ID}]") == 2


async def test_read_document_paginates_until_short_page(server: FastMCP, ks: FakeKS) -> None:
    """A full page must trigger another fetch; a short page must stop the loop."""
    full_page = [_content_item("CHUNK", "", content=f"para {i}") for i in range(100)]
    short_page = [_content_item("CHUNK", "", content="final para")]
    _wire_document(ks, [full_page, short_page])

    out = await call(server, "read", {"path_part_id": PATH_PART_ID, "max_chars": 50_000})
    text = out if isinstance(out, str) else json.dumps(out)

    assert "para 0" in text
    assert "para 99" in text
    assert "final para" in text
    offsets = [p for p in ks.paths("GET") if "/contents" in p]
    assert len(offsets) == 2, offsets
    assert "offset=0" in offsets[0]
    assert "offset=100" in offsets[1]


async def test_read_document_truncates_at_max_chars(server: FastMCP, ks: FakeKS) -> None:
    _wire_document(
        ks, [[_content_item("CHUNK", "", content="x" * 500)]]
    )

    out = await call(server, "read", {"path_part_id": PATH_PART_ID, "max_chars": 200})
    text = out if isinstance(out, str) else json.dumps(out)

    assert "truncated" in text
    assert len(text) < 400


async def test_read_folder_hints_at_list_contents(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "GET",
        rf"/v1/path-parts/{PATH_PART_ID}$",
        json_body=path_part_payload(PATH_PART_ID, "FOLDER", DOC_ID),
    )

    out = await call(server, "read", {"path_part_id": PATH_PART_ID})
    text = out if isinstance(out, str) else json.dumps(out)

    assert "FOLDER" in text
    assert "list_contents" in text


async def test_view_chunk_image_rejects_non_image_chunk(server: FastMCP, ks: FakeKS) -> None:
    ks.on("GET", rf"/v1/chunks/{CHUNK_ID}", json_body=chunk_payload(CHUNK_ID, asset_s3_urls=[]))

    with pytest.raises(Exception, match="no image assets"):
        await call(server, "view_chunk_image", {"chunk_id": CHUNK_ID})


# ---------------------------------------------------------------------------
# find / get_info
# ---------------------------------------------------------------------------


async def test_find_searches_by_name(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "GET",
        r"/v1/folders/search",
        json_body={
            "items": [folder_payload(PATH_PART_ID, DOC_ID, "Onboarding Handbook")],
            "total": 1,
            "limit": 50,
            "offset": 0,
            "counts_by_type": {},
        },
    )

    out = await call(server, "find", {"query": "handbook", "parent_path_part_id": PATH_PART_ID})

    items = out["result"] if isinstance(out, dict) and "result" in out else out
    assert len(items) == 1
    assert items[0]["name"] == "Onboarding Handbook"
    assert items[0]["part_type"] == "FOLDER"
    path = ks.paths("GET")[-1]
    assert "name_like=handbook" in path
    assert f"parent_path_part_id={PATH_PART_ID}" in path


async def test_get_info_returns_node_and_ancestry(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "GET",
        rf"/v1/path-parts/{PATH_PART_ID}/ancestry",
        json_body={
            "ancestors": [
                path_part_payload(PATH_PART_ID, "FOLDER", DOC_ID),
                path_part_payload(PATH_PART_ID, "DOCUMENT", DOC_ID),
            ]
        },
    )
    ks.on(
        "GET",
        rf"/v1/path-parts/{PATH_PART_ID}$",
        json_body=path_part_payload(PATH_PART_ID, "CHUNK", CHUNK_ID),
    )

    out = await call(server, "get_info", {"path_part_id": PATH_PART_ID})

    assert out["node"]["part_type"] == "CHUNK"
    # Ordered root -> parent, so the last entry is the immediate parent.
    assert [a["part_type"] for a in out["ancestry"]] == ["FOLDER", "DOCUMENT"]


async def test_get_info_rejects_unknown_path_part(server: FastMCP, ks: FakeKS) -> None:
    with pytest.raises(Exception, match="not found"):
        await call(server, "get_info", {"path_part_id": PATH_PART_ID})


# ---------------------------------------------------------------------------
# compare_versions
# ---------------------------------------------------------------------------

VERSION_A = "00000000-0000-0000-0000-0000000000f1"
VERSION_B = "00000000-0000-0000-0000-0000000000f2"


def _version_contents(ks: FakeKS, version_id: str, texts: list[str]) -> None:
    ks.on(
        "GET",
        rf"/v1/document_versions/{version_id}/contents",
        json_body={
            "items": [_content_item("CHUNK", "", content=t) for t in texts],
            "total": len(texts),
            "limit": 100,
            "offset": 0,
        },
    )


async def test_compare_versions_reports_added_and_removed(server: FastMCP, ks: FakeKS) -> None:
    _version_contents(ks, VERSION_A, ["intro unchanged", "old clause", "tail unchanged"])
    _version_contents(ks, VERSION_B, ["intro unchanged", "new clause", "tail unchanged"])

    out = await call(
        server,
        "compare_versions",
        {"document_id": DOC_ID, "from_version_id": VERSION_A, "to_version_id": VERSION_B},
    )

    assert out["added_chunks"] == 1
    assert out["removed_chunks"] == 1
    assert "-old clause" in out["unified_diff"]
    assert "+new clause" in out["unified_diff"]
    assert str(out["from_version_id"]) == VERSION_A
    assert str(out["to_version_id"]) == VERSION_B


async def test_compare_versions_reports_no_differences(server: FastMCP, ks: FakeKS) -> None:
    _version_contents(ks, VERSION_A, ["same"])
    _version_contents(ks, VERSION_B, ["same"])

    out = await call(
        server,
        "compare_versions",
        {"document_id": DOC_ID, "from_version_id": VERSION_A, "to_version_id": VERSION_B},
    )

    assert out["added_chunks"] == 0
    assert out["removed_chunks"] == 0
    assert out["unified_diff"] == "(no differences)"
