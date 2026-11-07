"""Unit tests for the ``ask`` tool's SSE parser and result builder."""

from uuid import UUID

import ksapi

from ks_mcp.tools.ask import (
    _Accumulator,
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
    raw = ksapi.EnrichedCitation(
        chunk_id=_CHUNK_ID,
        quote="Q",
        document_id=UUID("00000000-0000-0000-0000-000000000040"),
        document_name="Doc",
        materialized_path="/a/b/c",
        path_part_id=UUID("00000000-0000-0000-0000-000000000050"),
        version_number=3,
    )
    citation = _to_ask_citation(raw)
    assert citation is not None
    assert citation.chunk_id == _CHUNK_ID
    assert citation.quote == "Q"
    assert str(citation.document_id) == "00000000-0000-0000-0000-000000000040"
    assert citation.document_name == "Doc"
    assert citation.materialized_path == "/a/b/c"
    assert str(citation.path_part_id) == "00000000-0000-0000-0000-000000000050"
    assert citation.version_number == 3


def test_to_ask_citation_minimal_payload() -> None:
    citation = _to_ask_citation(ksapi.EnrichedCitation(chunk_id=_CHUNK_ID))
    assert citation is not None
    assert citation.chunk_id == _CHUNK_ID
    assert citation.quote == ""
    assert citation.document_id is None
    assert citation.document_name is None
    assert citation.materialized_path is None
    assert citation.version_number is None


def test_to_ask_citation_drops_citation_without_chunk_id() -> None:
    # Every EnrichedCitation field is optional upstream; a chunk-less citation
    # can't be cited, so it's dropped rather than faked.
    assert _to_ask_citation(ksapi.EnrichedCitation(quote="orphan")) is None


def _acc(
    parts: list[str],
    *,
    message_id: UUID | None = None,
    is_error: bool = False,
    error_text: str = "",
) -> _Accumulator:
    acc = _Accumulator(_THREAD_ID)
    acc.parts = parts
    acc.message_id = message_id
    acc.is_error = is_error
    acc.error_text = error_text
    return acc


def test_result_concatenates_text_parts() -> None:
    result = _acc(["Hello, ", "world!"], message_id=_MSG_ID).result()
    assert result.answer == "Hello, world!"
    assert result.is_error is False
    assert result.thread_id == _THREAD_ID
    assert result.message_id == _MSG_ID


def test_result_surfaces_error_text_when_no_partial_answer() -> None:
    result = _acc([], is_error=True, error_text="rate limited").result()
    assert result.is_error is True
    assert "rate limited" in result.answer


def test_result_keeps_partial_answer_on_error() -> None:
    result = _acc(["partial"], is_error=True, error_text="blew up").result()
    # We keep whatever the agent already streamed rather than overwriting with the error.
    assert result.answer == "partial"
    assert result.is_error is True


def test_result_empty_stream_returns_placeholder() -> None:
    result = _acc([]).result()
    assert result.answer == "(empty answer)"
    assert result.is_error is False


def test_result_on_timeout_keeps_partial_and_flags_error() -> None:
    result = _acc(["half an answ"]).result(timed_out=True)
    assert result.answer.startswith("half an answ")
    assert "timed out" in result.answer
    assert result.is_error is True


def test_accumulator_feeds_a_full_stream() -> None:
    # Event names mirror ThreadStreamEvent in the backend's streaming_protocol.
    acc = _Accumulator(_THREAD_ID)
    acc.feed("message_start", f'{{"id":"{_MSG_ID}"}}')
    acc.feed("text_start", f'{{"id":"{_MSG_ID}","part_id":"p1"}}')
    acc.feed("text_delta", '{"delta":"Refunds "}')
    acc.feed("text_delta", '{"delta":"take 30 days."}')
    acc.feed("text_end", f'{{"id":"{_MSG_ID}","part_id":"p1"}}')
    assert acc.done is False
    acc.feed("message_end", f'{{"id":"{_MSG_ID}"}}')

    assert acc.done is True
    result = acc.result()
    assert result.answer == "Refunds take 30 days."
    assert result.message_id == _MSG_ID
    # Citations never arrive on the stream — they're fetched from the message.
    assert result.citations == []


def test_accumulator_ignores_tool_and_reasoning_events() -> None:
    acc = _Accumulator(_THREAD_ID)
    for event in (
        "tool_call",
        "tool_result",
        "reasoning_start",
        "reasoning_delta",
        "reasoning_end",
        "user_message_appended",
    ):
        acc.feed(event, '{"id":"x","delta":"should not appear"}')
    acc.feed("text_delta", '{"delta":"real text"}')
    assert acc.result().answer == "real text"
    assert acc.done is False


def test_accumulator_records_error_code_and_message() -> None:
    # The error event carries {code, message} (WSError), not {error}.
    acc = _Accumulator(_THREAD_ID)
    acc.feed("error", '{"code":"quota_exceeded","message":"out of messages"}')
    result = acc.result()
    assert result.is_error is True
    assert "quota_exceeded" in result.answer
    assert "out of messages" in result.answer


def test_accumulator_ignores_malformed_json() -> None:
    acc = _Accumulator(_THREAD_ID)
    acc.feed("text_delta", "{not json")
    acc.feed("message_start", '{"id":"not-a-uuid"}')
    assert acc.result().answer == "(empty answer)"
    assert acc.result().citations == []
    assert acc.message_id is None


def test_accumulator_terminates_on_done_sentinel() -> None:
    # The backend yields the terminal block as a bare `data: [DONE]` with no
    # `event:` line (thread_stream.py), so it must match on data alone.
    acc = _Accumulator(_THREAD_ID)
    acc.feed(None, "[DONE]")
    assert acc.done is True


def test_accumulator_terminates_on_done_event_name_too() -> None:
    acc = _Accumulator(_THREAD_ID)
    acc.feed("done", "[DONE]")
    assert acc.done is True


def test_accumulator_takes_message_id_from_any_event() -> None:
    """We subscribe after the 202, so `message_start` may already be gone.

    Without this, `message_id` stays None, the citation fetch never runs, and
    `ask` quietly regresses to returning no citations.
    """
    acc = _Accumulator(_THREAD_ID)
    acc.feed("text_delta", f'{{"id":"{_MSG_ID}","part_id":"p1","delta":"hi"}}')
    assert acc.message_id == _MSG_ID
    assert acc.result().answer == "hi"


def test_accumulator_keeps_first_message_id_seen() -> None:
    other = UUID("00000000-0000-0000-0000-0000000000ff")
    acc = _Accumulator(_THREAD_ID)
    acc.feed("message_start", f'{{"id":"{_MSG_ID}"}}')
    acc.feed("text_delta", f'{{"id":"{other}","delta":"x"}}')
    assert acc.message_id == _MSG_ID


def test_accumulator_message_id_from_message_end_only() -> None:
    acc = _Accumulator(_THREAD_ID)
    acc.feed("message_end", f'{{"id":"{_MSG_ID}"}}')
    assert acc.message_id == _MSG_ID
    assert acc.done is True
