"""Semantic + keyword search over the tenant's knowledge base."""

from typing import Annotated, Any
from uuid import UUID

import ksapi
from ksapi.api.chunks_api import ChunksApi
from ksapi.models.search_type import SearchType
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ks_mcp.client import get_api_client
from ks_mcp.errors import rest_to_mcp
from ks_mcp.schema import ChunkHit, ChunkType, SearchResult


def _build_search_request(
    query: str,
    top_k: int,
    parent_path_part_ids: list[UUID] | None,
    tag_ids: list[UUID] | None,
    search_type: SearchType,
) -> ksapi.ChunkSearchRequest:
    # The backend field is `parent_path_ids` (UUIDs of path-part scopes).
    # The MCP-facing param keeps the longer name (`parent_path_part_ids`) for
    # clarity since "path_part_id" is the canonical id concept on the API.
    # ``with_document=True`` is required to populate ``chunk.document.name`` —
    # without it every hit's document name comes back empty.
    return ksapi.ChunkSearchRequest(
        query=query,
        top_k=top_k,
        search_type=search_type,
        parent_path_ids=list(parent_path_part_ids) if parent_path_part_ids else None,
        tag_ids=list(tag_ids) if tag_ids else None,
        with_document=True,
    )


def _hit_from_scored_chunk(scored: Any) -> ChunkHit:
    # ksapi's ScoredChunkResponse is flat: chunk fields and `score` live side-by-side
    # at the top level. The defensive `scored.chunk` fallback keeps backwards-compat
    # with older SDK builds where the chunk was nested.
    chunk = getattr(scored, "chunk", scored)
    document = getattr(chunk, "document", None)
    document_name = getattr(document, "name", None) or getattr(chunk, "document_name", None) or ""
    # The chunk text lives on `.content` in current ksapi; older builds used `.text`.
    body = getattr(chunk, "content", None) or getattr(chunk, "text", "") or ""
    raw_chunk_type: Any = getattr(chunk, "chunk_type", ChunkType.TEXT.value)
    chunk_type_value = getattr(raw_chunk_type, "value", None) or str(raw_chunk_type)
    try:
        chunk_type = ChunkType(chunk_type_value)
    except ValueError:
        chunk_type = ChunkType.UNKNOWN
    return ChunkHit(
        chunk_id=chunk.id,
        document_name=document_name,
        text=body,
        score=getattr(scored, "score", None),
        chunk_type=chunk_type,
        path_part_id=getattr(chunk, "path_part_id", None),
        materialized_path=getattr(chunk, "materialized_path", None),
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def search_knowledge(
        query: Annotated[
            str,
            Field(
                description="Natural-language query for semantic retrieval.",
                min_length=1,
                max_length=4000,
            ),
        ],
        top_k: Annotated[
            int, Field(description="Max number of chunks to return (1-50).", ge=1, le=50)
        ] = 5,
        parent_path_part_ids: Annotated[
            list[UUID] | None,
            Field(
                description="Restrict search to descendants of these path-parts. Omit for whole tenant."
            ),
        ] = None,
        tag_ids: Annotated[
            list[UUID] | None,
            Field(description="Only include chunks whose document carries all these tag UUIDs."),
        ] = None,
    ) -> SearchResult:
        """Semantic (dense-vector) search over the tenant's chunks.

        Use for conceptual questions ("how does X work", "anything about Y").
        Returns passages semantically related to the query with a relevance
        score (higher = better). For exact-term lookups, prefer
        ``search_keyword``.

        Each hit carries a ``chunk_id``, ``materialized_path`` and a ``text``
        snippet. Recommended follow-ups:

        * ``cite(chunk_id)`` — get a structured citation for the answer.
        * ``read_around(chunk_id, radius=2)`` — pull surrounding context.
        * ``read(path_part_id=chunk_id)`` — fetch the full chunk body.
        """
        api = ChunksApi(get_api_client())
        try:
            response = api.search_chunks(
                chunk_search_request=_build_search_request(
                    query,
                    top_k,
                    parent_path_part_ids,
                    tag_ids,
                    SearchType.DENSE_ONLY,
                )
            )
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc
        items = getattr(response, "items", None) or response or []
        return SearchResult(hits=[_hit_from_scored_chunk(r) for r in items])

    @mcp.tool()
    def search_keyword(
        query: Annotated[
            str,
            Field(
                description="Keyword or phrase to match (BM25 full-text).",
                min_length=1,
                max_length=4000,
            ),
        ],
        top_k: Annotated[
            int, Field(description="Max number of chunks to return (1-50).", ge=1, le=50)
        ] = 5,
        parent_path_part_ids: Annotated[
            list[UUID] | None, Field(description="Restrict to descendants of these path-parts.")
        ] = None,
        tag_ids: Annotated[
            list[UUID] | None,
            Field(description="Only chunks whose document carries all these tags."),
        ] = None,
    ) -> SearchResult:
        """BM25 / keyword search over the tenant's chunks.

        Use when the user mentions a specific term, name, identifier, or
        quoted phrase that needs an exact (or near-exact) match. For
        conceptual queries, prefer ``search_knowledge``.

        Each hit carries a ``chunk_id`` and ``materialized_path``. Pair with
        ``cite``/``read``/``read_around`` to ground an answer.
        """
        api = ChunksApi(get_api_client())
        try:
            response = api.search_chunks(
                chunk_search_request=_build_search_request(
                    query,
                    top_k,
                    parent_path_part_ids,
                    tag_ids,
                    SearchType.FULL_TEXT,
                )
            )
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc
        items = getattr(response, "items", None) or response or []
        return SearchResult(hits=[_hit_from_scored_chunk(r) for r in items])
