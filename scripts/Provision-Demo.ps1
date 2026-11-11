[CmdletBinding()]
param(
    [string]$EnvironmentName = 'training-knowledge-check',
    [string]$Location = 'eastus2',
    [string]$PrincipalId,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    if ($EnvironmentName -notmatch '^[a-z][a-z0-9-]{2,31}$') {
        throw 'EnvironmentName must be 3-32 lowercase letters, digits or hyphens.'
    }
    if (-not $PrincipalId) {
        $PrincipalId = & az ad signed-in-user show --query id -o tsv
        if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the signed-in operator.' }
    }
    $name = "$EnvironmentName-infra"
    $action = if ($Apply) { 'create' } else { 'what-if' }
    Write-Host "Subscription: $(& az account show --query name -o tsv)"
    Write-Host "Target: rg-$EnvironmentName in $Location. Action: $action"
    & az deployment sub $action --name $name --location $Location `
        --template-file .\infra\main.bicep `
        --parameters "environmentName=$EnvironmentName" "location=$Location" "principalId=$PrincipalId"
    if ($LASTEXITCODE -ne 0) { throw 'Bicep deployment failed.' }
    if ($Apply) {
        $outputs = & az deployment sub show --name $name --query properties.outputs -o json
        if ($LASTEXITCODE -ne 0) { throw 'Could not retrieve deployment outputs.' }
        New-Item -ItemType Directory -Force .\.local | Out-Null
        $outputs | Set-Content -Encoding utf8 .\.local\infra-outputs.json
        Write-Host 'Saved deployment outputs to .local\infra-outputs.json (gitignored).'
    }
} finally { Pop-Location }
