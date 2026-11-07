"""Guard the published `schemas/` against drift (issue #21).

Tool schemas are this server's public contract, but they're *derived* from
Python signatures and pydantic models — a renamed argument or a dropped output
field changes the contract with nothing in the diff to show it. Committing the
generated schemas makes that visible; this test is what stops them going stale.
"""

import json
from pathlib import Path

import pytest

from ks_mcp.schema_export import _sorted_json, build_schemas

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"


async def test_committed_schemas_match_the_live_server() -> None:
    """The one that matters: regenerate and compare.

    Fails with the exact command to fix it, because the failure is almost
    always "I changed a tool and forgot to re-export".
    """
    expected = await build_schemas()
    for relative, payload in sorted(expected.items()):
        path = SCHEMAS_DIR / relative
        assert path.is_file(), (
            f"schemas/{relative} is missing — run `uv run python scripts/export_schemas.py`"
        )
        assert path.read_text(encoding="utf-8") == _sorted_json(payload), (
            f"schemas/{relative} is out of date — run "
            "`uv run python scripts/export_schemas.py` and commit the result"
        )


async def test_no_stale_schema_files_are_committed() -> None:
    """A schema for a tool that no longer exists advertises a contract we dropped."""
    expected = {str(Path(relative)) for relative in (await build_schemas())}
    on_disk = {
        str(path.relative_to(SCHEMAS_DIR))
        for path in SCHEMAS_DIR.rglob("*.json")
    }
    assert on_disk == expected, (
        f"stale: {sorted(on_disk - expected)}; missing: {sorted(expected - on_disk)}"
    )


async def test_every_tool_has_an_input_and_output_schema() -> None:
    files = await build_schemas()
    tool_files = {k: v for k, v in files.items() if k.startswith("tools/")}
    assert tool_files, "no tool schemas were generated"
    for relative, payload in tool_files.items():
        assert payload["inputSchema"]["type"] == "object", relative
        # Every tool here returns a pydantic model or an MCP content type; a
        # missing outputSchema means a tool lost its return annotation.
        assert "outputSchema" in payload, f"{relative} has no outputSchema"
        assert (payload.get("description") or "").strip(), f"{relative} has no description"


async def test_index_lists_every_tool_and_prompt() -> None:
    files = await build_schemas()
    index = files["index.json"]
    tool_names = {k.removeprefix("tools/").removesuffix(".json") for k in files if k != "index.json"}

    assert {t["name"] for t in index["tools"]} == tool_names
    for entry in index["tools"]:
        assert entry["schema"] in files
        assert entry["summary"].strip()

    assert {p["name"] for p in index["prompts"]} == {
        "summarize-with-citations",
        "compare-documents",
        "expand-around-chunk",
        "answer-from-folder",
    }


async def test_index_reports_the_ks_mcp_version_not_the_mcp_library_version() -> None:
    from ks_mcp import __version__

    index = (await build_schemas())["index.json"]
    assert index["version"] == __version__
    assert index["server"] == "knowledgestack"


async def test_export_is_deterministic() -> None:
    """Two runs must be byte-identical, or every regeneration is a noisy diff.

    Note what this does *not* cover: both runs share one interpreter, so it
    cannot catch output that varies *between* Python versions. That gap let a
    3.13-generated `schemas/` through while CI ran 3.11/3.12 — see
    `test_tool_descriptions_are_dedented` for the guard that closes it.
    """
    first = _sorted_json(await build_schemas())
    second = _sorted_json(await build_schemas())
    assert first == second


async def test_tool_descriptions_are_dedented() -> None:
    """Descriptions must not carry docstring indentation on any interpreter.

    Python 3.13 strips common leading indentation from docstrings at compile
    time (gh-81283); 3.11 and 3.12 do not. Since tool descriptions come from
    docstrings, that made the exported schemas interpreter-dependent — the
    committed files matched 3.13 and CI failed on 3.12.

    `build_server` now runs every description through `inspect.cleandoc`. This
    asserts the result, so it fails on whichever version regresses rather than
    only on the one that generated the artifact.
    """
    from ks_mcp.server import build_server

    offenders: list[str] = []
    for tool in await build_server().list_tools():
        for lineno, line in enumerate((tool.description or "").splitlines(), 1):
            # A dedented docstring can still indent deliberately (code blocks,
            # nested bullets). What must never appear is the uniform 4+ space
            # body indent that an undedented docstring applies to every line.
            if line.startswith("    ") and line.strip() and not line.lstrip().startswith(("*", "-", "#")):
                offenders.append(f"{tool.name}:{lineno}: {line[:60]!r}")

    assert not offenders, (
        "tool descriptions carry docstring indentation — _normalize_tool_descriptions "
        f"is not running or no longer works on this interpreter: {offenders[:5]}"
    )


async def test_descriptions_have_no_trailing_whitespace() -> None:
    """`cleandoc` also strips the trailing newline; pin it so diffs stay quiet."""
    from ks_mcp.server import build_server

    for tool in await build_server().list_tools():
        description = tool.description or ""
        assert description == description.strip(), f"{tool.name} has edge whitespace"


async def test_library_owned_keys_are_excluded() -> None:
    """`icons`/`annotations`/`meta` come from `mcp` defaults, not from this repo.

    Including them would make an `mcp` upgrade rewrite every schema file
    without our contract having changed.
    """
    files = await build_schemas()
    for relative, payload in files.items():
        if not relative.startswith("tools/"):
            continue
        assert not {"icons", "annotations", "meta", "execution"} & set(payload)


@pytest.mark.parametrize("relative", ["index.json", "tools/ask.json"])
def test_committed_files_are_valid_utf8_json(relative: str) -> None:
    payload = json.loads((SCHEMAS_DIR / relative).read_text(encoding="utf-8"))
    assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_descriptions_are_written_unescaped() -> None:
    """`ensure_ascii=False` keeps the files human-reviewable."""
    raw = (SCHEMAS_DIR / "tools" / "get_workflow_status.json").read_text(encoding="utf-8")
    assert "\\u2014" not in raw
