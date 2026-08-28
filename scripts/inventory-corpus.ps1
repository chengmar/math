[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot '_corpus-common.ps1')
try {
    $paths = Get-CumcmLabPaths
    Invoke-CumcmTool 'inventory_corpus.py' @(
        '--problems-path', [string]$paths.problems_intake,
        '--papers-path', [string]$paths.papers_intake,
        '--split-config', (Join-Path $script:TrainerRoot 'config\corpus-split.yaml'),
        '--report-dir', (Join-Path $script:TrainerRoot 'corpus')
    )
} catch {
    Write-Error $_
    exit 1
}
