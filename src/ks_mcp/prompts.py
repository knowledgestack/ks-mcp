"""Server-side MCP prompts: best-practice tool sequencing, shipped as data.

A capable model will work out `search → read_around → cite` on its own. A
small one usually won't: it answers from the first search snippet, skips
`cite`, and produces prose with no chunk tags. These prompts hard-code the
sequence so that behaviour comes from the server rather than from the model's
own planning ability.

Everything here is text. No prompt calls the KS API — rendering one is free
and consumes no tenant quota; it just returns the instructions the client then
executes with the tools.

Each prompt sets ``name=`` explicitly: FastMCP would otherwise derive it from
the Python function name, giving ``summarize_with_citations``. The hyphenated
names are the documented public identifiers, so they can't drift when a
function is renamed.
"""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import base
from pydantic import Field

# Repeated verbatim in every prompt that produces prose. Citation discipline is
# the thing small models drop first, and restating the rule next to the step
# that needs it works better than stating it once at the top.
_CITATION_RULE = (
    "Citation rules:\n"
    "- Call `cite(chunk_id)` once per chunk you actually used, and append the "
    "`[chunk:UUID]` tag it returns to the specific sentence that chunk supports.\n"
    "- One tag per claim, placed at the end of that sentence — not grouped in a "
    "footer, not on the paragraph.\n"
    "- Never write a `[chunk:...]` tag you did not get back from `cite`.\n"
    "- If the retrieved chunks do not support a claim, leave the claim out. Say "
    "what the knowledge base does not cover rather than filling the gap from "
    "your own knowledge."
)

_SCOPE_HINT = (
    "If you don't know the folder's `path_part_id`, call `list_contents` (no "
    "arguments lists the tenant root) or `find` first — don't guess a UUID."
)


