"""Knowledge Stack MCP server — expose KS tools to any agent framework."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _metadata_version

try:
    # Read from installed package metadata rather than restating the literal.
    # `pyproject.toml` is the single source of truth, so this cannot drift from
    # the released version — which matters because `__version__` is what the
    # MCP `initialize` response, `/healthz` and `schemas/index.json` report.
    __version__ = _metadata_version("knowledgestack-mcp")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    # Importable without an install (e.g. `PYTHONPATH=src`). Deliberately not a
    # plausible-looking version: a wrong number is worse than an obvious one.
    __version__ = "0.0.0+unknown"
