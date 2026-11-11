targetScope = 'resourceGroup'

@description('Search region; independent of the existing Foundry model region.')
param location string = 'eastus'
param accountName string
param projectName string
param principalId string
@allowed(['User', 'ServicePrincipal'])
param principalType string = 'User'

resource account 'Microsoft.CognitiveServices/accounts@2026-03-15-preview' existing = {
  name: accountName
}
resource project 'Microsoft.CognitiveServices/accounts/projects@2026-03-15-preview' existing = {
  parent: account
  name: projectName
}
resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: 'srch-knowledge-${uniqueString(resourceGroup().id)}'
  location: location
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'Default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: true
    semanticSearch: 'free'
  }
}
var operatorRoles = [
  '7ca78c08-252a-4471-8644-bb5ff32d4ba0' // Search Service Contributor: index/KB definitions
  '8ebe5a00-799e-43f5-93ac-243d3dce84a7' // Search Index Data Contributor: seed fictional content
]
resource operatorAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for role in operatorRoles: {
    scope: search
    name: guid(search.id, principalId, role)
    properties: {
      principalId: principalId
      principalType: principalType
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role)
    }
  }
]
resource projectRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, project.id, 'knowledge-reader')
  properties: {
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
  }
}
resource modelAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, search.id, 'knowledge-model')
  properties: {
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
  }
}

output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchResourceId string = search.id
output openaiEndpoint string = 'https://${account.name}.openai.azure.com'
