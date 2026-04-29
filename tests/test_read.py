"""Unit tests for read.py helpers: text truncation, part-type normalization."""

from ks_mcp.tools.read import _normalize_part_type, _truncate


def test_truncate_under_limit_returns_input() -> None:
    assert _truncate("short", 100) == "short"


def test_truncate_over_limit_marks_remaining_chars() -> None:
    text = "x" * 150
    out = _truncate(text, 100)
    assert out.startswith("x" * 100)
    assert "truncated; 50 more chars" in out


def test_truncate_at_exact_limit_is_unchanged() -> None:
    text = "x" * 50
    assert _truncate(text, 50) == text


def test_normalize_part_type_strips_enum_prefix() -> None:
    assert _normalize_part_type("PartType.CHUNK") == "CHUNK"
    assert _normalize_part_type("PartType.SECTION") == "SECTION"
    assert _normalize_part_type("FOLDER") == "FOLDER"


def test_normalize_part_type_handles_empty_input() -> None:
    assert _normalize_part_type("") == ""
    assert _normalize_part_type(None) == ""
