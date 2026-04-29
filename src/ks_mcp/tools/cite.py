"""Build a structured citation for a chunk_id, ready to drop into an answer."""

from typing import Annotated, Any
from uuid import UUID

import ksapi
from ksapi.api.chunks_api import ChunksApi
from ksapi.api.path_parts_api import PathPartsApi
from ksapi.api.sections_api import SectionsApi
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ks_mcp.client import get_api_client
from ks_mcp.errors import rest_to_mcp
from ks_mcp.schema import Citation

_SNIPPET_CHARS = 240


def _snippet(text: str, limit: int = _SNIPPET_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    # Try to cut at a word boundary so the snippet reads cleanly.
    cut = text.rfind(" ", 0, limit)
    if cut < int(limit * 0.6):
        cut = limit
    return text[:cut].rstrip() + "…"


def _normalize_part_type(value: Any) -> str:
    text = str(value or "")
    if text.startswith("PartType."):
        text = text.removeprefix("PartType.")
    return text


def _page_number_from_ancestry(client: ksapi.ApiClient, path_part_id: UUID | None) -> int | None:
    """Walk root→leaf ancestry and return the nearest SECTION's page_number, if any."""
    if path_part_id is None:
        return None
    try:
        ancestry: Any = PathPartsApi(client).get_path_part_ancestry(path_part_id=path_part_id)
    except ksapi.ApiException:
        return None
    items = getattr(ancestry, "ancestors", None) or getattr(ancestry, "items", None) or []
    section_metadata_id: UUID | None = None
    # Pick the deepest (closest-to-leaf) SECTION ancestor.
    for item in items:
        inner = getattr(item, "actual_instance", None) or item
        if _normalize_part_type(getattr(inner, "part_type", "")) == "SECTION":
            candidate = getattr(inner, "metadata_obj_id", None)
            if candidate is not None:
                section_metadata_id = candidate
    if section_metadata_id is None:
        return None
    try:
        section: Any = SectionsApi(client).get_section(section_id=section_metadata_id)
    except ksapi.ApiException:
        return None
    page = getattr(section, "page_number", None)
    return int(page) if isinstance(page, int) else None


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def cite(
        chunk_id: Annotated[
            UUID,
            Field(
                description=(
                    "Chunk id to build a citation for — typically the ``chunk_id`` from a "
                    "``search_knowledge`` / ``search_keyword`` hit. NOT a ``path_part_id``."
                ),
            ),
        ],
    ) -> Citation:
        """Build a structured citation for a single chunk.

        Returns ``document_name``, ``materialized_path``, ``page_number`` (when
        the chunk lives under a paginated SECTION), a short ``snippet``, and a
        stable ``tag`` string (``[chunk:UUID]``) suitable for inline use in the
        agent's prose.

        Recommended usage: call ``cite`` once per chunk that backed an answer,
        then append the ``tag`` to the relevant sentence and surface the rest
        (document, path, page, snippet) in a footnote / sources block.
        """
        client = get_api_client()
        try:
            # `with_document=True` is required for `chunk.document.name` to be
            # populated — otherwise every citation comes back document-less.
            chunk: Any = ChunksApi(client).get_chunk(
                chunk_id=chunk_id,
                with_document=True,
            )
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

        document = getattr(chunk, "document", None)
        document_name = (
            getattr(document, "name", None)
            or getattr(chunk, "document_name", None)
            or "Untitled document"
        )
        materialized_path = getattr(chunk, "materialized_path", None)
        body = getattr(chunk, "content", None) or getattr(chunk, "text", "") or ""
        page = _page_number_from_ancestry(client, getattr(chunk, "path_part_id", None))

        return Citation(
            chunk_id=chunk_id,
            document_name=str(document_name),
            materialized_path=materialized_path,
            page_number=page,
            snippet=_snippet(body),
            tag=f"[chunk:{chunk_id}]",
        )
