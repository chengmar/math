[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot '_corpus-common.ps1')
try {
    $paths = Get-CumcmLabPaths
    $arguments = @(
        '--problems-path', [string]$paths.problems_intake,
        '--papers-path', [string]$paths.papers_intake,
        '--split-config', (Join-Path $script:TrainerRoot 'config\corpus-split.yaml'),
        '--report-dir', (Join-Path $script:TrainerRoot 'corpus'),
        '--local-paths', (Join-Path (Split-Path $script:TrainerRoot -Parent) 'local-paths.toml'),
        '--trainer-root', $script:TrainerRoot,
        '--import-result', (Join-Path $script:TrainerRoot 'corpus\import-result.json')
    )
    Invoke-CumcmTool 'validate_corpus.py' $arguments
} catch {
    Write-Error $_
    exit 1
}
