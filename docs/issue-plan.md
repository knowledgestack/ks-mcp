# Plan for the 19 open issues

Written 2026-11-09, against `main` at 0.3.0 and `ksapi` 3.26.6.

Every "blocked" claim below was checked against the installed SDK surface, not
against the issue text. That matters: the tracker has been wrong twice
already — #25 was filed as needing a backend route that had since shipped, and
#17 was open after `search_hybrid` was released. **Six more issues turn out to
be mis-triaged the same way.**

## 1. The headline: most of the "blocked" work isn't

| # | Issue | Previously assumed | Actual | Evidence |
| --- | --- | --- | --- | --- |
| 8 | `ingest_document` | blocked on write design | **ready** | `DocumentsApi.ingest_document(file, path_part_id, name, tag_ids, idempotency_key, …)` — returns 201 + Temporal workflow id |
| 9 | `ingest_url` | blocked | **ready, with a design call** | no crawl route exists; fetch client-side, then reuse #8 |
| 10 | `delete_document` / `delete_folder` | blocked | **ready** | `DocumentsApi.delete_document`, `FoldersApi.delete_folder` (both → trash), `TrashApi.permanently_delete_trash_item` (hard) |
| 11 | `reembed` | blocked | **ready** | `SystemJobsApi.dv_workflow_rerun`, `bulk_rerun_dv_workflows` |
| 26 | `run_document_workflow` | blocked | **ready, reshaped** | `WorkflowDefinitionsApi.create_workflow_run(definition_id, …, auto_start, idempotency_key)` + `WorkflowRunsApi.start_workflow_run` |
| 12 | Audit log for writes | blocked | **ready, re-scope** | backend already audits (`AuditEventsApi.list_audit_events`); ks-mcp needs only a local sink |
| 18 | `summarize_document` | blocked on backend | **ready via the agent** | no summarize route, but the KS agent already summarizes with citations — compose over `ask` |
| 14 | Per-tool rate limits | roadmap | **ready** | local token bucket; `TenantsApi.get_tenant_quota_state` for upstream quota |
| 15 | OpenTelemetry traces | roadmap | **ready** | pure instrumentation |
| 16 | Multi-tenant gateway | roadmap | **ready, needs a refactor first** | blocked by our own `@lru_cache` singleton in `client.py`, not by the API |
| 20 | Citation eval harness | roadmap | **ready** | self-contained |
| 27 | `validate_contract_fields` | blocked | **partial** | field extraction+typecheck is doable via `extract` + JSON Schema; the *schema registry* half needs backend |

**Genuinely blocked on upstream ks-backend work** — confirmed absent from the SDK:

| # | Issue | Missing |
| --- | --- | --- |
| 3 | OAuth 2.1 device flow | no device-authorization endpoint and no `/.well-known` metadata. `AuthApi` has `pw_signin`, `sso_signin`, `initiate_sso`, `oauth2_callback`, `refresh_uat` — authorization-code shaped, not device-code |
| 19 | `rerank_chunks` | no rerank route anywhere in `ksapi` |
| 23 | `explain_answer_sources` | no `/v1/explain` route |
| 24 | `verify_document_consistency` | no server-side consistency pass |
| 28 | `audit_cross_document_contradictions` | needs a cross-document reasoning service |

**Not code at all:**

| # | Issue | Gate |
| --- | --- | --- |
| 7 | MCP registry listing | needs a PyPI release. Blocked on a *pending publisher* being created at pypi.org — see §6 |
| 22 | LTS branch + semver guarantees | a 1.0 decision, not a task. Do last |

## 2. The leverage: two foundations collapse seven issues

Nine of the ready issues are not nine pieces of work. They share two
substrates, and building each once turns the rest into small leaves.

### Foundation A — the write-safety substrate

#8, #9, #10, #11 all need the same thing before any of them is safe, and #12
*is* part of it:

- an `--allow-write` CLI flag, default off, so a read-only deployment can't be
  talked into mutating anything by a prompt-injected document;
- admin-scope detection, so the flag alone isn't the only gate;
- write tools registered **conditionally**, so they don't even appear in
  `tools/list` when writes are off — a tool an agent can't see is one it can't
  be tricked into calling;
- the audit sink from #12, written *before* the first write tool, not after.

Build A once → #8, #9, #10, #11 are each a thin wrapper.

**Two prerequisites the issues don't mention**, both found in the SDK docstrings:

