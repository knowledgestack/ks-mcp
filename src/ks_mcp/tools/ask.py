"""Ask the Knowledge Stack agent a question and return the final answer."""

import asyncio
import json
from typing import Annotated, Any
from uuid import UUID

import httpx
import ksapi
from ksapi.api.thread_messages_api import ThreadMessagesApi
from ksapi.api.threads_api import ThreadsApi
from ksapi.models.supported_language import SupportedLanguage
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ks_mcp.client import (
    DEFAULT_AGENT_TIMEOUT_S,
    get_api_client,
    get_effective_api_key,
    get_effective_base_url,
)
from ks_mcp.errors import rest_to_mcp
from ks_mcp.schema import AskCitation, AskResult

_CONNECT_TIMEOUT_S = 30.0


async def _ensure_thread(client: ksapi.ApiClient, thread_id: UUID | None, question: str) -> UUID:
    """Return an existing thread id, or create a new thread auto-titled by ``question``."""
    if thread_id is not None:
        return thread_id
    create_req = ksapi.CreateThreadRequest(message_for_title=question[:4000])
    try:
        thread = await ThreadsApi(client).create_thread(create_thread_request=create_req)
    except ksapi.ApiException as exc:
        raise rest_to_mcp(exc) from exc
    return thread.id


async def _send_user_message(
    client: ksapi.ApiClient,
    thread_id: UUID,
    question: str,
    output_language: SupportedLanguage | None,
) -> str:
    """POST /threads/{id}/user_message → returns workflow_id (202)."""
    req = ksapi.UserMessageRequest(input_text=question, output_language=output_language)
    try:
        resp = await ThreadsApi(client).send_user_message(
            thread_id=thread_id,
            user_message_request=req,
        )
    except ksapi.ApiException as exc:
        raise rest_to_mcp(exc) from exc
    return str(getattr(resp, "workflow_id", "") or "")


def _parse_sse_block(block: str) -> tuple[str | None, str]:
    """Pull (event, data) out of one SSE event block. Returns (None, "") on garbage."""
    event: str | None = None
    data_lines: list[str] = []
    for raw in block.splitlines():
        if not raw or raw.startswith(":"):
            continue
        if ":" not in raw:
            continue
        field, _, value = raw.partition(":")
        value = value.lstrip(" ")
        if field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)
    return event, "\n".join(data_lines)


# Events the stream emits that `ask` has no use for: tool/reasoning activity and
# text-block framing. Listed explicitly so an unexpected event name is obvious.
_IGNORED_EVENTS = frozenset(
    {
        "text_start",
        "text_end",
        "tool_call",
        "tool_result",
        "reasoning_start",
        "reasoning_delta",
        "reasoning_end",
        "user_message_appended",
        "replay_complete",
    }
)


class _Accumulator:
    """Mutable stream state, so a timeout can still return what was assembled.

    The thread stream carries text deltas and lifecycle events only — citations
    are attached to the *persisted* message after post-processing, so they are
    fetched separately once ``message_end`` lands (see ``_fetch_enriched``).
    """

    def __init__(self, thread_id: UUID) -> None:
        self.thread_id = thread_id
        self.parts: list[str] = []
        self.citations: list[AskCitation] = []
        self.message_id: UUID | None = None
        self.is_error = False
        self.error_text = ""
        self.done = False

    def feed(self, event: str | None, data: str) -> None:
        # The terminal event is yielded as a bare `data: [DONE]` with no
        # `event:` line, so it must be matched before the event-name switch.
        if data == "[DONE]":
            self.done = True
            return
        if not event or event in _IGNORED_EVENTS:
            return
        try:
            payload: dict[str, Any] = json.loads(data) if data else {}
        except json.JSONDecodeError:
            return
        # Every message-scoped event carries `id`, and we subscribe *after* the
        # 202 — so `message_start` may already have been published before our
        # stream attaches. Take the id from whichever event arrives first, or
        # citation enrichment silently never runs.
        if self.message_id is None and payload.get("id"):
            try:
                self.message_id = UUID(str(payload["id"]))
            except ValueError:
                pass
        if event == "text_delta":
            delta = payload.get("delta")
            if isinstance(delta, str):
                self.parts.append(delta)
        elif event == "error":
            self.is_error = True
            # The error event carries {code, message}; older publishers used {error}.
            detail = str(payload.get("message") or payload.get("error") or "")
            code = str(payload.get("code") or "")
            self.error_text = f"{code}: {detail}".strip(": ") or "agent error"
        elif event in ("message_end", "done"):
            self.done = True

    def result(self, *, timed_out: bool = False) -> AskResult:
        answer = "".join(self.parts).strip()
        if self.is_error and not answer:
            answer = f"(agent error) {self.error_text}"
        elif timed_out:
            suffix = "\n\n(timed out waiting for the agent; answer may be incomplete)"
            answer = f"{answer}{suffix}" if answer else suffix.strip()
        return AskResult(
            answer=answer or "(empty answer)",
            citations=self.citations,
            thread_id=self.thread_id,
            message_id=self.message_id,
            is_error=self.is_error or timed_out,
        )


def _to_ask_citation(raw: ksapi.EnrichedCitation) -> AskCitation | None:
    """Project an ``EnrichedCitation`` onto the MCP-facing citation model."""
    if raw.chunk_id is None:
        return None
    return AskCitation(
        chunk_id=raw.chunk_id,
        quote=raw.quote or "",
        document_id=raw.document_id,
        document_name=raw.document_name,
        materialized_path=raw.materialized_path,
        path_part_id=raw.path_part_id,
        version_number=raw.version_number,
    )


