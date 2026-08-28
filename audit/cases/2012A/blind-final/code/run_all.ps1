param(
    [string]$Workspace = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw '[fail] PowerShell 7 or later is required for deterministic legacy-XLS text conversion; invoke with pwsh'
}

if ([string]::IsNullOrWhiteSpace($Workspace)) {
    $Workspace = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($PSScriptRoot, '..'))
}
$workspacePath = [System.IO.Path]::GetFullPath($Workspace)
$codePath = [System.IO.Path]::GetFullPath($PSScriptRoot)
$expectedCodePath = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($workspacePath, 'code'))
if (-not [string]::Equals($codePath, $expectedCodePath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "[fail] code directory is not the selected workspace's code directory"
}

$python = (Get-Command python -ErrorAction Stop).Source
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONHASHSEED = '20240824'
$env:MPLBACKEND = 'Agg'
$env:SOURCE_DATE_EPOCH = '0'

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "[fail] $Step exited with code $LASTEXITCODE"
    }
    Write-Output "[pass] $Step"
}

& $python ([System.IO.Path]::Combine($codePath, 'check_workspace.py')) --workspace $workspacePath
Assert-LastExitCode 'self-contained source and input check'

foreach ($name in @('results', 'figures')) {
    $target = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($workspacePath, $name))
    $expected = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($workspacePath, $name))
    if (-not [string]::Equals($target, $expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "[fail] generated-directory target validation failed: $name"
    }
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    [System.IO.Directory]::CreateDirectory($target) | Out-Null
}

$paperDir = [System.IO.Path]::Combine($workspacePath, 'paper')
foreach ($name in @('main.aux', 'main.log', 'main.out', 'main.toc', '<SOURCE_FILE_REDACTED>')) {
    $target = [System.IO.Path]::Combine($paperDir, $name)
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Force
    }
}

& ([System.IO.Path]::Combine($codePath, 'extract_xls.ps1')) -Workspace $workspacePath
Assert-LastExitCode 'legacy XLS read-only extraction'

& $python ([System.IO.Path]::Combine($codePath, 'prepare_data.py')) --workspace $workspacePath
Assert-LastExitCode 'data preparation and audit'

& $python ([System.IO.Path]::Combine($codePath, 'analyze.py')) --workspace $workspacePath
Assert-LastExitCode 'models, robustness checks, results, and figures'

Push-Location $paperDir
try {
    & xelatex -interaction=nonstopmode -halt-on-error main.tex
    Assert-LastExitCode 'XeLaTeX pass 1'
    & xelatex -interaction=nonstopmode -halt-on-error main.tex
    Assert-LastExitCode 'XeLaTeX pass 2'
}
finally {
    Pop-Location
}

foreach ($name in @('main.aux', 'main.log', 'main.out', 'main.toc')) {
    $target = [System.IO.Path]::Combine($paperDir, $name)
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Force
    }
}

& $python ([System.IO.Path]::Combine($codePath, 'verify.py')) --workspace $workspacePath
Assert-LastExitCode 'tri-state internal verification'

& $python ([System.IO.Path]::Combine($codePath, 'build_manifest.py')) --workspace $workspacePath
Assert-LastExitCode 'deterministic artifact manifest'

Write-Output '[pass] self-contained computational rerun complete'
Write-Output '[needs_review] external validation and unique mathematical truth are not claimed'
