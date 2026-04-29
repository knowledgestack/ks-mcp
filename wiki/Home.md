# Knowledge Stack MCP — Wiki

> **One MCP server. Every agent framework. Grounded answers in seconds.**

This wiki is the long-form companion to the repo's [README](https://github.com/knowledgestack/ks-mcp#readme). The README stays scannable; everything reference-y lives here.

## Where to start

```mermaid
flowchart LR
  A[New here?] --> B[Client setup]
  A --> C[Architecture]
  D[Building an agent?] --> E[Tools reference]
  D --> F[Cookbook recipes]
  G[Operating it?] --> H[Configuration]
  G --> I[Transports]
  G --> J[Security]
  G --> K[Diagnostics]
  L[Contributing?] --> M[Development]

  click B "Client-Setup"
  click C "Architecture"
  click E "Tools"
  click F "Cookbook-Recipes"
  click H "Configuration"
  click I "Transports"
  click J "Security"
  click K "Diagnostics"
  click M "Development"
```

## Pages

- **[Client setup](Client-Setup)** — Claude Desktop, Claude Code, Cursor, Windsurf, Zed, VS Code (Continue), pydantic-ai, LangGraph, CrewAI, OpenAI Agents SDK, Temporal.
- **[Architecture](Architecture)** — system diagram, dual paths to a grounded answer, identifier model, internals.
- **[Tools reference](Tools)** — every Phase 1 / 2 / 3 tool with inputs, outputs, and recommended pairings.
- **[Configuration](Configuration)** — environment variables, CLI flags, defaults.
- **[Transports](Transports)** — stdio vs Streamable HTTP, deployment patterns.
- **[Security model](Security)** — auth, tenant isolation, what is logged, vuln reporting.
- **[Diagnostics](Diagnostics)** — MCP inspector, debug logging, error catalogue.
- **[Development](Development)** — local setup, tests, contribution workflow.
- **[Cookbook recipes](Cookbook-Recipes)** — guided index of `ks-cookbook` examples by domain.

## External

- [GitHub repo](https://github.com/knowledgestack/ks-mcp) · [Issues](https://github.com/knowledgestack/ks-mcp/issues) · [Discussions](https://github.com/knowledgestack/ks-mcp/discussions) · [Discord](https://discord.gg/McHmxUeS)
- [`ks-cookbook`](https://github.com/knowledgestack/ks-cookbook) — production-style agent recipes
- [`ks-docs`](https://github.com/knowledgestack/ks-docs) — central product docs (Mintlify → docs.knowledgestack.ai)
