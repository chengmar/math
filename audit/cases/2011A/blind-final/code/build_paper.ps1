param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

$workspaceFull = [System.IO.Path]::GetFullPath($Workspace).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
$workspacePrefix = $workspaceFull + [System.IO.Path]::DirectorySeparatorChar
$paperDir = [System.IO.Path]::GetFullPath((Join-Path $workspaceFull 'paper'))
$buildDir = [System.IO.Path]::GetFullPath((Join-Path $paperDir 'build'))
$reportsDir = [System.IO.Path]::GetFullPath((Join-Path $workspaceFull 'reports'))

foreach ($target in @($paperDir, $buildDir, $reportsDir)) {
    if (-not $target.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Resolved target is outside workspace: $target"
    }
}
if (-not (Test-Path -LiteralPath $paperDir -PathType Container)) {
    throw "Paper directory not found: $paperDir"
}
if (Test-Path -LiteralPath $buildDir) {
    Remove-Item -LiteralPath $buildDir -Recurse -Force
}
[void](New-Item -ItemType Directory -Path $buildDir)
if (-not (Test-Path -LiteralPath $reportsDir)) {
    [void](New-Item -ItemType Directory -Path $reportsDir)
}

$sourceFiles = @(
    (Join-Path $paperDir 'main.tex'),
    (Join-Path $paperDir 'preamble.tex'),
    (Join-Path $paperDir 'paper.md')
)
$sourceFiles += @(Get-ChildItem -LiteralPath (Join-Path $paperDir 'generated') -File -Filter '*.tex' | Select-Object -ExpandProperty FullName)
$sourceFiles += @(Get-ChildItem -LiteralPath (Join-Path $workspaceFull 'figures') -File -Filter '*.png' | Select-Object -ExpandProperty FullName)
$sourceRecords = foreach ($path in ($sourceFiles | Sort-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Paper build input missing: $path"
    }
    $relative = [System.IO.Path]::GetRelativePath($workspaceFull, $path).Replace('\', '/')
    [PSCustomObject]@{
        path = $relative
        bytes = [int64](Get-Item -LiteralPath $path).Length
        sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

function Invoke-XeLaTeXBuild {
    param([int]$Run)
    Push-Location $paperDir
    try {
        $lines = @(& xelatex '-interaction=nonstopmode' '-halt-on-error' "-output-directory=$buildDir" 'main.tex' 2>&1 | ForEach-Object { [string]$_ })
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    [PSCustomObject]@{
        run = $Run
        status = if ($exitCode -eq 0) { 'pass' } else { 'fail' }
        exit_code = [int]$exitCode
        salient_output = @($lines | Where-Object { $_ -match 'Output written|Transcript written|LaTeX Error|Undefined control sequence|Emergency stop' })
    }
}

$compileRuns = @(
    (Invoke-XeLaTeXBuild -Run 1),
    (Invoke-XeLaTeXBuild -Run 2)
)
$compileStatus = if (($compileRuns | Where-Object { $_.status -ne 'pass' }).Count -eq 0) { 'pass' } else { 'fail' }
$builtPdf = Join-Path $buildDir '<SOURCE_FILE_REDACTED>'
$builtLog = Join-Path $buildDir 'main.log'
if ($compileStatus -ne 'pass' -or -not (Test-Path -LiteralPath $builtPdf) -or -not (Test-Path -LiteralPath $builtLog)) {
    $payload = [ordered]@{
        status = 'fail'
        clean_build_directory = 'paper/build'
        source_files = $sourceRecords
        compile_runs = $compileRuns
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $reportsDir 'paper-build.json'),
        ($payload | ConvertTo-Json -Depth 8) + [Environment]::NewLine,
        (New-Object System.Text.UTF8Encoding($false))
    )
    throw 'XeLaTeX clean build failed.'
}

$paperPdf = Join-Path $paperDir '<SOURCE_FILE_REDACTED>'
$paperLog = Join-Path $paperDir 'main.log'
Copy-Item -LiteralPath $builtPdf -Destination $paperPdf -Force
Copy-Item -LiteralPath $builtLog -Destination $paperLog -Force
Remove-Item -LiteralPath $buildDir -Recurse -Force
if (Test-Path -LiteralPath $buildDir) {
    throw "Temporary paper build directory was not removed: $buildDir"
}

$payload = [ordered]@{
    status = 'pass'
    clean_build_directory = 'paper/build (temporary)'
    temporary_build_directory_removed_status = 'pass'
    consecutive_compile_count = 2
    source_files = $sourceRecords
    compile_runs = $compileRuns
    pdf = [ordered]@{
        path = 'paper/<SOURCE_FILE_REDACTED>'
        bytes = [int64](Get-Item -LiteralPath $paperPdf).Length
        sha256 = (Get-FileHash -LiteralPath $paperPdf -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    log = [ordered]@{
        path = 'paper/main.log'
        sha256 = (Get-FileHash -LiteralPath $paperLog -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
[System.IO.File]::WriteAllText(
    (Join-Path $reportsDir 'paper-build.json'),
    ($payload | ConvertTo-Json -Depth 8) + [Environment]::NewLine,
    (New-Object System.Text.UTF8Encoding($false))
)

Write-Output 'pass: paper built twice from a clean build directory and copied with its manifest'
