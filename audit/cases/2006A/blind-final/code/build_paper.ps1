[CmdletBinding()]
param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$paperPath = Join-Path $workspacePath 'paper'
$resultsPath = Join-Path $workspacePath 'results'
New-Item -ItemType Directory -Path $resultsPath -Force | Out-Null
$reportPath = Join-Path $resultsPath 'paper-build-report.json'
$engine = Get-Command xelatex -ErrorAction SilentlyContinue
if ($null -eq $engine) {
    $report = [ordered]@{
        status = 'needs_review'
        reason = 'xelatex unavailable'
        command = 'xelatex --disable-installer -interaction=nonstopmode -halt-on-error -file-line-error main.tex'
        runs = 0
    }
    [System.IO.File]::WriteAllText(
        $reportPath,
        (($report | ConvertTo-Json -Depth 5) + [Environment]::NewLine),
        $utf8NoBom
    )
    Write-Output 'paper_build_status=needs_review'
    Write-Output 'reason=xelatex unavailable'
    exit 2
}

$versionLine = (& $engine.Source --version | Select-Object -First 1)
Push-Location $paperPath
try {
    for ($run = 1; $run -le 2; $run++) {
        & $engine.Source --disable-installer -interaction=nonstopmode -halt-on-error -file-line-error 'main.tex'
        if ($LASTEXITCODE -ne 0) {
            throw "XeLaTeX run $run failed with exit code $LASTEXITCODE."
        }
    }
}
finally {
    Pop-Location
}

$pdf = Join-Path $paperPath '<SOURCE_FILE_REDACTED>'
if (-not (Test-Path -LiteralPath $pdf -PathType Leaf)) {
    throw '<SOURCE_FILE_REDACTED> is absent.'
}
$log = Join-Path $paperPath 'main.log'
if (-not (Test-Path -LiteralPath $log -PathType Leaf)) {
    throw 'XeLaTeX returned success but main.log is absent.'
}

$warningPattern = '(LaTeX|Package .*?) Warning|Undefined control sequence|Overfull \\\\hbox|Underfull \\\\hbox|! LaTeX Error'
$warningLines = @(
    Select-String -LiteralPath $log -Pattern $warningPattern |
        ForEach-Object { $_.Line.Trim() } |
        Sort-Object -Unique
)
$status = if ($warningLines.Count -eq 0) { 'pass' } else { 'needs_review' }
$pdfItem = Get-Item -LiteralPath $pdf
$report = [ordered]@{
    status = $status
    engine = $engine.Source
    engine_version = $versionLine
    command = 'xelatex --disable-installer -interaction=nonstopmode -halt-on-error -file-line-error main.tex'
    working_directory = 'paper'
    runs = 2
    pdf = 'paper/<SOURCE_FILE_REDACTED>'
    pdf_bytes = $pdfItem.Length
    pdf_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $pdf).Hash.ToLowerInvariant()
    log = 'paper/main.log'
    warning_count = $warningLines.Count
    warnings = $warningLines
}
[System.IO.File]::WriteAllText(
    $reportPath,
    (($report | ConvertTo-Json -Depth 5) + [Environment]::NewLine),
    $utf8NoBom
)

$transientPaths = @(
    (Join-Path $paperPath 'main.aux'),
    (Join-Path $paperPath 'main.out'),
    (Join-Path $paperPath 'main.toc'),
    (Join-Path $paperPath 'main.xdv')
)
foreach ($transientPath in $transientPaths) {
    if (Test-Path -LiteralPath $transientPath -PathType Leaf) {
        Remove-Item -LiteralPath $transientPath -Force
    }
}

if ($status -ne 'pass') {
    Write-Output 'paper_build_status=needs_review'
    Write-Output "warning_count=$($warningLines.Count)"
    exit 3
}
Write-Output 'paper_build_status=pass'
Write-Output "pdf=$pdf"
