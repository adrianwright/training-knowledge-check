[CmdletBinding()]
param(
    [string]$SearchLocation = 'eastus',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $infra = Get-Content .\.local\infra-outputs.json -Raw | ConvertFrom-Json
    $parts = $infra.projectResourceId.value -split '/'
    $principal = & az ad signed-in-user show --query id -o tsv
    if ($LASTEXITCODE -ne 0) { throw 'Operator object ID lookup failed.' }
    $action = if ($Apply) { 'create' } else { 'what-if' }
    & az deployment group $action --resource-group $infra.resourceGroupName.value `
        --name knowledge-search --template-file .\infra\knowledge.bicep --parameters `
        "accountName=$($parts[8])" "projectName=$($parts[10])" "principalId=$principal" `
        "location=$SearchLocation"
    if ($LASTEXITCODE -ne 0) { throw 'Basic Search infrastructure deployment failed.' }
    if (-not $Apply) { return }
    $outputs = & az deployment group show --resource-group $infra.resourceGroupName.value `
        --name knowledge-search --query properties.outputs -o json
    if ($LASTEXITCODE -ne 0) { throw 'Search output readback failed.' }
    $outputs | Set-Content -Encoding utf8 .\.local\knowledge-outputs.json
    & uv run --no-sync knowledge-check setup-knowledge --apply
    if ($LASTEXITCODE -ne 0) {
        throw 'Knowledge setup failed. If newly assigned RBAC is propagating, wait and rerun.'
    }
    $mcp = Get-Content .\.local\knowledge-mcp.json -Raw | ConvertFrom-Json
    & uv run --no-sync knowledge-check attach-mcp --url $mcp.endpoint `
        --connection $mcp.connection_id --apply
    if ($LASTEXITCODE -ne 0) { throw 'Attaching Foundry IQ to the prompt agents failed.' }
} finally { Pop-Location }
