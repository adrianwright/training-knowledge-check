param(
    [string]$Output = "results\security-$(Get-Date -Format yyyyMMdd-HHmmss)",
    [ValidateRange(0,1800)][int]$WaitSeconds = 600
)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & uv run --no-sync knowledge-check generate --scenario security --count 1 --output $Output
    $agentResult = $LASTEXITCODE
    if (-not (Test-Path "$Output\manifest.json")) { throw 'Security prerequisites failed.' }
    & uv run --no-sync knowledge-check verify-defender --manifest "$Output\manifest.json" `
        --wait-seconds $WaitSeconds
    if ($LASTEXITCODE -ne 0) { throw 'Defender detection is unverified. This test did not pass.' }
    if ($agentResult -ne 0) { throw 'A Defender alert was found, but the agent quality gate failed.' }
} finally { Pop-Location }
