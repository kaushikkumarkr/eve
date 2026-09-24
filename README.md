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
uv run agency workspace <client-id>
uv run agency service-run <client-id>
uv run agency secret-list <client-id>
uv run agency ads-account <client-id>
uv run pytest
```

For a local PostgreSQL instance instead of SQLite:

```bash
docker compose up -d postgres
DATABASE_URL=postgresql+psycopg://agency:agency@localhost:55432/agency uv run agency db-init
```

The default SQLite database is suitable for a quick local run. PostgreSQL on port `55432` is the recommended development path for testing the production-compatible database backend.

For a shared private host using Docker, keep the host's `.env` out of Git and run:

```bash
docker compose up -d postgres
docker compose --profile shared up -d --build agency
```

The `agency` service listens on port `8000` using authenticated Streamable HTTP. Set both role tokens and `AGENCY_MCP_PUBLIC_URL` in the host `.env`. Put the host behind Cloudflare Access/private networking; do not port-forward the MCP service directly.

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
- `OPENAI_API_KEY`: optional standard OpenAI key; used for `RESEARCH_MODE=openai` when Azure is not configured.
- `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_DEPLOYMENT`: optional Azure OpenAI configuration for research. The endpoint is normalized to the Azure resource root automatically, so copied deployment paths are safe.
- `RESEARCH_MODE`: `heuristic` (default, free) or `openai`.
- `ADS_MODE`: `mock` (default) or `real` for the guarded read adapter.
- `OPENAI_ADS_API_KEY`: separate Advertiser API key; do not use a normal model API key here.
- `AGENCY_MUTATIONS_ENABLED`: keep `false` until real account access, budget, and approval procedures are ready.

The mock Ads adapter never contacts OpenAI and never spends money. Real campaign resources are forced to `paused`, require an approval record, and require an explicit positive budget and bid.

To use Azure OpenAI for research, set the four Azure variables in `.env` and run commands with `RESEARCH_MODE=openai`, for example:

```bash
RESEARCH_MODE=openai uv run agency research <client-id>
```

## Two-person internal access and encrypted client keys

For a small team, keep two roles:

- `admin`: can approve and apply campaign changes.
- `operator`: can ingest data, research, draft, validate, preview, and report.

Set `AGENCY_ROLE=admin` or `AGENCY_ROLE=operator` in each operator's local environment. Generate one host encryption key once and keep it outside Git:

```bash
uv run agency secrets-keygen
# place the output in AGENCY_MASTER_KEY in the host .env
uv run agency secret-set <client-id> OPENAI_ADS_API_KEY
uv run agency secret-set <client-id> OPENAI_CONVERSIONS_API_KEY
uv run agency secret-list <client-id>
```

Only encrypted ciphertext is stored in PostgreSQL. The plaintext key is never returned by an MCP tool or report. Keep the master key in the host environment and back it up separately from the database.

The lowest-cost shared setup is one always-on agency computer running PostgreSQL and the MCP server, with team access through a private network or authenticated tunnel. Keep MCP on `stdio` for local Codex/Claude Code. For a remote MCP endpoint, set `AGENCY_MCP_TRANSPORT=streamable-http`, configure both role tokens, and put it behind Cloudflare Access or another authenticated private gateway. Do not expose the unauthenticated HTTP transport directly to the Internet.

Cloudflare Tunnel is available on all Cloudflare plans, but stable protected access requires a properly configured domain and Access policy; quick tunnels are for testing, not production. ([Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/), [Cloudflare Access policies](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/))

## Recommended service workflow

1. Create a client workspace with `client-create` or `create_client_tool`.
2. Add ICP, products, markets, goals, and constraints with `client-profile` or `update_client_profile_tool`.
3. Ingest authorized transcripts, CRM exports, reviews, surveys, and website notes.
4. Run `service-run` or `run_service_package_tool`. This extracts buyer segments, generates questions, runs simulated visibility research, identifies gaps, drafts context hints, creates a campaign plan, validates it, and produces a dry-run preview.
5. Review the evidence, policy status, hints, landing page, and budget with the client.
6. Request and record explicit approval. Keep Ads in mock mode while training the service process.
7. Generate the client report and sync only read-only insights until official account access and measurement are ready.

Every research result is labeled as simulated or hypothesized unless supported by connected client-side measurement. Source content is hashed, common direct identifiers are redacted by default, and workflow jobs are persisted for operational review.

Context hints are relevance descriptions at the ad-group level, not keyword bids, exclusions, or delivery guarantees. Use platform targeting controls for geography and other restrictions, and keep separate ad groups for different products, use cases, and landing pages.

## Live Ads and measurement readiness test

The repository can fully test the local contract without spending money. Live account verification requires a client-created Ads account and an account-scoped Advertiser API key. Store it locally with `secret-set`, set `ADS_MODE=real`, and run:

```bash
ADS_MODE=real uv run agency ads-account <client-id>
```

This performs only `GET /v1/ad_account`. It does not create a campaign. The official API uses the account hierarchy `campaign → ad group → ad`; creation endpoints support idempotency keys and paused resources. ([Advertiser API overview](https://developers.openai.com/ads/api-overview))

For measurement, OpenAI uses a Pixel ID plus a separate Conversions API key. The MCP tool `check_conversion_batch_tool` performs local validation by default and can use the official `validate_only` request after the client key is stored. Use the same event ID across browser and server integrations for deduplication. ([Conversion tracking](https://developers.openai.com/ads/conversion-tracking), [Conversions API](https://developers.openai.com/ads/conversions-api))

## Production-readiness boundary

This repository is ready for zero-spend internal service delivery and client reporting. Before real spend, add the official Ads account credentials and documented API contract, verify policy and measurement requirements with the client, complete a human approval, and enable mutations only in a controlled environment. The real adapter remains paused-first and idempotency-protected; the default mock adapter cannot spend money.

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
