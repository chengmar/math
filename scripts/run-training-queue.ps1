[CmdletBinding()]
param(
    [int]$MaxCases,
    [string]$CaseId,
    [string]$StopAfterPhase,
    [string]$Model = 'gpt-5.6-sol',
    [ValidateSet('low', 'medium', 'high', 'xhigh', 'max')][string]$Reasoning = 'max',
    [switch]$All,
    [switch]$DryRun,
    [switch]$Detach,
    [switch]$Resume
)

. (Join-Path $PSScriptRoot '_corpus-common.ps1')
try {
    $paths = Get-CumcmLabPaths
    $arguments = @(
        '--queue', (Join-Path $script:TrainerRoot 'runtime\training-queue-state.json'),
        '--runtime-dir', (Join-Path $script:TrainerRoot 'runtime'),
        '--trainer-root', $script:TrainerRoot,
        '--codex-home', [string]$paths.codex_home,
        '--model', $Model,
        '--reasoning', $Reasoning,
        '--max-retries', '1'
    )
    if ($MaxCases -gt 0) { $arguments += @('--max-cases', [string]$MaxCases) }
    if ($CaseId) { $arguments += @('--case-id', $CaseId) }
    if ($StopAfterPhase) { $arguments += @('--stop-after-phase', $StopAfterPhase) }
    if ($All) { $arguments += '--all' }
    if ($DryRun) { $arguments += '--dry-run' }
    if ($Detach) { $arguments += '--detach' }
    if ($Resume) { $arguments += '--resume' }
    Invoke-CumcmTool 'run_training_queue.py' $arguments
} catch {
    Write-Error $_
    exit 1
}
