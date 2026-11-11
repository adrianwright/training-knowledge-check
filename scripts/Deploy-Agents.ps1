[CmdletBinding()]
param([string]$Prefix = 'knowledge', [switch]$Apply)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $infra = Get-Content .\.local\infra-outputs.json -Raw | ConvertFrom-Json
    $arguments = @(
        'run', '--no-sync', 'knowledge-check', 'deploy',
        '--endpoint', $infra.projectEndpoint.value,
        '--project-resource-id', $infra.projectResourceId.value,
        '--model', $infra.modelDeployment.value, '--prefix', $Prefix
    )
    if ($Apply) { $arguments += '--apply' }
    & uv @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Foundry prompt-agent deployment failed.' }
} finally { Pop-Location }
