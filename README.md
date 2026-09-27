# Eve — private ChatGPT Ads control plane

Eve is an internal, MCP-first system for running ChatGPT Ads as an agency service. ChatGPT is the team's reasoning/orchestration surface; Eve provides the durable client record, deterministic validation, approval controls, and safe Ads API boundary.

The intended ChatGPT workflow uses OpenAI's official Ads Manager app and Eve MCP as separate apps. Eve has no direct server-side connection to the Ads Manager app. Actions/metrics relayed into Eve are labeled operator-reported unless Eve obtained them from the Ads API. The former Azure staging deployment was retired on 2026-09-27; there is currently no live Eve MCP endpoint. See [Azure status and redeployment](#azure-status-and-redeployment) and the [Ads Manager handoff guide](docs/MCP_OPERATOR_GUIDE.md#chatgpt-team-workflow-after-eve-oauth-is-configured).

It is deliberately **not** an AEO/GEO tool, client dashboard, keyword platform, bid bot, visibility-score product, or generic Ads API proxy.

## Azure status and redeployment

The former Eve staging service was stopped and its dedicated Azure resource group was submitted for deletion on 2026-09-27. The old MCP URL is no longer usable; the old shared bearer tokens must not be reused. No production client workspace was present in the last Codex smoke test. No client data or Key Vault secret values were exported for migration; provision fresh credentials in the new environment.

The repository contains parameterized Azure Bicep templates, Alembic database migrations, container build files, operator/authentication guides, and a migration checklist. All five Bicep templates compile. These are deployment building blocks, **not yet a verified one-command deployment to a different Azure tenant**: the new tenant still needs its own Entra app/roles, private access route, managed-identity role assignments, Key Vault configuration, image registry/build, and secure runtime database bootstrap.

Before redeploying, follow [the Azure deployment guide](docs/AZURE_DEPLOYMENT.md), [shared deployment contract](docs/SHARED_DEPLOYMENT.md), and [Azure template notes](infra/azure/README.md). Do not deploy the old static-token staging configuration. The raw MCP endpoint must remain behind a private authenticated gateway; the `mcp-host.bicep` public-ingress pattern must not be used as-is. Confirm the new private route and Entra authorization with read-only Codex smoke tests before loading any client data or keys.

## What Eve does

- Keeps a tenant-scoped PostgreSQL record of client scope, source evidence, hint sets, blueprints, approvals, metric snapshots, experiments, and audit events.
- Stores versioned Context Hint sets linked to source evidence. Hints remain relevance descriptions, not keywords, audience targeting, geography, or a delivery guarantee.
- Compiles a human/agent-authored campaign blueprint into a deterministic, paused OpenAI Ads bulk-validation payload.
- Binds a client sign-off and admin approval to the SHA-256 of that exact immutable payload.
- Defaults to mock mode. A real account can be tested with `validate_only` before any resource is created.
- Preserves reporting snapshots with provider, timezone, fields, raw response, and freshness labels.
- Pre-registers controlled two-arm comparisons. They are never described as native randomized A/B tests.

## What Eve never does

- Claim that it measured organic ChatGPT recommendations, conversation-level matching, competitor presence, or “AI visibility.”
- Expose or accept Ads/CAPI keys through MCP, reports, source ingestion, or chat prompts.
- Activate an ad, upload an audience, send conversion data, or spend money by default.
- Let an LLM emit the JSON sent to the Ads API.
- Expose PostgreSQL, allow unauthenticated MCP requests, or expose the raw Streamable HTTP MCP service directly to the public Internet. Shared deployments must use a private authenticated gateway.

