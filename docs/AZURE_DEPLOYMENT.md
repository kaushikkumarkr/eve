# Azure deployment and migration guide

This guide is the handover record for staging and production. It intentionally contains no subscription IDs, tenant IDs, resource names, credentials, or client keys.

## Staging retirement

The previous NeonPing staging deployment was stopped and its dedicated resource group was submitted for deletion on 2026-09-27. There is no active staging MCP URL. The last Codex smoke test saw zero client records. No database export or Key Vault secret values were migrated; treat a new deployment as an empty environment and provision new credentials. The old PostgreSQL service had only same-subscription short-retention recovery, which is not a cross-account migration mechanism.

## Why Azure-first

Azure is the right foundation for Eve because it combines managed PostgreSQL, Key Vault, managed identities, Entra ID, container deployment, monitoring, and resource-group isolation. It is not a requirement to make every network component Azure-native on day one: a small private overlay such as Tailscale can provide safer operator connectivity than exposing MCP publicly.

## Resource layout

```text
Azure subscription
├── rg-eve-staging
│   ├── Azure Container Registry
│   ├── Log Analytics / Azure Monitor
│   ├── Container Apps environment + Eve control service
│   ├── Azure Database for PostgreSQL (private)
│   ├── Key Vault: runtime secrets
│   ├── Key Vault: client Ads/CAPI secrets
│   └── managed identities: eve-control, eve-executor
└── rg-eve-production
    └── same resources, isolated identities/data/secrets
```

Do not mix staging and production in one resource group, database, Key Vault, or client Ads account.

## Before deployment

