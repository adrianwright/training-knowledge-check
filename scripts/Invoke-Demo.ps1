param(
    [ValidateSet('mixed','clean','ambiguous','repaired')][string]$Scenario = 'mixed',
    [ValidateRange(1,20)][int]$Count = 3,
    [string]$Output = "results\run-$(Get-Date -Format yyyyMMdd-HHmmss)"
)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & uv run --no-sync knowledge-check generate --scenario $Scenario --count $Count --output $Output
    if ($LASTEXITCODE -ne 0) { throw 'The live Foundry demo failed; inspect saved evidence.' }
} finally { Pop-Location }
