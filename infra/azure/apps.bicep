targetScope = 'resourceGroup'

@description('Azure region. Must match the existing Container Apps environment region.')
param location string = resourceGroup().location

@description('Existing internal Container Apps environment created by main.bicep.')
param containerAppsEnvironmentName string

@description('Existing Azure Container Registry created by main.bicep.')
param registryName string

@description('Existing runtime Key Vault created by main.bicep.')
param runtimeVaultName string

@description('Existing client credentials Key Vault created by main.bicep.')
param clientVaultName string

@description('Existing Eve managed identity names created by main.bicep.')
param controlIdentityName string = 'eve-control'
param executorIdentityName string = 'eve-executor'

@description('Container App resource names.')
param controlAppName string
param executorJobName string

@description('Immutable image reference in ACR, preferably including an sha256 digest.')
param containerImage string

@description('Existing Key Vault secret name containing a least-privilege Eve database connection string.')
param databaseConnectionSecretName string = 'eve-database-url'

@description('Enable only for the one-shot database bootstrap deployment. Remove this job and its temporary vault role after it succeeds.')
param enableDatabaseBootstrap bool = false

@description('Bootstrap-only administrator database URL; supplied securely and never used by app or executor.')
@secure()
param postgresAdminConnectionUrl string = ''

param bootstrapJobName string = 'eve-db-bootstrap-staging'

@description('Single-tenant Microsoft Entra tenant GUID and API audience.')
param entraTenantId string
param mcpAudience string
@description('OAuth mode for the MCP endpoint. Keep entra for the current live deployment; use auth0 only after completing the Auth0 federation setup.')
@allowed([
  'entra'
  'auth0'
])
param mcpAuthMode string = 'entra'
@description('Auth0 tenant host, for example eve-team.us.auth0.com. Required only when mcpAuthMode is auth0.')
param auth0Domain string = ''
@description('Auth0 API identifier; for this deployment it must exactly equal mcpPublicUrl.')
param auth0Audience string = ''
param auth0RoleClaim string = 'https://eve.internal/roles'

@description('HTTPS URL operators use to reach the private MCP endpoint, including /mcp.')
param mcpPublicUrl string

@description('Mock remains the default until a real account-scoped Ads key is securely provisioned.')
@allowed([
  'mock'
  'real'
])
param adsMode string = 'mock'

@description('Must remain false by default. Enables only already-approved paused resource builds, never activation.')
param mutationsEnabled bool = false

@description('Common deployment tags.')
param tags object = {}

var acrPullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var keyVaultSecretsOfficerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')

resource environment 'Microsoft.App/managedEnvironments@2025-07-01' existing = {
  name: containerAppsEnvironmentName
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

resource runtimeVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: runtimeVaultName
}

resource clientVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: clientVaultName
}

resource controlIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: controlIdentityName
}

resource executorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: executorIdentityName
}

resource controlAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, controlIdentity.id, acrPullRoleId)
  scope: registry
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: controlIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource executorAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, executorIdentity.id, acrPullRoleId)
  scope: registry
  properties: {
    roleDefinitionId: acrPullRoleId
    principalId: executorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource controlApp 'Microsoft.App/containerApps@2025-07-01' = {
  name: controlAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${controlIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: registry.properties.loginServer
          identity: controlIdentity.id
        }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: '${runtimeVault.properties.vaultUri}secrets/${databaseConnectionSecretName}'
          identity: controlIdentity.id
        }
      ]
      ingress: {
        external: true
        allowInsecure: false
        targetPort: 8000
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'eve-control'
          image: containerImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'ADS_MODE'
              value: adsMode
            }
            {
              name: 'AGENCY_MUTATIONS_ENABLED'
              value: mutationsEnabled ? 'true' : 'false'
            }
            {
              name: 'AGENCY_MCP_TRANSPORT'
              value: 'streamable-http'
            }
            {
              name: 'AGENCY_MCP_JSON_RESPONSE'
              value: 'true'
            }
            {
              name: 'AGENCY_MCP_STATELESS_HTTP'
              value: 'true'
            }
            {
              name: 'AGENCY_MCP_HOST'
              value: '0.0.0.0'
            }
            {
              name: 'AGENCY_MCP_AUTH_MODE'
              value: mcpAuthMode
            }
            {
              name: 'AGENCY_MCP_PUBLIC_URL'
              value: mcpPublicUrl
            }
            {
              name: 'AZURE_TENANT_ID'
              value: entraTenantId
            }
            {
              name: 'AGENCY_MCP_AUDIENCE'
              value: mcpAudience
            }
            {
              name: 'AUTH0_DOMAIN'
              value: auth0Domain
            }
            {
              name: 'AUTH0_AUDIENCE'
              value: auth0Audience
            }
            {
              name: 'AUTH0_ROLE_CLAIM'
              value: auth0RoleClaim
            }
            {
              name: 'EVE_SECRET_BACKEND'
              value: 'azure_key_vault'
            }
            {
              name: 'EVE_PROCESS_ROLE'
              value: 'control'
            }
            {
              name: 'AGENCY_ROLE'
              value: 'operator'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 3
      }
    }
  }
  dependsOn: [
    controlAcrPull
  ]
}

