<!-- mcp-name: io.github.knowledgestack/ks-mcp -->

<h1 align="center">Knowledge Stack MCP</h1>
<p align="center"><strong>One MCP server. Every agent framework. Grounded answers in seconds.</strong></p>
<p align="center">
  Production-ready <a href="https://modelcontextprotocol.io">Model Context Protocol</a> server for tenant-scoped semantic search, keyword search, document reading, citations, image retrieval, and one-shot grounded Q&A.
</p>
<p align="center">
  <em>Works out of the box with</em>
  <strong>Claude Desktop</strong> · <strong>Claude Code</strong> · <strong>Cursor</strong> · <strong>Windsurf</strong> · <strong>Zed</strong> · <strong>VS Code (Continue)</strong> · <strong>pydantic-ai</strong> · <strong>LangChain / LangGraph</strong> · <strong>CrewAI</strong> · <strong>Temporal</strong> · <strong>OpenAI Agents SDK</strong>
  — anything that speaks MCP stdio or Streamable HTTP.
</p>

<p align="center">
  <a href="https://github.com/knowledgestack/ks-mcp/actions/workflows/ci.yml"><img src="https://github.com/knowledgestack/ks-mcp/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-1.2+-8A2BE2" alt="MCP 1.2+"></a>
  <a href="https://discord.gg/McHmxUeS"><img src="https://img.shields.io/badge/Discord-join-5865F2?logo=discord&logoColor=white" alt="Discord"></a>
  <a href="#install"><img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+"></a>
  <a href="#install"><img src="https://img.shields.io/badge/PyPI-coming%20soon-lightgrey" alt="PyPI: coming soon"></a>
</p>

