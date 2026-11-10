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


# ---------------------------------------------------------------------------
# release wiring
# ---------------------------------------------------------------------------


def _release_please_config() -> dict:
    return json.loads((REPO_ROOT / "release-please-config.json").read_text(encoding="utf-8"))


def _release_tag_glob() -> list[str]:
    """The tag patterns `release.yml` actually triggers on."""
    yaml = pytest.importorskip("yaml", reason="pyyaml is not installed")
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    )
    # PyYAML parses a bare `on:` key as the boolean True.
    triggers = workflow.get("on") or workflow.get(True)
    return list(triggers["push"]["tags"])


def test_release_please_tag_format_matches_the_publish_trigger() -> None:
    """The tag release-please creates must be one `release.yml` fires on.

    This broke exactly once and silently: with `include-component-in-tag` at
    its default (`true`), release-please tagged
    `knowledgestack-mcp-v0.3.0`, which does not match `v*`. The release PR
    merged, the GitHub release was created, and the publish workflow simply
    never ran — no error anywhere, nothing on PyPI.
    """
    import fnmatch

    config = _release_please_config()
    version = "9.9.9"

    prefix = ""
    if config.get("include-component-in-tag", True):
        component = config["packages"]["."]["package-name"]
        prefix = f"{component}{config.get('tag-separator', '-')}"
    tag = f"{prefix}{'v' if config.get('include-v-in-tag', True) else ''}{version}"

    patterns = _release_tag_glob()
    assert any(fnmatch.fnmatch(tag, pattern) for pattern in patterns), (
        f"release-please would tag {tag!r}, which matches none of release.yml's "
        f"trigger patterns {patterns}. The publish workflow would never run."
    )


def test_manifest_version_matches_pyproject(version: str) -> None:
    """A stale manifest makes release-please compute the next bump from the
    wrong baseline."""
    manifest = json.loads(
        (REPO_ROOT / ".release-please-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["."] == version, (
        f".release-please-manifest.json says {manifest['.']!r}, pyproject says {version!r}"
    )


def test_server_json_registry_type_is_pypi() -> None:
    """`mcp-publisher` resolves the package from PyPI, so this has to say so."""
    payload = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    assert payload["packages"][0]["registryType"] == "pypi"


# ---------------------------------------------------------------------------
# operator-facing contract
# ---------------------------------------------------------------------------


def _documented_env_vars() -> set[str]:
    """Every `KS_*` variable the code actually reads."""
    import re

    names: set[str] = set()
    for path in (REPO_ROOT / "src" / "ks_mcp").rglob("*.py"):
        names |= set(re.findall(r'os\.environ(?:\.get)?[(\[]"(KS_[A-Z_]+)"', path.read_text()))
        names |= set(re.findall(r'_env\(\s*"(KS_[A-Z_]+)"', path.read_text()))
        names |= set(re.findall(r'_env_flag\(\s*"(KS_[A-Z_]+)"', path.read_text()))
        names |= set(re.findall(r'_csv_env\(\s*"(KS_[A-Z_]+)"', path.read_text()))
    return names


def test_every_env_var_the_code_reads_is_documented() -> None:
    """A setting the code honours but the docs omit is a setting nobody uses.

    `KS_ALLOW_WRITE` and `KS_AUDIT_LOG` shipped undocumented, and nothing
    caught it — for a feature whose design is "the flag is the operator's
    consent", the docs *are* the feature.
    """
    readme = (REPO_ROOT / "README.md").read_text()
    undocumented = {name for name in _documented_env_vars() if f"`{name}`" not in readme}
    assert not undocumented, f"env vars read by the code but absent from README: {undocumented}"


def test_every_env_var_is_declared_in_the_registry_manifest() -> None:
    """`server.json`'s environmentVariables is what the MCP registry and client
    UIs read to offer configuration — an omission there is invisible to users."""
    payload = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))
    declared = {e["name"] for e in payload["packages"][0]["environmentVariables"]}
    missing = _documented_env_vars() - declared
    assert not missing, f"env vars read by the code but absent from server.json: {missing}"


def test_cli_flags_are_documented() -> None:
    """Same reasoning for flags: `--allow-write` shipped only in a module docstring."""
    readme = (REPO_ROOT / "README.md").read_text()
    server_src = (REPO_ROOT / "src" / "ks_mcp" / "server.py").read_text()

    import re

    flags = set(re.findall(r'add_argument\(\s*"(--[a-z-]+)"', server_src))
    flags -= {"--host", "--port", "--version"}  # unremarkable; not worth documenting
    # Substring, not backticked: a flag shown in a runnable code block is
    # documented. Requiring `--flag` inline would fail on `--check`, which the
    # README demonstrates three times in exactly the way a reader wants.
    undocumented = {f for f in flags if f not in readme}
    assert not undocumented, f"CLI flags absent from README: {undocumented}"