async def _fetch_enriched(
    client: ksapi.ApiClient, thread_id: UUID, message_id: UUID
) -> tuple[str | None, list[AskCitation], bool]:
    """Read the persisted assistant message for citations and normalized text.

    ``message_end`` is published *after* persistence, so by the time the stream
    closes the message carries its resolved citations. Best-effort: on any API
    error we fall back to the text assembled from the stream.
    """
    try:
        message = await ThreadMessagesApi(client).get_thread_message(
            thread_id=thread_id,
            message_id=message_id,
            with_details=False,
        )
    except ksapi.ApiException:
        return None, [], False
    content = message.content
    citations = [c for c in (_to_ask_citation(r) for r in content.citations or []) if c]
    return content.text or None, citations, bool(content.is_error)


async def _consume_stream(
    base_url: str, api_key: str, acc: _Accumulator, timeout_s: float
) -> None:
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"}
    url = f"{base_url.rstrip('/')}/v1/threads/{acc.thread_id}/stream"
    timeout = httpx.Timeout(timeout_s, connect=_CONNECT_TIMEOUT_S, read=timeout_s)
    async with httpx.AsyncClient(timeout=timeout) as http:
        async with http.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                await resp.aread()
                raise RuntimeError(
                    f"Stream returned HTTP {resp.status_code}: {resp.text[:300] if resp.text else ''}"
                )
            buffer = ""
            async for chunk in resp.aiter_text():
                if not chunk:
                    continue
                buffer += chunk
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    acc.feed(*_parse_sse_block(block))
                    if acc.done:
                        return


async def _stream_answer(
    base_url: str, api_key: str, thread_id: UUID, timeout_s: float
) -> tuple[_Accumulator, bool]:
    """Open the SSE stream and accumulate text deltas. Returns (state, timed_out).

    ``timeout_s`` is a hard ceiling: on expiry we return whatever arrived rather
    than raising, so a slow tool-heavy run still yields partial text. The caller
    gets the raw accumulator because a timeout is only really an error if the
    final message can't be fetched afterwards.
    """
    acc = _Accumulator(thread_id)
    try:
        async with asyncio.timeout(timeout_s):
            await _consume_stream(base_url, api_key, acc, timeout_s)
    except (TimeoutError, httpx.TimeoutException):
        return acc, True
    return acc, False


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def ask(
        question: Annotated[
            str,
            Field(
                description="Natural-language question to send to the KS agent.",
                min_length=1,
                max_length=8000,
            ),
        ],
        thread_id: Annotated[
            UUID | None,
            Field(
                description=(
                    "Reuse an existing thread for multi-turn follow-ups. "
                    "Omit to start a fresh thread, auto-titled from ``question``."
                ),
            ),
        ] = None,
        output_language: Annotated[
            SupportedLanguage | None,
            Field(
                description=(
                    "Language the answer is written in (`en` or `zh`). "
                    "Omit to use the tenant's configured default."
                )
            ),
        ] = None,
        timeout_s: Annotated[
            float,
            Field(
                description="Hard ceiling on the streaming wait. Beyond this we return whatever was assembled.",
                ge=10.0,
                le=600.0,
            ),
        ] = DEFAULT_AGENT_TIMEOUT_S,
    ) -> AskResult:
        """Ask the Knowledge Stack agent a question and return the final answer.

        Wraps the backend's two-step ask flow (POST user message → SSE stream)
        into a single tool call: assembles the streamed text, captures any
        inline citations, and returns once the agent emits ``message_end``.
        Use this when you want one grounded answer rather than running your own
        retrieval loop.

        Citations are read from the persisted message once the stream closes,
        so ``citations`` is populated even though the SSE protocol itself
        carries only text.

        Returns ``AskResult(answer, citations[], thread_id, message_id, ...)``.
        Pass the same ``thread_id`` back on a follow-up call to continue the
        conversation. For raw retrieval (without an LLM), use
        ``search_knowledge`` / ``search_hybrid`` + ``cite`` instead; for
        schema-shaped output use ``extract``.
        """
        client = get_api_client()
        thread = await _ensure_thread(client, thread_id, question)
        workflow_id = await _send_user_message(client, thread, question, output_language)

        # Effective, not env: this stream runs over its own httpx client rather
        # than the pooled ApiClient, so reading the env here would create the
        # thread as the caller and then read it back as the process.
        acc, timed_out = await _stream_answer(
            get_effective_base_url(), get_effective_api_key(), thread, timeout_s
        )

        text: str | None = None
        citations: list[AskCitation] = []
        content_error = False
        if acc.message_id is not None:
            text, citations, content_error = await _fetch_enriched(client, thread, acc.message_id)

        # A timeout only degrades the answer if we couldn't recover the final
        # message afterwards — the agent often finishes just after we stop reading.
        result = acc.result(timed_out=timed_out and not text)
        updates: dict[str, Any] = {}
        if workflow_id:
            updates["workflow_id"] = workflow_id
        # The persisted text is citation-resolved; prefer it over raw deltas.
        if text:
            updates["answer"] = text
        if citations:
            updates["citations"] = citations
        if content_error:
            updates["is_error"] = True
        return result.model_copy(update=updates) if updates else result
