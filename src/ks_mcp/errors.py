"""Map ``ksapi.ApiException`` onto MCP-friendly responses.

Every non-2xx KS body is ``{detail, code, request_id}`` where ``code`` comes
from a closed enum. The backend's own integration guide says to branch on
``code`` rather than parse ``detail``, so that's what we do — falling back to
the HTTP status when a proxy or gateway returns something that isn't a KS
error body.
"""

import json
from typing import Any

import ksapi
from mcp import McpError
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS, ErrorData

# Error codes the agent should treat as "stop looping and tell the user".
_QUOTA_CODES = frozenset({"quota_exceeded", "too_many_requests", "quota_period_missing"})
# Codes that mean "the request was wrong", i.e. retrying verbatim won't help.
_CLIENT_CODES = frozenset(
    {
        "bad_request",
        "unauthorized",
        "forbidden",
        "not_found",
        "no_workspace",
        "permission_limit",
        "unprocessable_entity",
        "payload_too_large",
        "unsupported_media_type",
        "tenant_deactivated",
    }
)


def _parse_body(exc: ksapi.ApiException) -> dict[str, Any]:
    """Pull the KS error envelope out of the exception, tolerating non-JSON bodies."""
    data = getattr(exc, "data", None)
    if isinstance(data, dict):
        return data
    for attr in ("detail", "code", "request_id"):
        if hasattr(data, attr):
            return {a: getattr(data, a, None) for a in ("detail", "code", "request_id")}
    body = getattr(exc, "body", None)
    if not body:
        return {}
    try:
        parsed = json.loads(body if isinstance(body, str) else body.decode())
    except (ValueError, AttributeError, UnicodeDecodeError):
        return {"detail": str(body)[:300]}
    return parsed if isinstance(parsed, dict) else {"detail": str(parsed)[:300]}


def _retry_after(exc: ksapi.ApiException) -> str | None:
    headers = getattr(exc, "headers", None)
    if headers is None:
        return None
    try:
        return headers.get("Retry-After") or headers.get("retry-after")
    except AttributeError:
        return None


def rest_to_mcp(exc: ksapi.ApiException) -> McpError:
    """Convert a REST error into an MCP error with an agent-readable message."""
    status = getattr(exc, "status", 0) or 0
    envelope = _parse_body(exc)
    code = str(envelope.get("code") or "")
    detail = str(envelope.get("detail") or "")[:300]
    request_id = envelope.get("request_id")
    suffix = f" (request_id: {request_id})" if request_id else ""

    if code in _QUOTA_CODES or status == 429:
        retry_after = _retry_after(exc)
        wait = f" Retry-After: {retry_after}s." if retry_after else ""
        msg = (
            f"KS quota exhausted ({status}). This key has hit its request limit; "
            f"stop looping and inform the user.{wait} {detail}{suffix}"
        )
        return McpError(ErrorData(code=INTERNAL_ERROR, message=msg))

    if code == "unauthorized" or status == 401:
        msg = (
            "Invalid KS_API_KEY (401). Re-check the key exported to the MCP server "
            f"— it must be a `sk-user-…` key.{suffix}"
        )
        return McpError(ErrorData(code=INVALID_PARAMS, message=msg))

    if code in ("forbidden", "permission_limit") or status == 403:
        msg = f"Forbidden for this path (403). {detail}{suffix}"
        return McpError(ErrorData(code=INVALID_PARAMS, message=msg))

    if code == "service_unavailable" or status >= 500:
        msg = (
            f"KS backend error ({status}). Transient; retry later, do not auto-loop. "
            f"{detail}{suffix}"
        )
        return McpError(ErrorData(code=INTERNAL_ERROR, message=msg))

    if code in _CLIENT_CODES or 400 <= status < 500:
        label = code or str(status)
        return McpError(
            ErrorData(code=INVALID_PARAMS, message=f"KS rejected the call ({label}). {detail}{suffix}")
        )

    return McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message=f"Unexpected {status} from KS. {detail or code}{suffix}",
        )
    )


def is_not_found(exc: ksapi.ApiException) -> bool:
    """404 is probe-able data — surface as a structured result, not an MCP error."""
    if getattr(exc, "status", 0) == 404:
        return True
    return str(_parse_body(exc).get("code") or "") == "not_found"
