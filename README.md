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

- **Mostly read-only.** Every tool is read-only except `ask`, which posts a user message to a (newly-created or reused) thread so the KS agent can stream a grounded answer back. There is no ingest / delete surface in v1.
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

```bash
# run without installing (recommended for end users)
uvx knowledgestack-mcp

# or install into an environment
pip install knowledgestack-mcp
# or
uv pip install knowledgestack-mcp
```

Requires Python **3.11+**.

## Configure

Export a Knowledge Stack API key (issued from the dashboard — see the [cookbook quickstart](https://github.com/knowledgestack/ks-cookbook)):

| Variable       | Required | Default                             | Notes                                                       |
| -------------- | -------- | ----------------------------------- | ----------------------------------------------------------- |
| `KS_API_KEY`   | yes      | —                                   | A `sk-user-…` key scoped to a single tenant user.           |
| `KS_BASE_URL`  | no       | `https://api.knowledgestack.ai`     | Point at staging or a self-hosted deployment.               |
| `KS_TIMEOUT_S` | no       | `30`                                | HTTP timeout for upstream calls.                            |
| `KS_LOG_LEVEL` | no       | `INFO`                              | `DEBUG` prints tool I/O to stderr (never stdout).           |

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
claude mcp add knowledgestack -- uvx knowledgestack-mcp
# then set the key in your shell or in ~/.claude/settings.json env
```

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

### Phase 2 — provenance & explanation (in progress)

| Tool | Description | Status |
| --- | --- | --- |
| `trace_chunk_lineage` | Return the lineage graph (merge / split / re-embed / re-ingest) for a chunk. | ✅ v0.2 |
| `compare_versions` | Unified diff between two versions of the same document. | ✅ v0.2 |
| `explain_answer_sources` | Given a generated answer, return the chunks + lineage that grounded it. | 🟡 backend-dependent |
| `verify_document_consistency` | Cross-check a document's sections for internal contradiction. | 🟡 backend-dependent |

### Phase 3 — workflows & audit (planned)

| Tool | Description | Status |
| --- | --- | --- |
| `run_document_workflow` | Kick off a Temporal workflow (re-embed, reclassify, translate, …) scoped to a version. | ⚪ v0.3 |
| `validate_contract_fields` | Check required fields exist and match types against a contract schema. | ⚪ v0.3 |
| `audit_cross_document_contradictions` | Find contradictions across a folder subtree. | ⚪ v0.4 |

Writes (ingest / delete / generate) are intentionally **not** exposed in Phase 1 or 2. See the [Roadmap](#roadmap) for the plan around admin-scoped write tools.

## How the tools fit together

You have **two paths** to a grounded answer. Pick the one that fits the agent
you're building.

```text
              ┌────────────── Quick path: one-shot grounded Q&A ──────────────┐
              │                                                                │
   user q. ──►│  ask(question, [thread_id])                                    │──► answer + citations
              │  (KS agent does the retrieval + drafting; you just ship it.)   │
              └────────────────────────────────────────────────────────────────┘

              ┌────────────── Custom path: roll your own loop ────────────────┐
              │                                                                │
              │   search_knowledge / search_keyword                            │
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

`ask` is the right choice when you want one tool call to do the whole job.
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

- The server never logs `KS_API_KEY` or full request bodies at `INFO`.
- All tool responses are schema-validated via Pydantic before being returned to the client.
- Tenant isolation is enforced **upstream** by the Knowledge Stack API; the server is a pass-through with no cross-tenant cache.
- Report vulnerabilities privately via [SECURITY.md](SECURITY.md).

## Diagnostics

Click through every tool with a real API key using the official inspector:

```bash
npx @modelcontextprotocol/inspector uvx knowledgestack-mcp
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

Layout:

```
src/ks_mcp/
├── server.py         # FastMCP entrypoint + CLI
├── client.py         # httpx/ksapi wrapper
├── schema.py         # pydantic IO models
├── errors.py         # typed error mapping
└── tools/
    ├── search.py     # search_knowledge, search_keyword
    ├── read.py       # read, read_around, view_chunk_image
    ├── cite.py       # cite (structured citation builder)
    ├── ask.py        # ask (one-shot agent Q&A over SSE)
    ├── browse.py     # list_contents, find, get_info
    ├── org.py        # get_organization_info, get_current_datetime
    └── provenance.py # trace_chunk_lineage, compare_versions
```

## Roadmap

See [ROADMAP.md](ROADMAP.md) and the [public issue tracker](https://github.com/knowledgestack/ks-mcp/issues) for everything on deck. **We prioritize what users thumbs-up** — if a milestone matters to you, react on the issue.

- **v0.2** — OAuth 2.1 device flow auth, resource templates for folders/documents, streaming partial results, prompt library.
- **v0.3** — admin-scoped **write tools** behind an explicit opt-in flag (`--allow-write`): ingest, delete, re-embed.
- **v0.4** — hosted Streamable HTTP deployment guide (Fly.io, Cloud Run, Modal), per-tool rate limits.
- **v0.5** — hybrid search (dense + BM25 fusion) tool, and a `summarize_document` convenience tool.
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
