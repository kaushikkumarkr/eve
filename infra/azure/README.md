# Azure foundation IaC

`main.bicep` creates the private data/network foundation for one isolated environment:

- container registry;
- log workspace and Container Apps environment;
- a dedicated VNet with separate Container Apps, private endpoint, and PostgreSQL subnets;
- an internal-only Container Apps environment with public network access disabled;
- private DNS and private endpoints for both Key Vaults;
- private Azure Database for PostgreSQL Flexible Server (no public endpoint) and its database;
- runtime Key Vault and separate client-secrets Key Vault;
- `eve-control` and `eve-executor` managed identities;
- runtime configuration read assignments and executor-only client-key read assignment.

It does **not** create client secrets, a public MCP endpoint, Container Apps revisions, an Entra application, the team VPN/private route, or a least-privilege PostgreSQL runtime login. `apps.bicep` is the separate second-stage deployment for the control and executor apps; it references an existing Key Vault database URL, uses distinct managed identities, defaults to mock Ads mode, and keeps mutations disabled. Details are in [the Azure deployment guide](../../docs/AZURE_DEPLOYMENT.md).

The Key Vaults and database have no public data-plane endpoint; the Container Apps environment is internal-only. Operators still need an approved private route (VPN or overlay subnet router) into the VNet before reaching MCP or administering Key Vault. Do not treat the infrastructure template alone as a running or authenticated Eve deployment.

Plan the network before deployment: subnet ranges must not overlap connected networks and are difficult or impossible to change after service creation. The template creates a fresh internal environment; do not retrofit an older external Container Apps environment. See Microsoft's [Container Apps networking documentation](https://learn.microsoft.com/en-us/azure/container-apps/custom-virtual-networks).

## Validate locally

```bash
az bicep build --file infra/azure/main.bicep
az bicep build --file infra/azure/apps.bicep
az bicep build --file infra/azure/secret-writer-role.bicep
```

`secret-writer-role.bicep` is a separate subscription-scope custom role because Azure requires role definitions to be deployed at subscription scope. It is not assigned automatically. Deploy it under a principal allowed to create custom roles, then assign it only to the small Entra admin group at the client-secrets vault. Its data actions allow set, soft-delete, and recover, but not list or read secret values.

## Staging deployment

Create a dedicated resource group first, then deploy with names that are globally unique where Azure requires it:

```bash
az deployment group create \
  --resource-group rg-eve-staging \
  --template-file infra/azure/main.bicep \
  --parameters \
    registryName=<unique-registry-name> \
    runtimeVaultName=<unique-runtime-vault-name> \
    clientVaultName=<unique-client-vault-name> \
    logAnalyticsName=eve-staging-logs \
    containerAppsEnvironmentName=eve-staging-env \
    virtualNetworkName=eve-staging-vnet \
    postgresServerName=<globally-unique-postgres-name> \
    postgresPrivateDnsZoneName=eve-staging-internal.postgres.database.azure.com \
    tags='{ "application": "eve", "environment": "staging", "owner": "agency" }'
```

The deployment also requires the secure `postgresAdministratorPassword` parameter. Supply it through a protected deployment mechanism, never a literal command-line value, shell history, committed parameter file, or CI log. Run `what-if` before `create` in a real subscription. Never pass an Ads API key, CAPI key, or client value in Bicep parameters.

## Deploy the app tier after bootstrap

After the foundation is deployed and reviewed, complete prerequisites before running `apps.bicep`:

1. Create separate `eve_migrator` and `eve_app` DB roles. Run `agency db-upgrade` from a private-route host using the migrator URL; services use only the non-DDL `eve_app` URL.
2. Store the least-privilege runtime URL as `eve-database-url` in the runtime Key Vault. Never use the PostgreSQL bootstrap administrator for either identity.
3. Build and push the reviewed image to ACR, preferably tagged by commit and referenced by digest.
4. Register the Entra API, app roles, and assignments; provision an HTTPS private DNS name that operators can reach through the VPN/overlay and set `mcpPublicUrl` accordingly.
5. Deploy the subscription-scoped secret-manager custom role and assign it at the client vault to the restricted Entra admin group. Do not assign Key Vault Secrets Officer to the control identity or ordinary operators.
6. Run `what-if` then deploy `apps.bicep` with resource names, image digest, tenant/audience, private MCP URL, and tags. Do not override the safe `adsMode=mock` and `mutationsEnabled=false` defaults until separately reviewed.
7. Test private DNS, restore, identity denial, no public ingress, and MCP clients before adding a test Ads key.

Example second-stage deployment (replace non-secret values; pass no passwords, DB URLs, Ads keys, or CAPI keys):

```bash
az deployment group what-if \
  --resource-group rg-eve-staging \
  --template-file infra/azure/apps.bicep \
  --parameters \
    containerAppsEnvironmentName=eve-staging-env \
    registryName=<registry-name> \
    runtimeVaultName=<runtime-vault-name> \
    clientVaultName=<client-vault-name> \
    controlAppName=eve-control-staging \
    executorAppName=eve-executor-staging \
    containerImage=<registry>.azurecr.io/eve@sha256:<image-digest> \
    entraTenantId=<tenant-guid> \
    mcpAudience=<api-audience> \
    mcpPublicUrl=https://<private-mcp-name>/mcp \
    tags='{ "application": "eve", "environment": "staging" }'
```

The executor is intentionally one always-on replica for queue latency and graceful polling; budget for its ongoing Container Apps compute charge.