The implementation follows the current official OpenAI Ads model: an account contains campaigns, campaigns contain ad groups, and ad groups contain ads; context hints are ad-group data, and OpenAI instructs advertisers to create resources paused before activation. [Ads API overview](https://developers.openai.com/ads/api-overview) · [Campaign management](https://developers.openai.com/ads/campaign-management)

## Architecture

```text
ChatGPT (official Ads Manager app + Eve MCP)
        │ app orchestration; no server-to-server Ads Manager bridge
        ▼
Eve control service ── PostgreSQL (source of truth)
        │                    │
        │                    └─ evidence, blueprints, approvals, audit chain,
        │                       snapshots, experiments; never plaintext secrets
        ▼
Secret backend ────── local Fernet (development) / Azure Key Vault (shared)
        │
        ▼
OpenAI Ads API — optional account-scoped integration; validate-only first, paused build only
```

Read [the architecture](docs/ARCHITECTURE.md), [MCP operator guide](docs/MCP_OPERATOR_GUIDE.md), and [Azure deployment guide](docs/AZURE_DEPLOYMENT.md) before shared use.

## Local setup — no Ads key or spend required

```bash
uv sync --extra dev
cp .env.example .env
uv run agency db-init
uv run pytest
```

Use PostgreSQL for any shared or long-running work. SQLite is for isolated local rehearsal only.

```bash
export AGENCY_POSTGRES_PASSWORD='long-url-safe-password'
docker compose up -d postgres
DATABASE_URL="postgresql+psycopg://agency:${AGENCY_POSTGRES_PASSWORD}@127.0.0.1:55432/agency" \
  uv run agency db-init
```

## Azure status and team access

There is no active shared Eve endpoint at present. When redeploying, each teammate must use their own agency identity, and the client must separately grant that teammate the appropriate role in the client's Ads account. Do not use shared static bearer tokens for team or client work. Codex connection instructions must be generated from the new deployment's private gateway URL and authentication configuration; see [the deployment guide](docs/AZURE_DEPLOYMENT.md).

### ChatGPT Ads Manager is separate

The official ChatGPT Ads Manager app is independent of Eve. The client account owner must invite agency operators to the advertiser account. Eve's `ads_manager_access_record` is an operator attestation, not a live platform check. To connect Eve itself to ChatGPT's custom MCP app, configure a supported OAuth flow; no active Eve endpoint or OAuth connection is currently deployed.

### Optional ChatGPT OAuth setup (not active in staging)

The following OAuth setup notes describe a future configuration. Hosting Eve does not require an OpenAI API key.

#### Legacy ChatGPT OAuth requirements (not current Codex setup)

- Each teammate uses their own ChatGPT account and creates their own developer-mode app; without a shared ChatGPT workspace, app setup and tool review are per person, not centrally published for the team.
- An Azure subscription, an Entra tenant where agency teammates can sign in, and permission to register/configure the Eve API application and assign app roles.
- Eve deployed as a persistent Streamable HTTP MCP service behind Azure-hosted authentication. PostgreSQL and Key Vault remain private; the public HTTPS route must reject unauthenticated requests and enforce the Eve operator/admin roles.
- An OAuth authorization server compatible with ChatGPT's MCP flow. For the free recommended setup, configure Auth0 federated to the agency Entra tenant, then assign each teammate an Eve role and client workspaces in Eve with `client_access_grant`.
- The client separately owns its ChatGPT Ads account and invites each agency operator who needs Ads access. ChatGPT workspace membership alone does not grant Ads account access.

#### Optional ChatGPT OAuth flow (not currently deployed)

1. Follow [Azure deployment](docs/AZURE_DEPLOYMENT.md) and [shared deployment/authentication](docs/SHARED_DEPLOYMENT.md). Deploy the private authenticated gateway, PostgreSQL, and Key Vault. Keep mutations disabled and Ads mode set to `mock` for the first connection test. Eve hosting does not require an OpenAI API key or tunnel credential.
2. Configure Codex for the new deployment's identity provider and private gateway. For a remote MCP app in ChatGPT web, follow [ChatGPT OAuth setup](docs/CHATGPT_OAUTH.md) only after implementing and testing an OAuth authorization server.
3. If configuring ChatGPT web, open **Settings → Security and login → Developer mode**, then open the custom MCP app creation screen. Use these app details:

   - **Name:** `Eve Ads Operations`
   - **Description:** `Private, Ads-only agency operations. Helps manage client records, review approved evidence, draft context hints and campaign plans, and track approvals and metrics. Does not promise ad placement or independently authorize campaign spend.`
   - **Connection:** choose the remote MCP server URL/HTTPS option.
   - **Server URL:** enter the deployed Eve HTTPS MCP endpoint, ending in `/mcp`.
   - **Authentication:** choose **OAuth** after the Auth0 MCP provider and Entra enterprise connection are configured; use supported registration/discovery rather than the direct Entra OAuth client values.
   - **Icon:** optional; skip during initial setup.

4. Scan the tools and review every action before creating the app. Enable only the tools required for the operator's role. Keep any activation, audience-upload, conversion-send, or spend-changing operation disabled; Eve's standard workflow must keep campaign builds paused and require the documented client/admin approvals. Start with harmless reads such as `clients_list` and `workspace_readiness`, then test an evidence-backed draft. Confirm that an operator cannot access an unassigned client.
5. Each teammate connects the app in their own ChatGPT account and signs in with their agency identity. Give each person only their required Eve client grants. Personal-account apps are not centrally published to coworkers like workspace apps, so teammates may need to add the connection individually.
6. Separately install/connect **ChatGPT Ads Manager** in ChatGPT. The client account owner invites each operator to the advertiser account; give write-capable Ads roles only to designated operators and Viewer access to other staff. In a new chat, select both Eve and ChatGPT Ads Manager. Use Eve for agency records and approved drafts; use the official Ads Manager app for account-facing work. Eve's `ads_manager_access_record` is an operator attestation, not a live platform check.

OpenAI documentation currently differs on plan eligibility: the [Developer Mode API docs](https://developers.openai.com/api/docs/guides/developer-mode) list Plus and Pro and describe read/write MCP tools, while the [Help Center article](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt) describes full write support as rolling out to Business, Enterprise, and Edu and says Pro is read/fetch-only. The app-creation screen confirms that an individual account can create an app, but tool scan and a harmless test call are still required to verify which actions that account can use. See also OpenAI's [Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), [Ads account roles](https://help.openai.com/en/articles/20001273-managing-identity-and-access-for-ads-manager), and [Ads Manager account setup for agencies](https://help.openai.com/en/articles/20001213-ads-manager-beta-account-setup).

#### Codex with Entra after redeployment

Use only after a new Entra API and private authenticated gateway are configured as described in [shared deployment/authentication](docs/SHARED_DEPLOYMENT.md). Replace every placeholder with values from the new deployment; never copy the former tenant ID or public staging URL.

After invitation redemption, sign in to the agency tenant and consent to Eve's delegated `operator` scope:

```bash
az login --tenant <tenant-guid> \
  --scope '<private-gateway-url>/operator'
```

In `~/.codex/config.toml`, configure the Eve server and the local helper (replace the path with the absolute path to this checkout):

```toml
[mcp_servers.eve]
url = "<private-gateway-url>/mcp"
http_headers_helper = "python3 /absolute/path/to/eve/scripts/codex_azure_mcp_headers.py <tenant-guid> <private-gateway-url>/operator"
```

Remove any saved OAuth credential for this server because Codex gives saved OAuth credentials precedence over the helper, then restart Codex:

```bash
codex mcp logout eve
codex mcp get eve
```

The helper runs `az account get-access-token` on the operator's machine and returns the short-lived token only to Codex; it does not save or print tokens to Eve's database. Verify the connection by asking Codex to list Eve's tools, then try only a read-only action. If Azure sign-in reports that the user is not in the tenant, confirm the invitation has been redeemed using the invited identity before retrying.

### Local development only

```bash
uv sync --extra dev
uv run agency db-init
uv run agency-mcp
```

This starts Eve over stdio on the local machine. It is not the shared remote service and is not directly connectable from ChatGPT web. For shared Codex staging use the bearer-token setup above; for ChatGPT web, the separate OAuth flow must be deployed first.

## Safe operator flow

1. An admin creates a client and assigns the operator with `client_access_grant`.
2. The operator records offer, ICP, markets, goals, exclusions, and constraints with `client_profile_update`.
3. The operator ingests only authorized source material. Eve redacts common email/phone/payment identifiers.
4. An admin reviews relevant evidence.
5. The operator creates a versioned hint set, then drafts a campaign blueprint. ChatGPT can write the strategy; Eve validates the schema and compiles the payload.
6. The operator locally compiles and preflights the exact paused bulk payload.
7. The operator proposes an immutable build change. Its payload hash becomes the approval object.
8. With a real test/client Ads key, `change_platform_validate` queues an executor-only `validate_only: true` request to OpenAI.
9. An admin records written client approval and approves the same hash. A paused build can then be submitted only when mutations are explicitly enabled.
10. Eve snapshots insights and reports metrics with freshness caveats. A controlled comparison is registered before any paid test.

## MCP tools

| Area | Tools | What they do |
| --- | --- | --- |
| Clients and evidence | `client_create` (admin), `clients_list`, `client_profile_update`, `source_ingest`, `source_list`, `evidence_search`, `evidence_review` (admin) | Create/list client workspaces, persist the client profile, ingest authorized sources with identifier redaction/provenance, search evidence (approved only by default), and record a human evidence decision. |
| Account and readiness | `ads_workspace_configure`, `ads_workspace_get`, `ads_account_verify`, `workspace_readiness` | Store non-secret advertiser metadata, view workspace setup, queue a private account check, and surface readiness blockers. The executor reads Ads credentials; MCP never returns them. |
| Context hints and campaign planning | `hint_set_draft`, `hint_sets_list`, `campaign_blueprint_draft`, `campaign_blueprint_compile`, `campaign_blueprint_preflight` | Draft versioned evidence-linked hints, list versions, save a desired campaign state, deterministically compile its paused payload, and persist a local preflight. |
| Change approvals and apply | `change_propose`, `change_get`, `change_platform_validate`, `change_record_client_approval` (admin), `change_admin_approve` (admin), `change_apply_paused` (admin) | Freeze a payload/hash, inspect approvals, queue OpenAI Ads `validate_only`, record client/admin approvals, and queue only an approved paused build. No tool activates delivery. |
| Metrics and experiments | `insights_sync`, `controlled_experiment_register`, `controlled_experiments_list`, `controlled_experiment_evaluate` | Queue metric snapshots and register/evaluate two-arm controlled comparisons. These are not platform-randomized A/B tests; the MCP cannot declare a final winner. |
| Reports | `ads_report_generate`, `ads_reports_list`, `ads_report_get`, `ads_report_review` (admin), `ads_report_mark_sent` (admin) | Generate immutable report drafts, inspect report/review status, retrieve a report, record admin review, and audit that a reviewed report was shared externally. Eve does not send it. |
| Official Ads Manager handoff | `ads_manager_access_record`, `ads_manager_access_get`, `ads_manager_action_record`, `ads_manager_actions_list`, `ads_manager_insights_import` | Record operator-attested Ads Manager access, log an action after it happened, and import operator-supplied aggregate metrics. These are not live-verified by Eve and do not operate the separate Ads Manager app. |
| Jobs, access, and audit | `workflow_jobs_list`, `executor_jobs_list`, `client_access_grant` (admin), `audit_chain_verify` (admin) | Inspect persisted workflow/executor jobs, assign a client to an Eve operator identity, and verify the audit hash chain. In static staging mode, all operator-token holders share the same identity and grants. |

That is **40 MCP tools** in the current code. Admin-only actions are labeled above. An operator needs an Eve client grant; the admin role can access all workspaces. Because every operator token maps to the same `agency-operator` identity, grants are shared and per-person client isolation/attribution are unavailable in this mode.

There is intentionally no `ads_api_request`, `secret_get`, `campaign_activate`, audience-upload, or conversion-send MCP tool.

## Context Hint and campaign blueprint contract

Use an evidence-backed hint set such as:

```json
[
  "Workflow software for operations teams evaluating a switch after their current process becomes difficult to manage."
]
```

Use explicit targeting fields for geography and audience eligibility; do not place them in a hint. OpenAI says hints provide additional product/use-case/need context, are not exact-match keywords, and do not guarantee that a conversation will trigger an ad. Updating a hint list replaces the existing list, which is why Eve versions complete hint sets. [Targeting and Context Hints](https://developers.openai.com/ads/campaign-targeting)

The current P0 bulk blueprint uses this desired-state shape:

```json
{
  "campaign": {
    "name": "Acme US lead launch",
    "billing_event_type": "click",
    "budget_type": "daily",
    "max_budget_micros": 10000000,
    "target_countries": ["US"]
  },
  "ad_group": {
    "name": "Switching trigger",
    "strategy": "fixed_bid",
    "max_bid_micros": 1500000
  },
  "ad": {
    "name": "Switching chat card",
    "title": "Explore Acme's workflow",
    "body": "See whether the workflow fits your team.",
    "target_url": "https://example.com/switching",
    "source_image_url": "https://example.com/assets/chat-card.png"
  }
}
```

`max_budget_micros` and `max_bid_micros` are integer micros. The compiler forces every operation to `paused`. It uses the documented bulk hierarchy/idempotency structure and never converts a single-resource body into bulk JSON by guesswork. [Bulk operations](https://developers.openai.com/ads/campaign-management)

## Secret handling

For local development only (encrypted locally with Fernet):

```bash
uv run agency secrets-keygen
# Store output as AGENCY_MASTER_KEY in local .env; never commit it.
uv run agency secret-set <client-id> OPENAI_ADS_API_KEY
uv run agency secret-delete <client-id> OPENAI_ADS_API_KEY
uv run agency audit-verify
```

For Azure/shared operation, install the Azure extra and set `EVE_SECRET_BACKEND=azure_key_vault`, `AZURE_KEY_VAULT_URL`, `AGENCY_SECRET_INTAKE_URL`, and `AGENCY_SECRET_INTAKE_SCOPE` on the admin workstation. The masked `secret-set` CLI writes the key directly from that workstation to the private Key Vault using the operator's Entra identity, then sends Eve only a deterministic locator. The control service has no client-vault permission; the executor alone can read client key values. Operators never need PostgreSQL credentials, and the MCP server has no secret-accepting tool.

Deploy the custom [Key Vault secret-management role](infra/azure/secret-writer-role.bicep) and assign it only to the small Entra admin group at the client-secrets vault scope. It permits set, soft-delete, and recover operations, but not listing or reading secret values. `secret-list` retrieves only Eve's safe metadata. `secret-delete` is confirmed interactively, soft-deletes the Key Vault secret directly, then removes Eve's locator; it does not revoke the upstream Ads key.

Real Ads mode does not accept a process-wide `OPENAI_ADS_API_KEY` fallback. Every Ads call must resolve the credential from the requested client's secret-store record; this prevents a missing client key from silently selecting another advertiser account.

`secret-delete` is an admin-only, interactive removal of Eve's stored copy. In Azure Key Vault, the secret is soft-deleted and not purged. This does **not** revoke the API key at OpenAI; the account owner must also revoke it in Ads Manager when rotating or offboarding.

An Azure/OpenAI model key is **not** an Ads API key. Each Ads key is scoped to one Ads account. [Authentication](https://developers.openai.com/ads/api-reference/authentication)

## Zero-spend real integration checkpoint

When the local suite and Azure staging deployment are ready, provide a dedicated test account’s `OPENAI_ADS_API_KEY` through `agency secret-set`, not through chat. Then:

```bash
ADS_MODE=real uv run agency ads-account-verify <client-id>
ADS_MODE=real uv run agency change-validate <change-id>
ADS_MODE=real EVE_PROCESS_ROLE=executor AGENCY_ROLE=operator uv run agency executor-drain
uv run agency executor-jobs <client-id>
```

The first two commands queue work only. The executor calls `GET /ad_account`, then submits a bulk job only with `validate_only: true`, polls it to a terminal state, and records every operation outcome. It does not create resources or spend money. This is the first point at which a real Ads key is needed. OpenAI requires the client account key, website/brand assets, and server-side secret storage for partner setup. [API Partner Setup](https://developers.openai.com/ads/api-partner-setup)

The Ads Bulk API is currently in limited preview and must be enabled for the specific ad account. `validate_only` checks request fields and dependencies but does not prove an image will fetch, resources can be created, or ads will serve. An endpoint `404` may mean that bulk access is not enabled for the account. [Bulk API](https://developers.openai.com/ads/bulk-api)

## Commands

```bash
uv run agency --help
uv run agency health
uv run agency client-create "Acme" --vertical digital_products --website https://example.com
uv run agency client-access-grant <client-id> operator@example.com
uv run agency ads-workspace-configure <client-id>
uv run agency workspace-readiness <client-id>
uv run agency hint-draft <client-id> "switching" '["..."]' --evidence-ids-json '["<evidence-id>"]'
uv run agency blueprint-draft <client-id> <workspace-id> <hint-set-id> "Launch" '<desired-state-json>'
uv run agency blueprint-compile <blueprint-id>
uv run agency change-propose <blueprint-id> "Evidence, page, and budget reviewed"
uv run agency insights-sync <client-id> <entity-id> 2026-09-01/2026-09-07 ad_group America/New_York
uv run agency executor-jobs <client-id>
uv run agency ads-report <client-id>
uv run agency ads-report-review <report-id> approve --notes "Reviewed evidence and metric caveats"
uv run agency ads-report-mark-sent <report-id> "CRM activity <reference>"
uv run pytest
```

For shared local Compose rehearsal, start PostgreSQL first, run an explicit admin migration from the host, then start Eve MCP and its separate private polling worker. Configure `AGENCY_POSTGRES_PASSWORD` and run:

```bash
docker compose up -d postgres
DATABASE_URL="postgresql+psycopg://agency:${AGENCY_POSTGRES_PASSWORD}@127.0.0.1:55432/agency" uv run agency db-upgrade
docker compose --profile shared up --build
```

The runtime MCP and executor processes only verify the Alembic schema revision; they never migrate at startup. The executor polls bounded batches and handles SIGTERM between batches, and it never starts an MCP listener.

The default suite uses isolated SQLite databases. CI also migrates a disposable PostgreSQL database and runs `tests/test_postgres_integration.py` to exercise the executor queue on the shared-deployment database engine. For a local PostgreSQL integration run, first migrate a dedicated database whose name ends in `_test`, then set both `DATABASE_URL` and `EVE_TEST_POSTGRES_URL` to it before running `uv run pytest`. The integration test refuses any database without that suffix and only appends uniquely named smoke records.

CI also builds and installs the wheel into a temporary location outside the checkout, then initializes a fresh SQLite database. This verifies that packaged Alembic configuration and migrations work in the installed distribution, not only from the source tree.

## Current boundary

Eve is ready for local mock rehearsal, team-safe drafting, immutable change control, production-oriented secret handling, controlled-comparison registration, and zero-spend Ads API validation once a test key is supplied.

Before any live client spend, it still needs the documented test-account integration pass, client-specific brand review/capability checks, conversion implementation, approved creative assets, a client budget cap, and formal operational sign-off. No preview or validation proves review approval or serving eligibility. [Campaign management](https://developers.openai.com/ads/campaign-management)
