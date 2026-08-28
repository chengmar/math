param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'solve.ps1') -Workspace $Workspace
& (Join-Path $PSScriptRoot 'verify-paper.ps1') -Workspace $Workspace
& (Join-Path $PSScriptRoot 'reproduce.ps1') -Workspace $Workspace
Write-Output '[PASS] solve, paper consistency, and two-run reproducibility completed'
