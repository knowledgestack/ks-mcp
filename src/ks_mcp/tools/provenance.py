"""Phase-2 provenance tools: chunk lineage and version comparison.

These tools expose the KS backend's lineage and document-version surfaces to
agents so they can reason about *where* a chunk came from and *how* a document
changed between two versions — not just retrieve text.
"""

from typing import Annotated
from uuid import UUID

import ksapi
from ksapi.api.chunk_lineages_api import ChunkLineagesApi
from ksapi.api.document_versions_api import DocumentVersionsApi
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from ks_mcp.client import get_api_client
from ks_mcp.errors import rest_to_mcp

# How many rendered diff rows / changes we put in the summary string. The
# backend already truncates its own payload (and tells us via `truncated`);
# this is a second ceiling so one enormous version pair can't blow the
# agent's context window.
_MAX_RENDERED_ROWS = 400
# Per-side character cap on a single rendered row.
_MAX_ROW_CHARS = 400


class LineageEdge(BaseModel):
    """One parent → child derivation step in a chunk's lineage graph."""

    chunk_id: UUID = Field(..., description="The derived (child) chunk.")
    parent_chunk_id: UUID | None = Field(
        default=None, description="The chunk it was derived from. None marks a root / first ingest."
    )


class LineageNode(BaseModel):
    """A chunk that participates in the lineage graph, with a text preview."""

    chunk_id: UUID
    chunk_type: str | None = None
    created_at: str | None = None
    preview: str = Field(default="", description="First ~160 chars of the chunk body.")


class LineageResult(BaseModel):
    chunk_id: UUID
    edges: list[LineageEdge]
    nodes: list[LineageNode]
    summary: str


class VersionDiffResult(BaseModel):
    """Server-computed diff between two versions of one document.

    The counts are whatever the backend's differ reports for the document's
    native shape, so they are *not* always line counts: a spreadsheet diff
    counts cells, a structured (JSON/CSV-ish) diff counts field paths, and a
    Word-style document diff counts blocks. ``unit`` names what was counted so
    an agent doesn't have to guess.
    """

    document_id: UUID | None = Field(
        default=None,
        description=(
            "Echo of the ``document_id`` you passed, if any. The diff endpoint is "
            "version-scoped and resolves the document itself, so this is a convenience "
            "echo rather than something the server returned."
        ),
    )
    from_version_id: UUID | None = Field(
        default=None,
        description=(
            "Older / left version actually diffed. Null when ``to_version_id`` is the "
            "document's first version (it is then diffed against empty)."
        ),
    )
    to_version_id: UUID = Field(..., description="Newer / right version that was diffed.")
    from_version: int | None = Field(
        default=None, description="Human-facing version number of the left side."
    )
    to_version: int | None = Field(
        default=None, description="Human-facing version number of the right side."
    )
    diff_format: str = Field(
        ...,
        description=(
            "Which differ ran, chosen by the backend from the document's type: "
            "`text` | `cells` (spreadsheets) | `structured` (JSON/CSV-ish) | "
            "`document` (Word-style block diff)."
        ),
    )
    unit: str = Field(
        ...,
        description="What ``added``/``removed``/``modified`` count: lines, cells, fields or blocks.",
    )
    added: int = Field(..., description="Units present only on the right side.")
    removed: int = Field(..., description="Units present only on the left side.")
    modified: int = Field(..., description="Units present on both sides with different content.")
    truncated: bool = Field(
        default=False,
        description=(
            "True when the backend capped the diff payload. Counts cover what was "
            "returned, not the whole document — do not state totals as final."
        ),
    )
    degraded: bool = Field(
        default=False,
        description=(
            "True when the backend could not run its preferred differ and fell back "
            "(e.g. undecodable source). Read ``degraded_reason`` before trusting the diff."
        ),
    )
    degraded_reason: str | None = Field(
        default=None, description="Why the diff was degraded, when it was."
    )
    rendered_diff: str = Field(
        ...,
        description=(
            "Human-readable rendering of the changes: a unified diff for `text`, "
            "otherwise one line per change. Capped in length; see ``truncated``."
        ),
    )
    summary: str = Field(..., description="One-line plain-English summary of the comparison.")


def _clip(value: str | None) -> str:
    """Trim one rendered side to ``_MAX_ROW_CHARS``, flattening newlines."""
    text = (value or "").replace("\r\n", " ").replace("\n", " ").strip()
    return text if len(text) <= _MAX_ROW_CHARS else f"{text[:_MAX_ROW_CHARS]}…"


