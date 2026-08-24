[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Resume
)

. (Join-Path $PSScriptRoot '_corpus-common.ps1')
try {
    $paths = Get-CumcmLabPaths
    foreach ($directory in @(
        $paths.problems_intake, $paths.papers_intake, $paths.question_bank,
        $paths.reference_vault, $paths.dev_vault, $paths.exam_vault,
        $paths.runtime_cases, (Join-Path $script:TrainerRoot 'corpus'),
        (Join-Path $script:TrainerRoot 'runtime')
    )) {
        New-Item -ItemType Directory -Force -Path ([string]$directory) | Out-Null
    }
    $common = @(
        '--problems-path', [string]$paths.problems_intake,
        '--papers-path', [string]$paths.papers_intake,
        '--split-config', (Join-Path $script:TrainerRoot 'config\corpus-split.yaml'),
        '--report-dir', (Join-Path $script:TrainerRoot 'corpus')
    )
    Invoke-CumcmTool 'inventory_corpus.py' $common
    $importArguments = $common + @('--local-paths', (Join-Path (Split-Path $script:TrainerRoot -Parent) 'local-paths.toml'))
    $importArguments += if ($Apply) { '--apply' } else { '--dry-run' }
    if ($Resume) { $importArguments += '--resume' }
    Invoke-CumcmTool 'import_corpus.py' $importArguments
    if ($Apply) {
        Invoke-CumcmTool 'validate_corpus.py' ($common + @(
            '--local-paths', (Join-Path (Split-Path $script:TrainerRoot -Parent) 'local-paths.toml'),
            '--trainer-root', $script:TrainerRoot,
            '--import-result', (Join-Path $script:TrainerRoot 'corpus\import-result.json')
        ))
        Invoke-CumcmTool 'initialize_training_queue.py' @(
            '--validation', (Join-Path $script:TrainerRoot 'corpus\corpus-validation.json'),
            '--queue', (Join-Path $script:TrainerRoot 'runtime\training-queue-state.json'),
            '--public-plan', (Join-Path $script:TrainerRoot 'corpus\training-queue.yaml'),
            '--seal-file', (Join-Path ([string]$paths.exam_vault) '2023A\SEALED.json')
        )
    }
} catch {
    Write-Error $_
    exit 1
}
