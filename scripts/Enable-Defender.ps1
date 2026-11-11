[CmdletBinding()]
param([switch]$IncludePromptEvidence, [switch]$Apply)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
$temporary = $null
try {
    $infra = Get-Content .\.local\infra-outputs.json -Raw | ConvertFrom-Json
    $subscription = ($infra.accountResourceId.value -split '/')[2]
    $uri = "https://management.azure.com/subscriptions/$subscription/providers/Microsoft.Security/pricings/AI?api-version=2026-07-01"
    $raw = & az rest --method get --uri $uri -o json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read the current Defender for AI Services plan.' }
    $current = $raw | ConvertFrom-Json
    $extensions = @($current.properties.extensions | Where-Object { $null -ne $_ })
    if ($IncludePromptEvidence) {
        $extensions = @($extensions | Where-Object { $_.name -ne 'AIPromptEvidence' })
        $extensions += @{ name = 'AIPromptEvidence'; isEnabled = 'True' }
    }
    $properties = @{ pricingTier = 'Standard'; extensions = $extensions }
    foreach ($key in @('subPlan','enforce')) {
        if ($current.properties.PSObject.Properties.Name -contains $key) {
            $properties[$key] = $current.properties.$key
        }
    }
    $body = @{ properties = $properties } | ConvertTo-Json -Depth 30
    Write-Warning 'This enables a PAID, SUBSCRIPTION-WIDE Defender plan. Prompt evidence can retain suspicious prompt snippets.'
    $body | Write-Output
    if (-not $Apply) { Write-Host 'Preview only. Use -Apply to change the plan.'; return }
    New-Item -ItemType Directory -Force .\.local | Out-Null
    $raw | Set-Content -Encoding utf8 ".\.local\defender-before-$(Get-Date -Format yyyyMMdd-HHmmss).json"
    $temporary = New-TemporaryFile
    $body | Set-Content -Encoding utf8 $temporary.FullName
    & az rest --method put --uri $uri --body "@$($temporary.FullName)" -o json
    if ($LASTEXITCODE -ne 0) { throw 'Defender plan update failed.' }
    Write-Host 'Defender plan updated. Run Test-Prerequisites.ps1 -Security to verify readback.'
} finally {
    if ($temporary) { Remove-Item -LiteralPath $temporary.FullName -Force }
    Pop-Location
}
