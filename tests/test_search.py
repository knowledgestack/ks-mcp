"""Unit tests for the search tool: request shape + scored hit projection."""

from typing import Any
from uuid import UUID

import pytest
from conftest import scored_chunk_payload
from ksapi.models.scored_chunk_response import ScoredChunkResponse
from ksapi.models.search_type import SearchType

from ks_mcp.schema import ChunkType
from ks_mcp.tools.search import _build_search_request, _hit_from_scored_chunk


def _scored(**overrides: Any) -> ScoredChunkResponse:
    """A real ScoredChunkResponse, so a schema change fails the test loudly."""
    return ScoredChunkResponse.model_validate(
        scored_chunk_payload(str(_CHUNK_ID), **overrides)
    )

_PARENT_ID = UUID("00000000-0000-0000-0000-0000000000aa")
_TAG_ID = UUID("00000000-0000-0000-0000-0000000000bb")
_CHUNK_ID = UUID("00000000-0000-0000-0000-0000000000cc")
_PATH_PART_ID = UUID("00000000-0000-0000-0000-0000000000dd")


def test_build_search_request_minimal() -> None:
    req = _build_search_request("hello", 10, None, None, SearchType.DENSE_ONLY)
    body = req.to_dict()
    # Renamed field reaches the wire (was `parent_path_part_ids`).
    assert "parent_path_part_ids" not in body
    assert body.get("parent_path_ids") is None
    # `with_document=True` is required for hits to carry document_name.
    assert body.get("with_document") is True
    assert body["query"] == "hello"
    assert body["top_k"] == 10
    # `distinct_files` is gone — the backend dropped it.
    assert "distinct_files" not in body


def test_build_search_request_with_filters() -> None:
    req = _build_search_request(
        "term",
        5,
        [_PARENT_ID],
        [_TAG_ID],
        SearchType.FULL_TEXT,
    )
    body = req.to_dict()
    assert body["parent_path_ids"] == [_PARENT_ID]
    assert body["tag_ids"] == [_TAG_ID]
    assert body["search_type"] == SearchType.FULL_TEXT


def test_hit_from_scored_chunk_full_payload() -> None:
    hit = _hit_from_scored_chunk(
        _scored(
            path_part_id=str(_PATH_PART_ID),
            document={
                "id": "00000000-0000-0000-0000-0000000000b1",
                "name": "The Onboarding Handbook",
                "document_type": "PDF",
                "document_origin": "SOURCE",
            },
        )
    )
    assert hit.chunk_id == _CHUNK_ID
    assert hit.document_name == "The Onboarding Handbook"
    assert hit.text == "The onboarding flow has three steps."
    assert hit.score == pytest.approx(0.87)
    assert hit.chunk_type == ChunkType.TEXT
    assert hit.path_part_id == _PATH_PART_ID
    assert hit.materialized_path == "HR/Handbooks/Onboarding"


def test_hit_from_scored_chunk_without_document_has_empty_name() -> None:
    # `with_document=False` responses carry no document; the name degrades to "".
    hit = _hit_from_scored_chunk(_scored(document=None))
    assert hit.document_name == ""
    assert hit.text == "The onboarding flow has three steps."


def test_hit_from_scored_chunk_handles_each_chunk_type() -> None:
    for wire_value, expected in [
        ("TEXT", ChunkType.TEXT),
        ("TABLE", ChunkType.TABLE),
        ("IMAGE", ChunkType.IMAGE),
        ("HTML", ChunkType.HTML),
        ("UNKNOWN", ChunkType.UNKNOWN),
    ]:
        hit = _hit_from_scored_chunk(_scored(chunk_type=wire_value))
        assert hit.chunk_type is expected


def test_hit_from_scored_chunk_unknown_chunk_type_degrades_to_unknown() -> None:
    # The SDK enum would reject "WAT", so bypass validation to simulate a
    # backend that has added a chunk type we don't know about yet.
    scored = _scored()
    object.__setattr__(scored, "chunk_type", "WAT")
    assert _hit_from_scored_chunk(scored).chunk_type == ChunkType.UNKNOWN
