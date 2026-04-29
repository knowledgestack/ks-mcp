"""Unit tests for ``cite`` tool helpers and the page-number ancestry walk."""

from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import ksapi

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


class _FakeAncestor:
    """Minimal stand-in for an ancestor item the SDK might return."""

    def __init__(self, part_type: str, metadata_obj_id: UUID | None = None) -> None:
        self.part_type = part_type
        self.metadata_obj_id = metadata_obj_id


class _FakeAncestryResp:
    def __init__(self, ancestors: list[_FakeAncestor]) -> None:
        self.ancestors = ancestors


class _FakeSection:
    def __init__(self, page_number: int | None) -> None:
        self.page_number = page_number


class _StubPathPartsApi:
    def __init__(self, ancestry: Any | Exception) -> None:
        self._ancestry = ancestry

    def get_path_part_ancestry(self, path_part_id: UUID) -> Any:
        if isinstance(self._ancestry, Exception):
            raise self._ancestry
        return self._ancestry


class _StubSectionsApi:
    def __init__(self, section: Any | Exception) -> None:
        self._section = section

    def get_section(self, section_id: UUID) -> Any:
        if isinstance(self._section, Exception):
            raise self._section
        return self._section


def test_page_number_from_ancestry_returns_none_when_no_path_part() -> None:
    client = cast(ksapi.ApiClient, _FakeClient(None, None))
    assert _page_number_from_ancestry(client, None) is None


def test_page_number_from_ancestry_picks_deepest_section(monkeypatch: Any) -> None:
    ancestors = [
        _FakeAncestor("FOLDER"),
        _FakeAncestor("DOCUMENT"),
        _FakeAncestor("SECTION", UUID("00000000-0000-0000-0000-0000000000aa")),
        _FakeAncestor("SECTION", _SECTION_METADATA_ID),
    ]
    section = _FakeSection(page_number=42)
    client = cast(ksapi.ApiClient, _FakeClient(_FakeAncestryResp(ancestors), section))

    import ks_mcp.tools.cite as cite_module

    monkeypatch.setattr(cite_module, "PathPartsApi", lambda c: _StubPathPartsApi(c.ancestry))
    monkeypatch.setattr(cite_module, "SectionsApi", lambda c: _StubSectionsApi(c.section))

    page = _page_number_from_ancestry(client, _PATH_PART_ID)
    assert page == 42


def test_page_number_from_ancestry_returns_none_when_no_section(monkeypatch: Any) -> None:
    ancestors = [_FakeAncestor("FOLDER"), _FakeAncestor("DOCUMENT")]
    client = cast(ksapi.ApiClient, _FakeClient(_FakeAncestryResp(ancestors), None))

    import ks_mcp.tools.cite as cite_module

    monkeypatch.setattr(cite_module, "PathPartsApi", lambda c: _StubPathPartsApi(c.ancestry))
    monkeypatch.setattr(cite_module, "SectionsApi", lambda c: _StubSectionsApi(c.section))

    assert _page_number_from_ancestry(client, _PATH_PART_ID) is None


def test_page_number_from_ancestry_swallows_api_errors(monkeypatch: Any) -> None:
    err = ksapi.ApiException(status=500, reason="boom")
    client = cast(ksapi.ApiClient, _FakeClient(err, None))

    import ks_mcp.tools.cite as cite_module

    monkeypatch.setattr(cite_module, "PathPartsApi", lambda c: _StubPathPartsApi(c.ancestry))
    monkeypatch.setattr(cite_module, "SectionsApi", lambda c: _StubSectionsApi(c.section))

    # Errors fetching ancestry / section should degrade to None, not propagate —
    # the caller treats page_number as best-effort metadata.
    assert _page_number_from_ancestry(client, _PATH_PART_ID) is None


class _FakeClient(SimpleNamespace):
    """Carries the ancestry / section stubs into the patched API constructors."""

    def __init__(self, ancestry: Any, section: Any) -> None:
        super().__init__(ancestry=ancestry, section=section)
