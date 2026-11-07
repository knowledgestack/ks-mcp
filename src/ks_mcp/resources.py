"""MCP ``resources/`` over the folder tree: ``ks://`` URIs for folders and documents.

Resources let a client enumerate and reference corpus nodes natively instead of
passing UUID strings through tool arguments, and let a user attach a document to
a conversation without the model having to retrieve it first.

Three URIs:

- ``ks://folders`` — the tenant root listing. A concrete resource, so it shows
  up in ``resources/list`` and gives a client somewhere to start.
- ``ks://folder/{path_part_id}`` — one folder's immediate children.
- ``ks://document/{path_part_id}`` — one document's text as Markdown.

The two parameterized ones are *templates*: they appear under
``resources/templates/list``, not ``resources/list``. That's deliberate.
Eagerly enumerating every folder and document in a tenant would mean walking
the whole tree on each `resources/list` — unbounded work, and on a large corpus
a response no client wants. Listings instead carry each child's own ``ks://``
URI, so a client traverses lazily, one level at a time.
"""

import json
from typing import Any
from uuid import UUID

import ksapi
from mcp.server.fastmcp import FastMCP

from ks_mcp.client import get_api_client
from ks_mcp.errors import rest_to_mcp

# Folder listings are capped: a resource read has no `limit` argument to pass,
# so the ceiling has to live here. 100 is the KS folder endpoints' own maximum
# (the SDK rejects 101 client-side), so it's the most a single read can return.
# `list_contents` is the paging-capable path.
_LISTING_LIMIT = 100
# Documents can be book-length; a resource read is often auto-attached to a
# conversation, so cap it well below the `read` tool's 50k ceiling.
_DOCUMENT_MAX_CHARS = 20_000

_ROOT_URI = "ks://folders"


def _child_uri(part_type: str, path_part_id: Any) -> str:
    """Pick the right ``ks://`` URI for a child node.

    Only folders and documents are addressable. Sections and chunks are
    sub-document detail — they're reachable through the `read` / `cite` tools,
    and giving each one a resource URI would bury the useful nodes.
    """
    if part_type == "FOLDER":
        return f"ks://folder/{path_part_id}"
    if part_type == "DOCUMENT":
        return f"ks://document/{path_part_id}"
    return ""


def _listing_payload(items: Any, *, source: str) -> str:
    """Render a folder listing as JSON, each child carrying its own URI."""
    from ks_mcp.tools.browse import _filter_pp_infos

    children: list[dict[str, Any]] = []
    for info in _filter_pp_infos(items):
        entry: dict[str, Any] = {
            "name": info.name,
            "part_type": info.part_type,
            "path_part_id": str(info.path_part_id),
            "materialized_path": info.materialized_path,
        }
        uri = _child_uri(info.part_type, info.path_part_id)
        if uri:
            entry["uri"] = uri
        children.append(entry)

    truncated = len(children) >= _LISTING_LIMIT
    payload: dict[str, Any] = {
        "source": source,
        "count": len(children),
        "children": children,
    }
    if truncated:
        # Say so rather than letting a client treat a capped page as the whole
        # folder. `list_contents` is the tool that pages.
        payload["truncated"] = True
        payload["note"] = (
            f"Listing capped at {_LISTING_LIMIT} entries; use the `list_contents` "
            "tool to page through the rest."
        )
    return json.dumps(payload, indent=2, ensure_ascii=False)


def register(mcp: FastMCP) -> None:
    @mcp.resource(
        _ROOT_URI,
        name="Knowledge Stack root folders",
        description=(
            "Top-level folders in the tenant's knowledge base. Each child carries a "
            "`ks://folder/...` or `ks://document/...` URI to read next."
        ),
        mime_type="application/json",
    )
    async def root_folders() -> str:
        from ksapi.api.folders_api import FoldersApi

        try:
            listing = await FoldersApi(get_api_client()).list_folders(limit=_LISTING_LIMIT)
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc
        return _listing_payload(listing.items, source=_ROOT_URI)

    @mcp.resource(
        "ks://folder/{path_part_id}",
        name="Knowledge Stack folder",
        description=(
            "Immediate children of one folder, as JSON. Each child carries its own "
            "`ks://` URI for traversal."
        ),
        mime_type="application/json",
    )
    async def folder(path_part_id: str) -> str:
        from ksapi.api.folders_api import FoldersApi

        from ks_mcp.tools.browse import _resolve_folder_id

        try:
            folder_id = UUID(path_part_id)
        except ValueError:
            # A resource URI is often hand-typed or model-generated; a clear
            # message beats a pydantic traceback the client renders raw.
            raise ValueError(
                f"ks://folder/{path_part_id} is not a valid UUID. Read "
                f"`{_ROOT_URI}` to discover valid folder URIs."
            ) from None

        client = get_api_client()
        try:
            resolved = await _resolve_folder_id(client, folder_id)
            listing = await FoldersApi(client).list_folder_contents(
                folder_id=resolved, limit=_LISTING_LIMIT
            )
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc
        return _listing_payload(listing.items, source=f"ks://folder/{path_part_id}")

    @mcp.resource(
        "ks://document/{path_part_id}",
        name="Knowledge Stack document",
        description=(
            "Full text of one document as Markdown, with section headings and "
            "`[chunk:UUID]` citation tags preserved."
        ),
        mime_type="text/markdown",
    )
    async def document(path_part_id: str) -> str:
        from ksapi.api.path_parts_api import PathPartsApi

        from ks_mcp.tools.read import _normalize_part_type, _read_document_body

        try:
            part_id = UUID(path_part_id)
        except ValueError:
            raise ValueError(
                f"ks://document/{path_part_id} is not a valid UUID. Read "
                f"`{_ROOT_URI}` to discover valid document URIs."
            ) from None

        client = get_api_client()
        try:
            pp = await PathPartsApi(client).get_path_part(path_part_id=part_id)
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

        part_type = _normalize_part_type(pp.part_type)
        if part_type != "DOCUMENT":
            # Pointing a folder at the document URI is a common mix-up; name the
            # URI that would have worked instead of returning its bare title.
            uri = _child_uri(part_type, path_part_id)
            hint = f" Try `{uri}`." if uri else ""
            raise ValueError(
                f"{path_part_id} is a {part_type}, not a DOCUMENT.{hint}"
            )

        # `metadata_obj_id` is the document id; the path-part id is not.
        document_id = pp.metadata_obj_id or part_id
        try:
            return await _read_document_body(
                client, document_id, pp.name or "", _DOCUMENT_MAX_CHARS
            )
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc
