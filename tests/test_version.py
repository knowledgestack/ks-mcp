"""Guard that every file restating the version agrees with `pyproject.toml`.

The version is repeated in five places outside `pyproject.toml`, and two of
them are load-bearing at release time:

- `server.json` is the MCP registry manifest `mcp-publisher publish` reads. A
  stale value publishes the wrong version to the registry, or points at a PyPI
  release that doesn't exist.
- `uv.lock` records the project's own version; a stale one fails
  `uv lock --check`.

Nothing in the normal test suite touches those files, so without this test a
version bump that forgets them stays green locally and breaks on the tag.
"""

import json
import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIX = "run `uv run python scripts/sync_version.py`"


@pytest.fixture(scope="module")
def version() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def test_version_is_pep440_normalized(version: str) -> None:
    """Tags are `v<version>`, so an odd version string breaks the release flow."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"expected a plain X.Y.Z version, got {version!r}"
    )


def test_dunder_version_is_derived_not_restated(version: str) -> None:
    """`__version__` must come from package metadata, not a second literal.

    If someone reintroduces a hardcoded string here, this fails on the next
    bump — which is the whole point of deriving it.
    """
    from ks_mcp import __version__

    assert __version__ == version, (
        f"ks_mcp.__version__ is {__version__!r} but pyproject says {version!r}. "
        "If you just bumped the version, re-install the package (`uv sync`) — "
        "__version__ reads installed metadata."
    )

    source = (REPO_ROOT / "src" / "ks_mcp" / "__init__.py").read_text(encoding="utf-8")
    assert f'"{version}"' not in source, (
        "__init__.py restates the version literal; it should read it from "
        "importlib.metadata so it cannot drift"
    )


def test_server_json_versions_match(version: str) -> None:
    """Both fields — `mcp-publisher` reads this manifest at release time."""
    payload = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))

    assert payload["version"] == version, f"server.json $.version is stale — {FIX}"
    assert payload["packages"], "server.json declares no packages"
    for index, package in enumerate(payload["packages"]):
        assert package["version"] == version, (
            f"server.json $.packages[{index}].version is stale — {FIX}"
        )


def test_server_json_package_identifier_matches_distribution(version: str) -> None:
    """A right version on the wrong package name still publishes nothing useful."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    payload = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    assert payload["packages"][0]["identifier"] == data["project"]["name"]


def test_uv_lock_records_the_current_version(version: str) -> None:
    text = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    match = re.search(
        r'^name = "knowledgestack-mcp"\nversion = "([^"]+)"', text, flags=re.MULTILINE
    )
    assert match, "could not find the project's own entry in uv.lock"
    assert match.group(1) == version, f"uv.lock is stale — {FIX}"


def test_published_schema_index_records_the_current_version(version: str) -> None:
    payload = json.loads((REPO_ROOT / "schemas" / "index.json").read_text(encoding="utf-8"))
    assert payload["version"] == version, f"schemas/index.json is stale — {FIX}"


def test_readme_sample_output_is_current(version: str) -> None:
    """Cosmetic, but it's the first version a reader sees."""
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    stale = [m for m in re.findall(r"^ks-mcp \d+\.\d+\.\d+.*$", text, flags=re.MULTILINE)
             if m != f"ks-mcp {version}"]
    assert not stale, f"README shows {stale} — {FIX}"


def test_sync_script_reports_no_drift() -> None:
    """Run the script's own checker, so the test and the script can't disagree.

    Loaded by path rather than imported: `scripts/` is not a package, and
    putting it on `sys.path` would make this depend on import order.
    """
    import importlib.util

    script = REPO_ROOT / "scripts" / "sync_version.py"
    spec = importlib.util.spec_from_file_location("sync_version", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    problems = module.collect_problems(module.read_project_version())
    assert not problems, f"{problems} — {FIX}"
