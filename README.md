# `knowledgestack-mcp`

> **Focus on agents. We handle document intelligence.**
>
> An MCP server exposing Knowledge Stack's read-side tools (semantic search, keyword search, document reading, image retrieval) to any agent framework — **pydantic-ai**, **LangChain / LangGraph**, **CrewAI**, **Temporal**, **OpenAI Agents SDK**, **Claude Desktop**, **Cursor**.

[![PyPI](https://img.shields.io/pypi/v/knowledgestack-mcp)](https://pypi.org/project/knowledgestack-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Discord](https://img.shields.io/badge/Discord-join%20the%20community-5865F2?logo=discord&logoColor=white)](https://discord.gg/McHmxUeS)

## Related repos

- **[ks-cookbook](https://github.com/knowledgestack/ks-cookbook)** — 32 production-style agent flagships using this server.
- **[ks-sdk-python](https://github.com/knowledgestack/ks-sdk-python)** — Python SDK (`ksapi` on PyPI) for admin / write operations.
- **[ks-sdk-ts](https://github.com/knowledgestack/ks-sdk-ts)** — TypeScript SDK (`@knowledge-stack/ksapi` on npm).
- **[ks-docs](https://github.com/knowledgestack/ks-docs)** — central developer docs (Mintlify → docs.knowledgestack.ai).

## Install

```bash
uvx knowledgestack-mcp           # run without installing (stdio)
# or
pip install knowledgestack-mcp
```

## Configure

Export a KS API key (issued from the dashboard — see the [cookbook quickstart](../README.md)):

```bash
export KS_API_KEY="sk-user-..."
export KS_BASE_URL="https://api.knowledgestack.ai"   # optional; defaults to prod
```

## Run

```bash
# stdio (Claude Desktop, Cursor, pydantic-ai, LangGraph)
uvx knowledgestack-mcp

# Streamable HTTP
uvx knowledgestack-mcp --http --port 8765
```

## Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

## Tools (v1 — read-only)

| Tool | Description |
|---|---|
| `search_knowledge` | Semantic (dense-vector) chunk search. |
| `search_keyword` | BM25 chunk search. |
| `read` | Read a folder / document / section / chunk. |
| `read_around` | Fetch chunks before + after an anchor. |
| `list_contents` | List children of a folder. |
| `find` | Fuzzy-match a path-part by name. |
| `get_info` | Path-part metadata + ancestry breadcrumb. |
| `view_chunk_image` | Download an IMAGE-chunk as MCP image content. |
| `get_organization_info` | Tenant name, language, timezone. |
| `get_current_datetime` | UTC + tenant-local datetime. |

Writes (ingest/delete/generate) are intentionally not exposed in v1.

## Diagnostics

Click through every tool with a real API key:

```bash
npx @modelcontextprotocol/inspector uvx knowledgestack-mcp
```

## License

MIT.
