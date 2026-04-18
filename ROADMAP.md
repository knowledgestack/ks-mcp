# Roadmap

`ks-mcp` is in active development. This is our public plan — each item below is tracked as a GitHub issue, grouped into a milestone. Thumbs-up the ones that matter most to you; we prioritize community signal.

> **Legend:** 🟢 shipped · 🟡 in progress · ⚪ planned

## v0.1 — Public beta 🟢

Read-only tool surface over the Knowledge Stack API. Shipped.

- 🟢 `search_knowledge`, `search_keyword`
- 🟢 `read`, `read_around`
- 🟢 `list_contents`, `find`, `get_info`
- 🟢 `view_chunk_image`
- 🟢 `get_organization_info`, `get_current_datetime`
- 🟢 stdio + Streamable HTTP transports

## v0.2 — Registry & auth 🟡 (target: Q2 2026)

- 🟡 [OAuth 2.1 device flow auth](https://github.com/knowledgestack/ks-mcp/issues) (in addition to API keys)
- ⚪ MCP `resources/` exposing folders and documents as URI templates
- ⚪ MCP `prompts/` library: "summarize-with-citations", "compare-documents", "expand-around"
- ⚪ Streaming partial search results via MCP progress notifications
- ⚪ Listing on the official MCP registry at [github.com/mcp](https://github.com/mcp)

## v0.3 — Admin-scoped writes ⚪ (target: Q3 2026)

Writes are **opt-in** behind `--allow-write` and a separate admin key.

- ⚪ `ingest_document` — upload a single file; returns workflow ID
- ⚪ `ingest_url` — crawl + ingest a URL
- ⚪ `delete_document`, `delete_folder`
- ⚪ `reembed` — re-run the embedding pipeline for a path subtree
- ⚪ Audit log entry for every write call

## v0.4 — Hosted deployments ⚪ (target: Q3 2026)

- ⚪ Reference Dockerfile + Fly.io / Cloud Run / Modal configs
- ⚪ Per-tool rate limits and concurrency caps
- ⚪ OpenTelemetry traces on every tool call
- ⚪ Multi-tenant gateway mode (one server, many API keys)

## v0.5 — Retrieval quality ⚪ (target: Q4 2026)

- ⚪ `search_hybrid` — dense + BM25 fusion with RRF
- ⚪ `summarize_document` — server-side summarization with citations
- ⚪ Re-ranker tool (`rerank_chunks`)
- ⚪ Language-aware chunking hints surfaced in results
- ⚪ Citation-quality eval harness in CI

## v1.0 — Stable ⚪ (target: Q1 2027)

- ⚪ Semver guarantees on the tool surface
- ⚪ Published JSON schema per tool
- ⚪ Long-term support branch
- ⚪ Case studies from three design-partner deployments

## Ideas / stretch

We're collecting ideas in [issues labeled `idea`](https://github.com/knowledgestack/ks-mcp/labels/idea). Some of what's floating around:

- GraphQL-style field selection on `read`
- Client-side caching hooks
- Offline corpus snapshot export
- "Explain this chunk" tool backed by a small model
- Native integrations with Temporal, Dagster, and Prefect for batch retrieval

Have something you'd like to see? [Open a feature request.](https://github.com/knowledgestack/ks-mcp/issues/new?template=feature_request.yml)