- `delete_document` *requires an active document checkout held by the caller*.
  #10 therefore has to acquire → delete → release via
  `DocumentCheckoutApi.{acquire,release}_document_checkout`, and release in a
  `finally`, or a failed delete leaves the document checked out and
  undeletable by anyone else.
- Soft vs hard delete maps exactly onto two different calls:
  `delete_document`/`delete_folder` move to trash;
  `TrashApi.permanently_delete_trash_item` is the irreversible one. The issue's
  `--hard` flag should select between them, not pass a parameter.

**#26 needs reshaping from what the issue describes.** It assumes a fixed set
of workflow types ("re-embed, reclassify, translate, extract contracts") behind
one `WorkflowsApi`. The real surface is a *definition registry*:
`create_workflow_run` takes a `definition_id` that varies per tenant. So the
tool cannot hard-code a type enum, and an agent given only
`run_document_workflow` has no way to discover a valid `definition_id` — it
would have to guess a UUID. Expose `list_workflow_definitions` alongside it, or
#26 ships unusable. Also pass `idempotency_key`: without it an agent retry
starts the workflow twice.

### Foundation B — the tool-call middleware

#14, #15, #16 are all "do something around every tool call". Today each tool is
registered independently with no interception point. Add one wrapper applied at
registration, then:

- **#15** emits an OTel span per call (tool name, tenant, upstream timing, result size);
- **#14** checks a token bucket keyed `(api_key, tool)` and returns an MCP error carrying `retry_after`;
- **#16** resolves a *per-request* client instead of the process singleton.

**#16 has a real prerequisite in our own code:** `client.py` caches one
`ApiClient` per process via `@lru_cache(maxsize=1)` with the key baked in at
construction. Multi-tenant mode needs a client per request, and "no shared
caches across tenants" also means `org.py`'s `_cached_tenant` module global has
to go. Do that refactor first or #16 will leak one tenant's data into another's
response — the one failure mode here that is a security bug, not a bug.

### Ordering constraint worth respecting

**#20 before #18.** #18's own acceptance criterion is "must pass our
citation-quality eval harness", which is #20. Building #18 first means having
no way to tell whether it works.

## 3. Sequence

Each phase is independently shippable; nothing here needs a big-bang release.

**Phase 1 — foundations (unblocks 7 issues)**
1. Tool-call middleware (Foundation B) + retire the client/tenant singletons.
2. Write-safety substrate (Foundation A) + **#12** audit sink.

**Phase 2 — the leaves, in dependency order**
3. **#15** OTel spans *(S)* — first consumer of the middleware, proves it.
4. **#14** rate limits *(S)*.
5. **#8** `ingest_document` *(M)* — pairs with the shipped `get_workflow_status` for polling.
6. **#9** `ingest_url` *(M)* — reuses #8. Design call: fetch client-side with
   robots.txt + size caps, since no backend crawler exists.
7. **#10** `delete_document` / `delete_folder` *(M)* — checkout dance + trash/hard split.
8. **#11** `reembed` *(S)* — `dv_workflow_rerun` / `bulk_rerun_dv_workflows` over a path subtree.
9. **#26** `run_document_workflow` + definition discovery *(M)* — general case of
   #11; surfaces the run id for the already-shipped `get_workflow_status`.
10. **#16** multi-tenant gateway *(L)* — last of the middleware set; needs the refactor done.

**Phase 3 — retrieval quality**
11. **#20** citation eval harness *(M)* — small labelled set, fails CI on >2pp precision regression.
12. **#18** `summarize_document` *(M)* — compose over the KS agent; gated by #20.
13. **#27** `validate_contract_fields` *(M, partial)* — ship the `extract`-based
    validator; defer the schema registry to backend.

**Phase 4 — 1.0**
14. **#7** registry listing — as soon as PyPI is live (§6).
15. **#22** LTS + semver — once the tool surface is frozen. `schemas/` already
    gives this its mechanism: the published per-tool schemas plus the CI drift
    guard are what a semver promise would be enforced against.

**Upstream track, in parallel** — #3, #19, #23, #24, #28 (§5).

## 4. What each ready issue needs, concretely

