[CmdletBinding()]
param(
    [int]$MaxCases,
    [string]$StopAfterPhase,
    [string]$Model = 'gpt-5.6-sol',
    [ValidateSet('low', 'medium', 'high', 'xhigh', 'max')][string]$Reasoning = 'max'
)

& (Join-Path $PSScriptRoot 'run-training-queue.ps1') -Resume -MaxCases $MaxCases -StopAfterPhase $StopAfterPhase -Model $Model -Reasoning $Reasoning
exit $LASTEXITCODE
