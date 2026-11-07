"""Tests for `ks://` MCP resources (issue #4).

Resources are a second public surface over the same corpus, so the parts worth
pinning are the ones a client depends on: which URIs exist, whether listings
carry traversable child URIs, and that the two parameterized URIs stay
*templates* rather than becoming an eager walk of the whole tenant tree.
"""

import json
from typing import Any

import pytest
from conftest import (
    PARENT_PATH_ID,
    TENANT_ID,
    FakeKS,
    folder_payload,
    path_part_payload,
)
from mcp.server.fastmcp import FastMCP

PATH_PART_ID = "00000000-0000-0000-0000-0000000000dd"
DOC_ID = "00000000-0000-0000-0000-0000000000b1"
VERSION_ID = "00000000-0000-0000-0000-0000000000e1"
CHUNK_ID = "00000000-0000-0000-0000-0000000000cc"
FOLDER_PATH_PART = "00000000-0000-0000-0000-0000000000d1"
_TS = "2026-01-01T00:00:00Z"


@pytest.fixture
def server() -> FastMCP:
    from ks_mcp.server import build_server

    return build_server()


async def read_resource(server: FastMCP, uri: str) -> str:
    contents = await server.read_resource(uri)
    return "".join(str(getattr(c, "content", "")) for c in contents)


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


async def test_root_is_a_concrete_resource(server: FastMCP) -> None:
    """A client needs one URI it can reach without already knowing an id."""
    uris = {str(r.uri) for r in await server.list_resources()}
    assert uris == {"ks://folders"}


async def test_folder_and_document_are_templates_not_listed_resources(
    server: FastMCP,
) -> None:
    """The load-bearing design choice.

    If these were concrete resources, `resources/list` would have to walk the
    whole tenant tree on every call — unbounded work, and a response no client
    wants on a real corpus. As templates, traversal is lazy.
    """
    templates = {t.uriTemplate for t in await server.list_resource_templates()}
    assert templates == {"ks://folder/{path_part_id}", "ks://document/{path_part_id}"}

    listed = {str(r.uri) for r in await server.list_resources()}
    assert not any("{" in uri for uri in listed)


async def test_resources_declare_mime_types(server: FastMCP) -> None:
    for resource in await server.list_resources():
        assert resource.mimeType == "application/json"
    by_template = {t.uriTemplate: t for t in await server.list_resource_templates()}
    assert by_template["ks://folder/{path_part_id}"].mimeType == "application/json"
    assert by_template["ks://document/{path_part_id}"].mimeType == "text/markdown"


async def test_every_resource_has_a_name_and_description(server: FastMCP) -> None:
    for item in [*await server.list_resources(), *await server.list_resource_templates()]:
        assert (item.name or "").strip()
        assert (item.description or "").strip()


# ---------------------------------------------------------------------------
# ks://folders  (root listing)
# ---------------------------------------------------------------------------


async def test_root_listing_includes_traversable_child_uris(
    server: FastMCP, ks: FakeKS
) -> None:
    """Listings are how a client walks the tree, so children need URIs."""
    ks.on(
        "GET",
        r"/v1/folders(\?|$)",
        json_body={
            "items": [folder_payload(FOLDER_PATH_PART, DOC_ID, name="HR")],
            "total": 1,
            "limit": 200,
            "offset": 0,
        },
    )

    payload = json.loads(await read_resource(server, "ks://folders"))

    assert payload["source"] == "ks://folders"
    assert payload["count"] == 1
    child = payload["children"][0]
    assert child["name"] == "HR"
    assert child["part_type"] == "FOLDER"
    assert child["uri"] == f"ks://folder/{FOLDER_PATH_PART}"


async def test_root_listing_maps_rest_errors(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "GET",
        r"/v1/folders(\?|$)",
        status=401,
        json_body={"detail": "bad key", "code": "unauthorized"},
    )

    with pytest.raises(Exception, match="KS_API_KEY"):
        await read_resource(server, "ks://folders")


# ---------------------------------------------------------------------------
# ks://folder/{path_part_id}
# ---------------------------------------------------------------------------


