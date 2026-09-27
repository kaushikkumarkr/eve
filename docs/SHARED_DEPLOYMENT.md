# Shared deployment contract

## Decision

Use one agency-controlled Eve service and one PostgreSQL database. Codex and Claude Code connect to the authenticated MCP service; no operator receives database credentials or a copied SQLite database.

```text
team laptops ─ private authenticated route ─> Eve MCP/control service ─> PostgreSQL queue
                                                                           │
private executor worker ──────────────────────────────────────────────────┘─> Azure Key Vault / Ads API
```

The executor is a continuously running private worker, not a one-shot job. It polls PostgreSQL for queued work, processes bounded batches, and sleeps between polls. Run it as a separate container/app with no MCP listener or public ingress. The Compose shared profile uses `agency executor-worker`; a production orchestrator should restart the worker after failure and send SIGTERM during deployments so it can stop between batches.

This is a private internal system. Do not expose the raw Streamable HTTP MCP service, the Postgres port, a Key Vault credential, or a client Ads key to the public internet.

## Environments

| Environment | Purpose | Credentials | Data |
| --- | --- | --- | --- |
| Local | Development and mock tests | Local Fernet development key only | Synthetic/test data only |
| Azure staging | Team smoke tests and zero-spend test Ads account | Staging Key Vault | Test data/account only |
| Azure production | Approved client work | Production Key Vault and managed identities | Client data, backups, audit retention |

Staging and production must use separate resource groups, Key Vaults, databases, identities, and client credentials. The former shared-static-token staging deployment was retired on 2026-09-27; there is no active shared endpoint or valid staging operator token. Do not recreate that exception. For redeployment, use individual Entra identities and the private authenticated route described below, following the [Azure deployment handover](AZURE_DEPLOYMENT.md).

## Identity and client scope (production target)

Every human signs in with their own Entra identity and MFA. Do not use a shared “admin” account.

- Add a person to `eve-operators` or `eve-admins` in Entra.
- Add an explicit Eve `client_access_grant` for each operator/client relationship.
- `eve-admins` contains at most the people who can authorize paused builds and manage credentials.
- Operators may draft and validate. They cannot inspect secrets, directly query Postgres, record client approval, or apply a change.

### Shared MCP authentication (production target)

Use the built-in single-tenant Entra token verifier for shared team access:

```dotenv
AGENCY_MCP_AUTH_MODE=entra
AZURE_TENANT_ID=<tenant-guid>
AGENCY_MCP_AUDIENCE=<API application ID URI or client ID>
AGENCY_MCP_PUBLIC_URL=https://<private-gateway-host>/mcp
```

Register Eve as a single-tenant API in Entra, expose the chosen audience, and define application roles with exact values `Eve.Operator` and `Eve.Admin`. Assign those roles only to the appropriate Entra groups/users and have the MCP client acquire an access token for Eve's API audience. Eve validates the signature against the tenant-specific Microsoft signing-key endpoint and enforces issuer, audience, tenant, expiry, object ID, and role. The internal actor key is `<tid>:<oid>`; use that exact value as `operator_id` when granting client access. Missing/unrecognized roles and identity-provider/key-fetch errors fail closed.

Do not use ID tokens as API bearer tokens. Use an access token whose audience is Eve's API. Do not map mutable email/name claims to authorization records. Review Entra token configuration and application-role assignment during go-live; the verifier deliberately does not infer admin from email, group display name, or a caller-supplied header.

The static two-token mode (`AGENCY_MCP_AUTH_MODE=static`) is only for isolated staging smoke tests. It authenticates a shared role, not a person: invitations are not checked, all operators share the `agency-operator` actor ID, and per-person client grants/audit attribution are unavailable. Require distinct 32-character-or-longer tokens and HTTPS; never use this mode with production client data or real spending. Generate values with `openssl rand -hex 32`; never reuse one token for both roles. Store tokens in a password manager and configure Codex with `bearer_token_env_var`; do not put values in source, shell history, prompts, or config files.

## Minimum Azure permissions

Use a dedicated resource group such as `rg-eve-staging`. Do not grant a developer Subscription Owner role.

The deployment identity needs `Contributor` on the dedicated resource group. If it must assign managed-identity roles, either grant narrowly scoped `User Access Administrator` there or have the subscription owner apply the supplied role assignments.

