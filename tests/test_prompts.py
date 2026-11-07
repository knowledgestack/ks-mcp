"""Tests for the server-side MCP prompt library (issue #5).

Prompts are text, so there's no API behaviour to assert. What *is* worth
guarding is the contract clients depend on: the public names, the declared
arguments, and the fact that the rendered body actually names the tools and
the citation rule it exists to enforce. A prompt that silently stops saying
"call `cite`" is the whole feature regressing with every test still green.
"""

from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

EXPECTED_PROMPTS = {
    "summarize-with-citations",
    "compare-documents",
    "expand-around-chunk",
    "answer-from-folder",
}

FOLDER_ID = "00000000-0000-0000-0000-0000000000ff"
CHUNK_ID = "00000000-0000-0000-0000-0000000000cc"


@pytest.fixture
def server() -> FastMCP:
    from ks_mcp.server import build_server

    return build_server()


async def render(server: FastMCP, name: str, args: dict[str, Any]) -> str:
    """Render a prompt and flatten its messages to one searchable string."""
    result = await server.get_prompt(name, args)
    parts: list[str] = []
    for message in result.messages:
        content = message.content
        parts.append(getattr(content, "text", "") or "")
    return "\n".join(parts)


async def test_all_prompts_registered(server: FastMCP) -> None:
    names = {p.name for p in await server.list_prompts()}
    assert EXPECTED_PROMPTS == names


async def test_prompt_names_are_hyphenated(server: FastMCP) -> None:
    """These are public identifiers — FastMCP would otherwise use the def name."""
    for prompt in await server.list_prompts():
        assert "_" not in prompt.name, f"{prompt.name} leaked the Python function name"


async def test_every_prompt_has_title_and_description(server: FastMCP) -> None:
    for prompt in await server.list_prompts():
        assert (prompt.title or "").strip(), f"{prompt.name} has no title"
        assert (prompt.description or "").strip(), f"{prompt.name} has no description"


async def test_every_prompt_argument_is_described(server: FastMCP) -> None:
    for prompt in await server.list_prompts():
        for argument in prompt.arguments or []:
            assert (argument.description or "").strip(), (
                f"{prompt.name}.{argument.name} has no description"
            )


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("summarize-with-citations", {"subject": "refund policy"}),
        ("compare-documents", {"left": "MSA v1", "right": "MSA v2"}),
        ("expand-around-chunk", {"chunk_id": CHUNK_ID}),
        ("answer-from-folder", {"question": "What is the notice period?", "path_part_id": FOLDER_ID}),
    ],
)
async def test_every_prompt_states_the_citation_rule(
    server: FastMCP, name: str, args: dict[str, Any]
) -> None:
    """The reason this library exists: small models skip `cite` unless told."""
    body = await render(server, name, args)
    assert "cite(chunk_id)" in body
    assert "[chunk:UUID]" in body


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("summarize-with-citations", {"subject": "x"}),
        ("compare-documents", {"left": "a", "right": "b"}),
        ("expand-around-chunk", {"chunk_id": CHUNK_ID}),
        ("answer-from-folder", {"question": "q", "path_part_id": FOLDER_ID}),
    ],
)
async def test_every_prompt_renders_non_trivial_text(
    server: FastMCP, name: str, args: dict[str, Any]
) -> None:
    body = await render(server, name, args)
    assert len(body) > 300, f"{name} rendered suspiciously short: {body!r}"


async def test_summarize_scopes_to_path_part_when_given(server: FastMCP) -> None:
    scoped = await render(
        server, "summarize-with-citations", {"subject": "onboarding", "path_part_id": FOLDER_ID}
    )
    assert f"parent_path_part_ids=[{FOLDER_ID}]" in scoped

    unscoped = await render(server, "summarize-with-citations", {"subject": "onboarding"})
    assert FOLDER_ID not in unscoped
    # Without a folder, the model must be told to discover one rather than
    # invent a UUID.
    assert "list_contents" in unscoped


async def test_summarize_passes_through_length_budget(server: FastMCP) -> None:
    body = await render(
        server, "summarize-with-citations", {"subject": "x", "max_words": "120"}
    )
    assert "120 words" in body


async def test_compare_documents_routes_versions_to_compare_versions(
    server: FastMCP,
) -> None:
    """Diffing two versions by retrieval instead of `compare_versions` is the
    expensive mistake this prompt is meant to head off."""
    body = await render(server, "compare-documents", {"left": "MSA v3", "right": "MSA v4"})
    assert "compare_versions(" in body
    assert "diff_format" in body and "unit" in body
    # Partial diffs must not be summarized as totals.
    assert "truncated" in body and "degraded" in body
    # Two distinct documents need per-document scoping or hits are unattributable.
    assert "parent_path_part_ids" in body
    assert "Cite **both** sides" in body


async def test_compare_documents_includes_focus_when_given(server: FastMCP) -> None:
    body = await render(
        server,
        "compare-documents",
        {"left": "a", "right": "b", "focus": "termination clauses"},
    )
    assert "termination clauses" in body


async def test_expand_around_chunk_embeds_the_chunk_id(server: FastMCP) -> None:
    body = await render(server, "expand-around-chunk", {"chunk_id": CHUNK_ID})
    assert f"read_around(chunk_id='{CHUNK_ID}')" in body
    assert f"view_chunk_image(chunk_id='{CHUNK_ID}')" in body
    assert f"trace_chunk_lineage(chunk_id='{CHUNK_ID}')" in body


async def test_expand_around_chunk_carries_the_question(server: FastMCP) -> None:
    body = await render(
        server,
        "expand-around-chunk",
        {"chunk_id": CHUNK_ID, "question": "Does this clause still apply?"},
    )
    assert "Does this clause still apply?" in body


async def test_answer_from_folder_enforces_scope(server: FastMCP) -> None:
    """The point of this prompt is refusing to answer outside the folder."""
    body = await render(
        server, "answer-from-folder", {"question": "What is the SLA?", "path_part_id": FOLDER_ID}
    )
    assert f"parent_path_part_ids=['{FOLDER_ID}']" in body
    assert "What is the SLA?" in body
    assert "do not widen the search" in body
    assert "own knowledge" in body


async def test_prompts_make_no_api_calls(server: FastMCP) -> None:
    """Rendering must be free: no HTTP, no tenant quota.

    No `ks` fixture here on purpose — there is no stub server running, so any
    upstream call would fail to connect rather than quietly succeed.
    """
    for name, args in [
        ("summarize-with-citations", {"subject": "x"}),
        ("compare-documents", {"left": "a", "right": "b"}),
        ("expand-around-chunk", {"chunk_id": CHUNK_ID}),
        ("answer-from-folder", {"question": "q", "path_part_id": FOLDER_ID}),
    ]:
        assert await render(server, name, args)
