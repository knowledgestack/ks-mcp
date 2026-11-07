"""Semantic, keyword, and hybrid search over the tenant's knowledge base."""

from typing import Annotated
from uuid import UUID

import ksapi
from ksapi.api.chunks_api import ChunksApi
from ksapi.models.hybrid_search_profile import HybridSearchProfile
from ksapi.models.scored_chunk_response import ScoredChunkResponse
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
    *,
    chunk_types: list[ChunkType] | None = None,
    hybrid_profile: HybridSearchProfile | None = None,
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
        chunk_types=[ksapi.ChunkType(t.value) for t in chunk_types] if chunk_types else None,
        hybrid_profile=hybrid_profile,
    )


def _hit_from_scored_chunk(scored: ScoredChunkResponse) -> ChunkHit:
    # ScoredChunkResponse is flat: chunk fields and `score` live side-by-side at
    # the top level, and the body is always on `.content`.
    document = scored.document
    raw_chunk_type = scored.chunk_type
    chunk_type_value = getattr(raw_chunk_type, "value", None) or str(raw_chunk_type)
    try:
        chunk_type = ChunkType(chunk_type_value)
    except ValueError:
        chunk_type = ChunkType.UNKNOWN
    return ChunkHit(
        chunk_id=scored.id,
        document_name=getattr(document, "name", None) or "",
        text=scored.content or "",
        score=scored.score,
        chunk_type=chunk_type,
        path_part_id=scored.path_part_id,
        materialized_path=scored.materialized_path,
    )


async def _run_search(request: ksapi.ChunkSearchRequest) -> SearchResult:
    api = ChunksApi(get_api_client())
    try:
        # ksapi >= 3 is async-first; the unsuffixed methods are coroutines.
        results = await api.search_chunks(chunk_search_request=request)
    except ksapi.ApiException as exc:
        raise rest_to_mcp(exc) from exc
    return SearchResult(hits=[_hit_from_scored_chunk(r) for r in results or []])


_QUERY_FIELD = Field(
    description="Natural-language query for semantic retrieval.",
    min_length=1,
    max_length=4000,
)
_TOP_K_FIELD = Field(description="Max number of chunks to return (1-50).", ge=1, le=50)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def search_knowledge(
        query: Annotated[str, _QUERY_FIELD],
        top_k: Annotated[int, _TOP_K_FIELD] = 5,
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
        chunk_types: Annotated[
            list[ChunkType] | None,
            Field(
                description="Only return chunks of these types (e.g. [TABLE] to find tabular data)."
            ),
        ] = None,
    ) -> SearchResult:
        """Semantic (dense-vector) search over the tenant's chunks.

        Use for conceptual questions ("how does X work", "anything about Y").
        Returns passages semantically related to the query with a relevance
        score (higher = better). For exact-term lookups use ``search_keyword``;
        when you want both at once use ``search_hybrid``.

        Each hit carries a ``chunk_id``, ``materialized_path`` and a ``text``
        snippet. Recommended follow-ups:

        * ``cite(chunk_id)`` — get a structured citation for the answer.
        * ``read_around(chunk_id, radius=2)`` — pull surrounding context.
        * ``read(path_part_id=chunk_id)`` — fetch the full chunk body.
        """
        return await _run_search(
            _build_search_request(
                query,
                top_k,
                parent_path_part_ids,
                tag_ids,
                SearchType.DENSE_ONLY,
                chunk_types=chunk_types,
            )
        )

    @mcp.tool()
    async def search_keyword(
        query: Annotated[
            str,
            Field(
                description="Keyword or phrase to match (BM25 full-text).",
                min_length=1,
                max_length=4000,
            ),
        ],
        top_k: Annotated[int, _TOP_K_FIELD] = 5,
        parent_path_part_ids: Annotated[
            list[UUID] | None, Field(description="Restrict to descendants of these path-parts.")
        ] = None,
        tag_ids: Annotated[
            list[UUID] | None,
            Field(description="Only chunks whose document carries all these tags."),
        ] = None,
        chunk_types: Annotated[
            list[ChunkType] | None, Field(description="Only return chunks of these types.")
        ] = None,
    ) -> SearchResult:
        """BM25 / keyword search over the tenant's chunks.

        Use when the user mentions a specific term, name, identifier, or
        quoted phrase that needs an exact (or near-exact) match. For
        conceptual queries prefer ``search_knowledge``; for a blend of both
        use ``search_hybrid``.

        Each hit carries a ``chunk_id`` and ``materialized_path``. Pair with
        ``cite``/``read``/``read_around`` to ground an answer.
        """
        return await _run_search(
            _build_search_request(
                query,
                top_k,
                parent_path_part_ids,
                tag_ids,
                SearchType.FULL_TEXT,
                chunk_types=chunk_types,
            )
        )

    @mcp.tool()
    async def search_hybrid(
        query: Annotated[str, _QUERY_FIELD],
        top_k: Annotated[int, _TOP_K_FIELD] = 5,
        parent_path_part_ids: Annotated[
            list[UUID] | None, Field(description="Restrict to descendants of these path-parts.")
        ] = None,
        tag_ids: Annotated[
            list[UUID] | None,
            Field(description="Only chunks whose document carries all these tags."),
        ] = None,
        chunk_types: Annotated[
            list[ChunkType] | None, Field(description="Only return chunks of these types.")
        ] = None,
        profile: Annotated[
            HybridSearchProfile,
            Field(
                description=(
                    "Weighting preset: `hybrid` balances both branches, `dense` leans "
                    "semantic, `sparse` leans keyword."
                )
            ),
        ] = HybridSearchProfile.HYBRID,
    ) -> SearchResult:
        """Fused dense + BM25 search — the best default when you're unsure.

        Runs the semantic and keyword branches server-side and fuses the
        rankings, so a query that mixes a concept with a literal term ("the
        refund policy for SKU-4417") retrieves well without you having to pick
        a strategy. Scores are fusion scores and are not comparable to the
        scores returned by ``search_knowledge`` / ``search_keyword``.

        Follow up with ``cite`` / ``read_around`` exactly as for the other
        search tools.
        """
        return await _run_search(
            _build_search_request(
                query,
                top_k,
                parent_path_part_ids,
                tag_ids,
                SearchType.HYBRID,
                chunk_types=chunk_types,
                hybrid_profile=profile,
            )
        )