| # | Entry point | Shape |
| --- | --- | --- |
| 8 | `DocumentsApi.ingest_document` | pass `idempotency_key` so an agent retry can't double-ingest; return the workflow id for `get_workflow_status`. Use `create_document_upload`/`upload_document_part`/`complete_document_upload` for large files |
| 9 | client-side fetch → #8 | enforce robots.txt, a byte cap, and a content-type allowlist *before* the fetch completes |
| 10 | `delete_document`, `delete_folder`, `TrashApi` | acquire/release checkout; `--hard` selects permanent delete |
| 11 | `SystemJobsApi.bulk_rerun_dv_workflows` | resolve the subtree to version ids, then bulk rerun; report per-item results |
| 26 | `WorkflowDefinitionsApi` + `WorkflowRunsApi` | expose `list_workflow_definitions` for discovery; `idempotency_key` on create; return the run id |
| 12 | local JSON sink | who, what, tenant, args hash (never raw args — they contain document text), upstream workflow id |
| 14 | middleware | token bucket; MCP error with `retry_after` |
| 15 | middleware | spans; exporter from env |
| 16 | middleware + refactor | per-request auth over Streamable HTTP; no cross-tenant caches |
| 18 | compose over `ask` | Markdown + `[chunk:UUID]` tags; must pass #20 |
| 20 | new `evals/` | labelled Q→expected-chunk set; precision/recall; CI gate |
| 27 | `extract` + JSON Schema | per-field present/absent + type match; registry deferred |

## 5. Backend contracts to request (the 5 truly blocked)

These are blocked *only* because an endpoint doesn't exist. Each needs a
ks-backend ticket stating the contract ks-mcp will consume, so the work can be
specified rather than discovered:

- **#19** `POST /v1/chunks/rerank` — `{query, chunk_ids[]}` → reordered ids + scores.
  Highest value per unit of backend work: it makes every existing search tool better.
- **#23** `POST /v1/explain` — `{answer, thread_id?}` → grounding chunk ids + lineage.
- **#24** `GET /v1/document_versions/{id}/consistency` — cached LLM pass per version.
- **#28** cross-document contradiction graph — a service, not a route. Largest; schedule last.
- **#3** device-authorization + token endpoints, plus
  `/.well-known/oauth-authorization-server`.
  **Cheaper alternative worth considering:** `initiate_sso` + `oauth2_callback`
  already exist, so an authorization-code + PKCE flow is reachable *today* and
  satisfies the issue's actual acceptance criteria ("starts without
  `KS_API_KEY`", "Inspector shows an authenticated session") without any
  backend change. Device flow is the nicer UX; PKCE is the shippable one.

## 6. #7 is one click away

The release pipeline works end to end and has been exercised: tag `v0.3.0`
fired `release.yml`, which built and attempted the upload. It failed with:

```
invalid-publisher: valid token, but no corresponding publisher
```

PyPI has no trusted publisher for this project yet. Create a **pending
publisher** at <https://pypi.org/manage/account/publishing/> (the *account*
sidebar — the project doesn't exist yet) with exactly the claims the failed run
reported:

| Field | Value |
| --- | --- |
| PyPI Project Name | `knowledgestack-mcp` |
| Owner | `knowledgestack` |
| Repository name | `ks-mcp` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

Then re-run the failed job; the tag already exists. A pending publisher does
not reserve the name, and `knowledgestack-mcp` is currently unclaimed.

Once PyPI is live, #7's checklist is: `server.json` already validates against
the registry schema, the README already has multi-client quickstarts, and
`release.yml` already runs `mcp-publisher publish` after a successful upload —
so #7 largely completes itself.

## 7. Suggested release mapping

| Release | Contents |
| --- | --- |
| 0.4.0 | Foundation B, #15, #14 |
| 0.5.0 | Foundation A, #12, #8, #9, #10, #11, #26 |
| 0.6.0 | #20, #18, #27 (partial) |
| 0.7.0 | #16 |
| 1.0.0 | #7, #22, plus whichever of #19/#23/#24 the backend has landed |

## 8. Two risks to name up front

- **Write tools are the prompt-injection surface.** Every tool in #8–#11
  mutates a tenant's corpus, and the arguments come from a model that may have
  just read an untrusted document. `--allow-write` defaulting off, conditional
  registration, and the #12 audit log are the mitigations — which is why
  Foundation A is one unit of work and not four.
- **#16 is the one with a security failure mode.** Cross-tenant cache leakage
  is a data breach, not a bug. Retiring `client.py`'s `@lru_cache` singleton and
  `org.py`'s `_cached_tenant` is a hard prerequisite, and the tests for it
  should assert isolation directly rather than inferring it.
