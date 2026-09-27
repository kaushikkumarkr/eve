targetScope = 'resourceGroup'

@description('Azure region for the staging network, private database, and public-HTTPS Container Apps environment.')
param location string = resourceGroup().location

@description('Existing shared Log Analytics workspace.')
param logAnalyticsName string
@description('Existing runtime and client-secret Key Vault names.')
param runtimeVaultName string
param clientVaultName string

@description('Names for new, isolated staging resources. Do not reuse existing environment names.')
param containerAppsEnvironmentName string
param virtualNetworkName string
param postgresServerName string
@description('Unique suffix for the private endpoints in this resource group.')
param privateEndpointSuffix string = 'eve'

@description('VNet range must not overlap any connected network.')
param virtualNetworkAddressPrefix string = '10.42.0.0/16'
param containerAppsSubnetPrefix string = '10.42.0.0/23'
param privateEndpointsSubnetPrefix string = '10.42.2.0/24'
param postgresSubnetPrefix string = '10.42.3.0/24'

@description('Private DNS zone for PostgreSQL; must end in .postgres.database.azure.com.')
param postgresPrivateDnsZoneName string = 'eve-live-internal.postgres.database.azure.com'

@secure()
@description('Random, unique bootstrap password. This database remains private; do not use as the application login.')
param postgresAdministratorPassword string

@description('PostgreSQL compute SKU; confirm regional availability and cost.')
param postgresSkuName string = 'Standard_B1ms'

@description('Common deployment tags.')
param tags object = {}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsName
}

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
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
  tags: tags
}

resource keyVaultPrivateDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: keyVaultPrivateDnsZone
  name: '${virtualNetworkName}-keyvault-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: virtualNetwork.id }
  }
}

resource runtimeVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: runtimeVaultName
}

resource clientVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: clientVaultName
}

resource runtimeVaultPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${runtimeVault.name}-${privateEndpointSuffix}-private-endpoint'
  location: location
  tags: tags
  properties: {
    subnet: { id: resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetworkName, 'private-endpoints') }
    privateLinkServiceConnections: [
      {
        name: '${runtimeVault.name}-${privateEndpointSuffix}-connection'
        properties: { privateLinkServiceId: runtimeVault.id, groupIds: ['vault'] }
      }
    ]
  }
}

resource runtimeVaultPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: runtimeVaultPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'key-vault', properties: { privateDnsZoneId: keyVaultPrivateDnsZone.id } }
    ]
  }
}

resource clientVaultPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: '${clientVault.name}-${privateEndpointSuffix}-private-endpoint'
  location: location
  tags: tags
  properties: {
    subnet: { id: resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetworkName, 'private-endpoints') }
    privateLinkServiceConnections: [
      {
        name: '${clientVault.name}-${privateEndpointSuffix}-connection'
        properties: { privateLinkServiceId: clientVault.id, groupIds: ['vault'] }
      }
    ]
  }
}

resource clientVaultPrivateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: clientVaultPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'key-vault', properties: { privateDnsZoneId: keyVaultPrivateDnsZone.id } }
    ]
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

resource postgresServer 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: postgresServerName
  location: location
  tags: tags
  sku: {
    name: postgresSkuName
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: 'eveprovisioner'
    administratorLoginPassword: postgresAdministratorPassword
    storage: {
      storageSizeGB: 32
      autoGrow: 'Disabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Disabled'
      delegatedSubnetResourceId: resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetworkName, 'postgresql-flexible-server')
      privateDnsZoneArmResourceId: postgresPrivateDnsZone.id
    }
    highAvailability: {
      mode: 'Disabled'
    }
  }
  dependsOn: [
    postgresPrivateDnsLink
  ]
}

resource postgresDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgresServer
  name: 'eve'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2025-07-01' = {
  name: containerAppsEnvironmentName
  location: location
  tags: tags
  properties: {
    publicNetworkAccess: 'Enabled'
    vnetConfiguration: {
      infrastructureSubnetId: resourceId('Microsoft.Network/virtualNetworks/subnets', virtualNetworkName, 'container-apps-infrastructure')
      internal: false
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
  dependsOn: [
    virtualNetwork
  ]
}

output containerAppsEnvironmentId string = containerAppsEnvironment.id
output containerAppsDefaultDomain string = containerAppsEnvironment.properties.defaultDomain
output postgresFqdn string = postgresServer.properties.fullyQualifiedDomainName
output postgresDatabaseName string = postgresDatabase.name
output keyVaultPrivateDnsZoneId string = keyVaultPrivateDnsZone.id
