"""Phase-2 provenance tools: chunk lineage and version comparison.

These tools expose the KS backend's lineage and document-version surfaces to
agents so they can reason about *where* a chunk came from and *how* a document
changed between two versions — not just retrieve text.
"""


import difflib
from typing import Annotated, Any
from uuid import UUID

import ksapi
from ksapi.api.chunk_lineages_api import ChunkLineagesApi
from ksapi.api.document_versions_api import DocumentVersionsApi
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from ks_mcp.client import get_api_client
from ks_mcp.errors import rest_to_mcp


class LineageEdge(BaseModel):
    parent_chunk_id: UUID | None = None
    child_chunk_id: UUID | None = None
    relation: str | None = None
    created_at: str | None = None


class LineageResult(BaseModel):
    chunk_id: UUID
    edges: list[LineageEdge]
    summary: str


class VersionDiffResult(BaseModel):
    document_id: UUID
    from_version_id: UUID
    to_version_id: UUID
    added_chunks: int
    removed_chunks: int
    unified_diff: str


def _flatten_version_text(client: ksapi.ApiClient, version_id: UUID, limit: int = 500) -> list[str]:
    """Materialize a version's chunk texts, in order, so we can diff them."""
    api = DocumentVersionsApi(client)
    lines: list[str] = []
    offset = 0
    while True:
        contents: Any = api.get_document_version_contents(
            version_id=version_id, limit=100, offset=offset,
        )
        items = getattr(contents, "items", None) or []
        if not items:
            break
        for item in items:
            inner = getattr(item, "actual_instance", None) or item
            text = getattr(inner, "text", None) or getattr(inner, "content", "") or ""
            if text:
                lines.append(text.strip())
        if len(items) < 100 or len(lines) >= limit:
            break
        offset += 100
    return lines[:limit]


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def trace_chunk_lineage(
        chunk_id: Annotated[UUID, Field(description="The chunk whose lineage you want to inspect.")],
    ) -> LineageResult:
        """Return the lineage graph for a chunk.

        KS tracks how chunks are derived (merge / split / re-embed / re-ingest) so
        that agents can explain *why* a piece of evidence exists. Use this when a
        downstream answer cites a chunk and you need to justify provenance.
        """
        client = get_api_client()
        api = ChunkLineagesApi(client)
        try:
            lineage: Any = api.get_chunk_lineage(chunk_id=chunk_id)
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

        raw_edges = getattr(lineage, "edges", None) or getattr(lineage, "items", None) or []
        edges = [
            LineageEdge(
                parent_chunk_id=getattr(e, "parent_chunk_id", None),
                child_chunk_id=getattr(e, "child_chunk_id", None),
                relation=getattr(e, "relation", None) or getattr(e, "kind", None),
                created_at=str(getattr(e, "created_at", "") or "") or None,
            )
            for e in raw_edges
        ]
        summary = (
            f"{len(edges)} lineage edge(s) for chunk {chunk_id}."
            if edges
            else f"No lineage recorded for chunk {chunk_id} (original ingest?)."
        )
        return LineageResult(chunk_id=chunk_id, edges=edges, summary=summary)

    @mcp.tool()
    def compare_versions(
        document_id: Annotated[UUID, Field(description="Document whose versions you want to diff.")],
        from_version_id: Annotated[UUID, Field(description="Older / baseline version id.")],
        to_version_id: Annotated[UUID, Field(description="Newer / target version id.")],
        max_chunks_per_side: Annotated[int, Field(description="Cap per-version chunks loaded for the diff.", ge=10, le=2000)] = 500,
    ) -> VersionDiffResult:
        """Produce a unified text diff between two versions of the same document.

        Client-side line diff over each version's flattened chunk text — enough
        for agents to answer "what changed in v5 vs v4?" without loading both
        versions wholesale into the prompt.
        """
        client = get_api_client()
        try:
            before = _flatten_version_text(client, from_version_id, limit=max_chunks_per_side)
            after = _flatten_version_text(client, to_version_id, limit=max_chunks_per_side)
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

        diff_lines = list(
            difflib.unified_diff(
                before, after,
                fromfile=f"v:{from_version_id}",
                tofile=f"v:{to_version_id}",
                lineterm="",
                n=2,
            )
        )
        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        return VersionDiffResult(
            document_id=document_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            added_chunks=added,
            removed_chunks=removed,
            unified_diff="\n".join(diff_lines) or "(no differences)",
        )
