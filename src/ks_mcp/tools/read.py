"""Read a PDO (document / section / chunk), read neighbours, view image chunks."""

import base64
from typing import Annotated, Any
from uuid import UUID

import httpx
import ksapi
from ksapi.api.chunks_api import ChunksApi
from ksapi.api.documents_api import DocumentsApi
from ksapi.api.path_parts_api import PathPartsApi
from ksapi.api.sections_api import SectionsApi
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent
from pydantic import Field

from ks_mcp.client import get_api_client
from ks_mcp.errors import is_not_found, rest_to_mcp


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated; {len(text) - limit} more chars]"


def _normalize_part_type(value: Any) -> str:
    """Coerce a ksapi PartType (or string) into a bare uppercase token."""
    text = str(value or "")
    if text.startswith("PartType."):
        text = text.removeprefix("PartType.")
    return text


def _read_chunk_body(client: ksapi.ApiClient, chunk_id: UUID, max_chars: int) -> str:
    chunk: Any = ChunksApi(client).get_chunk(chunk_id=chunk_id)
    # ksapi model exposes the text as .content on Chunk; older builds used .text.
    body = getattr(chunk, "content", None) or getattr(chunk, "text", "") or ""
    return _truncate(f"{body}\n\n[chunk:{chunk_id}]", max_chars)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def read(
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
        path_parts = PathPartsApi(client)
        try:
            pp = path_parts.get_path_part(path_part_id=path_part_id)
        except ksapi.ApiException as exc:
            # Agents often pass a chunk_id straight from a search hit. Treat 404
            # as "maybe it's a chunk id" and retry rather than dead-ending.
            if is_not_found(exc):
                try:
                    return _read_chunk_body(client, path_part_id, max_chars)
                except ksapi.ApiException as inner:
                    raise rest_to_mcp(inner) from inner
            raise rest_to_mcp(exc) from exc

        part_type = _normalize_part_type(getattr(pp, "part_type", ""))
        metadata_obj_id = getattr(pp, "metadata_obj_id", None)

        try:
            if part_type == "CHUNK" and metadata_obj_id:
                return _read_chunk_body(client, metadata_obj_id, max_chars)

            if part_type == "SECTION" and metadata_obj_id:
                section: Any = SectionsApi(client).get_section(section_id=metadata_obj_id)
                page = getattr(section, "page_number", None)
                page_suffix = f" (page {page})" if page is not None else ""
                header = f"# {getattr(section, 'name', None) or pp.name}{page_suffix}"
                hint = (
                    "(SECTION has no inline body — read its parent DOCUMENT for full text, "
                    "or use list_contents on this path-part to walk children.)"
                )
                return _truncate(f"{header}\n\n{hint}", max_chars)

            if part_type == "DOCUMENT" and metadata_obj_id:
                from ksapi.api.document_versions_api import DocumentVersionsApi

                doc: Any = DocumentsApi(client).get_document(document_id=metadata_obj_id)
                version_id = getattr(doc, "active_version_id", None)
                if version_id is None:
                    return getattr(doc, "name", pp.name)
                pieces: list[str] = [f"# {getattr(doc, 'name', pp.name)}\n"]
                offset = 0
                while True:
                    contents: Any = DocumentVersionsApi(client).get_document_version_contents(
                        version_id=version_id,
                        limit=100,
                        offset=offset,
                    )
                    items = getattr(contents, "items", None) or []
                    if not items:
                        break
                    for item in items:
                        inner = getattr(item, "actual_instance", None) or item
                        ptype = _normalize_part_type(getattr(inner, "part_type", ""))
                        name = getattr(inner, "name", "")
                        if ptype == "SECTION":
                            pieces.append(f"\n## {name}\n")
                        else:
                            # ChunkContentItem stores body on `.content`; tolerate `.text`.
                            text = (
                                getattr(inner, "content", None) or getattr(inner, "text", "") or ""
                            )
                            chunk_id = getattr(inner, "metadata_obj_id", None) or getattr(
                                inner, "id", None
                            )
                            tag = f" [chunk:{chunk_id}]" if chunk_id else ""
                            if text:
                                pieces.append(f"{text}{tag}\n")
                    if len(items) < 100:
                        break
                    offset += 100
                return _truncate("".join(pieces), max_chars)

            return f"{pp.name} ({part_type or 'UNKNOWN'}) — use list_contents to drill in."
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

    @mcp.tool()
    def read_around(
        chunk_id: Annotated[UUID, Field(description="Anchor chunk id (NOT a path_part_id).")],
        radius: Annotated[
            int,
            Field(
                description="How many chunks before AND after the anchor to include.", ge=0, le=10
            ),
        ] = 2,
    ) -> str:
        """Return the ``radius`` chunks before and after an anchor chunk.

        Great for pulling enough local context when a single chunk isn't
        enough — e.g. the answer hinges on a sentence that references "the
        table above".

        Output is ordered preceding → anchor → succeeding. Each neighbour is
        labelled (``[ANCHOR]`` or ``[ctx N]``) and tagged with its
        ``[chunk:UUID]`` so the agent can cite the right neighbour, not just
        the anchor.
        """
        api = ChunksApi(get_api_client())
        try:
            # ksapi exposes `prev` and `next` separately; `radius` is the symmetric
            # convenience the MCP tool surfaces to keep the agent UX simple.
            neighbours: Any = api.get_chunk_neighbors(
                chunk_id=chunk_id,
                prev=radius,
                next=radius,
                chunks_only=True,
            )
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

        items = getattr(neighbours, "items", []) or []
        anchor_index = getattr(neighbours, "anchor_index", -1)
        pieces: list[str] = []
        for idx, raw in enumerate(items):
            # Items are SectionContentItemOrChunkContentItem — unwrap the union.
            # `chunks_only=True` upstream means non-chunk neighbours are already
            # filtered out, so we just render whatever comes back.
            inner = getattr(raw, "actual_instance", None) or raw
            text = getattr(inner, "content", None) or getattr(inner, "text", "") or ""
            inner_chunk_id = getattr(inner, "metadata_obj_id", None) or getattr(inner, "id", None)
            label = "ANCHOR" if idx == anchor_index else f"ctx {idx - anchor_index:+d}"
            tag = f" [chunk:{inner_chunk_id}]" if inner_chunk_id else ""
            pieces.append(f"[{label}]{tag}\n{text}")
        return "\n\n".join(pieces) or "(no neighbours returned)"

    @mcp.tool()
    def view_chunk_image(
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
            chunk: Any = api.get_chunk(chunk_id=chunk_id)
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

        urls = getattr(chunk, "asset_s3_urls", None) or []
        if not urls:
            raise ValueError("Chunk has no image assets (not an IMAGE-type chunk).")

        with httpx.Client(timeout=30.0) as http:
            resp = http.get(urls[0])
            resp.raise_for_status()
            body = resp.content

        mime = resp.headers.get("content-type", "image/png")
        return ImageContent(
            type="image",
            data=base64.b64encode(body).decode(),
            mimeType=mime,
        )
