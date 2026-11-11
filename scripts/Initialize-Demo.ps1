$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & uv sync --group dev
    if ($LASTEXITCODE -ne 0) { throw 'Dependency restore failed.' }
} finally { Pop-Location }
