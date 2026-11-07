"""Test harness for the KS MCP server.

``ksapi`` >= 3 talks over ``httpx2``, which ``respx`` cannot intercept (respx
patches ``httpx``). Rather than stubbing the SDK out — which is how the async
regression in these tools went unnoticed — tests run against a real local HTTP
server and let the SDK do its own (de)serialization. A renamed or dropped field
in a KS response therefore fails the test instead of being silently swallowed by
a ``getattr`` fallback.
"""

import json
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

Responder = Callable[[str], tuple[int, Any]]


@dataclass
class RecordedRequest:
    method: str
    path: str
    body: str

    def json(self) -> dict[str, Any]:
        return json.loads(self.body) if self.body else {}


@dataclass
class FakeKS:
    """A local stand-in for the KS API that records what the SDK actually sent."""

    base_url: str
    requests: list[RecordedRequest] = field(default_factory=list)
    _routes: list[tuple[str, re.Pattern[str], Responder]] = field(default_factory=list)
    _raw: dict[re.Pattern[str], tuple[str, str, float]] = field(default_factory=dict)

    def on(
        self,
        method: str,
        path_pattern: str,
        *,
        json_body: Any = None,
        status: int = 200,
        responder: Responder | None = None,
    ) -> None:
        """Register a route. ``path_pattern`` is a regex matched against the path."""
        if responder is None:

            def static(_path: str, _payload: Any = json_body, _status: int = status) -> tuple[int, Any]:
                return _status, _payload

            responder = static

        self._routes.append((method.upper(), re.compile(path_pattern), responder))

    def on_sse(
        self, path_pattern: str, events: list[tuple[str | None, str]], hang_s: float = 0.0
    ) -> None:
        """Register an SSE route. ``events`` is a list of (event_name, data) blocks.

        ``event_name=None`` emits a bare ``data:`` block, which is how the
        backend frames its terminal ``[DONE]``. ``hang_s`` holds the connection
        open after the events, to exercise the client's timeout ceiling.
        """
        body = ""
        for name, data in events:
            if name is not None:
                body += f"event: {name}\n"
            body += f"data: {data}\n\n"
        self._raw[re.compile(path_pattern)] = ("text/event-stream", body, hang_s)

    def resolve_raw(self, path: str) -> tuple[str, str, float] | None:
        for pattern, payload in self._raw.items():
            if pattern.search(path):
                return payload
        return None

    def resolve(self, method: str, path: str) -> tuple[int, Any]:
        for route_method, pattern, responder in self._routes:
            if route_method == method and pattern.search(path):
                return responder(path)
        return 404, {"detail": f"no stub for {method} {path}", "code": "not_found"}

    def paths(self, method: str | None = None) -> list[str]:
        return [
            r.path for r in self.requests if method is None or r.method == method.upper()
        ]

    def last(self) -> RecordedRequest:
        assert self.requests, "no requests were recorded"
        return self.requests[-1]


@pytest.fixture(autouse=True)
def _stub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Provide fake env so imports and client creation are deterministic in tests.
    monkeypatch.setenv("KS_API_KEY", "sk-user-test")
    monkeypatch.setenv("KS_BASE_URL", "http://localhost:8000")
    monkeypatch.delenv("KS_TIMEOUT_S", raising=False)


@pytest.fixture
def ks(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeKS]:
    """Run a local KS stand-in and point the SDK client at it."""
    from ks_mcp.client import get_api_client
    from ks_mcp.tools import org

    state: dict[str, FakeKS] = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # keep pytest output clean
            pass

        def _handle(self, method: str) -> None:
            fake = state["fake"]
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode() if length else ""
            fake.requests.append(RecordedRequest(method, self.path, body))
            raw = fake.resolve_raw(self.path) if method == "GET" else None
            if raw is not None:
                content_type, text, hang_s = raw
                encoded = text.encode()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                if hang_s:
                    # No Content-Length: keep the body open so the client waits.
                    self.end_headers()
                    self.wfile.write(encoded)
                    self.wfile.flush()
                    time.sleep(hang_s)
                    return
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            status, payload = fake.resolve(method, self.path)
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    fake = FakeKS(base_url=f"http://127.0.0.1:{port}")
    state["fake"] = fake
    monkeypatch.setenv("KS_BASE_URL", fake.base_url)
    get_api_client.cache_clear()
    org._cached_tenant = None
    try:
        yield fake
    finally:
        get_api_client.cache_clear()
        org._cached_tenant = None
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Payload builders — every required field of the real ksapi models is present,
# so a schema change in the SDK surfaces as a ValidationError in the test.
# ---------------------------------------------------------------------------

