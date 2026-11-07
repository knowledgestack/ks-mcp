#!/usr/bin/env python
"""Regenerate `schemas/` from the live tool surface.

Run after changing a tool signature or an output model:

    uv run python scripts/export_schemas.py

`tests/test_schema_export.py` fails if the committed files are out of date, so
CI catches a forgotten run.
"""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    from ks_mcp.schema_export import write_schemas

    dest = REPO_ROOT / "schemas"
    written = asyncio.run(write_schemas(dest))
    for path in written:
        print(path.relative_to(REPO_ROOT))
    print(f"\n{len(written)} file(s) written to {dest.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