def register(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="summarize-with-citations",
        title="Summarize with citations",
        description=(
            "Summarize a document or folder subtree into Markdown where every claim "
            "carries a [chunk:UUID] citation tag."
        ),
    )
    def summarize_with_citations(
        subject: Annotated[
            str,
            Field(description="What to summarize — a document name, topic, or question."),
        ],
        path_part_id: Annotated[
            str | None,
            Field(description="Optional path_part_id to restrict retrieval to a subtree."),
        ] = None,
        max_words: Annotated[
            str, Field(description="Approximate length budget for the summary.")
        ] = "400",
    ) -> list[base.Message]:
        scope = (
            f"Restrict every search to `parent_path_part_ids=[{path_part_id}]`."
            if path_part_id
            else f"Search the whole knowledge base. {_SCOPE_HINT}"
        )
        return [
            base.UserMessage(
                f"Summarize what the knowledge base says about: {subject}\n\n"
                f"{scope}\n\n"
                "Follow these steps in order:\n"
                "1. `search_hybrid(query=..., top_k=10)` — it fuses semantic and "
                "keyword matching, so it's the right default here. Run two or three "
                "differently-worded queries rather than one; a single phrasing "
                "reliably misses a section.\n"
                "2. For each hit that looks load-bearing, `read_around(chunk_id)` to "
                "pick up the surrounding context. A chunk read in isolation is the "
                "main source of confident, wrong summaries.\n"
                "3. Write the summary in Markdown, at most about "
                f"{max_words} words, using `##` sections that follow the source's own "
                "structure rather than a template.\n"
                f"4. {_CITATION_RULE}\n\n"
                "Close with a one-line note on coverage: which parts of the subject "
                "the retrieved material did and did not address."
            )
        ]

    @mcp.prompt(
        name="compare-documents",
        title="Compare documents",
        description=(
            "Compare two documents (or two versions of one) and report real "
            "differences with citations on both sides."
        ),
    )
    def compare_documents(
        left: Annotated[str, Field(description="First document — name, or a path_part_id.")],
        right: Annotated[str, Field(description="Second document — name, or a path_part_id.")],
        focus: Annotated[
            str | None,
            Field(description="Optional aspect to compare, e.g. 'termination clauses'."),
        ] = None,
    ) -> list[base.Message]:
        aspect = f"Focus the comparison on: {focus}." if focus else ""
        return [
            base.UserMessage(
                f"Compare these two documents and report how they differ.\n\n"
                f"- Left:  {left}\n"
                f"- Right: {right}\n"
                f"{aspect}\n\n"
                "First, decide which kind of comparison this is — it changes the tool:\n"
                "- **Two versions of the same document** → call "
                "`compare_versions(to_version_id=..., from_version_id=...)`. It diffs "
                "server-side and is both cheaper and more exact than retrieving and "
                "eyeballing the text. Read `diff_format` and `unit` before describing "
                "the counts: a spreadsheet reports changed cells, not lines. Check "
                "`truncated` and `degraded` before stating any total as final.\n"
                "- **Two different documents** → resolve each with `find`, then run "
                "`search_hybrid` *scoped to each one separately* via "
                "`parent_path_part_ids`. Searching both at once makes it impossible to "
                "tell which document a hit came from.\n\n"
                "Then write the comparison as Markdown:\n"
                "- A short paragraph on what the two have in common.\n"
                "- A table or bulleted list of differences, each row naming both sides.\n"
                "- Cite **both** sides of every difference. A difference with one "
                "citation is an assertion about the uncited side.\n\n"
                f"{_CITATION_RULE}\n\n"
                "Call out explicitly anything present in one document and absent from "
                "the other — an omission is a difference, and it's the one most often "
                "missed."
            )
        ]

    @mcp.prompt(
        name="expand-around-chunk",
        title="Expand around a chunk",
        description=(
            "Take a chunk you already have and build out its full surrounding context "
            "and provenance."
        ),
    )
    def expand_around_chunk(
        chunk_id: Annotated[str, Field(description="The chunk_id to expand around.")],
        question: Annotated[
            str | None,
            Field(description="Optional question the expanded context should answer."),
        ] = None,
    ) -> list[base.Message]:
        goal = (
            f"Use the expanded context to answer: {question}"
            if question
            else "Report what this passage actually says in context."
        )
        return [
            base.UserMessage(
                f"Expand the context around chunk `{chunk_id}`.\n\n"
                "Steps:\n"
                f"1. `read_around(chunk_id='{chunk_id}')` for the neighbouring chunks.\n"
                "2. `get_info` on the chunk's `path_part_id` to see where it sits in the "
                "document — the section and document names usually change how the "
                "passage should be read.\n"
                "3. If the chunk's type is IMAGE, also call "
                f"`view_chunk_image(chunk_id='{chunk_id}')`; the text alone will be a "
                "caption or OCR fragment.\n"
                "4. If it matters whether this text is current, "
                f"`trace_chunk_lineage(chunk_id='{chunk_id}')` shows whether it survived "
                "from an earlier version or appeared recently.\n\n"
                f"{goal}\n\n"
                "Quote the passage itself rather than paraphrasing it, and say plainly "
                "if the surrounding context changes or contradicts what the chunk looks "
                "like on its own.\n\n"
                f"{_CITATION_RULE}"
            )
        ]

    @mcp.prompt(
        name="answer-from-folder",
        title="Answer from a folder",
        description=(
            "Answer a question using only one folder subtree, refusing to answer from "
            "outside it."
        ),
    )
    def answer_from_folder(
        question: Annotated[str, Field(description="The question to answer.")],
        path_part_id: Annotated[
            str, Field(description="path_part_id of the folder to answer from.")
        ],
    ) -> list[base.Message]:
        return [
            base.UserMessage(
                f"Answer this question using **only** material from the folder "
                f"`{path_part_id}`:\n\n{question}\n\n"
                "Steps:\n"
                f"1. `search_hybrid(query=..., parent_path_part_ids=['{path_part_id}'], "
                "top_k=10)`. The `parent_path_part_ids` filter is what enforces the "
                "scope — every search must carry it.\n"
                "2. `read_around` on the hits you intend to rely on.\n"
                "3. Answer from those chunks and nothing else.\n\n"
                "Scope is the hard constraint here. If the folder's contents don't "
                "answer the question, say so and stop — do not widen the search, and do "
                "not fall back on your own knowledge. A confident answer sourced from "
                "outside this folder is the specific failure this prompt exists to "
                "prevent.\n\n"
                f"{_CITATION_RULE}"
            )
        ]
