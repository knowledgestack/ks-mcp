"""Tests for the hosted-deployment surface (issue #13).

`/healthz` exists because the MCP endpoint can't serve as a health check:
`GET /mcp` returns 406 without an SSE Accept header and 400 without a session
id, so every platform's HTTP probe reads the service as down. If that route
regresses, three deploy configs and the Dockerfile HEALTHCHECK break at once —
and only in production, since nothing else calls it.
"""

import json
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_DIR = REPO_ROOT / "deploy"


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


def _client():
    from starlette.testclient import TestClient

    from ks_mcp.server import build_server

    return TestClient(build_server().streamable_http_app())


def test_healthz_returns_ok() -> None:
    from ks_mcp import __version__

    with _client() as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["server"] == "knowledgestack"
    assert body["version"] == __version__


def test_healthz_makes_no_upstream_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe must not consume tenant quota or fail on an upstream blip.

    `KS_API_KEY` is removed entirely: anything reaching for the KS client would
    raise, so a passing test proves the route touches neither.
    """
    monkeypatch.delenv("KS_API_KEY", raising=False)
    with _client() as client:
        assert client.get("/healthz").status_code == 200


def test_mcp_endpoint_is_not_usable_as_a_health_check() -> None:
    """Pins the reason `/healthz` exists, so nobody "simplifies" it away.

    `build_server()` defaults to a localhost bind, for which FastMCP turns on
    DNS-rebinding protection — and TestClient sends `Host: testserver`, so the
    rejection lands before the Accept/session checks. 421 is still the point:
    none of these are a usable health probe.
    """
    with _client() as client:
        assert client.get("/mcp").status_code == 421
        assert client.get("/mcp", headers={"Accept": "text/event-stream"}).status_code == 421


def test_healthz_is_exempt_from_host_validation() -> None:
    """Platform probes address the container by IP, not by hostname.

    If `/healthz` were Host-validated it would 421 for every probe and the
    deployment would never come up healthy.
    """
    with _client() as client:  # TestClient sends `Host: testserver`
        assert client.get("/healthz").status_code == 200


# ---------------------------------------------------------------------------
# DNS-rebinding protection (off by default on a public bind)
# ---------------------------------------------------------------------------


def test_transport_security_is_none_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset must preserve FastMCP's own behaviour rather than inventing one."""
    from ks_mcp.server import _transport_security

    monkeypatch.delenv("KS_ALLOWED_HOSTS", raising=False)
    assert _transport_security() is None


def test_allowed_hosts_enables_protection_on_a_public_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gap this closes: a 0.0.0.0 bind gets no Host validation by default."""
    from ks_mcp.server import build_server

    monkeypatch.delenv("KS_ALLOWED_HOSTS", raising=False)
    assert build_server(host="0.0.0.0").settings.transport_security is None

    monkeypatch.setenv("KS_ALLOWED_HOSTS", "mcp.example.com, mcp.internal:*")
    monkeypatch.setenv("KS_ALLOWED_ORIGINS", "https://mcp.example.com")
    settings = build_server(host="0.0.0.0").settings.transport_security
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == ["mcp.example.com", "mcp.internal:*"]
    assert settings.allowed_origins == ["https://mcp.example.com"]


def test_allowed_hosts_rejects_a_foreign_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured allowlist must actually reject, not just be stored."""
    from starlette.testclient import TestClient

    from ks_mcp.server import build_server

    monkeypatch.setenv("KS_ALLOWED_HOSTS", "mcp.example.com")
    app = build_server(host="0.0.0.0").streamable_http_app()
    with TestClient(app) as client:
        assert client.get("/mcp", headers={"Host": "evil.example.com"}).status_code == 421
        # The allowed host gets past Host validation and on to the real checks.
        assert client.get("/mcp", headers={"Host": "mcp.example.com"}).status_code != 421


