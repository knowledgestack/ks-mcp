"""Ask the Knowledge Stack agent a question and return the final answer."""

import json
import os
from typing import Annotated, Any
from uuid import UUID

import httpx
import ksapi
from ksapi.api.threads_api import ThreadsApi
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ks_mcp.client import get_api_client
from ks_mcp.errors import rest_to_mcp
from ks_mcp.schema import AskCitation, AskResult

_DEFAULT_TIMEOUT_S = 120.0
_SSE_READ_TIMEOUT_S = 180.0


def _ensure_thread(client: ksapi.ApiClient, thread_id: UUID | None, question: str) -> UUID:
    """Return an existing thread id, or create a new thread auto-titled by ``question``."""
    if thread_id is not None:
        return thread_id
    create_req = ksapi.CreateThreadRequest(message_for_title=question[:4000])
    try:
        thread: Any = ThreadsApi(client).create_thread(create_thread_request=create_req)
    except ksapi.ApiException as exc:
        raise rest_to_mcp(exc) from exc
    return thread.id


def _send_user_message(
    client: ksapi.ApiClient,
    thread_id: UUID,
    question: str,
) -> str:
    """POST /threads/{id}/user_message → returns workflow_id (202)."""
    req = ksapi.UserMessageRequest(input_text=question)
    try:
        resp: Any = ThreadsApi(client).send_user_message(
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


def _stream_answer(
    base_url: str,
    api_key: str,
    thread_id: UUID,
    timeout_s: float,
) -> AskResult:
    """Open the SSE stream, accumulate text deltas, return the assembled answer."""
    parts: list[str] = []
    citations: list[AskCitation] = []
    message_id: UUID | None = None
    is_error = False
    error_text = ""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "text/event-stream",
    }
    url = f"{base_url.rstrip('/')}/v1/threads/{thread_id}/stream"

    with httpx.Client(timeout=httpx.Timeout(timeout_s, read=_SSE_READ_TIMEOUT_S)) as http:
        with http.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                resp.read()
                raise RuntimeError(
                    f"Stream returned HTTP {resp.status_code}: "
                    f"{resp.text[:300] if resp.text else ''}"
                )
            buffer = ""
            for chunk in resp.iter_text():
                if not chunk:
                    continue
                buffer += chunk
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    event, data = _parse_sse_block(block)
                    if event is None and data == "[DONE]":
                        return _build_result(
                            parts,
                            citations,
                            thread_id,
                            message_id,
                            is_error,
                            error_text,
                        )
                    if not event:
                        continue
                    payload: dict[str, Any]
                    try:
                        payload = json.loads(data) if data else {}
                    except json.JSONDecodeError:
                        continue
                    if event == "message_start":
                        raw_id = payload.get("id")
                        if raw_id:
                            try:
                                message_id = UUID(str(raw_id))
                            except ValueError:
                                pass
                    elif event == "text_delta":
                        delta = payload.get("delta")
                        if isinstance(delta, str):
                            parts.append(delta)
                    elif event == "citations":
                        for raw in payload.get("citations") or []:
                            try:
                                citations.append(_to_ask_citation(raw))
                            except (KeyError, ValueError, TypeError):
                                continue
                    elif event == "error":
                        is_error = True
                        error_text = str(payload.get("error", "")) or "agent error"
                    elif event == "message_end":
                        return _build_result(
                            parts,
                            citations,
                            thread_id,
                            message_id,
                            is_error,
                            error_text,
                        )
    return _build_result(parts, citations, thread_id, message_id, is_error, error_text)


def _to_ask_citation(raw: dict[str, Any]) -> AskCitation:
    return AskCitation(
        chunk_id=UUID(str(raw["chunk_id"])),
        quote=str(raw.get("quote", "")),
        document_id=UUID(str(raw["document_id"])) if raw.get("document_id") else None,
        document_name=raw.get("document_name"),
        materialized_path=raw.get("materialized_path"),
        page_number=raw.get("page_number"),
    )


def _build_result(
    parts: list[str],
    citations: list[AskCitation],
    thread_id: UUID,
    message_id: UUID | None,
    is_error: bool,
    error_text: str,
) -> AskResult:
    answer = "".join(parts).strip()
    if is_error and not answer:
        answer = f"(agent error) {error_text}"
    return AskResult(
        answer=answer or "(empty answer)",
        citations=citations,
        thread_id=thread_id,
        message_id=message_id,
        is_error=is_error,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    def ask(
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
        timeout_s: Annotated[
            float,
            Field(
                description="Hard ceiling on the streaming wait. Beyond this we return whatever was assembled.",
                ge=10.0,
                le=600.0,
            ),
        ] = _DEFAULT_TIMEOUT_S,
    ) -> AskResult:
        """Ask the Knowledge Stack agent a question and return the final answer.

        Wraps the backend's two-step ask flow (POST user message → SSE stream)
        into a single synchronous tool call: assembles the streamed text,
        captures any inline citations, and returns once the agent emits
        ``message_end``. Use this when you want one grounded answer rather
        than running your own retrieval loop.

        Returns ``AskResult(answer, citations[], thread_id, message_id, ...)``.
        Pass the same ``thread_id`` back on a follow-up call to continue the
        conversation. For raw retrieval (without an LLM), use
        ``search_knowledge`` / ``search_keyword`` + ``cite`` instead.
        """
        client = get_api_client()
        thread = _ensure_thread(client, thread_id, question)
        workflow_id = _send_user_message(client, thread, question)

        api_key = os.environ.get("KS_API_KEY", "")
        base_url = os.environ.get("KS_BASE_URL", "https://api.knowledgestack.ai")

        result = _stream_answer(base_url, api_key, thread, timeout_s)
        if workflow_id and not result.workflow_id:
            result = result.model_copy(update={"workflow_id": workflow_id})
        return result
