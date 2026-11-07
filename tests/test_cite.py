"""Unit tests for ``cite`` tool helpers and the page-number ancestry walk."""

from uuid import UUID

from conftest import FakeKS, path_part_payload, section_payload

from ks_mcp.client import get_api_client
from ks_mcp.tools.cite import _normalize_part_type, _page_number_from_ancestry, _snippet

_PATH_PART_ID = UUID("00000000-0000-0000-0000-000000000001")
_SECTION_METADATA_ID = UUID("00000000-0000-0000-0000-000000000002")


def test_snippet_returns_short_text_unchanged() -> None:
    assert _snippet("hello") == "hello"


def test_snippet_truncates_long_text_at_word_boundary() -> None:
    text = "alpha beta gamma " * 50
    out = _snippet(text, limit=40)
    assert out.endswith("…")
    # Truncation must respect the cap — allow some slack for the ellipsis.
    assert len(out) <= 41
    # Word-boundary cut: should not slice mid-word.
    assert " " in out
    assert not out.removesuffix("…").endswith("alph")


def test_snippet_falls_back_to_hard_cut_when_no_late_space() -> None:
    text = "x" * 300
    out = _snippet(text, limit=50)
    assert out == "x" * 50 + "…"


def test_snippet_handles_empty_input() -> None:
    assert _snippet("") == ""
    assert _snippet("   ") == ""


def test_normalize_part_type_strips_enum_prefix() -> None:
    assert _normalize_part_type("PartType.SECTION") == "SECTION"
    assert _normalize_part_type("SECTION") == "SECTION"
    assert _normalize_part_type("") == ""
    assert _normalize_part_type(None) == ""


# ---------------------------------------------------------------------------
# The ancestry walk runs against the local KS stand-in rather than hand-rolled
# stubs, so the real AncestryResponse / SectionResponse shapes are exercised.
# ---------------------------------------------------------------------------

_SHALLOW_SECTION_ID = UUID("00000000-0000-0000-0000-0000000000aa")


async def test_page_number_from_ancestry_returns_none_when_no_path_part(ks: FakeKS) -> None:
    assert await _page_number_from_ancestry(get_api_client(), None) is None
    assert ks.requests == []


async def test_page_number_from_ancestry_picks_deepest_section(ks: FakeKS) -> None:
    pp = str(_PATH_PART_ID)
    ks.on(
        "GET",
        rf"/v1/path-parts/{pp}/ancestry",
        json_body={
            "ancestors": [
                path_part_payload(pp, "FOLDER", str(_SHALLOW_SECTION_ID)),
                path_part_payload(pp, "SECTION", str(_SHALLOW_SECTION_ID)),
                path_part_payload(pp, "SECTION", str(_SECTION_METADATA_ID)),
            ]
        },
    )
    ks.on(
        "GET",
        rf"/v1/sections/{_SECTION_METADATA_ID}",
        json_body=section_payload(str(_SECTION_METADATA_ID), 42),
    )

    assert await _page_number_from_ancestry(get_api_client(), _PATH_PART_ID) == 42


async def test_page_number_from_ancestry_returns_none_when_no_section(ks: FakeKS) -> None:
    pp = str(_PATH_PART_ID)
    ks.on(
        "GET",
        rf"/v1/path-parts/{pp}/ancestry",
        json_body={"ancestors": [path_part_payload(pp, "FOLDER", str(_SHALLOW_SECTION_ID))]},
    )

    assert await _page_number_from_ancestry(get_api_client(), _PATH_PART_ID) is None


async def test_page_number_from_ancestry_swallows_api_errors(ks: FakeKS) -> None:
    ks.on(
        "GET",
        rf"/v1/path-parts/{_PATH_PART_ID}/ancestry",
        status=500,
        json_body={"detail": "boom", "code": "internal_error"},
    )

    # Errors fetching ancestry / section should degrade to None, not propagate —
    # the caller treats page_number as best-effort metadata.
    assert await _page_number_from_ancestry(get_api_client(), _PATH_PART_ID) is None


async def test_page_number_none_when_section_lookup_fails(ks: FakeKS) -> None:
    pp = str(_PATH_PART_ID)
    ks.on(
        "GET",
        rf"/v1/path-parts/{pp}/ancestry",
        json_body={"ancestors": [path_part_payload(pp, "SECTION", str(_SECTION_METADATA_ID))]},
    )
    ks.on(
        "GET",
        rf"/v1/sections/{_SECTION_METADATA_ID}",
        status=404,
        json_body={"detail": "gone", "code": "not_found"},
    )

    assert await _page_number_from_ancestry(get_api_client(), _PATH_PART_ID) is None