def _captured_warnings(host: str) -> str:
    """Run `_warn_if_unprotected` and return what the `ks_mcp` logger emitted.

    Not `caplog`: `configure_logging()` sets `propagate = False` on this logger
    (stdout is the stdio transport's frame channel), and caplog captures via
    propagation to root. Whether another test has already called it decides
    whether caplog sees anything — which made this pass alone and fail in the
    full suite. Attaching a handler to the logger itself is order-independent.
    """
    import logging

    from ks_mcp.client import logger
    from ks_mcp.server import _warn_if_unprotected

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture(level=logging.WARNING)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.WARNING)
    try:
        _warn_if_unprotected(host)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    return "\n".join(record.getMessage() for record in records)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_no_warning_for_a_localhost_bind(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FastMCP protects localhost itself — warning there would be noise."""
    monkeypatch.delenv("KS_ALLOWED_HOSTS", raising=False)
    assert "KS_ALLOWED_HOSTS" not in _captured_warnings(host)


def test_warns_on_a_public_bind_without_an_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KS_ALLOWED_HOSTS", raising=False)
    warnings = _captured_warnings("0.0.0.0")
    assert "KS_ALLOWED_HOSTS" in warnings
    assert "validation are disabled" in warnings


def test_no_warning_once_an_allowlist_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KS_ALLOWED_HOSTS", "mcp.example.com")
    assert "KS_ALLOWED_HOSTS" not in _captured_warnings("0.0.0.0")


# ---------------------------------------------------------------------------
# deploy configs
# ---------------------------------------------------------------------------


def test_dockerfile_exists_and_runs_unprivileged() -> None:
    body = (REPO_ROOT / "Dockerfile").read_text()
    assert "USER ksmcp" in body, "container must not run as root"
    # The venv is not relocatable; builder and runtime paths have to match.
    assert "COPY --from=builder --chown=ksmcp:ksmcp /app/.venv /app/.venv" in body
    # Editable installs leave a .pth pointing at a path the runtime lacks.
    assert "--no-editable" in body
    assert "/healthz" in body, "HEALTHCHECK should probe /healthz"
    assert "--host" in body and "0.0.0.0" in body, "must bind beyond loopback"


def test_dockerignore_excludes_env_files() -> None:
    """A stray .env in the build context is a key in an image layer."""
    body = (REPO_ROOT / ".dockerignore").read_text().splitlines()
    assert ".env" in body
    assert ".git/" in body


def test_no_deploy_config_hardcodes_an_api_key() -> None:
    """The one review that matters on these files."""
    for path in sorted(DEPLOY_DIR.rglob("*")) + [REPO_ROOT / "Dockerfile"]:
        if not path.is_file():
            continue
        body = path.read_text()
        # `sk-user-...` / `sk-user-…` placeholders are fine; a real key is not.
        for line in body.splitlines():
            if "sk-user-" in line:
                remainder = line.split("sk-user-", 1)[1]
                assert remainder[:1] in {"", ".", "…", '"', "'", ")", " "}, (
                    f"{path.name} may contain a real API key: {line.strip()}"
                )


def test_every_deploy_config_sets_an_allowed_hosts_placeholder() -> None:
    """Protection is off by default on a public bind — each config must say so."""
    for name in ("fly.toml", "cloudrun-service.yaml", "modal_app.py"):
        body = (DEPLOY_DIR / name).read_text()
        assert "KS_ALLOWED_HOSTS" in body, f"{name} leaves Host validation off"


def test_fly_config_keeps_one_warm_machine() -> None:
    """MCP sessions are stateful — scale-to-zero drops live sessions."""
    config = tomllib.loads((DEPLOY_DIR / "fly.toml").read_bytes().decode())
    service = config["http_service"]
    assert service["internal_port"] == 8765
    assert service["min_machines_running"] >= 1
    assert service["auto_stop_machines"] == "off"
    assert config["http_service"]["checks"][0]["path"] == "/healthz"
    # The key must come from `fly secrets`, not [env].
    assert "KS_API_KEY" not in config.get("env", {})


def test_cloudrun_config_binds_the_port_it_declares() -> None:
    """Cloud Run's default port is not the image's, so args must pass it through."""
    yaml = pytest.importorskip("yaml", reason="pyyaml is not a project dependency")
    spec = yaml.safe_load((DEPLOY_DIR / "cloudrun-service.yaml").read_text())
    template = spec["spec"]["template"]
    container = template["spec"]["containers"][0]

    declared = container["ports"][0]["containerPort"]
    args = container["args"]
    assert str(declared) == args[args.index("--port") + 1], (
        "containerPort and --port disagree; the service would never pass a probe"
    )
    assert container["livenessProbe"]["httpGet"]["port"] == declared
    assert container["startupProbe"]["httpGet"]["path"] == "/healthz"

    annotations = template["metadata"]["annotations"]
    assert annotations["run.googleapis.com/sessionAffinity"] == "true"
    assert int(annotations["autoscaling.knative.dev/minScale"]) >= 1
    # Must exceed the `ask` tool's own 600s ceiling or the proxy cuts the stream.
    assert template["spec"]["timeoutSeconds"] > 600

    env = {e["name"]: e for e in container["env"]}
    assert "value" not in env["KS_API_KEY"], "KS_API_KEY must come from a secret"
    assert env["KS_API_KEY"]["valueFrom"]["secretKeyRef"]["name"]


def test_modal_app_is_importable_python_and_serves_the_asgi_app() -> None:
    import ast

    body = (DEPLOY_DIR / "modal_app.py").read_text()
    ast.parse(body)  # raises on a syntax error
    assert "streamable_http_app()" in body
    assert "min_containers=1" in body, "scale-to-zero drops live MCP sessions"
    assert "Secret.from_name" in body, "key must come from a Modal secret"


def test_deploy_readme_documents_every_config() -> None:
    readme = (DEPLOY_DIR / "README.md").read_text()
    for name in ("fly.toml", "cloudrun-service.yaml", "modal_app.py"):
        assert name in readme, f"{name} is undocumented"
    # The server has no auth of its own yet; that has to be said out loud.
    assert "authenticating proxy" in readme


def test_root_readme_links_the_deploy_guide() -> None:
    assert "deploy/README.md" in (REPO_ROOT / "README.md").read_text()


def test_schemas_index_is_valid_json() -> None:
    """Cheap guard that the committed manifest is loadable by a consumer."""
    payload = json.loads((REPO_ROOT / "schemas" / "index.json").read_text(encoding="utf-8"))
    assert payload["tools"]
