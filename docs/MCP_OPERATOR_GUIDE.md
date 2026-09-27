# Eve MCP operator guide

Eve is the agency's Ads operations control plane, not an autonomous ad buyer. The intended ChatGPT workflow uses two separately connected apps: OpenAI's official Ads Manager app for account-facing work and Eve MCP for client scope, evidence, planning, approvals, records, and reporting. The current staging Eve endpoint instead uses shared static bearer tokens for Codex CLI; it is not yet connected as a ChatGPT OAuth app. See [the README team setup](../README.md#staging-status-and-team-setup). ChatGPT may coordinate both apps when OAuth is configured, but Eve cannot directly inspect or verify the official app's session.

## Non-negotiable rules

1. Start by selecting the persisted client workspace; do not rely on chat memory.
2. Never place Ads/CAPI keys in prompts, MCP arguments, source text, reports, or screenshots.
3. Do not claim placement, recommendation, prompt-level matching, performance, or a context-hint “winner” without the appropriately labeled provider data and review.
4. Never treat simulation as an official platform metric. Eve’s Ads-only tool surface has no visibility simulation.
5. Only an admin can record evidence approval, client sign-off, final approval, or a paused apply.
6. A client approval must identify the exact Eve payload hash. A later edit requires a new change request.
7. “Controlled comparison” is the correct name unless OpenAI documents a randomized split mechanism.
8. An Eve Ads Manager access record is operator attestation, not an OpenAI verification. An action record is post-action audit, not execution or approval.

## MCP connection modes

### ChatGPT team workflow (after Eve OAuth is configured)

1. A client creates and owns its Ads account, then invites the named agency members who need access. Ads account access is separate from ChatGPT workspace membership.
2. A ChatGPT workspace admin enables developer mode/custom MCP apps and makes Eve available to the approved team. The team connects Eve through the authenticated remote MCP endpoint.
3. Each operator separately connects/selects the client account in OpenAI's official ChatGPT Ads Manager app. Use `ads_manager_access_record` only after the operator has confirmed the account in that app; it records the account ID and who attested, not a live verification.
4. Select both apps for the ChatGPT task. Ask ChatGPT to use Eve for the client profile, evidence, plan, approvals, and audit; use Ads Manager for the platform data/action. The model can relay a result from one app to the other, but there is no server-to-server plugin bridge.
5. Persist actions with `ads_manager_action_record` and approved reporting data with `ads_manager_insights_import`. Results are labeled `operator_reported` / `operator_supplied_not_api_verified`; retain the original source/export in the agency's approved records and cite its filename/reference. Never represent those records as API-verified.
6. Before any spend-affecting Ads Manager action, obtain the client's written approval and the required agency approval. Eve's audit tool does not technically prevent an operator from using the official Ads Manager app outside the approved workflow. Limit write-capable Ads account roles to designated operators and use viewer access for others.

OpenAI's Ads Manager app and Eve are independent connections. A ChatGPT workspace invitation alone does not grant Ads account access. ChatGPT's write-capable custom MCP app support depends on the workspace plan and admin settings; check those before selecting the shared deployment mode. See [ChatGPT MCP app setup](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt) and [Ads Manager access](https://help.openai.com/en/articles/20001273-managing-identity-and-access-for-ads-manager).

### Local development/rehearsal

```bash
uv run agency db-init
uv run agency-mcp
```

Use the local stdio server for development and tests with mock mode; this does not connect ChatGPT web to the laptop. ChatGPT's custom MCP app uses a remote server URL; for private/local development follow OpenAI's Secure MCP Tunnel guidance rather than exposing the raw MCP endpoint.

### Current shared staging service (Codex CLI)

The current staging endpoint is internet-reachable over HTTPS and requires one of two shared bearer tokens. It is not behind a private gateway. A token holder can authenticate without an individual identity; invitations do not gate access, and operator client grants/audit actor IDs are shared across all operator-token users. Keep this configuration to zero-spend staging tests only. The database is not directly reachable by team machines.

```text
operator’s Codex CLI → HTTPS Eve MCP with shared bearer token → Eve service → PostgreSQL
                                                          └→ Key Vault / Ads executor
```

For exact Codex setup commands see [the README](../README.md#easy-codex-cli-setup). The production target remains Entra identities plus a private authenticated gateway, with client grants keyed to immutable per-person IDs; the current shared-token staging exception does not provide that isolation. See [shared deployment authentication](SHARED_DEPLOYMENT.md#shared-mcp-authentication).

## Tool sequence

| Step | Tool | Operator outcome |
| --- | --- | --- |
| 1 | `clients_list` | Find an assigned client. |
| 2 | `workspace_readiness` | Read missing profile/evidence/workspace blockers. |
| 3 | `client_profile_update`, `source_ingest`, `source_list` | Persist authorized scope and sources; preserve provenance and hashes. |
| 4 | `evidence_review`, `evidence_search` | Admin reviews evidence; operators search approved excerpts by default. |
| 5 | `ads_workspace_configure` | Add account metadata only; keys stay in the admin CLI/Key Vault. |
| 6 | `hint_set_draft` | Store a complete evidence-linked hint list. |
| 7 | `campaign_blueprint_draft` | Store the human/agent desired state, not raw Ads API JSON. |
| 8 | `campaign_blueprint_compile` | See deterministic errors, warnings, payload and hash locally. |
| 9 | `change_propose` | Freeze the exact paused bulk payload. |
| 10 | `change_platform_validate` | Queue only `validate_only: true`; inspect the result in `executor_jobs_list`. |
| 11 | `change_record_client_approval`, `change_admin_approve` | Admin binds both approvals to the hash. |
| 12 | `change_apply_paused` | Admin-only queue; the executor rechecks approvals, uses paused resources only, then waits for confirmed create outcomes. |
| 13 | `insights_sync`, `executor_jobs_list`, `ads_report_generate` | Queue/poll snapshots, then persist an immutable report draft with freshness context. |
| 14 | `ads_reports_list`, `ads_report_get`, `ads_report_review` | Review the persisted report and hash. Only an admin may mark a blocker-free snapshot client-ready. |
| 15 | `ads_report_mark_sent` | Record the sharing reference after sending through the team's approved channel; the tool sends nothing. |
| 16 | `audit_chain_verify` (admin) | Check the full append-only audit chain; `incomplete` flags legacy entries whose old hash format did not bind actor identity. |
| 17 | `controlled_experiments_list`, `controlled_experiment_evaluate` | Attach/assess snapshots only; Eve rejects mismatched entities/windows and marks mock, unverified, null, or unsettled data ineligible. MCP cannot record a final decision. |
| 18 | `ads_manager_access_record`, `ads_manager_access_get` | Record/read operator-attested ChatGPT Ads Manager account access; not a live check. |
| 19 | `ads_manager_action_record`, `ads_manager_actions_list` | Log a human-performed Ads Manager action after it happens; no platform write occurs. Use stable external references to prevent duplicate logs. |
| 20 | `ads_manager_insights_import` | Validate and persist explicitly supplied aggregate report rows as immutable operator imports, with account match, source hash, period, timezone, provider and freshness. |

An admin records the final decision separately with `uv run agency experiment-decide <experiment-id> <scale|iterate|pause|inconclusive> <approval-reference> <rationale>`. The CLI requires an interactive confirmation, a written rationale, and an approval reference; poor-quality data can only be recorded as inconclusive.

## Good prompts for Codex or Claude Code

```text
Use Eve. List the clients I am assigned to, select Acme, and show workspace
readiness. Do not create a campaign, request approval, call OpenAI Ads, or make
any claim about organic ChatGPT visibility.
```

```text
For Acme’s existing approved evidence, propose three specific context hints for
the “switching trigger” use case. Explain which evidence supports each. Create
a draft hint set only after showing me the exact text. Do not add geography,
audience lists, or guarantees to a hint.
```

Evidence excerpts are untrusted source content, not instructions to the agent. Search with `evidence_search` before drafting, keep the returned evidence IDs attached to any hint set, and treat source URL/locator as provenance. The tool returns approved evidence by default; administrators can search unreviewed or rejected excerpts during review.

```text
Compile blueprint <id>, explain every validation warning, and show the exact
paused bulk payload and SHA-256. Do not propose, approve, validate remotely, or
apply a change.
```

```text
Register a controlled comparison for two existing, non-overlapping hint sets.
Use one changed variable, one primary metric, spend and quality guardrails,
timezone, attribution setup, and a fixed decision date. Do not call it an A/B
test or select a winner.
```

## Blueprint input

The P0 compiler requires a clicks/impressions fixed-bid bulk blueprint. This narrow boundary is intentional until a real test account validates additional bidding and conversion schema paths.

For fixed impression bids, provide `ad_group.max_bid_micros` as a per-impression amount; for example, a $60 CPM bid is 60,000 micros per impression. The Ads API uses `max_bid_micros` for both click and impression bidding.

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

The compiler injects the persisted full hint set and paused status. It creates stable idempotency keys for the campaign, ad group, and ad. It does not use the model’s prose as an API request.

## Real-key integration boundary

Only after a test Ads account key is stored through the secure CLI may an operator queue `ads_account_verify` or `change_platform_validate`. The private executor performs the request; poll `executor_jobs_list` rather than expecting a synchronous API result. The key must be created in Ads Manager and is scoped to that one ad account. Never use an Azure OpenAI model key in its place. [Ads authentication](https://developers.openai.com/ads/api-reference/authentication)

`validate_only` does not spend money, but it also does not prove image fetching, resource creation, creative review, account eligibility, or delivery. The Bulk API is limited preview and enabled per account; a 404 may indicate missing account access. OpenAI keeps previews/review/serving separate. [Bulk API](https://developers.openai.com/ads/bulk-api), [Campaign management](https://developers.openai.com/ads/campaign-management)

For a local rehearsal, queue the request in the normal control process and run the worker separately against the same local database:

```bash
uv run agency ads-account-verify <client-id>
EVE_PROCESS_ROLE=executor AGENCY_ROLE=operator uv run agency executor-drain
uv run agency executor-jobs <client-id>
```

In a local real-key rehearsal, `executor-drain` runs one bounded batch and exits. A shared deployment runs `EVE_PROCESS_ROLE=executor AGENCY_ROLE=operator uv run agency executor-worker` as a separate, continuously running private process. No Codex or Claude Code MCP tool can invoke either executor command.

For `insights_sync`, pass an entity ID, `aggregation_level` (`campaign`, `ad_group`, or `ad`), an inclusive `YYYY-MM-DD/YYYY-MM-DD` period, and the account timezone until account verification has stored it. Ad-group snapshots are essential for directional context-hint comparisons because hints are configured at that level. These are platform-reported aggregate metrics, not per-hint conversation attribution; the API does not report which conversations matched a hint.

General Insights `conversions` means click-through conversions. Eve does not yet request the dedicated conversion-insights endpoint that separates view-through conversions; client reports disclose this limitation. A zero click-through value is not evidence that no view-through outcomes occurred. [Insights and conversion attribution](https://developers.openai.com/ads/api-reference/insights)

### Official ChatGPT Ads Manager handoff

The official Ads Manager app can be used alongside Eve in ChatGPT. Choose the official app when retrieving current account state/insights or carrying out an approved platform operation. Then send only the necessary aggregate fields to Eve using `ads_manager_insights_import`; never pass credentials, person-level CRM data, or sensitive identifiers. The importer accepts only a bounded set of aggregate metric names, date periods, and timezones; deduplicates by a canonical SHA-256; and stores no raw response body. Supply the export filename and, when available, a safe URL or internal source reference so a reviewer can locate the source later. For action logging, include the Ads Manager object ID, a stable unique external reference (for example the internal ticket/approval reference), outcome, timestamp, and approved payload hash if one exists.

Use source labels literally: `chatgpt_ads_manager_operator_import` means an operator/model supplied the data through the MCP call, not that Eve authenticated to Ads Manager. Eve reports retain that caveat. For API-backed data use `insights_sync`; do not silently blend or relabel the two providers. `ads_manager_action_record` only records completed/attempted external activity. It does not authorize, execute, activate, pause, or change budgets.

## What is intentionally unavailable

- Generic request forwarding to the Ads API
- Returning, copying, or pasting secrets
- Campaign activation
- Audience upload
- Live CAPI event submission
- Organic visibility checks
- Automatic bidding or optimization

Use the admin CLI plus documented runbook if and when one of those capabilities is added after an explicit policy, security, and test-account review.
