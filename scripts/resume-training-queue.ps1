[CmdletBinding()]
param(
    [int]$MaxCases,
    [string]$StopAfterPhase,
    [string]$Model = 'gpt-5.4',
    [ValidateSet('low', 'medium', 'high', 'xhigh')][string]$Reasoning = 'xhigh'
)

& (Join-Path $PSScriptRoot 'run-training-queue.ps1') -Resume -MaxCases $MaxCases -StopAfterPhase $StopAfterPhase -Model $Model -Reasoning $Reasoning
exit $LASTEXITCODE
