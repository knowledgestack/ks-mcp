"""Unit tests for the ``ask`` tool's SSE parser and result builder."""

from uuid import UUID

from ks_mcp.schema import AskResult
from ks_mcp.tools.ask import (
    _build_result,
    _parse_sse_block,
    _to_ask_citation,
)

_THREAD_ID = UUID("00000000-0000-0000-0000-000000000010")
_MSG_ID = UUID("00000000-0000-0000-0000-000000000020")
_CHUNK_ID = UUID("00000000-0000-0000-0000-000000000030")


def test_parse_sse_block_event_and_data() -> None:
    event, data = _parse_sse_block('event: text_delta\ndata: {"delta":"hi"}')
    assert event == "text_delta"
    assert data == '{"delta":"hi"}'


def test_parse_sse_block_terminal_done() -> None:
    event, data = _parse_sse_block("data: [DONE]")
    assert event is None
    assert data == "[DONE]"


def test_parse_sse_block_multiline_data_is_joined() -> None:
    event, data = _parse_sse_block("event: x\ndata: line1\ndata: line2")
    assert event == "x"
    assert data == "line1\nline2"


def test_parse_sse_block_ignores_comments_and_garbage() -> None:
    event, data = _parse_sse_block(": keepalive\n\n")
    assert event is None
    assert data == ""

    # ``id:`` and unknown fields silently dropped.
    event, data = _parse_sse_block("id: 42\nevent: text_delta\ndata: payload")
    assert event == "text_delta"
    assert data == "payload"


def test_parse_sse_block_handles_field_without_colon() -> None:
    # SSE technically allows "field" without colon — we drop it rather than crash.
    event, data = _parse_sse_block("event: x\ndata: ok\nweird-line")
    assert event == "x"
    assert data == "ok"


def test_to_ask_citation_full_payload() -> None:
    raw = {
        "chunk_id": str(_CHUNK_ID),
        "quote": "Q",
        "document_id": "00000000-0000-0000-0000-000000000040",
        "document_name": "Doc",
        "materialized_path": "/a/b/c",
        "page_number": 7,
    }
    citation = _to_ask_citation(raw)
    assert citation.chunk_id == _CHUNK_ID
    assert citation.quote == "Q"
    assert str(citation.document_id) == "00000000-0000-0000-0000-000000000040"
    assert citation.document_name == "Doc"
    assert citation.materialized_path == "/a/b/c"
    assert citation.page_number == 7


def test_to_ask_citation_minimal_payload() -> None:
    citation = _to_ask_citation({"chunk_id": str(_CHUNK_ID), "quote": ""})
    assert citation.chunk_id == _CHUNK_ID
    assert citation.quote == ""
    assert citation.document_id is None
    assert citation.document_name is None
    assert citation.materialized_path is None
    assert citation.page_number is None


def test_build_result_concatenates_text_parts() -> None:
    result: AskResult = _build_result(["Hello, ", "world!"], [], _THREAD_ID, _MSG_ID, False, "")
    assert result.answer == "Hello, world!"
    assert result.is_error is False
    assert result.thread_id == _THREAD_ID
    assert result.message_id == _MSG_ID


def test_build_result_surfaces_error_text_when_no_partial_answer() -> None:
    result = _build_result([], [], _THREAD_ID, None, True, "rate limited")
    assert result.is_error is True
    assert "rate limited" in result.answer


def test_build_result_keeps_partial_answer_on_error() -> None:
    result = _build_result(["partial"], [], _THREAD_ID, None, True, "blew up")
    # We keep whatever the agent already streamed rather than overwriting with the error.
    assert result.answer == "partial"
    assert result.is_error is True


def test_build_result_empty_stream_returns_placeholder() -> None:
    result = _build_result([], [], _THREAD_ID, None, False, "")
    assert result.answer == "(empty answer)"
    assert result.is_error is False
