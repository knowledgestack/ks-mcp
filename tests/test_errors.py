import json

import ksapi
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS

from ks_mcp.errors import is_not_found, rest_to_mcp


def _api_exception(status: int, body: str = "") -> ksapi.ApiException:
    return ksapi.ApiException(status=status, reason="error", body=body)


def _ks_error(status: int, code: str, detail: str = "", request_id: str | None = None) -> ksapi.ApiException:
    """An exception carrying a real KS error envelope: {detail, code, request_id}."""
    payload: dict[str, object] = {"detail": detail, "code": code}
    if request_id:
        payload["request_id"] = request_id
    return ksapi.ApiException(status=status, reason="error", body=json.dumps(payload))


def test_rest_to_mcp_maps_401_to_invalid_params() -> None:
    error = rest_to_mcp(_api_exception(401))

    assert error.error.code == INVALID_PARAMS
    assert "Invalid KS_API_KEY" in error.error.message


def test_rest_to_mcp_maps_403_to_invalid_params_with_body() -> None:
    error = rest_to_mcp(_api_exception(403, body="denied"))

    assert error.error.code == INVALID_PARAMS
    assert "Forbidden for this path" in error.error.message
    assert "denied" in error.error.message


def test_rest_to_mcp_maps_429_quota_to_internal_error() -> None:
    # Backend 3.x returns 429 + `quota_exceeded`; it has no 402 path at all.
    error = rest_to_mcp(_ks_error(429, "quota_exceeded", "daily cap", request_id="req-9"))

    assert error.error.code == INTERNAL_ERROR
    assert "quota exhausted" in error.error.message
    assert "stop looping" in error.error.message
    assert "req-9" in error.error.message


def test_rest_to_mcp_branches_on_code_not_status() -> None:
    # A gateway may mangle the status; the `code` field is the stable signal.
    error = rest_to_mcp(_ks_error(200, "quota_exceeded"))
    assert "quota exhausted" in error.error.message

    error = rest_to_mcp(_ks_error(500, "service_unavailable", "restarting"))
    assert error.error.code == INTERNAL_ERROR
    assert "retry later" in error.error.message


def test_rest_to_mcp_includes_request_id_when_present() -> None:
    error = rest_to_mcp(_ks_error(403, "forbidden", "no access", request_id="req-abc"))
    assert "req-abc" in error.error.message


def test_rest_to_mcp_tolerates_non_json_body() -> None:
    error = rest_to_mcp(_api_exception(502, body="<html>gateway</html>"))
    assert error.error.code == INTERNAL_ERROR
    assert "gateway" in error.error.message


def test_is_not_found_detects_code_without_status() -> None:
    assert is_not_found(_ks_error(0, "not_found")) is True


def test_rest_to_mcp_maps_500_to_internal_error() -> None:
    error = rest_to_mcp(_api_exception(500, body="boom"))

    assert error.error.code == INTERNAL_ERROR
    assert "backend error (500)" in error.error.message
    assert "boom" in error.error.message


def test_is_not_found_only_for_404() -> None:
    assert is_not_found(_api_exception(404)) is True
    assert is_not_found(_api_exception(403)) is False


class _FakeResponse:
    """Mimics ksapi's RESTResponse so ApiException populates `headers`."""

    def __init__(self, status: int, body: str, headers: dict[str, str]) -> None:
        self.status = status
        self.reason = "error"
        self.data = body.encode()
        self.headers = headers


def test_rest_to_mcp_surfaces_retry_after_header() -> None:
    """429 bodies carry Retry-After; the agent needs it to back off sensibly."""
    exc = ksapi.ApiException(
        http_resp=_FakeResponse(
            429,
            json.dumps({"detail": "slow down", "code": "too_many_requests"}),
            {"Retry-After": "30"},
        ),
        body=None,
        data=None,
    )

    error = rest_to_mcp(exc)

    assert "Retry-After: 30s" in error.error.message
    assert "quota exhausted" in error.error.message


def test_rest_to_mcp_reads_lowercase_retry_after() -> None:
    exc = ksapi.ApiException(
        http_resp=_FakeResponse(429, json.dumps({"detail": "", "code": ""}), {"retry-after": "5"}),
        body=None,
        data=None,
    )
    assert "Retry-After: 5s" in rest_to_mcp(exc).error.message


def test_rest_to_mcp_reads_deserialized_error_model() -> None:
    """ksapi hands back a parsed ErrorResponse in `data` when it can."""
    exc = ksapi.ApiException(
        status=403,
        reason="error",
        data=ksapi.ErrorResponse(detail="no access to that folder", code="forbidden"),
    )

    error = rest_to_mcp(exc)

    assert error.error.code == INVALID_PARAMS
    assert "no access to that folder" in error.error.message


def test_rest_to_mcp_handles_empty_exception() -> None:
    # A transport-level failure with no status and no body must not crash.
    error = rest_to_mcp(ksapi.ApiException())
    assert error.error.code == INTERNAL_ERROR
    assert error.error.message
