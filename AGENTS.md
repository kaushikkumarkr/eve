# Agency MCP Operating Contract

This repository is a private, zero-spend agency operations system.

## Required behavior

- Use the MCP tools and CLI as the primary interface.
- Keep PostgreSQL/SQLite records as the source of truth; do not rely on chat history.
- Do not create, store, or present synthetic visibility results, organic ranking claims, competitor-presence claims, or prompt-level match claims; they are outside this Ads-only system.
- Preserve evidence, provider, freshness, payload-hash, and approval state for every client-facing output.
- Default to read-only, dry-run, and paused campaign behavior.
- Never activate a campaign, upload an audience, send conversion data, or spend budget without explicit approval.
- Never print or commit API keys.
- Keep client data tenant-scoped and authorized.
- Store client Ads and Conversions API keys only through the encrypted secret-store CLI or Azure Key Vault backend; never pass them through an LLM prompt or MCP result.
- Operators may draft and preview; only the admin role may approve or apply campaign changes.
- Never expose streamable HTTP MCP directly to the public Internet; use a private authenticated gateway.
- Redact common direct identifiers at ingestion and preserve source hashes and redaction metadata.
- Codex/Claude may draft strategy, but deterministic code alone compiles Ads payloads; never post model-generated JSON to the Ads API.
- Use the persisted client profile to scope ICP, markets, products, goals, and exclusions before drafting hints or creative.
- Preserve source URL/retrieval metadata where available; require human evidence review before a report can be marked client-ready.
- Treat paid “A/B tests” as controlled campaign comparisons unless the platform supplies a documented randomized split. Pre-register one changed variable, primary KPI, guardrails, timeframe, attribution setup, and decision rule; never declare a winner from mock, partial, or incomparable data.
- In shared operation, MCP clients must use the authenticated Eve service and must never connect directly to the production database or exchange SQLite copies.

## Safe workflow

1. Inspect existing records.
2. Confirm the client profile and authorized source scope.
3. Draft a versioned context-hint set and campaign blueprint from reviewed evidence.
4. Compile locally and inspect the persisted preflight job plus exact payload hash.
5. Run platform `validate_only` when a test/client account is authorized.
6. Record client written approval and matching admin approval.
7. Apply only the immutable approved paused payload.
8. Record the outcome in the audit log and report provider/freshness limitations.
