"""Read a PDO (document / section / chunk), read neighbours, view image chunks."""

import base64
from typing import Annotated, Any
from uuid import UUID

import httpx
import ksapi
from ksapi.api.chunks_api import ChunksApi
from ksapi.api.document_versions_api import DocumentVersionsApi
from ksapi.api.documents_api import DocumentsApi
from ksapi.api.path_parts_api import PathPartsApi
from ksapi.api.sections_api import SectionsApi
from ksapi.models.part_type import PartType
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent
from pydantic import Field

from ks_mcp.client import get_api_client, get_timeout_s
from ks_mcp.errors import is_not_found, rest_to_mcp

_PAGE_SIZE = 100


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated; {len(text) - limit} more chars]"


def _normalize_part_type(value: Any) -> str:
    """Coerce a ksapi PartType (or string) into a bare uppercase token."""
    text = str(getattr(value, "value", None) or value or "")
    return text.removeprefix("PartType.").upper()


async def _read_chunk_body(client: ksapi.ApiClient, chunk_id: UUID, max_chars: int) -> str:
    chunk = await ChunksApi(client).get_chunk(chunk_id=chunk_id)
    return _truncate(f"{chunk.content or ''}\n\n[chunk:{chunk_id}]", max_chars)


async def _read_document_body(
    client: ksapi.ApiClient, document_id: UUID, fallback_name: str, max_chars: int
) -> str:
    doc = await DocumentsApi(client).get_document(document_id=document_id)
    # `active_version_id` is required and non-nullable in the KS schema, so a
    # DOCUMENT always has a version to read.
    version_id = doc.active_version_id
    versions = DocumentVersionsApi(client)
    pieces: list[str] = [f"# {doc.name or fallback_name}\n"]
    offset = 0
    while True:
        contents = await versions.get_document_version_contents(
            version_id=version_id,
            limit=_PAGE_SIZE,
            offset=offset,
        )
        items = contents.items or []
        if not items:
            break
        for item in items:
            # Items are a discriminated union of SectionContentItem /
            # ChunkContentItem; unwrap to the concrete model.
            inner = getattr(item, "actual_instance", None) or item
            if _normalize_part_type(getattr(inner, "part_type", "")) == "SECTION":
                pieces.append(f"\n## {getattr(inner, 'name', '')}\n")
                continue
            text = getattr(inner, "content", "") or ""
            if not text:
                continue
            chunk_id = getattr(inner, "metadata_obj_id", None)
            tag = f" [chunk:{chunk_id}]" if chunk_id else ""
            pieces.append(f"{text}{tag}\n")
        if len(items) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
        if sum(len(p) for p in pieces) > max_chars:
            break
    return _truncate("".join(pieces), max_chars)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def read(
        path_part_id: Annotated[
            UUID,
            Field(
                description=(
                    "Any PDO id (folder, document, section, or chunk path-part) — OR a "
                    "raw chunk_id from a search hit. The tool first tries to resolve it "
                    "as a path-part; on 404 it falls back to fetching as a chunk."
                ),
            ),
        ],
        max_chars: Annotated[
            int,
            Field(description="Truncate returned text to this many characters.", ge=100, le=50_000),
        ] = 4000,
    ) -> str:
        """Read the contents of any PDO and return Markdown text.

        Dispatch:

        * **CHUNK** → raw chunk text + a ``[chunk:UUID]`` citation tag.
        * **SECTION** → section name + page number; use ``read`` on the parent
          DOCUMENT for full text.
        * **DOCUMENT** → flattened, ordered chunks with section headings,
          paginated up to ``max_chars``.
        * **FOLDER / unknown** → name + a hint to use ``list_contents`` to drill in.

        If ``path_part_id`` 404s as a path-part, ``read`` retries the lookup as
        a chunk, so callers can pass either a ``path_part_id`` or a ``chunk_id``
        from a search result without a second round-trip. Pair with ``cite``
        when you also need a citation footer.
        """
        client = get_api_client()
        try:
            pp = await PathPartsApi(client).get_path_part(path_part_id=path_part_id)
        except ksapi.ApiException as exc:
            # Agents often pass a chunk_id straight from a search hit. Treat 404
            # as "maybe it's a chunk id" and retry rather than dead-ending.
            if is_not_found(exc):
                try:
                    return await _read_chunk_body(client, path_part_id, max_chars)
                except ksapi.ApiException as inner:
                    raise rest_to_mcp(inner) from inner
            raise rest_to_mcp(exc) from exc

        part_type = _normalize_part_type(pp.part_type)
        metadata_obj_id = pp.metadata_obj_id

        try:
            if part_type == "CHUNK" and metadata_obj_id:
                return await _read_chunk_body(client, metadata_obj_id, max_chars)

            if part_type == "SECTION" and metadata_obj_id:
                section = await SectionsApi(client).get_section(section_id=metadata_obj_id)
                page = section.page_number
                page_suffix = f" (page {page})" if page is not None else ""
                header = f"# {section.name or pp.name}{page_suffix}"
                hint = (
                    "(SECTION has no inline body — read its parent DOCUMENT for full text, "
                    "or use list_contents on this path-part to walk children.)"
                )
                return _truncate(f"{header}\n\n{hint}", max_chars)

            if part_type == "DOCUMENT" and metadata_obj_id:
                return await _read_document_body(client, metadata_obj_id, pp.name, max_chars)

            return f"{pp.name} ({part_type or 'UNKNOWN'}) — use list_contents to drill in."
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

    @mcp.tool()
    async def read_around(
        chunk_id: Annotated[UUID, Field(description="Anchor chunk id (NOT a path_part_id).")],
        radius: Annotated[
            int,
            Field(
                description="How many chunks before AND after the anchor to include.", ge=0, le=10
            ),
        ] = 2,
        within_section: Annotated[
            bool,
            Field(
                description=(
                    "True (default) stays inside the anchor's own section. False walks the "
                    "whole document version in order, crossing section boundaries."
                )
            ),
        ] = True,
    ) -> str:
        """Return the ``radius`` chunks before and after an anchor chunk.

        Great for pulling enough local context when a single chunk isn't
        enough — e.g. the answer hinges on a sentence that references "the
        table above". Set ``within_section=False`` when the context you need
        probably spills into the neighbouring section.

        Output is ordered preceding → anchor → succeeding. Each neighbour is
        labelled (``[ANCHOR]`` or ``[ctx N]``) and tagged with its
        ``[chunk:UUID]`` so the agent can cite the right neighbour, not just
        the anchor.
        """
        api = ChunksApi(get_api_client())
        try:
            # ksapi exposes `prev` and `next` separately; `radius` is the symmetric
            # convenience the MCP tool surfaces to keep the agent UX simple.
            # `content_type=CHUNK` drops SECTION rows from the neighbour window.
            neighbours = await api.get_chunk_neighbors(
                chunk_id=chunk_id,
                prev=radius,
                next=radius,
                content_type=PartType.CHUNK,
                within_section=within_section,
            )
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

        items = neighbours.items or []
        anchor_index = neighbours.anchor_index
        pieces: list[str] = []
        for idx, raw in enumerate(items):
            inner = getattr(raw, "actual_instance", None) or raw
            text = getattr(inner, "content", "") or ""
            inner_chunk_id = getattr(inner, "metadata_obj_id", None)
            label = "ANCHOR" if idx == anchor_index else f"ctx {idx - anchor_index:+d}"
            tag = f" [chunk:{inner_chunk_id}]" if inner_chunk_id else ""
            pieces.append(f"[{label}]{tag}\n{text}")
        return "\n\n".join(pieces) or "(no neighbours returned)"

    @mcp.tool()
    async def view_chunk_image(
        chunk_id: Annotated[UUID, Field(description="IMAGE-type chunk id to materialize.")],
    ) -> ImageContent:
        """Fetch the image bytes for an IMAGE-type chunk and return them to the agent.

        Only works for chunks whose metadata carries at least one S3 URL
        (typically ``chunk_type == "IMAGE"``). Agent frameworks that support
        multi-modal content (Claude, GPT-4o, Gemini) render the result inline;
        text-only frameworks should expect this tool to error and call
        ``read``/``cite`` for a textual surrogate instead.
        """
        api = ChunksApi(get_api_client())
        try:
            chunk = await api.get_chunk(chunk_id=chunk_id)
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

        urls = chunk.asset_s3_urls or []
        if not urls:
            raise ValueError("Chunk has no image assets (not an IMAGE-type chunk).")

        async with httpx.AsyncClient(timeout=get_timeout_s()) as http:
            resp = await http.get(urls[0])
            resp.raise_for_status()
            body = resp.content

        return ImageContent(
            type="image",
            data=base64.b64encode(body).decode(),
            mimeType=resp.headers.get("content-type", "image/png"),
        )