def _version_payload() -> dict[str, Any]:
    return {
        "id": VERSION_ID,
        "path_part_id": PATH_PART_ID,
        "version": 3,
        "name": "v3",
        "parent_path_id": PARENT_PATH_ID,
        "materialized_path": "HR/Onboarding/v3",
        "system_managed": False,
        "tenant_id": TENANT_ID,
        "uploader": {
            "user_id": "00000000-0000-0000-0000-000000001001",
            "first_name": "A",
            "last_name": "B",
        },
        "created_at": _TS,
        "updated_at": _TS,
    }


def _document_child() -> dict[str, Any]:
    """A DocumentResponse with every required field the real model declares.

    Deliberately complete (see tests/conftest.py): a missing or renamed field
    in `ksapi` must surface as a ValidationError here rather than being
    swallowed by a getattr fallback.
    """
    return {
        "part_type": "DOCUMENT",
        "id": DOC_ID,
        "path_part_id": PATH_PART_ID,
        "name": "The Onboarding Handbook",
        "parent_path_part_id": FOLDER_PATH_PART,
        "document_type": "PDF",
        "document_origin": "SOURCE",
        "active_version_id": VERSION_ID,
        "active_version": _version_payload(),
        "materialized_path": "HR/Onboarding",
        "system_managed": False,
        "approval_state": "not_required",
        "exclude_from_qdrant": False,
        "tenant_id": TENANT_ID,
        "created_at": _TS,
        "updated_at": _TS,
    }


async def test_folder_listing_gives_documents_a_document_uri(
    server: FastMCP, ks: FakeKS
) -> None:
    ks.on(
        "GET",
        rf"/v1/path-parts/{FOLDER_PATH_PART}$",
        json_body=path_part_payload(FOLDER_PATH_PART, "FOLDER", DOC_ID),
    )
    ks.on(
        "GET",
        rf"/v1/folders/{DOC_ID}/contents",
        json_body={"items": [_document_child()], "total": 1, "limit": 200, "offset": 0},
    )

    payload = json.loads(await read_resource(server, f"ks://folder/{FOLDER_PATH_PART}"))

    child = payload["children"][0]
    assert child["part_type"] == "DOCUMENT"
    assert child["uri"] == f"ks://document/{PATH_PART_ID}"


async def test_folder_uri_rejects_a_non_uuid_with_a_readable_message(
    server: FastMCP,
) -> None:
    """Resource URIs get hand-typed and model-generated; don't leak a traceback."""
    with pytest.raises(Exception, match="not a valid UUID"):
        await read_resource(server, "ks://folder/not-a-uuid")


# ---------------------------------------------------------------------------
# ks://document/{path_part_id}
# ---------------------------------------------------------------------------


def _content_item(part_type: str, name: str, *, content: str = "") -> dict[str, Any]:
    item: dict[str, Any] = {
        "part_type": part_type,
        "path_part_id": PATH_PART_ID,
        "name": name,
        "parent_path_id": PARENT_PATH_ID,
        "metadata_obj_id": CHUNK_ID,
        "depth": 1,
        "materialized_path": "HR/Onboarding",
        "system_managed": False,
        "approval_state": "not_required",
        "created_at": _TS,
        "updated_at": _TS,
    }
    if part_type == "CHUNK":
        item |= {"content": content, "chunk_type": "TEXT", "chunk_metadata": {}}
    else:
        item |= {"page_number": 1, "chunk_start_index": 0, "chunk_end_index": 1}
    return item


def _wire_document(ks: FakeKS) -> None:
    ks.on(
        "GET",
        rf"/v1/path-parts/{PATH_PART_ID}$",
        json_body=path_part_payload(PATH_PART_ID, "DOCUMENT", DOC_ID),
    )
    ks.on("GET", rf"/v1/documents/{DOC_ID}", json_body=_document_child())
    ks.on(
        "GET",
        rf"/v1/document_versions/{VERSION_ID}/contents",
        json_body={
            "items": [
                _content_item("SECTION", "Getting Started"),
                _content_item("CHUNK", "", content="Step one is paperwork."),
            ],
            "total": 2,
            "limit": 100,
            "offset": 0,
        },
    )


