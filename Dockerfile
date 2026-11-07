# Reference image for hosting ks-mcp over Streamable HTTP.
#
# Build:  docker build -t ks-mcp .
# Run:    docker run --rm -p 8765:8765 -e KS_API_KEY=sk-user-... ks-mcp
# Verify: curl -fsS localhost:8765/healthz
# Doctor: docker run --rm -e KS_API_KEY=sk-user-... \
#             --entrypoint ks-mcp ks-mcp --check
#
# Never bake KS_API_KEY into the image — pass it at run time (or via your
# platform's secret store). A key in an image layer is a key in your registry.

# ---------------------------------------------------------------------------
# Stage 1 — build a wheel and resolve dependencies into a standalone venv.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Pinned, not :latest — two people building this same commit should get the
# same image. Bump deliberately.
COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Build in the *runtime* path, not /build. A uv/virtualenv is not relocatable:
# the console-script shebangs hardcode an absolute interpreter path, so a venv
# built at /build/.venv and copied to /app/.venv dies with
# "exec /app/.venv/bin/ks-mcp: no such file or directory".
WORKDIR /app

# Dependency manifests first: these change far less often than source, so the
# resolved-dependency layer survives most rebuilds.
COPY pyproject.toml uv.lock README.md ./

# `--no-install-project` installs only third-party deps, so editing src/ doesn't
# invalidate this layer. `--frozen` fails on a stale lockfile rather than
# silently resolving something the lockfile never pinned.
RUN uv sync --frozen --no-dev --no-install-project --no-editable

COPY src/ ./src/

# `--no-editable` matters: the default editable install leaves a .pth pointing
# at /app/src, which the runtime stage doesn't carry — the entrypoint would
# then start and die on `ModuleNotFoundError: No module named 'ks_mcp'`.
RUN uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------------------
# Stage 2 — runtime. Carries the venv only: no uv, no build deps, no lockfile.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# curl is here for HEALTHCHECK. If you'd rather not ship it, drop both the
# install and the HEALTHCHECK and let your platform probe /healthz instead.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Run unprivileged. MCP servers execute model-chosen tool calls, so the blast
# radius of a bug should not include root in the container.
RUN useradd --create-home --uid 10001 ksmcp

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --from=builder --chown=ksmcp:ksmcp /app/.venv /app/.venv

USER ksmcp
EXPOSE 8765

# Liveness only — it does not call the KS API, so a probe can't consume tenant
# quota or restart the container over an upstream blip. Run `ks-mcp --check`
# (one-shot) to validate the key and host.
# Port is hardcoded to match CMD. If you override the port, override this too —
# a parameterized probe that CMD ignores just checks the wrong port silently.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8765/healthz || exit 1

# Streamable HTTP, not stdio: stdio in a container only works with `docker run -i`
# and a client that owns the process. Binding 0.0.0.0 is required — 127.0.0.1
# is unreachable from outside the container. Scope exposure with your network.
#
# Different port:
#   docker run -p 9000:9000 ... ks-mcp --http --host 0.0.0.0 --port 9000
#
# For stdio, clear the default args with --entrypoint (appending `ks-mcp` would
# pass it as a *flag* to the entrypoint and fail argument parsing):
#   docker run --rm -i -e KS_API_KEY=... --entrypoint ks-mcp ks-mcp
ENTRYPOINT ["ks-mcp"]
CMD ["--http", "--host", "0.0.0.0", "--port", "8765"]
