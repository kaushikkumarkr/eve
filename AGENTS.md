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

## Safe workflow

1. Inspect existing records.
2. Run research or planning workflows.
3. Validate the result.
4. Show a dry-run payload.
5. Request explicit approval.
6. Apply only the approved action.
7. Record the outcome in the audit log.
