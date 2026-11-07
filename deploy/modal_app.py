"""Modal — sample app for hosting ks-mcp over Streamable HTTP.

    modal secret create ks-api-key KS_API_KEY=sk-user-...   # not in this file
    modal deploy deploy/modal_app.py

Endpoint: https://<workspace>--ks-mcp-server.modal.run/mcp
Health:   https://<workspace>--ks-mcp-server.modal.run/healthz

This serves the Starlette app directly rather than shelling out to the CLI, so
Modal owns the event loop and the ASGI lifespan.
"""

import modal

# Installed from the published package. To deploy unreleased changes, swap
# `.pip_install("knowledgestack-mcp")` for
# `.add_local_dir("src", "/root/src").env({"PYTHONPATH": "/root/src"})`.
image = modal.Image.debian_slim(python_version="3.12").pip_install("knowledgestack-mcp")

app = modal.App("ks-mcp")


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("ks-api-key")],
    # Required when hosting: FastMCP only auto-enables DNS-rebinding protection
    # for localhost binds, so a hosted app accepts any Host/Origin unless
    # KS_ALLOWED_HOSTS is set. Replace with your real `.modal.run` hostname.
    env={
        "KS_ALLOWED_HOSTS": "your-workspace--ks-mcp-server.modal.run",
        "KS_ALLOWED_ORIGINS": "https://your-workspace--ks-mcp-server.modal.run",
    },
    # MCP sessions are stateful: per-session state lives in the container that
    # created it. One always-warm container keeps a session valid for its whole
    # lifetime; scaling out needs session-aware routing, which Modal's default
    # round-robin is not.
    min_containers=1,
    max_containers=1,
    # `ask` streams for minutes — this must exceed the tool's own `timeout_s`.
    timeout=900,
)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def server():
    from ks_mcp.server import build_server

    return build_server().streamable_http_app()