def _render_text(diff: ksapi.TextDiff, left_label: str, right_label: str) -> list[str]:
    """Render ``TextDiff.rows`` as a unified diff.

    The backend returns side-by-side rows (``equal`` / ``insert`` / ``delete`` /
    ``replace``); unified diff is what models read most reliably, so we project
    onto it rather than inventing a new notation.
    """
    # Bare `---`/`+++` headers with nothing under them read as a diff that was
    # computed and came back empty-handed; return nothing so the caller can say
    # "no differences" instead.
    if not diff.rows:
        return []
    lines = [f"--- {left_label}", f"+++ {right_label}"]
    for row in diff.rows[:_MAX_RENDERED_ROWS]:
        kind = getattr(row.type, "value", str(row.type))
        if kind == "equal":
            lines.append(f" {_clip(row.left)}")
        elif kind == "insert":
            lines.append(f"+{_clip(row.right)}")
        elif kind == "delete":
            lines.append(f"-{_clip(row.left)}")
        else:  # replace — emit both sides so the model can see the edit
            lines.append(f"-{_clip(row.left)}")
            lines.append(f"+{_clip(row.right)}")
    return lines


def _render_cells(diff: ksapi.CellDiff) -> list[str]:
    lines: list[str] = []
    for change in diff.changes[:_MAX_RENDERED_ROWS]:
        kind = getattr(change.type, "value", str(change.type))
        line = (
            f"[{kind}] {change.sheet}!{change.address}: {_clip(change.old)} -> {_clip(change.new)}"
        )
        # A formula edit that leaves the cached value untouched is invisible in
        # old/new, so call it out rather than reporting the cell as unchanged.
        if change.formula_changed:
            line += f" (formula {_clip(change.old_formula)} -> {_clip(change.new_formula)})"
        lines.append(line)
    for visibility in diff.sheet_visibility_changes[:_MAX_RENDERED_ROWS]:
        lines.append(
            f"[sheet] {visibility.sheet}: {visibility.old_state} -> {visibility.new_state}"
        )
    return lines


def _render_structured(diff: ksapi.StructuredDiff) -> list[str]:
    return [
        f"[{getattr(c.type, 'value', str(c.type))}] {c.path}: {_clip(c.old)} -> {_clip(c.new)}"
        for c in diff.changes[:_MAX_RENDERED_ROWS]
    ]


def _render_document(diff: ksapi.DocumentDiff) -> list[str]:
    lines: list[str] = []
    for block in diff.blocks[:_MAX_RENDERED_ROWS]:
        kind = getattr(block.type, "value", str(block.type))
        block_kind = getattr(block.kind, "value", str(block.kind))
        change_class = getattr(block.change_class, "value", str(block.change_class))
        where = (
            block.to_paragraph_index
            if block.to_paragraph_index is not None
            else block.from_paragraph_index
        )
        line = f"[{kind}/{block_kind}/{change_class}] ¶{where}: {_clip(block.old_text)} -> {_clip(block.new_text)}"
        # A numeric delta is the single most useful thing in a contract diff.
        if block.numeric_delta:
            line += f" (Δ {block.numeric_delta}"
            line += f", {block.numeric_pct}%)" if block.numeric_pct else ")"
        lines.append(line)
    return lines


