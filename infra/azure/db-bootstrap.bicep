targetScope = 'resourceGroup'

@description('Azure region of the existing Container Apps environment.')
param location string = resourceGroup().location

@description('Existing Container Apps environment.')
param containerAppsEnvironmentName string
@description('Existing Azure Container Registry.')
param registryName string
@description('Existing runtime Key Vault receiving the least-privilege database URL.')
param runtimeVaultName string
@description('Existing Eve control managed identity. Its vault write role is temporary.')
param controlIdentityName string = 'eve-control'
@description('Immutable image reference in ACR.')
param containerImage string
@description('One-shot manual job resource name.')
param bootstrapJobName string = 'eve-db-bootstrap-cu'
@description('Name for the least-privilege runtime database URL secret.')
param databaseConnectionSecretName string = 'eve-database-url'
@secure()
@description('Bootstrap-only PostgreSQL administrator URL; never supplied to runtime apps.')
param postgresAdminConnectionUrl string
@description('Common tags.')
param tags object = {}

var keyVaultSecretsOfficerRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
var acrPullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource environment 'Microsoft.App/managedEnvironments@2025-07-01' existing = {
  name: containerAppsEnvironmentName
}
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}
resource runtimeVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: runtimeVaultName
}
resource controlIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: controlIdentityName
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

resource bootstrapVaultWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(runtimeVault.id, controlIdentity.id, keyVaultSecretsOfficerRoleId, 'temporary-db-bootstrap')
  scope: runtimeVault
  properties: {
    roleDefinitionId: keyVaultSecretsOfficerRoleId
    principalId: controlIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource databaseBootstrapJob 'Microsoft.App/jobs@2025-07-01' = {
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
          command: ['/bin/sh', '-c']
          args: ['echo EVE_BOOTSTRAP_EVENT container_started; exec agency db-bootstrap-runtime']
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-admin-url' }
            { name: 'EVE_PROCESS_ROLE', value: 'bootstrap' }
            { name: 'EVE_SECRET_BACKEND', value: 'azure_key_vault' }
            { name: 'AZURE_KEY_VAULT_URL', value: runtimeVault.properties.vaultUri }
            { name: 'AZURE_CLIENT_ID', value: controlIdentity.properties.clientId }
            { name: 'EVE_DATABASE_RUNTIME_SECRET_NAME', value: databaseConnectionSecretName }
          ]
        }
      ]
    }
  }
  dependsOn: [bootstrapVaultWriter, controlAcrPull]
}

output bootstrapRoleAssignmentName string = bootstrapVaultWriter.name
output bootstrapJobId string = databaseBootstrapJob.id
