"""Phase-2 provenance tools: chunk lineage and version comparison.

These tools expose the KS backend's lineage and document-version surfaces to
agents so they can reason about *where* a chunk came from and *how* a document
changed between two versions — not just retrieve text.
"""

import difflib
from typing import Annotated
from uuid import UUID

import ksapi
from ksapi.api.chunk_lineages_api import ChunkLineagesApi
from ksapi.api.document_versions_api import DocumentVersionsApi
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from ks_mcp.client import get_api_client
from ks_mcp.errors import rest_to_mcp

_PAGE_SIZE = 100


class LineageEdge(BaseModel):
    """One parent → child derivation step in a chunk's lineage graph."""

    chunk_id: UUID = Field(..., description="The derived (child) chunk.")
    parent_chunk_id: UUID | None = Field(
        default=None, description="The chunk it was derived from. None marks a root / first ingest."
    )


class LineageNode(BaseModel):
    """A chunk that participates in the lineage graph, with a text preview."""

    chunk_id: UUID
    chunk_type: str | None = None
    created_at: str | None = None
    preview: str = Field(default="", description="First ~160 chars of the chunk body.")


class LineageResult(BaseModel):
    chunk_id: UUID
    edges: list[LineageEdge]
    nodes: list[LineageNode]
    summary: str


class VersionDiffResult(BaseModel):
    document_id: UUID
    from_version_id: UUID
    to_version_id: UUID
    added_chunks: int
    removed_chunks: int
    unified_diff: str


async def _flatten_version_text(
    client: ksapi.ApiClient, version_id: UUID, limit: int = 500
) -> list[str]:
    """Materialize a version's chunk texts, in order, so we can diff them."""
    api = DocumentVersionsApi(client)
    lines: list[str] = []
    offset = 0
    while True:
        contents = await api.get_document_version_contents(
            version_id=version_id,
            limit=_PAGE_SIZE,
            offset=offset,
        )
        items = contents.items or []
        if not items:
            break
        for item in items:
            inner = getattr(item, "actual_instance", None) or item
            text = getattr(inner, "content", "") or ""
            if text:
                lines.append(text.strip())
        if len(items) < _PAGE_SIZE or len(lines) >= limit:
            break
        offset += _PAGE_SIZE
    return lines[:limit]


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def trace_chunk_lineage(
        chunk_id: Annotated[
            UUID, Field(description="The chunk whose lineage you want to inspect.")
        ],
        max_depth: Annotated[
            int, Field(description="How many derivation hops to walk (1-10).", ge=1, le=10)
        ] = 5,
    ) -> LineageResult:
        """Return the lineage graph for a chunk (merge/split/re-embed history).

        KS tracks how chunks are derived (merge / split / re-embed /
        re-ingest) so that agents can explain *why* a piece of evidence
        exists. Use this when a downstream answer cites a chunk and you need
        to justify provenance — for example to answer "did this fact survive
        from v3, or did it appear in v4?".

        ``edges`` are parent → child derivation steps; an edge whose
        ``parent_chunk_id`` is null marks an original ingest. ``nodes`` carries
        a short preview of each chunk in the graph so you can see what changed
        without a second round-trip.
        """
        api = ChunkLineagesApi(get_api_client())
        try:
            lineage = await api.get_chunk_lineage(chunk_id=chunk_id, max_depth=max_depth)
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

        edges = [
            LineageEdge(chunk_id=e.chunk_id, parent_chunk_id=e.parent_chunk_id)
            for e in lineage.edges or []
        ]
        nodes = [
            LineageNode(
                chunk_id=n.id,
                chunk_type=getattr(n.chunk_type, "value", None) or str(n.chunk_type or "") or None,
                created_at=str(n.created_at) if n.created_at else None,
                preview=((n.content or "").strip()[:160]),
            )
            for n in lineage.nodes or []
        ]
        summary = (
            f"{len(edges)} lineage edge(s) across {len(nodes)} chunk(s) for {chunk_id}."
            if edges
            else f"No lineage recorded for chunk {chunk_id} (original ingest?)."
        )
        return LineageResult(chunk_id=chunk_id, edges=edges, nodes=nodes, summary=summary)

    @mcp.tool()
    async def compare_versions(
        document_id: Annotated[
            UUID, Field(description="Document whose versions you want to diff.")
        ],
        from_version_id: Annotated[UUID, Field(description="Older / baseline version id.")],
        to_version_id: Annotated[UUID, Field(description="Newer / target version id.")],
        max_chunks_per_side: Annotated[
            int, Field(description="Cap per-version chunks loaded for the diff.", ge=10, le=2000)
        ] = 500,
    ) -> VersionDiffResult:
        """Produce a unified text diff between two versions of the same document.

        Client-side line diff over each version's flattened chunk text —
        enough for agents to answer "what changed in v5 vs v4?" without
        loading both versions wholesale into the prompt. The diff is capped
        to ``max_chunks_per_side`` chunks per version; for very long
        documents, scope the question with ``parent_path_part_ids`` on a
        prior ``search_*`` call instead.
        """
        client = get_api_client()
        try:
            before = await _flatten_version_text(client, from_version_id, limit=max_chunks_per_side)
            after = await _flatten_version_text(client, to_version_id, limit=max_chunks_per_side)
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

        diff_lines = list(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"v:{from_version_id}",
                tofile=f"v:{to_version_id}",
                lineterm="",
                n=2,
            )
        )
        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(
            1 for line in diff_lines if line.startswith("-") and not line.startswith("---")
        )
        return VersionDiffResult(
            document_id=document_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            added_chunks=added,
            removed_chunks=removed,
            unified_diff="\n".join(diff_lines) or "(no differences)",
        )
