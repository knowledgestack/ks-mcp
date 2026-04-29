"""Unit tests for the search tool: request shape + scored hit projection."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from ksapi.models.search_type import SearchType

from ks_mcp.schema import ChunkType
from ks_mcp.tools.search import _build_search_request, _hit_from_scored_chunk

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
    document = SimpleNamespace(name="The Onboarding Handbook")
    scored = SimpleNamespace(
        id=_CHUNK_ID,
        path_part_id=_PATH_PART_ID,
        content="The onboarding flow has three steps.",
        chunk_type=ChunkType.TEXT.value,
        score=0.87,
        materialized_path="HR/Handbooks/Onboarding/...",
        document=document,
    )
    hit = _hit_from_scored_chunk(scored)
    assert hit.chunk_id == _CHUNK_ID
    assert hit.document_name == "The Onboarding Handbook"
    assert hit.text == "The onboarding flow has three steps."
    assert hit.score == pytest.approx(0.87)
    assert hit.chunk_type == ChunkType.TEXT
    assert hit.path_part_id == _PATH_PART_ID
    assert hit.materialized_path == "HR/Handbooks/Onboarding/..."


def test_hit_from_scored_chunk_falls_back_to_text_field() -> None:
    # Older SDK builds exposed body on `.text`; we tolerate either.
    scored = SimpleNamespace(
        id=_CHUNK_ID,
        path_part_id=None,
        text="legacy body",
        chunk_type="TEXT",
        score=None,
        document=None,
    )
    hit = _hit_from_scored_chunk(scored)
    assert hit.text == "legacy body"
    assert hit.document_name == ""
    assert hit.path_part_id is None
    assert hit.materialized_path is None


def test_hit_from_scored_chunk_unknown_chunk_type_degrades_to_unknown() -> None:
    scored = SimpleNamespace(
        id=_CHUNK_ID,
        path_part_id=None,
        content="x",
        chunk_type="WAT",
        score=0.1,
        document=None,
    )
    hit = _hit_from_scored_chunk(scored)
    assert hit.chunk_type == ChunkType.UNKNOWN


def test_hit_from_scored_chunk_handles_enum_chunk_type() -> None:
    # ksapi sometimes hands back an Enum, sometimes a bare string. Both must work.
    scored = SimpleNamespace(
        id=_CHUNK_ID,
        path_part_id=None,
        content="x",
        chunk_type=SimpleNamespace(value="TABLE"),
        score=0.1,
        document=None,
    )
    hit = _hit_from_scored_chunk(scored)
    assert hit.chunk_type == ChunkType.TABLE
