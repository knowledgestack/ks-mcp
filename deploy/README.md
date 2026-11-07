# Deploying ks-mcp

Reference configs for hosting the server over Streamable HTTP. All three
assume the [`Dockerfile`](../Dockerfile) at the repo root (Modal installs from
PyPI instead).

| Platform | Config | Deploy |
| --- | --- | --- |
| Fly.io | [`fly.toml`](fly.toml) | `fly secrets set KS_API_KEY=…` then `fly deploy --config deploy/fly.toml` |
| Cloud Run | [`cloudrun-service.yaml`](cloudrun-service.yaml) | key into Secret Manager, then `gcloud run services replace` |
| Modal | [`modal_app.py`](modal_app.py) | `modal secret create ks-api-key …` then `modal deploy deploy/modal_app.py` |

Each file's header comment has the full command sequence.

## Two things that bite everyone

**MCP sessions are stateful.** The Streamable HTTP transport keeps per-session
state in the process that created the session. Two consequences, and every
config here is already set up for both:

- **Don't scale to zero.** A stopped instance drops live sessions, and the
  client's next request fails with `Missing session ID` rather than
  reconnecting.
- **Route a session back to its own instance.** If you scale past one
  instance you need sticky sessions / session affinity. Plain round-robin
  sends a request to an instance that has never heard of that session id.

**Requests are long-lived.** `ask` streams until the agent finishes, up to its
`timeout_s` (600s ceiling). Set the platform's request timeout *above* that, or
the proxy cuts the stream mid-answer. The configs here use 900s.

## Set `KS_ALLOWED_HOSTS` — it is off by default

FastMCP auto-enables DNS-rebinding protection *only* when bound to localhost.
Every container binds `0.0.0.0`, so Host and Origin validation are **disabled**
unless you set an allowlist:

```bash
KS_ALLOWED_HOSTS=mcp.example.com
KS_ALLOWED_ORIGINS=https://mcp.example.com   # optional; only matters for browser clients
```

Each config here ships a placeholder — replace it with your real hostname.
Without it the server accepts any `Host` header pointed at it; with it, a
foreign host gets `421 Misdirected Request`. The server logs a warning at
startup when you serve HTTP on a public interface without one.

`/healthz` is deliberately exempt, because platform probes address the
container by IP rather than by hostname.

Both variables accept a comma-separated list, and a `:*` suffix wildcards the
port (`ks-mcp.internal:*`).

## Credentials

`KS_API_KEY` is a secret. Pass it through the platform's secret store at run
time — never in the image, the config file, or a build arg. A key in an image
layer is a key in your registry.

Verify a deployment two ways:

```bash
curl -fsS https://<host>/healthz          # liveness; makes no KS API call
docker run --rm -e KS_API_KEY=sk-user-… \
    --entrypoint ks-mcp ks-mcp --check    # validates key + host, exits non-zero on failure
```

`/healthz` deliberately doesn't touch the KS API, so a health probe can't burn
tenant quota or restart your container over an upstream blip. `--check` is the
one that proves the credentials work.

## Exposing it safely

The server trusts whoever can reach `/mcp` — it has no auth of its own yet
(OAuth is [#3](https://github.com/knowledgestack/ks-mcp/issues/3), multi-tenant
keys are [#16](https://github.com/knowledgestack/ks-mcp/issues/16)). The key
in the environment is the tenant's full read scope. Put it behind your own
authenticating proxy, a private network, or an allowlist before putting it on
the public internet.
