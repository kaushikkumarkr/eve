# Agency MCP

Private, UI-free operating system for an AI advertising agency. Codex and Claude Code operate it through MCP or the CLI; PostgreSQL persists the source of truth; LangGraph coordinates research and campaign workflows.

This repository is intentionally MCP-first. It does not include a web UI, and it does not require advertising spend for local development.

## Safety defaults

- Ads mode is `mock` by default.
- Real Ads API mutations are disabled unless `AGENCY_MUTATIONS_ENABLED=true`.
- Campaign plans are drafts until explicitly approved.
- The first build does not spend advertising budget.
- AI outputs retain evidence, confidence, and provenance fields.
- CRM ingestion excludes direct identifiers by default.
- Policy preflight is conservative and never replaces platform review.

## Local setup

Requirements: Python 3.11+, `uv`, and optionally Docker Desktop for PostgreSQL.

```bash
uv sync --extra dev
uv run agency db-init
uv run agency demo-seed
uv run agency clients
uv run agency research <client-id>
uv run agency plan <client-id>
uv run agency report <client-id>
uv run pytest
```

For a local PostgreSQL instance instead of SQLite:

```bash
docker compose up -d postgres
DATABASE_URL=postgresql+psycopg://agency:agency@localhost:55432/agency uv run agency db-init
```

The default SQLite database is suitable for a quick local run. PostgreSQL on port `55432` is the recommended development path for testing the production-compatible database backend.

## MCP setup

Run the MCP server over stdio for Codex or Claude Code:

```bash
uv run agency-mcp
```

Register it with Codex:

```bash
codex mcp add agency -- uv run agency-mcp
```

Register it with Claude Code:

```bash
claude mcp add agency -- uv run agency-mcp
```

The MCP server exposes read, research, CRM ingestion, policy, measurement, planning, approval, reporting, and Ads tools. Real Ads writes are intentionally blocked unless explicitly enabled.

## Configuration

Copy `.env.example` to `.env` or export variables in the shell. Never commit `.env` or API keys.

- `DATABASE_URL`: SQLite by default; use the documented PostgreSQL URL for the container.
- `OPENAI_API_KEY`: optional; required only for `RESEARCH_MODE=openai`.
- `RESEARCH_MODE`: `heuristic` (default, free) or `openai`.
- `ADS_MODE`: `mock` (default) or `real` for the guarded read adapter.
- `OPENAI_ADS_API_KEY`: separate Advertiser API key; do not use a normal model API key here.
- `AGENCY_MUTATIONS_ENABLED`: keep `false` until real account access, budget, and approval procedures are ready.

The mock Ads adapter never contacts OpenAI and never spends money. Real campaign resources are forced to `paused`, require an approval record, and require an explicit positive budget and bid.

## Project shape

- `agency_mcp/models.py`: persistent entities
- `agency_mcp/service.py`: deterministic agency workflows
- `agency_mcp/workflows.py`: LangGraph orchestration
- `agency_mcp/ads.py`: mock and guarded Ads adapters
- `agency_mcp/connectors.py`: privacy-conscious CRM CSV ingestion
- `agency_mcp/policy.py`: conservative category and creative preflight
- `agency_mcp/measurement.py`: conversion source and event deduplication
- `agency_mcp/reporting.py`: structured and Markdown reports
- `agency_mcp/mcp_server.py`: MCP tool surface
- `agency_mcp/cli.py`: terminal fallback and automation interface
- `tests/`: unit and workflow tests

## Safety and operating model

- Synthetic visibility results are hypotheses, not official OpenAI organic visibility metrics.
- CRM ingestion excludes direct identifiers such as email and phone by default.
- AI outputs retain evidence, confidence, provider, and approval state.
- Campaign mutations use dry-run previews and audit logs.
- The policy preflight is conservative and does not replace OpenAI review.

Run the complete local verification with:

```bash
uv run pytest
```
