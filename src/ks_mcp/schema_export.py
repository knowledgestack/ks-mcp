"""Export the tool and prompt surface as JSON Schema files under ``schemas/``.

The point is a reviewable artifact: a tool's input or output schema is part of
this server's public contract, but it is *derived* from Python signatures and
pydantic models, so a rename or a dropped field changes the contract silently.
Committing the generated schemas turns that into a visible diff, and
``tests/test_schema_export.py`` fails when the committed files drift from what
the server actually serves.

Regenerate after any tool signature or output-model change:

    uv run python scripts/export_schemas.py

Schemas are written one file per tool so a consumer can fetch exactly the one
it cares about, plus an ``index.json`` manifest for discovery.
"""

import json
from pathlib import Path
from typing import Any

from ks_mcp import __version__

# Keys we deliberately drop: FastMCP fills these from library defaults rather
# than from anything this repo declares, so including them would make the
# committed schemas churn on an `mcp` upgrade without the contract changing.
_DROPPED_TOOL_KEYS = frozenset({"icons", "annotations", "meta", "execution"})

_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _sorted_json(payload: Any) -> str:
    """Serialize deterministically — stable key order, trailing newline, UTF-8.

    ``ensure_ascii=False`` keeps the em-dashes in our descriptions readable in
    the committed files instead of exploding into ``\\u2014`` escapes.
    """
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


async def build_schemas() -> dict[str, Any]:
    """Return ``{relative_path: payload}`` for every file under ``schemas/``."""
    # Imported here: `build_server` reads no env at import time, but keeping the
    # dependency local means `scripts/` never needs the package pre-configured.
    from ks_mcp.server import build_server

    mcp = build_server()
    tools = sorted(await mcp.list_tools(), key=lambda t: t.name)
    prompts = sorted(await mcp.list_prompts(), key=lambda p: p.name)

    files: dict[str, Any] = {}
    for tool in tools:
        payload = tool.model_dump(exclude_none=True, by_alias=True)
        for key in _DROPPED_TOOL_KEYS:
            payload.pop(key, None)
        files[f"tools/{tool.name}.json"] = {"$schema": _SCHEMA_DIALECT, **payload}

    files["index.json"] = {
        "$schema": _SCHEMA_DIALECT,
        "server": mcp.name,
        # Intentionally the ks-mcp version, not the `mcp` library version: this
        # manifest describes our contract, and consumers pin against ours.
        "version": __version__,
        "tools": [
            {"name": t.name, "schema": f"tools/{t.name}.json", "summary": _first_line(t.description)}
            for t in tools
        ],
        "prompts": [
            {
                "name": p.name,
                "description": p.description or "",
                "arguments": [
                    {"name": a.name, "required": bool(a.required)} for a in (p.arguments or [])
                ],
            }
            for p in prompts
        ],
    }
    return files


def _first_line(description: str | None) -> str:
    """First sentence of a docstring — enough to scan the manifest by."""
    return (description or "").strip().split("\n", 1)[0]


async def write_schemas(dest: Path) -> list[Path]:
    """Write every schema file under ``dest``, returning the paths written."""
    files = await build_schemas()
    written: list[Path] = []
    for relative, payload in sorted(files.items()):
        path = dest / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_sorted_json(payload), encoding="utf-8")
        written.append(path)

    # Drop schemas for tools that no longer exist, or `schemas/` keeps serving a
    # contract the server stopped honouring.
    expected = {dest / relative for relative in files}
    for stale in sorted((dest / "tools").glob("*.json")):
        if stale not in expected:
            stale.unlink()
    return written
