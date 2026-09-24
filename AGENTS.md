# Agency MCP Operating Contract

This repository is a private, zero-spend agency operations system.

## Required behavior

- Use the MCP tools and CLI as the primary interface.
- Keep PostgreSQL/SQLite records as the source of truth; do not rely on chat history.
- Treat synthetic visibility results as hypotheses, never as official OpenAI metrics.
- Preserve evidence, confidence, provider, and approval state for AI outputs.
- Default to read-only, dry-run, and paused campaign behavior.
- Never activate a campaign, upload an audience, send conversion data, or spend budget without explicit approval.
- Never print or commit API keys.
- Keep client data tenant-scoped and authorized.
- Redact common direct identifiers at ingestion and preserve source hashes and redaction metadata.
- Treat Azure/OpenAI outputs as generated research; retain provider and job status and never present simulation as platform reporting.
- Use the persisted client profile to scope ICP, markets, products, goals, and exclusions before drafting hints or creative.

## Safe workflow

1. Inspect existing records.
2. Confirm the client profile and authorized source scope.
3. Run research or planning workflows.
4. Validate the result and inspect the persisted workflow job.
5. Show a dry-run payload.
6. Request explicit approval.
7. Apply only the approved action.
8. Record the outcome in the audit log and report measurement limitations.