The runtime identities are separate:

| Identity | Needs | Must not have |
| --- | --- | --- |
| Eve control | PostgreSQL access, telemetry write | Client Ads/CAPI secret read |
| Eve executor | PostgreSQL action queue, Key Vault secret read, outbound OpenAI Ads access | Public MCP ingress, MCP service identity |
| Deployer | Create/update resources | Ongoing access to client secrets |

## Secrets

Production uses:

```dotenv
EVE_SECRET_BACKEND=azure_key_vault
AZURE_KEY_VAULT_URL=https://<vault>.vault.azure.net/
AGENCY_SECRET_INTAKE_URL=https://<private-gateway>/admin/client-secrets
AGENCY_SECRET_INTAKE_SCOPE=api://<eve-api-app-id>/.default
```

Install the production image and admin CLI with the `azure` dependency extra. Eve control has no client-secrets-vault permission; only the executor managed identity receives `Key Vault Secrets User`. Admins deploy [the subscription-scoped custom role](../infra/azure/secret-writer-role.bicep), then assign that role at the client vault to the small Entra admin group. It permits setting, soft-deleting, and recovering secrets but not listing or reading values. The operator types the key into the masked CLI; it is sent directly to Key Vault from the operator workstation. Eve's authenticated private admin route receives only the deterministic locator and persists that metadata in PostgreSQL. The secret value never enters MCP, Eve's HTTP request body, or PostgreSQL. The local `AGENCY_MASTER_KEY` mechanism is development fallback and should not be the shared production secret authority.

The client owns its Ads account and may revoke its account-scoped key at any point. Eve records the secret name/locator but never outputs the plaintext. For rotation, run `agency secret-set` again; Key Vault creates a new version at the same deterministic name. For offboarding, the account owner first revokes the key in Ads Manager, then an Eve admin uses the confirmed `agency secret-delete` command. The custom role allows soft-delete and recovery but not purge; the account owner still revokes the upstream key separately.

The shared CLI does not need PostgreSQL access. It uses the user's Entra identity for direct Key Vault writes and an admin-role access token for the metadata-only private Eve route. Configure `AGENCY_SECRET_INTAKE_URL` and the exact Entra scope `AGENCY_SECRET_INTAKE_SCOPE` on the workstation. Keep the endpoint private; never forward secret values to Codex/Claude, MCP tools, shell arguments, logs, or chat. A failed metadata registration after a successful vault write is safe to retry; the credential remains in Key Vault until registration succeeds or an admin removes it.

Do not configure a shared process-wide Ads API key. The executor resolves each credential by `client_id` from the client-secret vault; a missing record is a hard failure, not a reason to fall back to another account.

## Database and recovery

- Use managed Azure Database for PostgreSQL for production, with private networking and scheduled backups.
- Keep PostgreSQL private. Do not allow client or operator laptops to connect directly.
- Test restoration into an isolated database before calling the backup policy complete.
- Store database backups and Key Vault recovery procedures separately. A database backup alone does not recreate a revoked secret.
- Treat audit logs, source documents, approvals, and snapshots as client data with a documented retention owner.

## Private network

Recommended small-team route: Tailscale or another identity-aware private overlay, with Eve’s MCP service private. Azure hosts the application/data; the overlay only supplies authenticated encrypted operator connectivity.

If a client requires Azure-only networking, use a private VNet, internal ingress, VPN/Private Link, and Entra-backed gateway. Do not simplify by making `/mcp` public.

## Go-live runbook

1. Deploy tested image to Azure staging.
2. Verify no public database route and no public raw MCP route.
3. Confirm Entra roles, two client assignments, and denied-access behavior.
4. Run mock MCP smoke tests from Codex and Claude Code.
5. Add a dedicated test Ads key through secure CLI/Key Vault.
6. Verify account with `GET /ad_account` and one `validate_only` bulk job.
7. Inspect stored audit, observed account state, and no-secret logs.
8. Create production environment only after staging restore/identity/API tests pass.

## Prohibited shortcuts

- Sharing a `.env` file or admin token with the whole team
- Letting Codex/Claude connect to Postgres
- Adding client Ads keys to GitHub Actions, source files, prompts, or MCP arguments
- Exposing port 5432 or the raw MCP port to the internet
- Reusing staging Key Vault/database for production
- Using a real client key to debug an untested system
