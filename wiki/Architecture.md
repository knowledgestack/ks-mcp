# Architecture

`ks-mcp` is a thin, typed façade over the Knowledge Stack REST API (`ksapi`). No retrieval logic lives in this server — every tool call is translated into one or more typed HTTP calls and projected back into MCP content blocks (text, JSON, image).

## System view

```mermaid
flowchart LR
  subgraph Client["Agent / IDE client"]
    direction TB
    C1[Claude Desktop]
    C2[Cursor / Windsurf / Zed]
    C3[pydantic-ai · LangGraph · CrewAI]
    C4[OpenAI Agents SDK · Temporal]
  end

  subgraph KSMCP["ks-mcp (this repo)"]
    direction TB
    T1[Tool dispatch · FastMCP]
    T2[ksapi client · httpx]
    T3[Schema validation · Pydantic]
    T1 --> T2
    T1 --> T3
  end

  subgraph Backend["Knowledge Stack backend"]
    direction TB
    B1[REST API · FastAPI]
    B2[(Postgres / TimescaleDB)]
    B3[(Vector DB · Qdrant)]
    B4[(Object store · MinIO)]
    B5[Temporal worker · agents]
    B1 --> B2
    B1 --> B3
    B1 --> B4
    B1 --> B5
  end

  Client -- "MCP stdio / Streamable HTTP" --> KSMCP
  KSMCP -- "HTTPS · Bearer sk-user-…" --> Backend
```

**Key properties:**

- Mostly read-only. Only `ask` mutates (creates a thread + assistant message); everything else is GET-only.
- Tenant-scoped — every call carries a per-user `KS_API_KEY`; tenant isolation is enforced upstream.
- Grounded — every search hit and `read` payload returns stable chunk IDs you can cite.

## Two paths to a grounded answer

```mermaid
flowchart TB
  Q([User question])

  Q --> Quick[ask&#40;question, thread_id?&#41;]
  Quick --> A1[KS agent retrieves + drafts]
  A1 --> SSE[SSE: text_delta · citations · message_end]
  SSE --> Out1([Answer + AskCitation&#91;&#93;])

  Q --> S1{Concept or exact term?}
  S1 -- concept --> Sk[search_knowledge]
  S1 -- exact --> Sw[search_keyword]
  Sk --> Hit[ChunkHit: chunk_id, materialized_path, text]
  Sw --> Hit
  Hit --> Pull{Need more context?}
  Pull -- yes --> RA[read_around&#40;chunk_id&#41;]
  Pull -- yes --> Rd[read&#40;chunk_id|path_part_id&#41;]
  Pull -- yes --> Img[view_chunk_image&#40;chunk_id&#41;]
  RA --> Cite[cite&#40;chunk_id&#41;]
  Rd --> Cite
  Img --> Cite
  Cite --> Out2([Answer + Citation per chunk])
```

- **Quick path (`ask`)** — one tool call. Right when you want a single grounded answer.
- **Custom path** — you orchestrate. Right when the answer needs multiple chunks across documents, when you want to control the prompt, or when you're interleaving retrieval with other tools.

Side tools — `list_contents`, `find`, `get_info` — exist for navigation. `trace_chunk_lineage` and `compare_versions` answer "where did this evidence come from?" once you already have a chunk in hand.

## Identifier model

The two UUIDs you'll see most often look identical but are different objects:

```mermaid
flowchart LR
  subgraph Tree["Path tree (every node = a path-part)"]
    direction TB
    F[FOLDER /pp_id]
    D[DOCUMENT /pp_id]
    Sec[SECTION /pp_id]
    Cnk[CHUNK /pp_id  ⇄  chunk_id]
    F --> D --> Sec --> Cnk
  end

  Cnk -- chunk_id --> CK[(chunk content,
  chunk_metadata,
  asset_s3_urls)]

  Sec -. metadata_obj_id .-> SK[(section: name, page_number)]
  D -. metadata_obj_id .-> DK[(document: name, active_version_id)]
```

| Field | Comes from | Use it with |
| --- | --- | --- |
| `chunk_id` | `search_*` hits, `[chunk:UUID]` tags, `cite` | `cite`, `read_around`, `view_chunk_image`, `read` (fallback) |
| `path_part_id` | `list_contents`, `find`, `get_info`, search hits | `read`, `get_info`, `list_contents`, search filters |
| `materialized_path` | every chunk / path-part response | display only — **never** as an id |

> When in doubt, pass it to `read` — it accepts either a `path_part_id` or a `chunk_id` and falls back automatically on 404.

## Request lifecycle (custom path)

```mermaid
sequenceDiagram
  autonumber
  participant Agent
  participant MCP as ks-mcp
  participant API as KS API
  participant VDB as Vector DB

  Agent->>MCP: search_knowledge("onboarding flow")
  MCP->>API: POST /v1/chunks/search (with_document=true)
  API->>VDB: dense vector search
  VDB-->>API: top_k chunk ids
  API-->>MCP: List[ScoredChunkResponse] (incl. document.name)
  MCP-->>Agent: SearchResult{hits[]}
  Agent->>MCP: cite(chunk_id)
  MCP->>API: GET /v1/chunks/{id} + ancestry
  API-->>MCP: chunk + nearest-section page_number
  MCP-->>Agent: Citation{document, path, page, snippet, tag}
```

## `ask` lifecycle (quick path)

```mermaid
sequenceDiagram
  autonumber
  participant Agent
  participant MCP as ks-mcp
  participant API as KS API
  participant W as KS agent worker

  Agent->>MCP: ask("Summarize handbook with citations.")
  MCP->>API: POST /v1/threads (auto-title)
  API-->>MCP: ThreadResponse{id}
  MCP->>API: POST /v1/threads/{id}/user_message
  API-->>MCP: 202 {workflow_id}
  API->>W: dispatch agent workflow
  MCP->>API: GET /v1/threads/{id}/stream (SSE)
  W-->>API: text_delta · citations · message_end
  API-->>MCP: SSE events
  MCP-->>Agent: AskResult{answer, citations[], thread_id, message_id}
```

`AskResult.thread_id` can be passed back on the next `ask(...)` call for multi-turn follow-ups.

## Source layout

```
src/ks_mcp/
├── server.py         # FastMCP entrypoint + CLI
├── client.py         # ksapi client factory (KS_API_KEY / KS_BASE_URL)
├── schema.py         # pydantic IO models (ChunkHit, Citation, AskResult, …)
├── errors.py         # ksapi.ApiException → McpError
└── tools/
    ├── search.py     # search_knowledge, search_keyword
    ├── read.py       # read, read_around, view_chunk_image
    ├── cite.py       # cite (structured citation builder)
    ├── ask.py        # ask (one-shot agent Q&A over SSE)
    ├── browse.py     # list_contents, find, get_info
    ├── org.py        # get_organization_info, get_current_datetime
    └── provenance.py # trace_chunk_lineage, compare_versions
```
