[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot '_corpus-common.ps1')
try {
    Invoke-CumcmTool 'autopilot_status.py' @(
        '--runtime-dir', (Join-Path $script:TrainerRoot 'runtime'),
        '--queue', (Join-Path $script:TrainerRoot 'runtime\training-queue-state.json')
    )
} catch {
    Write-Error $_
    exit 1
}
