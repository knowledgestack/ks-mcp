# Tools reference

Every tool registered by `ks-mcp`, grouped by phase. Inputs/outputs are validated by Pydantic — your client framework should surface them in the tool palette.

```mermaid
flowchart TB
  subgraph Phase1["Phase 1 — shipped (v0.1)"]
    direction TB
    P1A[ask]
    P1B[search_knowledge]
    P1C[search_keyword]
    P1D[read]
    P1E[read_around]
    P1F[cite]
    P1G[list_contents]
    P1H[find]
    P1I[get_info]
    P1J[view_chunk_image]
    P1K[get_organization_info]
    P1L[get_current_datetime]
  end
  subgraph Phase2["Phase 2 — shipped + planned"]
    direction TB
    P2A[trace_chunk_lineage ✅]
    P2B[compare_versions ✅]
    P2C[explain_answer_sources 🟡]
    P2D[verify_document_consistency 🟡]
  end
  subgraph Phase3["Phase 3 — planned"]
    direction TB
    P3A[run_document_workflow ⚪]
    P3B[validate_contract_fields ⚪]
    P3C[audit_cross_document_contradictions ⚪]
  end
```

## Phase 1 — retrieval (v0.1, shipped)

### `ask`

One-shot grounded Q&A. Wraps the backend's two-step ask flow (`POST user_message` → SSE stream) into a single synchronous call.

| Input | Type | Notes |
| --- | --- | --- |
| `question` | `str` (1–8000) | Natural-language question for the KS agent. |
| `thread_id` | `UUID?` | Reuse for multi-turn follow-ups. Omit for a fresh thread auto-titled from the question. |
| `timeout_s` | `float` (10–600) | Hard ceiling on the streaming wait. |

**Returns** `AskResult{ answer, citations[], thread_id, message_id, workflow_id, is_error }`. Pass `thread_id` back to continue the conversation.

### `search_knowledge`

Semantic (dense-vector) chunk search.

| Input | Type | Notes |
| --- | --- | --- |
| `query` | `str` (1–4000) | Concept-style query. |
| `top_k` | `int` (1–50) | Default 5. |
| `parent_path_part_ids` | `list[UUID]?` | Restrict to descendants of these path-parts. |
| `tag_ids` | `list[UUID]?` | AND-filter on document tags. |

**Returns** `SearchResult{ hits: [ChunkHit] }`. Each hit carries `chunk_id`, `materialized_path`, `text`, `score`, `chunk_type`, `path_part_id`.

### `search_keyword`

BM25 / full-text search. Same inputs as `search_knowledge`. Use for exact terms, identifiers, quoted phrases.

### `read`

Read any PDO and return Markdown text. Dispatches on `part_type`:

- **CHUNK** → raw chunk text + `[chunk:UUID]` tag.
- **SECTION** → section name + page number; tells you to read the parent DOCUMENT for full body.
- **DOCUMENT** → flattened, ordered chunks with section headings, paginated up to `max_chars`.
- **FOLDER / unknown** → name + hint to use `list_contents`.

| Input | Type | Notes |
| --- | --- | --- |
| `path_part_id` | `UUID` | Any PDO id, **or** a chunk_id (auto-fallback on 404). |
| `max_chars` | `int` (100–50_000) | Truncate. Default 4000. |

### `read_around`

Return the `radius` chunks before and after an anchor chunk.

| Input | Type | Notes |
| --- | --- | --- |
| `chunk_id` | `UUID` | Anchor chunk (NOT a path_part_id). |
| `radius` | `int` (0–10) | Default 2. |

Output is ordered preceding → anchor → succeeding, each labelled `[ANCHOR]` or `[ctx ±N]` with its `[chunk:UUID]` tag.

### `cite`

Build a structured citation for a single chunk.

**Returns** `Citation{ chunk_id, document_name, materialized_path, page_number, snippet, tag }`. Page number comes from walking ancestry to the nearest SECTION; degrades gracefully to `None` for non-paginated documents.

### `list_contents`

List the immediate children of a folder.

| Input | Type | Notes |
| --- | --- | --- |
| `folder_id` | `UUID?` | Folder PDO id or path_part_id (both accepted). Omit for tenant root. |

Falls back to root listing on stale folder ids (no dead-end 404s).

### `find`

Fuzzy-search path-parts by name.

| Input | Type | Notes |
| --- | --- | --- |
| `query` | `str` (1–255) | Substring of the path-part's name. |
| `parent_path_part_id` | `UUID?` | Restrict to descendants of this folder. |

For matching the *body* of a document, use `search_keyword` instead — `find` only looks at names.

### `get_info`

Path-part info plus root-to-leaf ancestry. Use to build human-readable paths or resolve a node's type before calling `read`.

### `view_chunk_image`

Fetch image bytes for an IMAGE-type chunk and return them inline (multi-modal frameworks render automatically; text-only frameworks should expect an error and call `read`/`cite` for a textual surrogate).

### `get_organization_info`

Tenant metadata: id, name, default language (ISO-639), timezone (IANA). Cached per process.

### `get_current_datetime`

Current date/time in both UTC and the tenant's timezone. Useful for relative queries ("yesterday's notes", "this quarter's results").

---

## Phase 2 — provenance & explanation

| Tool | Status | Description |
| --- | --- | --- |
| `trace_chunk_lineage` | ✅ shipped | Lineage graph for a chunk (merge / split / re-embed / re-ingest). |
| `compare_versions` | ✅ shipped | Unified text diff between two versions of the same document. |
| `explain_answer_sources` | 🟡 backend-dependent | Given an answer, return the chunks + lineage that grounded it. |
| `verify_document_consistency` | 🟡 backend-dependent | Cross-check a document's sections for internal contradiction. |

## Phase 3 — workflows & audit (planned)

| Tool | Status | Description |
| --- | --- | --- |
| `run_document_workflow` | ⚪ v0.3 | Kick off a Temporal workflow (re-embed, reclassify, translate, …) scoped to a version. |
| `validate_contract_fields` | ⚪ v0.3 | Required-field / type checks against a contract schema. |
| `audit_cross_document_contradictions` | ⚪ v0.4 | Find contradictions across a folder subtree. |

> Writes (ingest / delete / generate) are **not** in Phase 1 or 2. Phase 3 will add admin-scoped write tools behind an explicit `--allow-write` flag and a separate admin key. See [ROADMAP.md](https://github.com/knowledgestack/ks-mcp/blob/main/ROADMAP.md).
