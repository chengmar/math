[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot '_corpus-common.ps1')
try {
    Invoke-CumcmTool 'queue_status.py' @('--queue', (Join-Path $script:TrainerRoot 'runtime\training-queue-state.json'))
} catch {
    Write-Error $_
    exit 1
}
