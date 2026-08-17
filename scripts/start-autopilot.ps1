[CmdletBinding()]
param(
    [int]$MaxCases,
    [string]$Model = 'gpt-5.4',
    [ValidateSet('low', 'medium', 'high', 'xhigh')][string]$Reasoning = 'xhigh',
    [switch]$Foreground
)

$arguments = @{ MaxCases = $MaxCases; Model = $Model; Reasoning = $Reasoning; All = $true }
if (-not $Foreground) { $arguments.Detach = $true }
& (Join-Path $PSScriptRoot 'run-training-queue.ps1') @arguments
exit $LASTEXITCODE
