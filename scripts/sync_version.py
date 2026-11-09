#!/usr/bin/env python
"""Propagate the version in `pyproject.toml` to every file that repeats it.

`pyproject.toml` `[project] version` is the single source of truth. Five other
places restate it, and until this script existed they drifted independently:

- `server.json` `$.version` and `$.packages[0].version` — the MCP registry
  manifest. `mcp-publisher publish` reads these, so a stale value publishes the
  wrong version to the registry (or fails against a PyPI release that doesn't
  exist yet).
- `schemas/index.json` `$.version` — regenerated here via the schema exporter.
- `uv.lock` — records the project's own version; a stale one fails
  `uv lock --check`.
- `README.md` — the sample `--check` output.

`src/ks_mcp/__init__.py` is deliberately *not* in that list: `__version__` is
read from installed package metadata, so it cannot drift.

Usage:

    uv run python scripts/sync_version.py            # fix everything
    uv run python scripts/sync_version.py --check     # report drift, exit 1

`tests/test_version.py` runs the same comparison, so CI fails with the fix
command rather than shipping a mismatched manifest.
"""

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def read_project_version() -> str:
    """The one authoritative version string."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _server_json_versions(text: str) -> list[str]:
    payload = json.loads(text)
    found = [str(payload.get("version", ""))]
    found += [str(p.get("version", "")) for p in payload.get("packages", [])]
    return found


def _sync_server_json(version: str, *, write: bool) -> list[str]:
    """Update both version fields in `server.json`, preserving formatting.

    Edited as text rather than re-serialized so the diff stays reviewable and
    key order survives — this file is a published manifest, not a scratch file.
    """
    path = REPO_ROOT / "server.json"
    text = path.read_text(encoding="utf-8")
    problems = [
        f"server.json has version {v!r}, expected {version!r}"
        for v in _server_json_versions(text)
        if v != version
    ]
    if problems and write:
        # Only `"version": "..."` lines; nothing else in this file matches.
        updated = re.sub(r'("version":\s*)"[^"]*"', rf'\g<1>"{version}"', text)
        json.loads(updated)  # refuse to write a file we just broke
        path.write_text(updated, encoding="utf-8")
    return problems


def _uv_lock_version(text: str) -> str | None:
    """The project's own version as recorded in `uv.lock`."""
    match = re.search(
        r'^name = "knowledgestack-mcp"\nversion = "([^"]+)"', text, flags=re.MULTILINE
    )
    return match.group(1) if match else None


def _sync_uv_lock(version: str, *, write: bool) -> list[str]:
    path = REPO_ROOT / "uv.lock"
    recorded = _uv_lock_version(path.read_text(encoding="utf-8"))
    if recorded == version:
        return []
    problems = [f"uv.lock records version {recorded!r}, expected {version!r}"]
    if write:
        # `uv lock` rather than a regex: it is the only thing that keeps the
        # rest of the lockfile internally consistent.
        subprocess.run(["uv", "lock"], cwd=REPO_ROOT, check=True)
    return problems


_README_SAMPLE = re.compile(r"^ks-mcp \d+\.\d+\.\d+.*$", flags=re.MULTILINE)


def _sync_readme(version: str, *, write: bool) -> list[str]:
    path = REPO_ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    stale = [m for m in _README_SAMPLE.findall(text) if m != f"ks-mcp {version}"]
    if stale and write:
        text = _README_SAMPLE.sub(f"ks-mcp {version}", text)
        path.write_text(text, encoding="utf-8")
    return [f"README.md shows {s!r}, expected 'ks-mcp {version}'" for s in stale]


def _sync_schemas(version: str, *, write: bool) -> list[str]:
    """`schemas/index.json` carries the version; regenerate rather than patch."""
    path = REPO_ROOT / "schemas" / "index.json"
    if not path.is_file():
        return []
    recorded = str(json.loads(path.read_text(encoding="utf-8")).get("version", ""))
    if recorded == version:
        return []
    problems = [f"schemas/index.json records version {recorded!r}, expected {version!r}"]
    if write:
        # The exporter reads `ks_mcp.__version__`, which comes from installed
        # metadata — so a bare `pyproject.toml` bump isn't visible until the
        # package is reinstalled.
        subprocess.run(["uv", "sync", "--quiet", "--extra", "dev"], cwd=REPO_ROOT, check=True)
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "export_schemas.py")],
            cwd=REPO_ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    return problems


def collect_problems(version: str) -> list[str]:
    """Every place the version is restated and currently disagrees."""
    problems: list[str] = []
    for sync in (_sync_server_json, _sync_uv_lock, _sync_readme, _sync_schemas):
        problems += sync(version, write=False)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift and exit non-zero instead of fixing it.",
    )
    args = parser.parse_args()

    version = read_project_version()
    problems = collect_problems(version)

    if not problems:
        print(f"version {version}: all files in sync")
        return 0

    if args.check:
        print(f"version {version}: {len(problems)} file(s) out of sync", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("\nRun: uv run python scripts/sync_version.py", file=sys.stderr)
        return 1

    for problem in problems:
        print(f"fixing: {problem}")
    for sync in (_sync_server_json, _sync_uv_lock, _sync_readme, _sync_schemas):
        sync(version, write=True)

    remaining = collect_problems(version)
    if remaining:
        print("\nstill out of sync after syncing:", file=sys.stderr)
        for problem in remaining:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"\nversion {version}: synced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