async def test_document_resource_returns_markdown_with_citation_tags(
    server: FastMCP, ks: FakeKS
) -> None:
    _wire_document(ks)

    body = await read_resource(server, f"ks://document/{PATH_PART_ID}")

    assert "## Getting Started" in body
    assert "Step one is paperwork." in body
    # Citation tags must survive: a resource read is often what grounds an
    # answer, and without the tag the model can't cite it.
    assert f"[chunk:{CHUNK_ID}]" in body


async def test_document_uri_on_a_folder_names_the_uri_that_works(
    server: FastMCP, ks: FakeKS
) -> None:
    """Mixing up the two URIs is the obvious mistake; point at the fix."""
    ks.on(
        "GET",
        rf"/v1/path-parts/{FOLDER_PATH_PART}$",
        json_body=path_part_payload(FOLDER_PATH_PART, "FOLDER", DOC_ID),
    )

    with pytest.raises(Exception, match=f"ks://folder/{FOLDER_PATH_PART}"):
        await read_resource(server, f"ks://document/{FOLDER_PATH_PART}")


async def test_document_uri_rejects_a_non_uuid(server: FastMCP) -> None:
    with pytest.raises(Exception, match="not a valid UUID"):
        await read_resource(server, "ks://document/nope")


async def test_unknown_document_maps_rest_error(server: FastMCP, ks: FakeKS) -> None:
    ks.on(
        "GET",
        rf"/v1/path-parts/{PATH_PART_ID}$",
        status=404,
        json_body={"detail": "no such path part", "code": "not_found"},
    )

    with pytest.raises(Exception, match="not_found|rejected"):
        await read_resource(server, f"ks://document/{PATH_PART_ID}")


# ---------------------------------------------------------------------------
# listing cap
# ---------------------------------------------------------------------------


async def test_capped_listing_says_it_was_capped(server: FastMCP, ks: FakeKS) -> None:
    """A resource read takes no `limit`, so the cap is invisible without this.

    Letting a client treat a capped page as the whole folder is the quiet
    failure here.
    """
    from ks_mcp.resources import _LISTING_LIMIT

    ks.on(
        "GET",
        r"/v1/folders(\?|$)",
        json_body={
            "items": [
                folder_payload(f"00000000-0000-0000-0000-{i:012d}", DOC_ID, name=f"F{i}")
                for i in range(_LISTING_LIMIT)
            ],
            "total": _LISTING_LIMIT + 50,
            "limit": _LISTING_LIMIT,
            "offset": 0,
        },
    )

    payload = json.loads(await read_resource(server, "ks://folders"))

    assert payload["truncated"] is True
    assert "list_contents" in payload["note"]


# ---------------------------------------------------------------------------
# limit bounds
# ---------------------------------------------------------------------------


async def test_listing_limit_is_within_the_sdk_bound() -> None:
    """Regression: `list_contents`/`find` advertised `le=200`, but the KS folder
    endpoints cap `limit` at 100 and the SDK rejects 101 client-side with a raw
    pydantic error — which reaches the agent as an internal failure, not a
    usable message. Anything trusting the advertised schema hit exactly that.
    """
    import ksapi
    from ksapi.api.folders_api import FoldersApi

    from ks_mcp.resources import _LISTING_LIMIT
    from ks_mcp.server import build_server

    api = FoldersApi(ksapi.ApiClient(ksapi.Configuration(host="http://127.0.0.1:1")))

    # Our resource cap must be accepted by the SDK.
    with pytest.raises(Exception) as excinfo:
        await api.list_folders(limit=_LISTING_LIMIT)
    assert "validation error" not in str(excinfo.value).lower()

    # And the tools must not advertise more than the SDK will take.
    tools = {t.name: t for t in await build_server().list_tools()}
    for name in ("list_contents", "find"):
        bound = tools[name].inputSchema["properties"]["limit"]["maximum"]
        assert bound <= _LISTING_LIMIT, f"{name} advertises limit={bound}, SDK caps at 100"
