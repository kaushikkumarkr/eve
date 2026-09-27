# Eve architecture decision record

## Scope

Eve is only the paid ChatGPT Ads operations control plane. It does not scan or improve AEO/GEO/SEO, generate an organic-visibility score, scrape ChatGPT answers, or operate another advertising platform.

ChatGPT is the operator's reasoning/orchestration layer. Eve is deterministic stateful infrastructure. This avoids a second opaque agent framework and keeps campaign decisions visible in the operator’s conversation and Eve’s database.

For the agency's primary ChatGPT workflow, the ChatGPT conversation may use two separately connected apps: OpenAI's official Ads Manager app and Eve MCP. ChatGPT can orchestrate calls across both, but there is no Eve-to-Ads-Manager server credential bridge. Any Ads Manager result explicitly passed into Eve is stored as operator-supplied; any action log is operator-attested, not platform-verified. The official app connection does not replace Eve's per-client authorization, approvals, or audit records.

## Trust boundaries

```text
Untrusted content (pages, reviews, transcripts)
              │ source ingestion + PII redaction
              ▼
PostgreSQL evidence records ──> Codex / Claude drafts strategy
              │                         │
              │                         └─ no secret tools, no generic HTTP tool
              ▼
Deterministic compiler + lint + policy preflight
              │
              ▼
Immutable change request (payload SHA-256)
              │             │
              │             ├─ recorded client sign-off
              │             └─ hash-bound admin approval
              ▼
Ads executor / Advertiser API
```

No model-generated JSON is posted to OpenAI Ads. The model may propose text and a desired state; deterministic code constructs the payload and refuses invalid shapes.

## State machine

```text
hint set: draft → blocked | ready

blueprint: draft → locally_valid → change_proposed → built_paused
                       └──────→ blocked

change: proposed → platform_validation_pending → platform_validated → client_approved → admin_approved → build_pending → built_paused
                   └──────────→ platform_validation_failed                                └────────→ apply_failed
any unapproved/expired state → not applicable for build
```

Only `build_paused` exists in the implementation. Activation is deliberately outside the current MCP surface until test-account contract validation and a two-person production runbook are complete.

## Durable data

| Record | Why it exists |
| --- | --- |
| `clients`, `client_access_grants` | Tenant boundary and named operator membership. |
| `source_documents`, `evidence` | Redacted source, provenance, review decision. |
| `ads_workspaces` | Account metadata and observed state, never credentials. |
| `hint_sets` | Complete versioned context-hint list, rationale, lint, evidence IDs. |
| `campaign_blueprints` | Agent/human desired state and compiled exact payload. |
| `change_requests` | Immutable payload, hash, client approval, admin approval, expiry, result. |
| `ads_insight_snapshots` | Reproducible metric request/response plus freshness status. |
| `ads_reports` | Immutable report JSON/Markdown, content hash, readiness state, reviewer, and optional sent-reference audit. |
| `controlled_experiments` | Pre-registered controlled comparison and decision rule. |
| `audit_logs` | Append-only hash-linked operations history. |

PostgreSQL is the only shared source of truth. MCP clients never connect directly to it.

`ads_manager_connections` records the operator and account ID they attested to in the separate official ChatGPT Ads Manager app. `ads_external_actions` is append-only operational history for human-performed app actions. `ads_import_records` hashes and deduplicates submitted aggregate reports; each resulting `ads_insight_snapshots` row retains the provider label `chatgpt_ads_manager_operator_import` and operator-reported freshness. None of these records proves the upstream action or metric is authentic; review the source in Ads Manager and preserve its supplied file/reference.

## Credentials

Development uses Fernet encryption with a host-local `AGENCY_MASTER_KEY`. Shared Azure uses Key Vault through `EVE_SECRET_BACKEND=azure_key_vault`. PostgreSQL stores a Key Vault locator only; it never stores Azure Key Vault plaintext.

The safe production design separates the MCP/control identity from the executor identity:

- Control service: database access and no permission to list/read client Ads secrets.
- Executor/worker: can read a selected client key at operation time and has no public MCP endpoint.
- Key Vault: grants secret read only to the executor managed identity.

The local implementation remains a monolith for zero-cost rehearsal, so do not treat a developer laptop as production secret isolation.

Real Ads calls require an explicit `client_id` plus a matching client-scoped secret-store entry. A process-wide Ads key is not supported as a fallback. This is intentional: a missing client credential must fail closed rather than risk using another advertiser's account.

## Roles

- `operator`: only explicitly assigned client workspaces; can ingest, draft, compile, validate, read reports, and propose changes.
- `reviewer`: reserved for future report/evidence review delegation; no spend ability.
- `admin`: creates client records and assignments; records evidence decisions and client sign-off; provides hash-bound final approval; may submit an already approved paused build.

Roles are enforced server-side. Tool descriptions help the model, but they are not the security control.

## Context-hint rules

- A hint set belongs to one intended ad-group strategy and is versioned as a full list because the Ads API replaces the list on update.
- Hints describe product, use case, or need. Geography, platform constraints, and audiences belong to campaign targeting.
- Hint performance cannot be attributed to individual conversations because the documented reporting interface does not expose prompts that triggered ads. Interpret results at the configured campaign/ad-group level only.
- A linter blocks delivery guarantees and warns on likely audience/geography text. It is not platform policy approval.

## Reporting and comparisons

Snapshots preserve raw provider output, timezone, requested fields, and freshness. `mock` is never performance. New live delivery metrics are labelled `unsettled`; the worker should refresh the trailing conversion window before final reporting.

Generated reports are persisted as immutable point-in-time snapshots. Admin review binds to the report content hash; only a snapshot with `reviewable` readiness can become `client_ready`. A subsequent `sent` record stores who shared it and an external reference, but does not send email or message the client.

Eve calls tests “controlled comparisons,” not “A/B tests.” Each requires exactly two arms, one changed variable, primary KPI, guardrails, attribution, timeframe, and decision rule. The current public Ads documentation does not define a native randomized experiment endpoint.

## Deliberate non-features

- LangGraph or server-side multi-agent orchestration
- Client-facing dashboard
- Autonomous optimization, bid management, activation, or budget changes
- Generic Ads API request tool
- Persist raw provider error bodies or exception messages in executor jobs
- Audience uploads and real-time CAPI relay
- Product-feed automation until a client has a qualifying catalog and a reviewed account capability
- Cross-client data sharing or model fine-tuning

Persisted executor and workflow failures visible to MCP store only a bounded
exception type and a generic withheld-details message. Provider response bodies
and exception text are intentionally not echoed into durable job state, CLI
output, or tool errors because those fields may contain credentials or client
data. Diagnose failures from provider-side request IDs and private operational
telemetry rather than copying raw responses into agent-visible records.

Database migrations are an explicit admin/deployment operation (`agency db-upgrade`). The MCP control service, executor, and ordinary operational CLI commands perform a read-only Alembic-head check and fail with a migration instruction if the schema is behind. Runtime service identities never execute DDL or race to upgrade the schema during startup.