_TS = "2026-01-01T00:00:00Z"
TENANT_ID = "00000000-0000-0000-0000-00000000f001"
PARENT_PATH_ID = "00000000-0000-0000-0000-0000000000ff"
PERMISSIONS = {
    "can_read": True,
    "can_write": False,
    "can_delete": False,
    "can_approve": False,
    "can_grant_permissions": False,
}


def document_ref(document_id: str, name: str) -> dict[str, Any]:
    """A ChunkDocumentResponse — what `with_document=true` attaches to a chunk."""
    return {
        "id": document_id,
        "name": name,
        "document_type": "PDF",
        "document_origin": "SOURCE",
    }


def chunk_payload(chunk_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": chunk_id,
        "path_part_id": "00000000-0000-0000-0000-0000000000dd",
        "content_id": "00000000-0000-0000-0000-0000000000ee",
        "content": "The onboarding flow has three steps.",
        "chunk_type": "TEXT",
        "chunk_metadata": {},
        "parent_path_id": "00000000-0000-0000-0000-0000000000ff",
        "materialized_path": "HR/Handbooks/Onboarding",
        "system_managed": False,
        "tenant_id": TENANT_ID,
        "created_at": _TS,
        "updated_at": _TS,
    }
    payload.update(overrides)
    return payload


def scored_chunk_payload(chunk_id: str, score: float = 0.87, **overrides: Any) -> dict[str, Any]:
    return chunk_payload(chunk_id, score=score, **overrides)


def path_part_payload(path_part_id: str, part_type: str, metadata_obj_id: str) -> dict[str, Any]:
    return {
        "path_part_id": path_part_id,
        "name": "Onboarding Handbook",
        "part_type": part_type,
        "parent_path_id": PARENT_PATH_ID,
        "metadata_obj_id": metadata_obj_id,
        "materialized_path": "HR/Handbooks/Onboarding",
        "system_managed": False,
        "exclude_from_qdrant": False,
        "permissions": PERMISSIONS,
        "created_at": _TS,
        "updated_at": _TS,
    }


def section_payload(section_id: str, page_number: int | None = 4) -> dict[str, Any]:
    return {
        "id": section_id,
        "path_part_id": "00000000-0000-0000-0000-000000000091",
        "name": "Section 2",
        "parent_path_id": PARENT_PATH_ID,
        "materialized_path": "HR/Handbooks/Onboarding/Section 2",
        "system_managed": False,
        "tenant_id": TENANT_ID,
        "created_at": _TS,
        "updated_at": _TS,
        "page_number": page_number,
    }


def folder_payload(path_part_id: str, folder_id: str, name: str = "HR") -> dict[str, Any]:
    """A FolderResponse — what the folder list/search endpoints return."""
    return {
        "part_type": "FOLDER",
        "id": folder_id,
        "path_part_id": path_part_id,
        "name": name,
        "parent_path_part_id": None,
        "materialized_path": name,
        "system_managed": False,
        "approval_state": "not_required",
        "permissions": PERMISSIONS,
        "exclude_from_qdrant": False,
        "tenant_id": TENANT_ID,
        "created_at": _TS,
        "updated_at": _TS,
    }


def tenant_payload() -> dict[str, Any]:
    return {
        "id": TENANT_ID,
        "name": "Acme Corp",
        "owner": None,
        "seats": 10,
        "subscription_id": "00000000-0000-0000-0000-00000000c001",
        "settings": {
            "language": "en",
            "description": "Acme Corp knowledge base",
            "timezone": "America/New_York",
            "display_name": "first_last",
            "invite_link": {"enabled": False, "role": "USER"},
        },
    }


def current_user_payload() -> dict[str, Any]:
    return {
        "id": "00000000-0000-0000-0000-000000001001",
        "email": "agent@acme.test",
        "first_name": "A",
        "last_name": "Gent",
        "idp_type": "PASSWORD",
        "current_tenant_id": TENANT_ID,
        "current_tenant_role": "USER",
        "default_tenant_id": TENANT_ID,
        "language_preference": "en",
        "join_method": "INVITED",
    }
