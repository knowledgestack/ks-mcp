"""Schema round-trip tests for the public output models."""

from uuid import UUID

import pytest
from pydantic import ValidationError

from ks_mcp.schema import AskCitation, AskResult, ChunkHit, ChunkType, Citation

_CHUNK_ID = UUID("00000000-0000-0000-0000-000000000001")
_PATH_PART_ID = UUID("00000000-0000-0000-0000-000000000002")
_THREAD_ID = UUID("00000000-0000-0000-0000-000000000003")
_DOC_ID = UUID("00000000-0000-0000-0000-000000000004")


def test_chunk_hit_carries_materialized_path() -> None:
    hit = ChunkHit(
        chunk_id=_CHUNK_ID,
        document_name="Doc",
        text="body",
        score=0.5,
        chunk_type=ChunkType.TEXT,
        path_part_id=_PATH_PART_ID,
        materialized_path="A/B/C",
    )
    dumped = hit.model_dump()
    assert dumped["materialized_path"] == "A/B/C"
    assert dumped["chunk_type"] == ChunkType.TEXT


def test_chunk_hit_defaults() -> None:
    hit = ChunkHit(chunk_id=_CHUNK_ID, document_name="", text="")
    assert hit.score is None
    assert hit.path_part_id is None
    assert hit.materialized_path is None
    assert hit.chunk_type == ChunkType.TEXT


def test_citation_serializes_to_expected_keys() -> None:
    cit = Citation(
        chunk_id=_CHUNK_ID,
        document_name="Handbook",
        materialized_path="HR/Onboarding/Section 2",
        page_number=4,
        snippet="hello",
        tag=f"[chunk:{_CHUNK_ID}]",
    )
    dumped = cit.model_dump()
    assert set(dumped) == {
        "chunk_id",
        "document_name",
        "materialized_path",
        "page_number",
        "snippet",
        "tag",
    }
    assert dumped["tag"] == f"[chunk:{_CHUNK_ID}]"


def test_ask_result_default_citations_is_empty_list() -> None:
    result = AskResult(answer="ok", thread_id=_THREAD_ID)
    assert result.citations == []
    assert result.is_error is False
    assert result.message_id is None
    assert result.workflow_id is None


def test_ask_result_with_citations_round_trip() -> None:
    citation = AskCitation(
        chunk_id=_CHUNK_ID,
        quote="the quoted span",
        document_id=_DOC_ID,
        document_name="Doc",
        materialized_path="A/B",
        page_number=2,
    )
    result = AskResult(
        answer="grounded answer",
        citations=[citation],
        thread_id=_THREAD_ID,
        message_id=UUID("00000000-0000-0000-0000-000000000099"),
        workflow_id="wf-123",
    )
    dumped = result.model_dump()
    assert dumped["answer"] == "grounded answer"
    assert dumped["workflow_id"] == "wf-123"
    assert dumped["citations"][0]["quote"] == "the quoted span"
    assert dumped["citations"][0]["document_id"] == _DOC_ID


def test_ask_result_requires_thread_id() -> None:
    with pytest.raises(ValidationError):
        # Intentionally constructed without thread_id to confirm the model
        # rejects it; pyright correctly flags the missing kwarg.
        AskResult.model_validate({"answer": "x"})
