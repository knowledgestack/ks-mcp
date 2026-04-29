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

## 🚀 60-second quickstart

```bash
export KS_API_KEY="sk-user-..."          # issue one in the KS dashboard
uvx knowledgestack-mcp                   # stdio (for Claude Desktop, Cursor, etc.)
# — or —
uvx knowledgestack-mcp --http --port 8765   # Streamable HTTP (remote agents)
```

Add it to **Claude Desktop** by dropping this into `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

Other clients (Cursor, Windsurf, Zed, VS Code Continue, pydantic-ai, LangGraph, CrewAI, OpenAI Agents SDK, Temporal) → see **[Wiki / Client setup »](https://github.com/knowledgestack/ks-mcp/wiki/Client-Setup)**.

---

## 🧰 Tools at a glance

| Tool | What it does |
| --- | --- |
| `ask` | One-shot grounded Q&A. KS agent retrieves + drafts; you ship the answer. |
| `search_knowledge` | Semantic chunk search (concepts). |
| `search_keyword` | BM25 chunk search (exact terms / IDs). |
| `read` / `read_around` | Pull a chunk, document, or N neighbours of a chunk. |
| `cite` | Structured citation: document, path, page, snippet, `[chunk:UUID]`. |
| `list_contents` / `find` / `get_info` | Folder navigation + ancestry breadcrumbs. |
| `view_chunk_image` | Inline image bytes for IMAGE-type chunks. |
| `trace_chunk_lineage` / `compare_versions` | Provenance + per-version diffs. |
| `get_organization_info` / `get_current_datetime` | Tenant context for relative queries. |

Full reference, including Phase 2 / Phase 3 plans → **[Wiki / Tools »](https://github.com/knowledgestack/ks-mcp/wiki/Tools)**.

---

## 📚 Documentation

Most reference material lives in the **[Wiki](https://github.com/knowledgestack/ks-mcp/wiki)** so the README stays scannable:

- **[Client setup](https://github.com/knowledgestack/ks-mcp/wiki/Client-Setup)** — Claude Desktop, Cursor, Windsurf, Zed, VS Code Continue, pydantic-ai, LangGraph, CrewAI, OpenAI Agents SDK, Temporal.
- **[Architecture](https://github.com/knowledgestack/ks-mcp/wiki/Architecture)** — how `ks-mcp` sits between agent frameworks and the KS backend; tool composition; identifier model. (Mermaid diagrams.)
- **[Tools reference](https://github.com/knowledgestack/ks-mcp/wiki/Tools)** — full per-tool docs, inputs/outputs, recommended pairings.
- **[Configuration](https://github.com/knowledgestack/ks-mcp/wiki/Configuration)** — every env var, default, and override.
- **[Transports](https://github.com/knowledgestack/ks-mcp/wiki/Transports)** — stdio vs Streamable HTTP, when to pick each.
- **[Security model](https://github.com/knowledgestack/ks-mcp/wiki/Security)** — auth, tenant isolation, what we log.
- **[Diagnostics](https://github.com/knowledgestack/ks-mcp/wiki/Diagnostics)** — MCP inspector, debug logging, common errors.
- **[Development](https://github.com/knowledgestack/ks-mcp/wiki/Development)** — local dev, tests, contribution flow.
- **[Cookbook recipes](https://github.com/knowledgestack/ks-mcp/wiki/Cookbook-Recipes)** — guided index of `ks-cookbook` examples by domain.

> Source for these wiki pages lives under [`wiki/`](wiki/) in this repo so changes can be reviewed in PRs and synced into the GitHub wiki. See [`wiki/README.md`](wiki/README.md) for the sync command.

---

## 🗺️ Roadmap

Highlights — see [ROADMAP.md](ROADMAP.md) for the full plan.

- **v0.2** — OAuth 2.1 auth, MCP resource templates, streaming partials, prompt library.
- **v0.3** — admin-scoped **write tools** behind `--allow-write`.
- **v0.4** — hosted deployment guide (Fly.io, Cloud Run, Modal), rate limits.
- **v0.5** — hybrid search, `summarize_document`, re-ranker, citation eval harness.
- **v1.0** — stable tool surface + semver, listing on [github.com/mcp](https://github.com/mcp).

**Help shape it:** ⭐ [star the repo](https://github.com/knowledgestack/ks-mcp/stargazers), 👍 [thumbs-up issues](https://github.com/knowledgestack/ks-mcp/issues), ✨ [open a feature request](https://github.com/knowledgestack/ks-mcp/issues/new?template=feature_request.yml).

---

## 🔗 Related repos

- **[ks-cookbook](https://github.com/knowledgestack/ks-cookbook)** — production-style agent flagships built on this server.
- **[ks-sdk-python](https://github.com/knowledgestack/ks-sdk-python)** — Python SDK (`ksapi` on PyPI).
- **[ks-sdk-ts](https://github.com/knowledgestack/ks-sdk-ts)** — TypeScript SDK (`@knowledge-stack/ksapi` on npm).
- **[ks-docs](https://github.com/knowledgestack/ks-docs)** — central developer docs (Mintlify → docs.knowledgestack.ai).

---

## 🤝 Contributing

Issues and PRs welcome. Read [SECURITY.md](SECURITY.md) for vuln reports, and the **[Development wiki page](https://github.com/knowledgestack/ks-mcp/wiki/Development)** for local setup. Feature branches land via PR with CI (pytest + ruff + pyright) required to pass.

**Two ways to help even if you can't open a PR:** ⭐ star the repo, or 💬 [tell us on Discord](https://discord.gg/McHmxUeS) what you're building — community use cases turn into cookbook recipes.

## License

MIT — see [LICENSE](LICENSE).
