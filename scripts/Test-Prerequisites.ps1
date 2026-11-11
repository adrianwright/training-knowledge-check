param([switch]$Security)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $arguments = @('run', '--no-sync', 'knowledge-check', 'preflight')
    if ($Security) { $arguments += '--security' }
    & uv @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Preflight failed; resolve the reported control/permission.' }
} finally { Pop-Location }
