"""Folder-tree navigation: list children, fuzzy-find by name, introspect a node."""

from typing import Annotated, Any
from uuid import UUID

import ksapi
from ksapi.api.folders_api import FoldersApi
from ksapi.api.path_parts_api import PathPartsApi
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ks_mcp.client import get_api_client
from ks_mcp.errors import is_not_found, rest_to_mcp
from ks_mcp.schema import PathPartAncestry, PathPartInfo


def _normalize_part_type(value: Any) -> str:
    text = str(getattr(value, "value", None) or value or "")
    return text.removeprefix("PartType.").upper()


def _pp_info(pp: Any) -> PathPartInfo | None:
    # List endpoints return discriminated unions (FolderResponse |
    # DocumentResponse | …); every member carries `path_part_id`.
    inner = getattr(pp, "actual_instance", None) or pp
    path_part_id = getattr(inner, "path_part_id", None)
    if path_part_id is None:
        return None
    return PathPartInfo(
        path_part_id=path_part_id,
        name=getattr(inner, "name", "") or "",
        part_type=_normalize_part_type(getattr(inner, "part_type", "")) or "UNKNOWN",
        materialized_path=getattr(inner, "materialized_path", None),
    )


def _filter_pp_infos(items: Any) -> list[PathPartInfo]:
    out: list[PathPartInfo] = []
    for i in items or []:
        info = _pp_info(i)
        if info is not None:
            out.append(info)
    return out


async def _resolve_folder_id(client: ksapi.ApiClient, folder_id: UUID) -> UUID:
    """Accept either a folder id or a folder path_part id.

    Agent loops often reuse the ``path_part_id`` returned by ``list_contents``.
    The KS folders API expects the underlying folder metadata id for nested
    listings, so resolve path-part ids to the backing folder id when possible.
    """
    try:
        pp = await PathPartsApi(client).get_path_part(path_part_id=folder_id)
    except ksapi.ApiException as exc:
        if is_not_found(exc):
            return folder_id
        raise rest_to_mcp(exc) from exc

    if _normalize_part_type(pp.part_type) != "FOLDER":
        return folder_id
    return pp.metadata_obj_id or folder_id


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def list_contents(
        folder_id: Annotated[
            UUID | None,
            Field(
                description=(
                    "Folder PDO id (or its path_part_id — both are accepted). "
                    "Omit to list root-level folders in the tenant."
                ),
            ),
        ] = None,
        limit: Annotated[
            int, Field(description="Max entries to return (1-200).", ge=1, le=200)
        ] = 100,
    ) -> list[PathPartInfo]:
        """List the immediate children of a folder, like ``ls``.

        Pass no argument to list root-level folders. Returns one entry per
        child (folder or document) with its ``path_part_id``, ``name``,
        ``part_type``, and ``materialized_path``. If a stale / unknown
        ``folder_id`` is supplied, the tool falls back to listing the tenant
        root rather than failing — agents discovering the corpus get a useful
        result instead of a dead-end 404.
        """
        client = get_api_client()
        folders = FoldersApi(client)
        try:
            if folder_id is None:
                root = await folders.list_folders(limit=limit)
            else:
                resolved_folder_id = await _resolve_folder_id(client, folder_id)
                root = await folders.list_folder_contents(
                    folder_id=resolved_folder_id, limit=limit
                )
        except ksapi.ApiException as exc:
            if folder_id is not None and is_not_found(exc):
                # Agents frequently recycle stale or hallucinated folder ids.
                # Falling back to tenant root keeps discovery moving without
                # requiring the caller to already know the folder structure.
                try:
                    root = await folders.list_folders(limit=limit)
                except ksapi.ApiException as inner:
                    raise rest_to_mcp(inner) from inner
            else:
                raise rest_to_mcp(exc) from exc
        return _filter_pp_infos(root.items)

    @mcp.tool()
    async def find(
        query: Annotated[
            str,
            Field(
                description="Fuzzy substring of the path-part's name.", min_length=1, max_length=255
            ),
        ],
        parent_path_part_id: Annotated[
            UUID | None,
            Field(
                description="Restrict search to descendants of this folder. Omit for whole tenant."
            ),
        ] = None,
        limit: Annotated[
            int, Field(description="Max entries to return (1-200).", ge=1, le=200)
        ] = 50,
    ) -> list[PathPartInfo]:
        """Fuzzy-search path-parts (folders, documents, sections) by name.

        Use when the user refers to a document by a remembered title fragment
        ("the onboarding handbook", "Q3 forecast"). For matching the *body* of
        a document, use ``search_keyword`` instead — ``find`` only looks at
        names.
        """
        folders = FoldersApi(get_api_client())
        try:
            result = await folders.search_items(
                name_like=query,
                parent_path_part_id=parent_path_part_id,
                limit=limit,
            )
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc
        return _filter_pp_infos(result.items)

    @mcp.tool()
    async def get_info(
        path_part_id: Annotated[
            UUID, Field(description="Any PDO id — folder, document, section, or chunk.")
        ],
    ) -> PathPartAncestry:
        """Return a path-part's own info plus its root-to-leaf ancestry breadcrumb.

        Use when you need to resolve a node's type or build a human-readable
        path before calling ``read`` or ``cite``. The returned ``ancestry``
        list is ordered root → … → parent (excluding the node itself), so the
        last element is the immediate parent.
        """
        api = PathPartsApi(get_api_client())
        try:
            node = await api.get_path_part(path_part_id=path_part_id)
            ancestry_resp = await api.get_path_part_ancestry(path_part_id=path_part_id)
        except ksapi.ApiException as exc:
            if is_not_found(exc):
                raise ValueError("path_part not found") from exc
            raise rest_to_mcp(exc) from exc

        node_info = _pp_info(node)
        if node_info is None:
            raise ValueError("path_part lookup returned no id")
        return PathPartAncestry(
            node=node_info,
            ancestry=_filter_pp_infos(ancestry_resp.ancestors),
        )