1. Create a dedicated resource group (for example `rg-eve-staging`) in the chosen region.
2. Grant the deployment identity `Contributor` on that resource group only.
3. Decide who applies Azure RBAC grants. If the deployment identity cannot assign roles, a tenant/admin owner must apply the generated assignments.
4. Create Entra groups `eve-operators` and `eve-admins`; require MFA for admins. Register Eve as a single-tenant API and assign the `Eve.Operator` / `Eve.Admin` application roles described in the [shared authentication contract](SHARED_DEPLOYMENT.md#shared-mcp-authentication).
5. Choose a private access method: a small-team Tailscale tailnet, or an Azure-only VNet/VPN/gateway design.
6. Deploy the repository’s `infra/azure` baseline after reviewing parameters. It is parameterized and must never contain client credentials.

## Secret roles

Use two Key Vaults:

- **Runtime vault:** database connection/configuration. The control and executor identities may read these runtime settings.
- **Client secrets vault:** per-client Ads/CAPI keys. Only the executor identity may read them.

The control service has no client-secrets-vault permission. The executor identity can read client credentials; the masked admin CLI writes keys directly to the private vault using a human Entra identity. Deploy and assign the custom set/delete/recover-without-read role documented below before enabling shared credential intake.

## Network and trust boundaries

The raw MCP endpoint must remain private behind an authenticated gateway/private operator route; do not expose the Container App's MCP ingress directly to the Internet. Eve also validates a single-tenant Microsoft Entra v2 access token with the `operator` delegated scope and assigned `Eve.Operator` or `Eve.Admin` role. PostgreSQL public access is disabled. Both Key Vaults are reached through private endpoints and private DNS. MCP clients never receive database credentials and must not connect directly to PostgreSQL.

For a fresh deployment, use `infra/azure/main.bicep` as the parameterized foundation and `infra/azure/apps.bicep` for the control app and scheduled no-ingress executor. `main.bicep` has no public-ingress option; it creates a private Container Apps environment, PostgreSQL, Key Vaults, VNet, registry, logs, and managed identities. Keep `ADS_MODE=mock` and `AGENCY_MUTATIONS_ENABLED=false`. Follow the bootstrap steps below before starting the app tier.

`infra/azure/mcp-host.bicep` is an alternate external-ingress host pattern, not the default fresh-deployment path. It creates a public-network-enabled Container Apps environment. Do **not** deploy it as-is under this repository's security contract: the raw MCP service must not be exposed directly to the public Internet. If a remote client requires public routing, first put a private authenticated gateway in front and restrict Container Apps ingress to that gateway; the gateway and its identity policy are not provisioned by this template. For a private team route, deploy and verify the VPN/overlay routing separately before onboarding operators.

`infra/azure/db-bootstrap.bicep` is a one-time privileged step. It runs migrations, creates or updates the least-privilege `eve_app` login, and writes only that runtime URL to the runtime Key Vault. It temporarily grants Key Vault Secrets Officer to the bootstrap identity. After completion, delete the bootstrap job and temporary role assignment. Never retain the PostgreSQL admin URL in app settings, parameter files, CLI history, or Eve. Allow several minutes for Azure RBAC propagation before starting the job.

After deploying each Key Vault private endpoint and DNS zone group, verify that its hostname has an A record in `privatelink.vaultcore.azure.net` and that the zone is linked to the Container Apps VNet. A successful endpoint approval and zone-group resource do not guarantee that the A record exists. If absent, create the record from the endpoint NIC's private IP; otherwise managed-identity token acquisition may succeed while Key Vault data-plane requests fail.

Direct Entra authentication remains available for supported MCP clients. For ChatGPT, use the documented Auth0-federated option in [ChatGPT OAuth setup](CHATGPT_OAUTH.md) unless you have verified the Azure OAuth broker route against the current ChatGPT OAuth requirements. Do not assume direct Entra configuration alone is sufficient for ChatGPT's stricter discovery and callback requirements.

## PostgreSQL

The Bicep foundation creates the private Flexible Server with seven-day backups, but the deployment administrator is only for provisioning. After deployment, create a separate least-privilege login scoped to the Eve database, test that it cannot access other databases or administer the server, and store its connection string as a runtime Key Vault secret. Do not put the bootstrap password or runtime URL in container environment files, source control, CLI history, or logs. Test restore into a separate staging-restored database before relying on the backup policy.

For local development, Docker PostgreSQL is sufficient. SQLite must not become the shared deployment database.

## Container deployment

The control app must be reachable only through the approved private route/gateway; the app's Entra validation is defense in depth, not a substitute for network isolation. Its separately authenticated admin route accepts only a deterministic secret locator from the CLI; it never receives a credential value. The app reaches PostgreSQL and runtime Key Vault through the VNet.

The executor is a scheduled Container Apps Job with no ingress; it drains only explicitly queued work and reads a client Ads key only when required. Build images for `linux/amd64` (Container Apps rejects a native ARM64 image) and deploy by ACR digest. Verify the unauthenticated `/mcp` OAuth challenge and protected-resource metadata, connect from ChatGPT with OAuth, and test that an unassigned user is denied and an operator cannot approve or apply. An admin role alone does not activate or spend. Keep Ads objects paused and mock mode enabled until client approval.

### Admin secret intake without database access

The operator workstation needs the Azure extra, `EVE_SECRET_BACKEND=azure_key_vault`, the private Key Vault URL, and:

```dotenv
AGENCY_SECRET_INTAKE_URL=https://<private-host>/admin/client-secrets
AGENCY_SECRET_INTAKE_SCOPE=api://<eve-api-app-id>/.default
```

Deploy the subscription-scoped role definition in `infra/azure/secret-writer-role.bicep` with a principal allowed to create custom roles. Assign the resulting role ID to the restricted Entra admin group at the client-secrets-vault resource scope. The role permits secret set, soft-delete, and recovery, but has no list/read permissions. The control identity is not assigned this role. Operators then use `agency secret-set <client-id> OPENAI_ADS_API_KEY`: the value is read with no echo, written directly from the workstation to Key Vault, and never included in the HTTP request to Eve. Eve receives only `{client_id, name, vault_secret_name}` and stores the locator. `agency secret-list` reads metadata through the admin route; `agency secret-delete` soft-deletes in Key Vault then removes the locator. Neither MCP nor Eve ever returns a key. A failed metadata registration after a successful vault write is retryable and does not require exposing the key again.

Example role setup (replace placeholders; the role assignment scope is the **client secrets vault**, not the subscription):

```bash
az deployment sub create \
  --location <deployment-region> \
  --template-file infra/azure/secret-writer-role.bicep

az role assignment create \
  --assignee-object-id <eve-admins-group-object-id> \
  --assignee-principal-type Group \
  --role <custom-role-definition-id-from-deployment-output> \
  --scope <client-secrets-vault-resource-id>
```

Custom role creation requires subscription-level role-definition administration; the identity performing the assignment needs permission to create role assignments at the vault scope. Do not grant operators `Key Vault Secrets Officer`, which includes secret read capability. The custom role uses only the Key Vault `setSecret`, `delete`, and `recover` data actions; Azure documents these as distinct from secret-value read actions in its [RBAC permission catalog](https://learn.microsoft.com/en-us/azure/role-based-access-control/permissions/security).

## Azure migration procedure

The repo uses parameterized infrastructure and database migrations, so moving to another Azure subscription/account is a controlled rebuild rather than a copy of a machine.

1. Create a new resource group and deploy the reviewed `main.bicep` + `apps.bicep` path with fresh names, passwords, identity configuration, and private access routing.
2. Create the new tenant's Entra application, app roles, operator/admin groups, managed-identity RBAC, gateway/VPN, and Key Vault configuration. These tenant-specific settings cannot be copied from Git.
3. If migrating live records, export PostgreSQL logically (for example with `pg_dump`) while the source server is still available, transfer the encrypted export through an approved channel, restore it into the new private server, and run the suite plus MCP smoke tests. Azure-managed flexible-server backups are not a cross-subscription database-transfer artifact; test a logical export/restore before retiring a source that contains data.
4. Re-enter or rotate client Ads/CAPI keys through the new Key Vault. Do **not** export plaintext secrets from the old vault as a migration artifact.
5. Compare client/workspace counts, sample evidence hashes, change hashes, and metric snapshots.
6. Shift private DNS/gateway route only after staging checks pass; keep old resources read-only through the agreed rollback window.
7. Revoke old identities and client keys after cutover confirmation.

## Required documentation updates after every deployment

Update the private deployment runbook (outside Git if it contains sensitive identifiers) with:

- resource group and region;
- image digest/repository revision;
- database backup and restore owner;
- Key Vault names and RBAC assignments, without secret values;
- gateway/private access configuration;
- Entra group/application identifiers;
- client/workspace count and latest restore-drill date;
- active operator/admin roster and offboarding owner.

## Azure cost boundary

Build and local mock tests are free. Azure managed PostgreSQL, Container Apps, log retention, networking, and Key Vault operations can incur charges. Create only staging resources first, tag them with `environment=staging`, set budget alerts, and do not deploy production until the test account integration passes.