# Per-format projection onto the flat (unit, added, removed, modified) shape.
# `text` has no "modified" of its own — a replace row is one removal plus one
# addition on the wire — so `changed` is what maps onto `modified`.
def _project(response: ksapi.VersionDiffResponse) -> tuple[str, int, int, int, bool, list[str]]:
    """Return (unit, added, removed, modified, truncated, rendered_lines)."""
    fmt = getattr(response.format, "value", str(response.format))
    left = f"v{response.from_version}" if response.from_version is not None else "(empty)"
    right = f"v{response.to_version}" if response.to_version is not None else "(new)"

    if fmt == "text" and response.text is not None:
        d = response.text
        return "lines", d.added, d.removed, d.changed, d.truncated, _render_text(d, left, right)
    if fmt == "cells" and response.cells is not None:
        d = response.cells
        return "cells", d.added, d.removed, d.modified, d.truncated, _render_cells(d)
    if fmt == "structured" and response.structured is not None:
        d = response.structured
        return "fields", d.added, d.removed, d.modified, d.truncated, _render_structured(d)
    if fmt == "document" and response.document is not None:
        d = response.document
        return "blocks", d.added, d.removed, d.modified, d.truncated, _render_document(d)
    # The backend declared a format but sent no payload for it. Report the gap
    # instead of claiming the versions are identical.
    return "unknown", 0, 0, 0, False, []


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def trace_chunk_lineage(
        chunk_id: Annotated[
            UUID, Field(description="The chunk whose lineage you want to inspect.")
        ],
        max_depth: Annotated[
            int, Field(description="How many derivation hops to walk (1-10).", ge=1, le=10)
        ] = 5,
    ) -> LineageResult:
        """Return the lineage graph for a chunk (merge/split/re-embed history).

        KS tracks how chunks are derived (merge / split / re-embed /
        re-ingest) so that agents can explain *why* a piece of evidence
        exists. Use this when a downstream answer cites a chunk and you need
        to justify provenance — for example to answer "did this fact survive
        from v3, or did it appear in v4?".

        ``edges`` are parent → child derivation steps; an edge whose
        ``parent_chunk_id`` is null marks an original ingest. ``nodes`` carries
        a short preview of each chunk in the graph so you can see what changed
        without a second round-trip.
        """
        api = ChunkLineagesApi(get_api_client())
        try:
            lineage = await api.get_chunk_lineage(chunk_id=chunk_id, max_depth=max_depth)
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

        edges = [
            LineageEdge(chunk_id=e.chunk_id, parent_chunk_id=e.parent_chunk_id)
            for e in lineage.edges or []
        ]
        nodes = [
            LineageNode(
                chunk_id=n.id,
                chunk_type=getattr(n.chunk_type, "value", None) or str(n.chunk_type or "") or None,
                created_at=str(n.created_at) if n.created_at else None,
                preview=((n.content or "").strip()[:160]),
            )
            for n in lineage.nodes or []
        ]
        summary = (
            f"{len(edges)} lineage edge(s) across {len(nodes)} chunk(s) for {chunk_id}."
            if edges
            else f"No lineage recorded for chunk {chunk_id} (original ingest?)."
        )
        return LineageResult(chunk_id=chunk_id, edges=edges, nodes=nodes, summary=summary)

    @mcp.tool()
    async def compare_versions(
        to_version_id: Annotated[
            UUID, Field(description="Newer / target version id (the right side of the diff).")
        ],
        from_version_id: Annotated[
            UUID | None,
            Field(
                description=(
                    "Older / baseline version id (the left side). Omit to diff against "
                    "the immediate predecessor, which is what 'what changed in the "
                    "latest version?' usually means."
                )
            ),
        ] = None,
        document_id: Annotated[
            UUID | None,
            Field(
                description=(
                    "Optional: the owning document, echoed back for your own "
                    "bookkeeping. Not needed to compute the diff — the server resolves "
                    "the document from the version id."
                )
            ),
        ] = None,
    ) -> VersionDiffResult:
        """Diff two versions of the same document, computed server-side.

        Answers "what changed in v5 vs v4?" without pulling either version's
        chunks into this process: the backend diffs on the fly and returns only
        the changes, so a 900-page contract costs one small response instead of
        two full document loads.

        The backend picks the differ from the document's type, so read
        ``diff_format`` and ``unit`` before interpreting the counts — a
        spreadsheet reports changed *cells*, a Word-style document reports
        changed *blocks*, and plain text reports *lines*. ``rendered_diff`` is a
        unified diff for text and a one-line-per-change list otherwise.

        Check ``truncated`` and ``degraded`` before summarizing: a truncated
        diff's counts describe only what was returned, and a degraded one means
        the backend fell back to a weaker differ. For per-chunk provenance
        ("where did this sentence come from?") use ``trace_chunk_lineage``.

        Note: viewing a diff is a privileged read, so the backend records a
        ``document.diff.viewed`` audit event — much like downloading the file.
        """
        api = DocumentVersionsApi(get_api_client())
        try:
            response = await api.get_document_version_diff(
                version_id=to_version_id,
                from_version_id=from_version_id,
            )
        except ksapi.ApiException as exc:
            raise rest_to_mcp(exc) from exc

        fmt = getattr(response.format, "value", str(response.format))
        unit, added, removed, modified, truncated, lines = _project(response)

        if unit == "unknown":
            rendered = f"(no {fmt} diff payload returned)"
            summary = (
                f"The backend reported format {fmt!r} but returned no diff payload; "
                "treat the comparison as unavailable rather than empty."
            )
        elif not lines:
            rendered = "(no differences)"
            summary = "No differences between the two versions."
        else:
            rendered = "\n".join(lines)
            summary = f"{added} added, {removed} removed, {modified} modified {unit} ({fmt} diff)."
            if truncated:
                summary += " Diff was truncated by the backend — counts are partial."
            if response.degraded:
                reason = response.degraded_reason or "unspecified"
                summary += f" Diff is degraded ({reason})."

        return VersionDiffResult(
            document_id=document_id,
            from_version_id=response.from_version_id,
            to_version_id=response.to_version_id,
            from_version=response.from_version,
            to_version=response.to_version,
            diff_format=fmt,
            unit=unit,
            added=added,
            removed=removed,
            modified=modified,
            truncated=truncated,
            degraded=bool(response.degraded),
            degraded_reason=response.degraded_reason,
            rendered_diff=rendered,
            summary=summary,
        )
