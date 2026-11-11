param(
    [string]$Output = "results\knowledge-$(Get-Date -Format yyyyMMdd-HHmmss)"
)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $state = Get-Content .\.local\foundry.json -Raw | ConvertFrom-Json
    if (-not $state.mcp) { throw 'Deploy-Knowledge.ps1 -Apply must succeed first.' }
    & uv run --no-sync knowledge-check preflight
    if ($LASTEXITCODE -ne 0) { throw 'Native A365 collection preflight failed.' }
    & uv run --no-sync knowledge-check generate --scenario mixed --count 3 --output $Output
    if ($LASTEXITCODE -ne 0) { throw 'Live knowledge retrieval/workflow failed; inspect evidence.' }
    $calls = Get-ChildItem -LiteralPath $Output -Recurse -Filter '*-tool-calls.json'
    if ($calls.Count -lt 6) { throw 'Missing actual author/reviewer MCP call receipts.' }
    Write-Host "Verified real knowledge-base tool calls. Private evidence: $Output"
    Write-Host 'A365 portal tool visibility remains a separate readback check.'
} finally { Pop-Location }