> ⭐ **If `ks-mcp` saves you a day of wiring up retrieval, please [star the repo](https://github.com/knowledgestack/ks-mcp/stargazers) — it's the single best signal we use to prioritize the [roadmap](#roadmap).**
> Got a tool you wish existed? [Open a feature request](https://github.com/knowledgestack/ks-mcp/issues/new?template=feature_request.yml). Want a working example? See the [`ks-cookbook`](https://github.com/knowledgestack/ks-cookbook).

---

## Table of contents

- [Why ks-mcp](#why-ks-mcp)
- [How it fits](#how-it-fits)
- [Install](#install)
- [Configure](#configure)
- [Run](#run)
- [Client setup](#client-setup)
  - [Claude Desktop](#claude-desktop)
  - [Claude Code](#claude-code)
  - [Cursor](#cursor)
  - [VS Code (Continue)](#vs-code-continue)
  - [pydantic-ai](#pydantic-ai)
  - [LangGraph](#langgraph)
  - [OpenAI Agents SDK](#openai-agents-sdk)
- [Tools](#tools)
- [How the tools fit together](#how-the-tools-fit-together)
- [Examples & cookbooks](#examples--cookbooks)
- [Transports](#transports)
- [Security model](#security-model)
- [Diagnostics](#diagnostics)
- [Development](#development)
- [Roadmap](#roadmap)
- [Related repos](#related-repos)
- [Contributing](#contributing)
- [License](#license)

---

## Why ks-mcp

Most agent frameworks ship their own "retrieval toolbox" the moment you need to ground a model in real documents. That quickly becomes:

- one RAG implementation per framework,
- one set of auth headers per environment,
- and one slightly-different search API per team.

`ks-mcp` collapses all of that into a single, portable MCP server. Point any MCP client at it and you get the same high-quality, tenant-scoped tools — semantic search, BM25, structured reading, image retrieval, path browsing, citations — regardless of which agent framework you use this week.

**Key properties:**

- **Mostly read-only.** Every tool is read-only except `ask`, which posts a user message to a (newly-created or reused) thread so the KS agent can stream a grounded answer back. `compare_versions` reads only, but counts as a privileged read and is audit-logged upstream (like a download). There is no ingest / delete surface in v1.
- **Tenant-scoped.** Every call is authenticated with a per-user API key; nothing crosses tenant boundaries.
- **Grounded.** Every search result and `read` payload returns stable chunk IDs + path parts you can cite.
- **Two transports.** Local stdio for desktop agents; Streamable HTTP for remote / multi-agent deployments.
- **Typed.** Built on `mcp>=1.2` + `pydantic>=2.6`. All tool arguments/results are schema-validated.

## How it fits

```
┌──────────────────────┐         ┌──────────────────────┐        ┌─────────────────────┐
│  Agent / IDE client  │  MCP    │     ks-mcp           │  HTTPS │  Knowledge Stack    │
│  (Claude, Cursor,    │ ──────► │  (this repo)         │ ─────► │  API  + vector DB   │
│   LangGraph, …)      │  stdio  │  tools: search, read │        │  + object store     │
└──────────────────────┘  / HTTP └──────────────────────┘        └─────────────────────┘
```

The server is a thin, audited façade over the Knowledge Stack REST API (`ksapi`). No retrieval logic lives here — we only translate MCP tool calls into typed HTTP calls and project the response into MCP content blocks (text, JSON, image).

## Install

> **Pre-release:** `knowledgestack-mcp` is not on PyPI yet, so install from GitHub
> for now. The `uvx knowledgestack-mcp` form below starts working the moment we
> tag a release, and every snippet in this README uses it — swap in the
> `--from git+…` flag until then.

```bash
# run without installing, straight from the repo (works today)
uvx --from git+https://github.com/knowledgestack/ks-mcp knowledgestack-mcp

# once published to PyPI
uvx knowledgestack-mcp

# or install into an environment
pip install git+https://github.com/knowledgestack/ks-mcp
```

Requires Python **3.11+**.

Verify the install and your API key in one step — this calls the real API,
prints your tenant, and lists every registered tool:

```bash
export KS_API_KEY="sk-user-..."
uvx --from git+https://github.com/knowledgestack/ks-mcp knowledgestack-mcp --check
```

```text
ks-mcp 0.2.0
KS_BASE_URL: https://api.knowledgestack.ai
OK: authenticated to tenant 'Acme Corp' (6f1c…)
    language=en timezone=America/New_York
    17 tools registered: ask, cite, compare_versions, extract, find, …
```

It exits non-zero with a specific hint when the key or host is wrong, so it's
also a usable CI smoke test.

## Configure

Export a Knowledge Stack API key (issued from the dashboard — see the [cookbook quickstart](https://github.com/knowledgestack/ks-cookbook)):

| Variable       | Required | Default                             | Notes                                                       |
| -------------- | -------- | ----------------------------------- | ----------------------------------------------------------- |
| `KS_API_KEY`   | yes      | —                                   | A `sk-user-…` key scoped to a single tenant user.           |
| `KS_BASE_URL`  | no       | `https://api.knowledgestack.ai`     | Point at staging or a self-hosted deployment.               |
| `KS_TIMEOUT_S` | no       | `30`                                | HTTP timeout for upstream KS calls. Must be > 0. (Raises the SDK's own 5s default.) |
| `KS_LOG_LEVEL` | no       | `INFO`                              | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. Sets the whole server's verbosity; logs go to stderr, never stdout. |
| `KS_ALLOWED_HOSTS` | no   | _(unset)_                           | Comma-separated `Host` allowlist for `--http`, e.g. `mcp.example.com` (a `:*` suffix wildcards the port). **Set this when hosting:** FastMCP only auto-enables DNS-rebinding protection on a localhost bind, so `0.0.0.0` accepts any `Host` without it. |
| `KS_ALLOWED_ORIGINS` | no | _(unset)_                           | Comma-separated `Origin` allowlist, used only alongside `KS_ALLOWED_HOSTS`. Leave unset to allow clients that send no `Origin` (most non-browser MCP clients). |

```bash
export KS_API_KEY="sk-user-..."
export KS_BASE_URL="https://api.knowledgestack.ai"  # optional
```

## Run

```bash
# stdio — the right choice for Claude Desktop, Cursor, pydantic-ai, LangGraph
uvx knowledgestack-mcp

# Streamable HTTP — remote agents, ngrok tunnels, container deployments
uvx knowledgestack-mcp --http --host 0.0.0.0 --port 8765

# verify key + connectivity, print the tool list, exit
uvx knowledgestack-mcp --check
```

## Client setup

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "knowledgestack": {
      "command": "uvx",
      "args": ["knowledgestack-mcp"],
      "env": { "KS_API_KEY": "sk-user-..." }
    }
  }
}
```

### Claude Code

```bash
claude mcp add knowledgestack -e KS_API_KEY=sk-user-... -- uvx knowledgestack-mcp
```

Without `-e`, the server starts but every tool returns 401 — `claude mcp add`
does not inherit your shell environment.

### Cursor

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "knowledgestack": {
      "command": "uvx",
      "args": ["knowledgestack-mcp"],
      "env": { "KS_API_KEY": "sk-user-..." }
    }
  }
}
```

### VS Code (Continue)

```yaml
# ~/.continue/config.yaml
mcpServers:
  - name: knowledgestack
    command: uvx
    args: ["knowledgestack-mcp"]
    env:
      KS_API_KEY: "sk-user-..."
```

### pydantic-ai

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio

ks = MCPServerStdio("uvx", ["knowledgestack-mcp"])
agent = Agent("openai:gpt-4.1", mcp_servers=[ks])

async with agent.run_mcp_servers():
    result = await agent.run("Summarize the onboarding handbook with citations.")
    print(result.output)
```

### LangGraph

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "knowledgestack": {
        "command": "uvx",
        "args": ["knowledgestack-mcp"],
        "transport": "stdio",
    }
})
tools = await client.get_tools()
```

### OpenAI Agents SDK

```python
from agents import Agent
from agents.mcp import MCPServerStdio

server = MCPServerStdio(params={"command": "uvx", "args": ["knowledgestack-mcp"]})
agent = Agent(name="Research", mcp_servers=[server])
```

## Tools

### Phase 1 — retrieval (v0.1, shipped)

| Tool | Description |
| --- | --- |
| `ask` | One-shot grounded Q&A: dispatches to the KS agent, streams the assistant reply, and returns assembled text + citations. |
| `extract` | Schema-conforming JSON extraction: pass a JSON Schema, get typed fields instead of prose. |
| `search_hybrid` | Fused dense + BM25 search — the best default when you don't know which to pick. |
| `search_knowledge` | Semantic (dense-vector) chunk search over the tenant corpus. |
| `search_keyword` | BM25 chunk search for exact terminology and identifiers. |
| `read` | Read a folder / document / section / chunk by `path_part_id` (also accepts a `chunk_id` directly). |
| `read_around` | Fetch the N chunks before and after an anchor chunk for context expansion. |
| `cite` | Build a structured citation (document, path, page, snippet, `[chunk:UUID]` tag) for one chunk. |
| `list_contents` | List children of a folder (like `ls`). |
| `find` | Fuzzy-match a path-part by name when you don't know the exact path. |
| `get_info` | Path-part metadata + ancestry breadcrumb, for citations. |
| `view_chunk_image` | Download an IMAGE chunk and return it as MCP image content. |
| `get_organization_info` | Tenant name, language, timezone. |
| `get_current_datetime` | UTC + tenant-local datetime (handy for relative queries). |

`ask` and `extract` each consume one message of tenant quota; every other tool
is read-only and free. `search_*` accept a `chunk_types` filter (e.g.
`["TABLE"]` to retrieve only tabular chunks), and `ask` accepts
`output_language` (`en` / `zh`).

Both agent tools run a workflow to completion, so they take a `timeout_s`
(default 120s) independent of `KS_TIMEOUT_S`. `ask` returns citations by
reading them off the persisted assistant message once the stream closes — the
thread SSE protocol itself carries text only. `ask` citations have no page
number; call `cite(chunk_id)` when you need a page-aware footnote.

### Phase 2 — provenance & explanation (in progress)

| Tool | Description | Status |
| --- | --- | --- |
| `trace_chunk_lineage` | Return the lineage graph (merge / split / re-embed / re-ingest) for a chunk. | ✅ v0.2 |
| `compare_versions` | Server-side diff between two versions of the same document — text, spreadsheet cells, structured fields or document blocks. | ✅ v0.2 |
| `explain_answer_sources` | Given a generated answer, return the chunks + lineage that grounded it. | 🟡 backend-dependent |
| `verify_document_consistency` | Cross-check a document's sections for internal contradiction. | 🟡 backend-dependent |

### Phase 3 — workflows & audit (planned)

| Tool | Description | Status |
| --- | --- | --- |
| `get_workflow_status` | Poll a background workflow (from `ask`, `extract`, or an ingest job) for `running` / `succeeded` / `failed`. | ✅ v0.2 |
| `run_document_workflow` | Kick off a Temporal workflow (re-embed, reclassify, translate, …) scoped to a version. | ⚪ v0.3 |
| `validate_contract_fields` | Check required fields exist and match types against a contract schema. | ⚪ v0.3 |
| `audit_cross_document_contradictions` | Find contradictions across a folder subtree. | ⚪ v0.4 |

Writes (ingest / delete / generate) are intentionally **not** exposed in Phase 1 or 2. See the [Roadmap](#roadmap) for the plan around admin-scoped write tools.

### Prompts

The server also ships MCP `prompts/`, which hard-code the recommended tool
sequence for four common jobs. A capable model will derive `search →
read_around → cite` by itself; a smaller one usually answers from the first
snippet and never calls `cite`. These move that discipline server-side.

| Prompt | Arguments | What it enforces |
| --- | --- | --- |
| `summarize-with-citations` | `subject`, `path_part_id?`, `max_words?` | Multiple query phrasings, `read_around` before writing, a citation tag per claim. |
| `compare-documents` | `left`, `right`, `focus?` | Routes two *versions* to `compare_versions` instead of retrieval, scopes two *documents* separately, cites both sides. |
| `expand-around-chunk` | `chunk_id`, `question?` | Neighbours + ancestry + image + lineage before interpreting a chunk. |
| `answer-from-folder` | `question`, `path_part_id` | Refuses to answer from outside the folder rather than widening the search. |

Rendering a prompt is pure text assembly — no API call, no tenant quota.
Clients that don't support `prompts/` simply never list them.

### Resources

Folders and documents are also addressable as MCP `resources/`, so a client can
enumerate the tree and reference a document natively instead of passing UUIDs
through tool arguments:

| URI | Kind | Returns |
| --- | --- | --- |
| `ks://folders` | resource | Tenant root folders, as JSON |
| `ks://folder/{path_part_id}` | template | One folder's immediate children, as JSON |
| `ks://document/{path_part_id}` | template | One document's full text, as Markdown with `[chunk:UUID]` tags |

The two parameterized URIs are **templates**, not listed resources. Enumerating
every folder and document would mean walking the whole tenant tree on each
`resources/list` — unbounded work and a response no client wants on a real
corpus. Listings instead carry each child's own `ks://` URI, so traversal is
lazy: read `ks://folders`, then follow the children you care about.

### Published schemas

Every tool's input and output JSON Schema is committed under
[`schemas/`](schemas/), one file per tool plus an
[`index.json`](schemas/index.json) manifest:

```text
schemas/
├── index.json              # manifest: version, tools, prompts
└── tools/
    ├── ask.json            # { name, description, inputSchema, outputSchema }
    ├── search_hybrid.json
    └── …
```

Generate from a code change with:

```bash
uv run python scripts/export_schemas.py
```

CI fails if the committed files drift from what the server actually serves, so
a renamed argument or a dropped output field shows up as a reviewable diff
instead of silently changing the contract.

## Hosting it

A reference [`Dockerfile`](Dockerfile) and sample configs for Fly.io, Cloud Run
and Modal live in [`deploy/`](deploy/README.md):

```bash
docker build -t ks-mcp .
docker run --rm -p 8765:8765 -e KS_API_KEY=sk-user-... ks-mcp
curl -fsS localhost:8765/healthz
```

The image runs unprivileged, defaults to Streamable HTTP on `:8765`, and
exposes `GET /healthz` for liveness probes — `GET /mcp` can't serve that
purpose, since it returns 406 without an SSE `Accept` header and 400 without a
session id. `/healthz` makes no KS API call, so a probe can't consume tenant
quota; use `ks-mcp --check` to validate credentials.

Two things to get right when hosting, both covered in
[`deploy/README.md`](deploy/README.md): **don't scale to zero** and **route a
session back to its own instance** — MCP sessions are stateful, so a dropped or
misrouted session fails with `Missing session ID` rather than reconnecting. The
server also has no auth of its own yet ([#3](https://github.com/knowledgestack/ks-mcp/issues/3)),
so put it behind your own authenticating proxy before exposing it publicly.

## How the tools fit together

You have **three paths** to a grounded answer. Pick the one that fits the agent
you're building.

```text
              ┌────────────── Quick path: one-shot grounded Q&A ──────────────┐
              │                                                                │
   user q. ──►│  ask(question, [thread_id])                                    │──► answer + citations
              │  (KS agent does the retrieval + drafting; you just ship it.)   │
              └────────────────────────────────────────────────────────────────┘

              ┌────────── Structured path: typed fields, not prose ───────────┐
              │                                                                │
   schema  ──►│  extract(prompt, output_schema={...})                          │──► {"field": value}
              │  (KS agent retrieves, then fills your JSON Schema.)            │
              └────────────────────────────────────────────────────────────────┘

              ┌────────────── Custom path: roll your own loop ────────────────┐
              │                                                                │
              │   search_hybrid / search_knowledge / search_keyword            │
              │              │                                                 │
              │              ▼   chunk_id, materialized_path                   │
              │   read_around(chunk_id) · read(chunk_id|pp_id) · view_chunk_image
              │              │                                                 │
              │              ▼                                                 │
              │   cite(chunk_id)  →  [chunk:UUID] tag + structured footnote    │
              │              │                                                 │
              │              ▼                                                 │
              │       you assemble the answer                                  │
              └────────────────────────────────────────────────────────────────┘
```

`ask` is the right choice when you want one tool call to do the whole job, and
`extract` when the consumer is code rather than a human (populating records,
filling forms, building tables).
The custom path is right when you need to weave multiple chunks across
documents, when you want to control the prompt, or when you're building a
multi-step agent that interleaves retrieval with other tools.

Side tools — `list_contents`, `find`, `get_info` — exist for navigation when
the user asks about a specific document by name or wants you to walk a folder
tree. `trace_chunk_lineage` and `compare_versions` answer "where did this
evidence come from?" once you already have a chunk in hand.

**Identifier cheat sheet**

| Field | Source | Use it with |
| --- | --- | --- |
| `chunk_id` | `search_*` hits, `read` output, neighbour `[chunk:UUID]` tags | `cite`, `read_around`, `view_chunk_image`, `read` (fallback path) |
| `path_part_id` | `list_contents`, `find`, `get_info`, search hits | `read`, `get_info`, `list_contents`, search filters |
| `materialized_path` | every chunk / path-part response | display only — never use as an id |

`chunk_id` and `path_part_id` look identical (both are UUIDs) but are
**different objects**. When in doubt, pass it to `read` — it accepts either.

## Examples & cookbooks

End-to-end, citation-grounded examples live in **[`ks-cookbook`](https://github.com/knowledgestack/ks-cookbook)** — every recipe drives this MCP server (stdio plumbing, real `[chunk:UUID]` citations) from a working agent. The cookbook organises recipes by domain; some categories you'll find:

- **Sales / RevOps** — account research, ICP matching, deal-loss retros, churn risk evidence.
- **Legal / Privacy** — NDA review, DPA gap checks, clause extraction, data-subject request responder.
- **Healthcare** — discharge summary rewrite, drug-interaction checker, audit-defensible HCC coder.
- **Finance & risk** — Basel III risk weighting, AML/SAR narrative drafting, cash-flow anomaly detection.
- **Engineering ops** — ADR drafter, changelog from commits, API deprecation notices, change-monitor → PR.

Browse the full list in [`recipes/INDEX.md`](https://github.com/knowledgestack/ks-cookbook/blob/main/recipes/INDEX.md) (and the longer-form **flagships/** directory for multi-step agents).

If you build something interesting on top of `ks-mcp`, please [open a PR against `ks-cookbook`](https://github.com/knowledgestack/ks-cookbook/pulls) — we feature community recipes on the cookbook front page.

## Transports

| Transport          | When to use                                                     | Command                                |
| ------------------ | --------------------------------------------------------------- | -------------------------------------- |
| `stdio`            | Desktop IDE clients, local agents, CI fixtures                  | `uvx knowledgestack-mcp`               |
| `streamable-http`  | Hosted agents, multi-tenant gateways, ngrok / Cloud Run / Fly   | `uvx knowledgestack-mcp --http`        |

Both transports speak the same tool surface.

## Security model

- The server never logs `KS_API_KEY`, the `Authorization` header, or request bodies —
  at any level, including `KS_LOG_LEVEL=DEBUG`.
- All tool responses are schema-validated via Pydantic before being returned to the client.
- Tenant isolation is enforced **upstream** by the Knowledge Stack API; the server is a pass-through with no cross-tenant cache.
- Report vulnerabilities privately via [SECURITY.md](SECURITY.md).

## Diagnostics

Click through every tool with a real API key using the official inspector:

```bash
npx @modelcontextprotocol/inspector uvx knowledgestack-mcp
```

Check your key, host and tool list without a client (exits non-zero on failure):

```bash
uvx knowledgestack-mcp --check
```

Enable verbose tool tracing (prints to stderr, safe for stdio):

```bash
KS_LOG_LEVEL=DEBUG uvx knowledgestack-mcp
```

## Development

```bash
git clone https://github.com/knowledgestack/ks-mcp
cd ks-mcp
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run pyright
```

**Writing a tool?** `ksapi` >= 3 is async-first: its unsuffixed methods are
coroutines, and the `*_sync` variants would block the MCP event loop. Every
tool here is an `async def` that awaits the SDK, and `tests/test_server.py`
enforces both rules. Tool tests run against a local HTTP stand-in
(`tests/conftest.py`) rather than mocking the SDK, so a renamed field in a KS
response fails the suite instead of being swallowed by a `getattr` fallback.

Layout:

```
src/ks_mcp/
├── server.py         # FastMCP entrypoint + CLI
├── client.py         # httpx/ksapi wrapper
├── schema.py         # pydantic IO models
├── errors.py         # typed error mapping
└── tools/
    ├── search.py     # search_knowledge, search_keyword, search_hybrid
    ├── read.py       # read, read_around, view_chunk_image
    ├── cite.py       # cite (structured citation builder)
    ├── ask.py        # ask (one-shot agent Q&A over SSE)
    ├── extract.py    # extract (schema-shaped agent output)
    ├── browse.py     # list_contents, find, get_info
    ├── org.py        # get_organization_info, get_current_datetime
    ├── provenance.py # trace_chunk_lineage, compare_versions
    └── workflows.py  # get_workflow_status
```

## Roadmap

See [ROADMAP.md](ROADMAP.md) and the [public issue tracker](https://github.com/knowledgestack/ks-mcp/issues) for everything on deck. **We prioritize what users thumbs-up** — if a milestone matters to you, react on the issue.

- **v0.2** — OAuth 2.1 device flow auth, streaming partial results, ~~resource templates for folders/documents~~ ✅ shipped, ~~prompt library~~ ✅ shipped.
- **v0.3** — admin-scoped **write tools** behind an explicit opt-in flag (`--allow-write`): ingest, delete, re-embed.
- **v0.4** — hosted Streamable HTTP deployment guide (Fly.io, Cloud Run, Modal), per-tool rate limits.
- **v0.5** — ~~hybrid search (dense + BM25 fusion)~~ ✅ shipped as `search_hybrid`; next up a `summarize_document` convenience tool and a `rerank_chunks` second pass.
- **v1.0** — stable tool surface, semver guarantees, registry listing on [github.com/mcp](https://github.com/mcp).

Three ways to influence the roadmap:

1. ⭐ **[Star the repo](https://github.com/knowledgestack/ks-mcp/stargazers)** — stars are how we justify investment in this surface.
2. 👍 **Thumbs-up issues** in the [tracker](https://github.com/knowledgestack/ks-mcp/issues) — we sort by reactions when picking the next milestone.
3. ✨ **[Open a feature request](https://github.com/knowledgestack/ks-mcp/issues/new?template=feature_request.yml)** — concrete use cases beat abstract wishlists.

## Related repos

- **[ks-cookbook](https://github.com/knowledgestack/ks-cookbook)** — production-style agent flagships built on this server (start here for working code).
- **[ks-sdk-python](https://github.com/knowledgestack/ks-sdk-python)** — Python SDK (`ksapi` on PyPI) for admin / write operations.
- **[ks-sdk-ts](https://github.com/knowledgestack/ks-sdk-ts)** — TypeScript SDK (`@knowledge-stack/ksapi` on npm).
- **[ks-docs](https://github.com/knowledgestack/ks-docs)** — central developer docs (Mintlify → docs.knowledgestack.ai).

## Contributing

Issues and PRs welcome. Please read [SECURITY.md](SECURITY.md) before reporting anything sensitive, and open a discussion first for large feature proposals so we can align on shape before you write code.

Development happens in the open on `main`; feature branches land via PR with CI (pytest + ruff + pyright) required to pass.

**Two quick ways to help, even if you can't open a PR:**

- ⭐ **Star** the repo — it directly shapes our investment.
- 💬 Drop a note on [Discord](https://discord.gg/McHmxUeS) telling us what you're building. We frequently turn user stories into cookbook recipes.

## License

MIT — see [LICENSE](LICENSE).
