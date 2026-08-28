param(
    [Parameter(Mandatory = $true)]
    [string]$Workspace
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw '[fail] clean reproduction requires PowerShell 7 or later'
}

$workspacePath = [System.IO.Path]::GetFullPath($Workspace)
$tempName = '.clean-repro-' + [Guid]::NewGuid().ToString('N')
$reproPath = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($workspacePath, $tempName))
if (-not $reproPath.StartsWith($workspacePath + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw '[fail] clean reproduction path escaped workspace'
}
$reportPath = [System.IO.Path]::Combine($workspacePath, 'results', 'reproduction-test.json')
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-GeneratedMap {
    param([string]$Root)
    $map = [ordered]@{}
    foreach ($name in @('results', 'figures')) {
        $directory = [System.IO.Path]::Combine($Root, $name)
        if (Test-Path -LiteralPath $directory) {
            foreach ($file in (Get-ChildItem -LiteralPath $directory -Recurse -File | Sort-Object FullName)) {
                $relative = [System.IO.Path]::GetRelativePath($Root, $file.FullName).Replace('\', '/')
                if ($relative -in @('results/artifact-manifest.json', 'results/reproduction-test.json')) {
                    continue
                }
                $map[$relative] = $file.FullName
            }
        }
    }
    $paperPdf = [System.IO.Path]::Combine($Root, 'paper', '<SOURCE_FILE_REDACTED>')
    if (Test-Path -LiteralPath $paperPdf -PathType Leaf) {
        $map['paper/<SOURCE_FILE_REDACTED>'] = $paperPdf
    }
    return $map
}

function Get-PdfSemantic {
    param(
        [string]$Pdf,
        [string]$Scratch,
        [string]$Label
    )
    $safe = $Label -replace '[^A-Za-z0-9_-]', '_'
    $textPath = [System.IO.Path]::Combine($Scratch, $safe + '.txt')
    & pdftotext -layout $Pdf $textPath
    if ($LASTEXITCODE -ne 0) { throw "[fail] pdftotext failed for $Label" }
    $prefix = [System.IO.Path]::Combine($Scratch, $safe + '-page')
    & pdftoppm -png -r 96 $Pdf $prefix | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "[fail] pdftoppm failed for $Label" }
    $pages = Get-ChildItem -LiteralPath $Scratch -File -Filter ($safe + '-page*.png') | Sort-Object Name
    return [ordered]@{
        text_sha256 = Get-Sha256 $textPath
        rendered_pages = @($pages | ForEach-Object { Get-Sha256 $_.FullName })
    }
}

$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$childOutput = @()
$childExitCode = -1
$payload = $null
try {
    [System.IO.Directory]::CreateDirectory($reproPath) | Out-Null
    Copy-Item -LiteralPath ([System.IO.Path]::Combine($workspacePath, 'code')) -Destination ([System.IO.Path]::Combine($reproPath, 'code')) -Recurse
    $pycache = [System.IO.Path]::Combine($reproPath, 'code', '__pycache__')
    if (Test-Path -LiteralPath $pycache) { Remove-Item -LiteralPath $pycache -Recurse -Force }
    Copy-Item -LiteralPath ([System.IO.Path]::Combine($workspacePath, 'input')) -Destination ([System.IO.Path]::Combine($reproPath, 'input')) -Recurse
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::Combine($reproPath, 'paper')) | Out-Null
    foreach ($name in @('main.tex', 'paper.md')) {
        Copy-Item -LiteralPath ([System.IO.Path]::Combine($workspacePath, 'paper', $name)) -Destination ([System.IO.Path]::Combine($reproPath, 'paper', $name))
    }
    foreach ($name in @(
        'problem-analysis.md', 'data-audit.md', 'assumptions.yaml', 'variables.yaml',
        'model-selection.md', 'solution-report.yaml', 'reproducibility.yaml', 'revision-trace.md'
    )) {
        Copy-Item -LiteralPath ([System.IO.Path]::Combine($workspacePath, $name)) -Destination ([System.IO.Path]::Combine($reproPath, $name))
    }

    $childOutput = @(& pwsh -NoProfile -NonInteractive -File ([System.IO.Path]::Combine($reproPath, 'code', 'run_all.ps1')) -Workspace $reproPath 2>&1 | ForEach-Object { $_.ToString() })
    $childExitCode = $LASTEXITCODE
    if ($childExitCode -ne 0) {
        throw "[fail] clean-copy entrypoint exited with code $childExitCode"
    }

    $mainMap = Get-GeneratedMap $workspacePath
    $reproMap = Get-GeneratedMap $reproPath
    $mainPaths = @($mainMap.Keys | Sort-Object)
    $reproPaths = @($reproMap.Keys | Sort-Object)
    $missing = @($mainPaths | Where-Object { -not $reproMap.Contains($_) })
    $unexpected = @($reproPaths | Where-Object { -not $mainMap.Contains($_) })
    $byteMismatches = [System.Collections.Generic.List[object]]::new()
    $byteCompared = 0
    foreach ($relative in $mainPaths) {
        if (-not $reproMap.Contains($relative) -or $relative.EndsWith('.pdf')) { continue }
        $byteCompared += 1
        $mainHash = Get-Sha256 $mainMap[$relative]
        $reproHash = Get-Sha256 $reproMap[$relative]
        if ($mainHash -ne $reproHash) {
            $byteMismatches.Add([ordered]@{ path = $relative; main_sha256 = $mainHash; clean_sha256 = $reproHash })
        }
    }

    $scratch = [System.IO.Path]::Combine($reproPath, '.pdf-compare')
    [System.IO.Directory]::CreateDirectory($scratch) | Out-Null
    $pdfRows = [System.Collections.Generic.List[object]]::new()
    foreach ($relative in ($mainPaths | Where-Object { $_.EndsWith('.pdf') -and $reproMap.Contains($_) })) {
        $mainSemantic = Get-PdfSemantic $mainMap[$relative] $scratch ('main-' + $relative)
        $cleanSemantic = Get-PdfSemantic $reproMap[$relative] $scratch ('clean-' + $relative)
        $textEqual = $mainSemantic.text_sha256 -eq $cleanSemantic.text_sha256
        $pagesEqual = (ConvertTo-Json $mainSemantic.rendered_pages -Compress) -eq (ConvertTo-Json $cleanSemantic.rendered_pages -Compress)
        $pdfRows.Add([ordered]@{
            path = $relative
            text_status = $(if ($textEqual) { 'pass' } else { 'fail' })
            rendered_pages_status = $(if ($pagesEqual) { 'pass' } else { 'fail' })
            page_count = $mainSemantic.rendered_pages.Count
        })
    }
    $pdfStatus = if (@($pdfRows | Where-Object { $_.text_status -eq 'fail' -or $_.rendered_pages_status -eq 'fail' }).Count -eq 0) { 'pass' } else { 'fail' }
    $status = if ($missing.Count -eq 0 -and $unexpected.Count -eq 0 -and $byteMismatches.Count -eq 0 -and $pdfStatus -eq 'pass') { 'pass' } else { 'fail' }
    $stopwatch.Stop()
    $payload = [ordered]@{
        schema_version = 1
        case_id = '2012A'
        phase = 'blind-revision'
        status = $status
        source_copy = 'code + input + paper sources + required root documents; no prior results, figures, or PDF'
        command = 'pwsh -NoProfile -NonInteractive -File .\code\run_all.ps1 -Workspace . (inside isolated copy)'
        exit_code = $childExitCode
        wall_time_seconds = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 3)
        stdout_stderr = $childOutput
        generated_path_set = [ordered]@{
            missing = $missing
            unexpected = $unexpected
            status = $(if ($missing.Count -eq 0 -and $unexpected.Count -eq 0) { 'pass' } else { 'fail' })
        }
        non_pdf_byte_comparison = [ordered]@{
            compared = $byteCompared
            mismatches = @($byteMismatches)
            status = $(if ($byteMismatches.Count -eq 0) { 'pass' } else { 'fail' })
        }
        pdf_semantic_comparison = [ordered]@{
            status = $pdfStatus
            files = @($pdfRows)
            method = 'pdftotext -layout plus pdftoppm 96 dpi page hashes'
        }
        network_used = $false
        other_phase_skill_invoked = $false
    }
    [System.IO.File]::WriteAllText($reportPath, ($payload | ConvertTo-Json -Depth 10), $utf8NoBom)
    Write-Output "[$status] clean-copy unique-entrypoint reproduction"
    if ($status -ne 'pass') { exit 1 }
}
finally {
    if (Test-Path -LiteralPath $reproPath) {
        Remove-Item -LiteralPath $reproPath -Recurse -Force
    }
}
