"""Guards on the installed-package contract.

`uv.lock` only governs this repo's dev environment. `pip install` and `uvx`
resolve dependencies fresh from `[project.dependencies]`, so a missing upper
bound or a missing console script breaks every end user while the whole test
suite stays green locally. These checks read `pyproject.toml` directly.
"""

import tomllib
from pathlib import Path
from typing import Any

import pytest

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


@pytest.fixture(scope="module")
def pyproject() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text())


def _requirement(pyproject: dict[str, Any], name: str) -> str:
    for spec in pyproject["project"]["dependencies"]:
        if spec.split(">")[0].split("<")[0].split("=")[0].strip() == name:
            return spec
    raise AssertionError(f"{name} is not a declared dependency")


def test_mcp_is_capped_below_2(pyproject: dict[str, Any]) -> None:
    """mcp 2.x removed `mcp.server.fastmcp` (FastMCP -> MCPServer).

    Without the cap, a fresh `uvx knowledgestack-mcp` resolves mcp 2.x and dies
    with ModuleNotFoundError before serving a single tool.
    """
    assert "<2" in _requirement(pyproject, "mcp")


@pytest.mark.parametrize(
    ("package", "cap"),
    [("mcp", "<2"), ("ksapi", "<4"), ("pydantic", "<3"), ("httpx", "<1")],
)
def test_runtime_deps_cap_the_next_major(
    package: str, cap: str, pyproject: dict[str, Any]
) -> None:
    """Every runtime dep needs an upper bound; ksapi 3 already broke us once."""
    assert cap in _requirement(pyproject, package)


def test_console_script_matches_distribution_name(pyproject: dict[str, Any]) -> None:
    """`uvx <pkg>` resolves the executable by package name.

    The README, server.json's runtimeHint and every client config in the docs
    use `uvx knowledgestack-mcp`, which fails unless a script of that exact
    name exists.
    """
    scripts = pyproject["project"]["scripts"]
    distribution = pyproject["project"]["name"]
    assert distribution in scripts, f"no console script named {distribution!r}"
    assert scripts[distribution] == "ks_mcp.server:main"


def test_fastmcp_import_path_still_exists() -> None:
    """The exact import the mcp<2 cap exists to protect."""
    from mcp.server.fastmcp import FastMCP

    assert FastMCP is not None