resource executorJob 'Microsoft.App/jobs@2025-07-01' = {
  name: executorJobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${executorIdentity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 600
      replicaRetryLimit: 1
      scheduleTriggerConfig: {
        cronExpression: '*/1 * * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: executorIdentity.id
        }
      ]
      secrets: [
        {
          name: 'database-url'
          keyVaultUrl: '${runtimeVault.properties.vaultUri}secrets/${databaseConnectionSecretName}'
          identity: executorIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'eve-executor-drain'
          image: containerImage
          command: ['agency']
          args: ['executor-drain', '--limit', '10']
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'ADS_MODE'
              value: adsMode
            }
            {
              name: 'AGENCY_MUTATIONS_ENABLED'
              value: mutationsEnabled ? 'true' : 'false'
            }
            {
              name: 'EVE_SECRET_BACKEND'
              value: 'azure_key_vault'
            }
            {
              name: 'AZURE_KEY_VAULT_URL'
              value: clientVault.properties.vaultUri
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: executorIdentity.properties.clientId
            }
            {
              name: 'EVE_PROCESS_ROLE'
              value: 'executor'
            }
            {
              name: 'AGENCY_ROLE'
              value: 'operator'
            }
          ]
        }
      ]
    }
  }
  dependsOn: [executorAcrPull]
}

resource bootstrapVaultWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableDatabaseBootstrap) {
  name: guid(runtimeVault.id, controlIdentity.id, keyVaultSecretsOfficerRoleId, 'temporary-db-bootstrap')
  scope: runtimeVault
  properties: {
    roleDefinitionId: keyVaultSecretsOfficerRoleId
    principalId: controlIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource databaseBootstrapJob 'Microsoft.App/jobs@2025-07-01' = if (enableDatabaseBootstrap) {
  name: bootstrapJobName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${controlIdentity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: controlIdentity.id
        }
      ]
      secrets: [
        { name: 'database-admin-url', value: postgresAdminConnectionUrl }
      ]
    }
    template: {
      containers: [
        {
          name: 'eve-db-bootstrap'
          image: containerImage
          command: ['agency']
          args: ['db-bootstrap-runtime']
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-admin-url' }
            { name: 'EVE_PROCESS_ROLE', value: 'bootstrap' }
            { name: 'EVE_SECRET_BACKEND', value: 'azure_key_vault' }
            { name: 'AZURE_KEY_VAULT_URL', value: runtimeVault.properties.vaultUri }
            { name: 'EVE_DATABASE_RUNTIME_SECRET_NAME', value: databaseConnectionSecretName }
          ]
        }
      ]
    }
  }
  dependsOn: [bootstrapVaultWriter, controlAcrPull]
}

output controlAppId string = controlApp.id
output controlAppFqdn string = controlApp.properties.configuration.ingress.fqdn
output executorJobId string = executorJob.id
output databaseBootstrapJobId string = enableDatabaseBootstrap ? databaseBootstrapJob!.id : ''
