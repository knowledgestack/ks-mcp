"""Pydantic input + output models for every MCP tool.

``FastMCP`` auto-generates JSON schemas from these, which is what the calling
agent framework (pydantic-ai, LangGraph, Claude Desktop, etc.) surfaces as
per-argument documentation. Good descriptions here == good tool use.
"""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class ChunkType(str, Enum):
    TEXT = "TEXT"
    TABLE = "TABLE"
    IMAGE = "IMAGE"
    HTML = "HTML"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Shared result shapes
# ---------------------------------------------------------------------------


class ChunkHit(BaseModel):
    chunk_id: UUID = Field(
        ...,
        description=(
            "Stable id for this chunk. Use it with `cite`, `read_around`, `view_chunk_image`, "
            "or `read` (which now also accepts a chunk_id). NOT the same as `path_part_id`."
        ),
    )
    document_name: str = Field(..., description="Name of the owning document.")
    text: str = Field(..., description="Raw text of the chunk.")
    score: float | None = Field(
        default=None, description="Relevance score from the search backend (higher = better)."
    )
    chunk_type: ChunkType = Field(default=ChunkType.TEXT)
    path_part_id: UUID | None = Field(
        default=None,
        description=(
            "PDO id of the chunk node in the path tree. Pass to `get_info` for ancestry; "
            "do NOT confuse with `chunk_id` — they are different UUIDs."
        ),
    )
    materialized_path: str | None = Field(
        default=None,
        description=(
            "Full root-to-leaf path of the chunk (e.g. `Folder/SubFolder/Document/Section/...`). "
            "Use this when displaying citations or grouping hits by document."
        ),
    )


class PathPartInfo(BaseModel):
    path_part_id: UUID
    name: str
    part_type: str = Field(
        ..., description="One of FOLDER | DOCUMENT | SECTION | CHUNK | THREAD_MESSAGE."
    )
    materialized_path: str | None = None


# ---------------------------------------------------------------------------
# Tool inputs
# ---------------------------------------------------------------------------


class SearchInput(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=4_000,
        description="Natural-language query for semantic search, or keyword phrase for BM25.",
    )
    top_k: int = Field(default=5, ge=1, le=50, description="Maximum number of chunks to return.")
    parent_path_part_ids: list[UUID] | None = Field(
        default=None,
        description="Restrict search to descendants of these path-parts. None = whole tenant.",
    )
    tag_ids: list[UUID] | None = Field(
        default=None,
        description="Only include chunks whose owning document carries all these tags.",
    )


class ReadInput(BaseModel):
    path_part_id: UUID = Field(
        ...,
        description=(
            "Any PDO identifier — folder, document, section, or chunk path-part. The tool "
            "dispatches on part type, and falls back to fetching as a chunk_id on 404."
        ),
    )
    max_chars: int = Field(
        default=4_000,
        ge=100,
        le=50_000,
        description="Truncate returned text to this many characters.",
    )


class ReadAroundInput(BaseModel):
    chunk_id: UUID = Field(..., description="Anchor chunk.")
    radius: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Number of chunks before AND after the anchor to include.",
    )


class ListContentsInput(BaseModel):
    folder_id: UUID | None = Field(
        default=None,
        description="Folder PDO id. Omit to list root-level folders in the tenant.",
    )


class FindInput(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Fuzzy-match substring of the path-part's name.",
    )
    parent_path_part_ids: list[UUID] | None = Field(
        default=None,
        description="Restrict to descendants of these path-parts.",
    )


class GetInfoInput(BaseModel):
    path_part_id: UUID = Field(..., description="PDO id to describe.")


class ViewChunkImageInput(BaseModel):
    chunk_id: UUID = Field(..., description="IMAGE-type chunk to materialize.")


# ---------------------------------------------------------------------------
# Tool outputs (for the ones that return structured data; text/image come back
# as MCP content types)
# ---------------------------------------------------------------------------


class SearchResult(BaseModel):
    hits: list[ChunkHit]


class PathPartAncestry(BaseModel):
    node: PathPartInfo
    ancestry: list[PathPartInfo] = Field(
        ..., description="Root → … → parent, excluding the node itself."
    )


class OrganizationInfo(BaseModel):
    tenant_id: UUID
    name: str
    default_language: str = Field(..., description="ISO-639 code, e.g. 'en'.")
    timezone: str = Field(..., description="IANA tz, e.g. 'America/New_York'.")


class CurrentDateTime(BaseModel):
    iso_utc: str = Field(..., description="Current time in UTC, ISO-8601.")
    iso_local: str = Field(..., description="Current time in the tenant's timezone, ISO-8601.")
    timezone: str = Field(..., description="IANA tz used for ``iso_local``.")


class Citation(BaseModel):
    """Compact citation payload for a single chunk.

    Designed to be appended verbatim to an agent's prose answer. The ``tag``
    field is a stable inline reference (``[chunk:UUID]``); ``snippet`` is a
    short excerpt suitable for tooltips or footnotes; ``materialized_path``
    + ``page_number`` give a human-readable source location.
    """

    chunk_id: UUID
    document_name: str = Field(..., description="Name of the owning document.")
    materialized_path: str | None = Field(
        default=None,
        description="Root-to-leaf path of the chunk (e.g. ``Handbook/Onboarding/Section 2/...``).",
    )
    page_number: int | None = Field(
        default=None,
        description=(
            "Page number of the chunk's nearest SECTION ancestor, when available. "
            "May be None for non-paginated documents (web pages, plain text)."
        ),
    )
    snippet: str = Field(
        ..., description="Up to ~240 chars of the chunk text, suitable for a footnote."
    )
    tag: str = Field(
        ...,
        description=(
            "Inline reference token: ``[chunk:UUID]``. Append to the sentence in the agent's "
            "answer that the chunk supports."
        ),
    )


class AskCitation(BaseModel):
    """Citation surfaced inline by the KS agent during an ``ask`` call.

    Lighter than ``Citation`` — the agent emits these directly, so we expose
    only what the streaming protocol provides without an extra round-trip.
    """

    chunk_id: UUID
    quote: str = Field(..., description="The quoted text from the chunk.")
    document_id: UUID | None = None
    document_name: str | None = None
    materialized_path: str | None = None
    page_number: int | None = None


class AskResult(BaseModel):
    """Final assistant answer assembled from a thread streaming run."""

    answer: str = Field(..., description="The agent's final assistant message text.")
    citations: list[AskCitation] = Field(
        default_factory=list,
        description="Citations the agent emitted while answering. May be empty.",
    )
    thread_id: UUID = Field(
        ..., description="Thread the conversation lives on (reuse for follow-ups)."
    )
    message_id: UUID | None = Field(
        default=None,
        description="Assistant message id (use with `read_around`/`cite` if you need more context).",
    )
    workflow_id: str | None = Field(
        default=None,
        description="Underlying agent workflow id; surfaced for debugging and audit.",
    )
    is_error: bool = Field(
        default=False,
        description="True if the agent hit a terminal error mid-stream; ``answer`` then contains the error message.",
    )
