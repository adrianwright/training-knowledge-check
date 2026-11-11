[CmdletBinding()]
param(
    [string]$AppVersion = '1.0.0',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $arguments = @(
        'run', '--no-sync', 'knowledge-check', 'publish-m365',
        '--app-version', $AppVersion
    )
    if ($Apply) { $arguments += '--apply' }
    & uv @arguments
    if ($LASTEXITCODE -ne 0) {
        throw 'Personal-scope publication failed. Inspect private .local\m365-publishing receipts.'
    }
} finally { Pop-Location }
