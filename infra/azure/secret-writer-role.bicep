targetScope = 'subscription'

@description('Stable display name for Eve administrators who manage client Ads keys without reading them.')
param roleName string = 'Eve Client Secret Manager'

resource clientSecretManagerRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  name: guid(subscription().id, 'eve-client-secret-manager-v1')
  properties: {
    roleName: roleName
    description: 'Set, soft-delete, and recover Eve client secrets. Does not list or read secret values.'
    type: 'CustomRole'
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [
          'Microsoft.KeyVault/vaults/secrets/setSecret/action'
          'Microsoft.KeyVault/vaults/secrets/delete'
          'Microsoft.KeyVault/vaults/secrets/recover/action'
        ]
        notDataActions: []
      }
    ]
    assignableScopes: [subscription().id]
  }
}

output roleDefinitionId string = clientSecretManagerRole.id
