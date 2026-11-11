param location string
param principalId string
param principalType string
param modelName string
param modelVersion string
param modelCapacity int

var accountName = 'kc-${uniqueString(resourceGroup().id)}'
var userRoleId = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

resource account 'Microsoft.CognitiveServices/accounts@2026-03-15-preview' = {
  name: accountName
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: accountName
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    a365LoggingEnabled: true
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2026-03-15-preview' = {
  parent: account
  name: 'knowledge-check'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Fictional training knowledge check'
    description: 'Two prompt agents, synthetic content, human approval and security exercises.'
  }
}

resource operatorAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, principalId, userRoleId)
  scope: project
  properties: {
    principalId: principalId
    principalType: principalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', userRoleId)
  }
}

var standardFilters = [
  for item in [
    { name: 'Hate', source: 'Prompt' }
    { name: 'Sexual', source: 'Prompt' }
    { name: 'Violence', source: 'Prompt' }
    { name: 'Selfharm', source: 'Prompt' }
    { name: 'Hate', source: 'Completion' }
    { name: 'Sexual', source: 'Completion' }
    { name: 'Violence', source: 'Completion' }
    { name: 'Selfharm', source: 'Completion' }
  ]: {
    name: item.name
    source: item.source
    enabled: true
    blocking: true
    severityThreshold: 'Medium'
  }
]

resource shields 'Microsoft.CognitiveServices/accounts/raiPolicies@2026-03-15-preview' = {
  dependsOn: [project]
  parent: account
  name: 'knowledge-check-shields'
  properties: {
    basePolicyName: 'Microsoft.Default'
    mode: 'Default'
    contentFilters: concat(standardFilters, [
      {
        name: 'Jailbreak'
        source: 'Prompt'
        enabled: true
        blocking: true
      }
    ])
  }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2026-03-15-preview' = {
  parent: account
  name: modelName
  sku: {
    name: 'GlobalStandard'
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    raiPolicyName: shields.name
    versionUpgradeOption: 'NoAutoUpgrade'
  }
}

output accountResourceId string = account.id
output projectResourceId string = project.id
output projectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${project.name}'
output modelDeployment string = model.name
