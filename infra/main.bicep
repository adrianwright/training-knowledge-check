targetScope = 'subscription'

@description('Use a new name for an isolated demo resource group.')
@minLength(3)
@maxLength(32)
param environmentName string = 'training-knowledge-check'
param location string = 'eastus2'
@description('Object ID of the operator who will create and invoke the prompt agents.')
param principalId string
@allowed(['User', 'ServicePrincipal'])
param principalType string = 'User'
param modelName string = 'gpt-5.6-luna'
param modelVersion string = '2026-07-09'
@minValue(1)
param modelCapacity int = 30

resource group 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: {
    purpose: 'fictional-training-demo'
    managedBy: 'bicep'
  }
}

module foundry './foundry.bicep' = {
  name: '${environmentName}-foundry'
  scope: group
  params: {
    location: location
    principalId: principalId
    principalType: principalType
    modelName: modelName
    modelVersion: modelVersion
    modelCapacity: modelCapacity
  }
}

output resourceGroupName string = group.name
output accountResourceId string = foundry.outputs.accountResourceId
output projectResourceId string = foundry.outputs.projectResourceId
output projectEndpoint string = foundry.outputs.projectEndpoint
output modelDeployment string = foundry.outputs.modelDeployment
