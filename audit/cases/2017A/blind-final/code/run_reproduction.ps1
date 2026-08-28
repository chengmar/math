param(
    [Parameter(Mandatory = $true)]
    [string]$InputRoot,
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [int]$StabilityReplicatesPerSeed = 40
)

$ErrorActionPreference = 'Stop'
$solutionRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
& (Join-Path $solutionRoot 'run_reproduction.ps1') `
    -InputRoot $InputRoot `
    -OutputRoot $OutputRoot `
    -StabilityReplicatesPerSeed $StabilityReplicatesPerSeed
