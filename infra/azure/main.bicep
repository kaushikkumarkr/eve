targetScope = 'resourceGroup'

@description('Azure region for this isolated Eve environment.')
param location string = resourceGroup().location

@description('Globally unique lowercase ACR name, 5-50 alphanumeric characters.')
param registryName string

@description('Globally unique Key Vault name for runtime configuration.')
param runtimeVaultName string

@description('Globally unique Key Vault name for client Ads/CAPI credentials.')
param clientVaultName string

@description('Log Analytics workspace name.')
param logAnalyticsName string

@description('Container Apps managed environment name.')
param containerAppsEnvironmentName string

@description('VNet name. Use a new VNet for a private staging/production environment.')
param virtualNetworkName string

@description('VNet address range; must not overlap connected operator/VPN networks.')
param virtualNetworkAddressPrefix string = '10.42.0.0/16'

@description('Dedicated Container Apps infrastructure subnet. Size it before first deployment; it cannot be resized in place.')
param containerAppsSubnetPrefix string = '10.42.0.0/23'

@description('Dedicated subnet for private endpoints.')
param privateEndpointsSubnetPrefix string = '10.42.2.0/24'

@description('Dedicated subnet delegated only to Azure Database for PostgreSQL Flexible Server.')
param postgresSubnetPrefix string = '10.42.3.0/24'

@description('Globally unique lowercase PostgreSQL Flexible Server name.')
param postgresServerName string

@description('PostgreSQL administrator login. Do not use this account for Eve runtime connections.')
param postgresAdministratorLogin string = 'eveprovisioner'

@secure()
@description('PostgreSQL bootstrap administrator password. Pass securely; never store in source or deployment parameter files.')
param postgresAdministratorPassword string

@description('Azure PostgreSQL Flexible Server SKU. Confirm regional availability and cost before deployment.')
param postgresSkuName string = 'Standard_B1ms'

@description('Private DNS zone for PostgreSQL. Must end in .postgres.database.azure.com and must not equal the server name.')
param postgresPrivateDnsZoneName string = 'eve-internal.postgres.database.azure.com'

@description('PostgreSQL database Eve creates in the server.')
param postgresDatabaseName string = 'eve'

@description('Common tags. Include environment=staging or environment=production.')
param tags object = {}

var keyVaultSecretsUserRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
var keyVaultPrivateDnsZoneName = 'privatelink.vaultcore.azure.net'

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: virtualNetworkName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [virtualNetworkAddressPrefix]
    }
    subnets: [
      {
        name: 'container-apps-infrastructure'
        properties: {
          addressPrefix: containerAppsSubnetPrefix
          delegations: [
            {
              name: 'container-apps'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: privateEndpointsSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'postgresql-flexible-server'
        properties: {
          addressPrefix: postgresSubnetPrefix
          delegations: [
            {
              name: 'postgresql-flexible-server'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
        }
      }
    ]
  }
}

resource keyVaultPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: keyVaultPrivateDnsZoneName
  location: 'global'
  tags: tags
}

resource keyVaultPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: keyVaultPrivateDnsZone
  name: '${virtualNetworkName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

resource postgresPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: postgresPrivateDnsZoneName
  location: 'global'
  tags: tags
}

resource postgresPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: postgresPrivateDnsZone
  name: '${virtualNetworkName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource runtimeVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: runtimeVaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      bypass: 'None'
      defaultAction: 'Deny'
    }
  }
}

resource clientSecretsVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: clientVaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      bypass: 'None'
      defaultAction: 'Deny'
    }
  }
}

resource postgresServer 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: postgresServerName
  location: location
  tags: tags
  sku: {
    name: postgresSkuName
    tier: 'Burstable'
  }
  properties: {
    administratorLogin: postgresAdministratorLogin
    administratorLoginPassword: postgresAdministratorPassword
    version: '16'
    highAvailability: {
      mode: 'Disabled'
    }
    storage: {
      storageSizeGB: 32
      autoGrow: 'Disabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    network: {
      delegatedSubnetResourceId: resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetworkName, 'postgresql-flexible-server')
      privateDnsZoneArmResourceId: postgresPrivateDnsZone.id
      publicNetworkAccess: 'Disabled'
    }
  }
  dependsOn: [
    postgresPrivateDnsLink
  ]
}

resource postgresDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgresServer
  name: postgresDatabaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

resource runtimeVaultPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${runtimeVaultName}-private-endpoint'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetworkName, 'private-endpoints')
    }
    privateLinkServiceConnections: [
      {
        name: '${runtimeVaultName}-connection'
        properties: {
          privateLinkServiceId: runtimeVault.id
          groupIds: ['vault']
        }
      }
    ]
  }
}

resource runtimeVaultPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: runtimeVaultPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'key-vault'
        properties: {
          privateDnsZoneId: keyVaultPrivateDnsZone.id
        }
      }
    ]
  }
}

resource clientVaultPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${clientVaultName}-private-endpoint'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetworkName, 'private-endpoints')
    }
    privateLinkServiceConnections: [
      {
        name: '${clientVaultName}-connection'
        properties: {
          privateLinkServiceId: clientSecretsVault.id
          groupIds: ['vault']
        }
      }
    ]
  }
}

resource clientVaultPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: clientVaultPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'key-vault'
        properties: {
          privateDnsZoneId: keyVaultPrivateDnsZone.id
        }
      }
    ]
  }
}

resource controlIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'eve-control'
  location: location
  tags: tags
}

resource executorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'eve-executor'
  location: location
  tags: tags
}

resource controlRuntimeSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(runtimeVault.id, controlIdentity.id, keyVaultSecretsUserRoleId)
  scope: runtimeVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleId
    principalId: controlIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource executorRuntimeSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(runtimeVault.id, executorIdentity.id, keyVaultSecretsUserRoleId)
  scope: runtimeVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleId
    principalId: executorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource executorClientSecrets 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(clientSecretsVault.id, executorIdentity.id, keyVaultSecretsUserRoleId)
  scope: clientSecretsVault
  properties: {
    roleDefinitionId: keyVaultSecretsUserRoleId
    principalId: executorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2025-07-01' = {
  name: containerAppsEnvironmentName
  location: location
  tags: tags
  properties: {
    publicNetworkAccess: 'Disabled'
    vnetConfiguration: {
      infrastructureSubnetId: resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetworkName, 'container-apps-infrastructure')
      internal: true
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

output registryLoginServer string = registry.properties.loginServer
output runtimeVaultUri string = runtimeVault.properties.vaultUri
output clientSecretsVaultUri string = clientSecretsVault.properties.vaultUri
output controlIdentityId string = controlIdentity.id
output executorIdentityId string = executorIdentity.id
output containerAppsEnvironmentId string = containerAppsEnvironment.id
output virtualNetworkId string = virtualNetwork.id
output containerAppsSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetworkName, 'container-apps-infrastructure')
output privateEndpointsSubnetId string = resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetworkName, 'private-endpoints')
output postgresServerId string = postgresServer.id
output postgresFqdn string = postgresServer.properties.fullyQualifiedDomainName
output postgresDatabaseName string = postgresDatabase.name
output postgresPrivateDnsZoneId string = postgresPrivateDnsZone.id
