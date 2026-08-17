[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Resume,
    [switch]$ForceReindex
)

. (Join-Path $PSScriptRoot '_corpus-common.ps1')
try {
    if ($Resume -and -not $Apply) { throw '-Resume 只能与 -Apply 一起使用。' }
    if ($ForceReindex -and $Apply) { throw '-ForceReindex 不能与 -Apply 一起使用。' }
    $paths = Get-CumcmLabPaths
    $arguments = @(
        '--problems-path', [string]$paths.problems_intake,
        '--papers-path', [string]$paths.papers_intake,
        '--split-config', (Join-Path $script:TrainerRoot 'config\corpus-split.yaml'),
        '--report-dir', (Join-Path $script:TrainerRoot 'corpus'),
        '--local-paths', (Join-Path (Split-Path $script:TrainerRoot -Parent) 'local-paths.toml')
    )
    $arguments += if ($Apply) { '--apply' } else { '--dry-run' }
    if ($Resume) { $arguments += '--resume' }
    if ($ForceReindex) { $arguments += '--force-reindex' }
    Invoke-CumcmTool 'import_corpus.py' $arguments
} catch {
    Write-Error $_
    exit 1
}
