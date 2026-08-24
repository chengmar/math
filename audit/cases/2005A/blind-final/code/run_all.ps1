param(
    [switch]$RebuildDocx
)

$ErrorActionPreference = 'Stop'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'

$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$inputProblem = Join-Path $workspaceRoot 'input\problem\<SOURCE_FILE_REDACTED>'
$inputArchive = Join-Path $workspaceRoot 'input\attachments\<SOURCE_FILE_REDACTED>'
$workingDir = Join-Path $workspaceRoot 'working\current-run'
$attachmentDir = Join-Path $workingDir 'attachments'
$docxDir = if ($RebuildDocx) { Join-Path $workingDir 'docx' } else { Join-Path $workspaceRoot 'working\docx' }
$extractedDir = Join-Path $workingDir 'extracted'
$dataDir = Join-Path $workspaceRoot 'results\data'
$resultsDir = Join-Path $workspaceRoot 'results'
$figuresDir = Join-Path $workspaceRoot 'figures'
$paperDir = Join-Path $workspaceRoot 'paper'

foreach ($path in @($workingDir, $extractedDir, $dataDir, $resultsDir, $figuresDir, $paperDir)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}

$expectedInputHashes = @{
    $inputProblem = 'CEA35513E302801D4504F3FEBDF444AF783D928C4748BDF91A21724A910E271A'
    $inputArchive = 'DDD7B8E70AA727A2858E2476DDFEDA7E3042BE09D55B304297F990A203071E4F'
}
foreach ($entry in $expectedInputHashes.GetEnumerator()) {
    $actualHash = (Get-FileHash -LiteralPath $entry.Key -Algorithm SHA256).Hash
    if ($actualHash -ne $entry.Value) {
        throw "Input hash mismatch: $($entry.Key)"
    }
}

if ($RebuildDocx) {
    foreach ($path in @($attachmentDir, $docxDir)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
    $unrarPath = '<ABSOLUTE_PATH>'
    if (-not (Test-Path -LiteralPath $unrarPath)) {
        throw "UnRAR not found at $unrarPath"
    }
    & $unrarPath x -o+ -inul $inputArchive $attachmentDir
    if ($LASTEXITCODE -ne 0) {
        throw "UnRAR failed with exit code $LASTEXITCODE"
    }

    $sourceDocs = @(
        $inputProblem,
        (Join-Path $attachmentDir '<SOURCE_FILE_REDACTED>'),
        (Join-Path $attachmentDir '<SOURCE_FILE_REDACTED>'),
        (Join-Path $attachmentDir '<SOURCE_FILE_REDACTED>'),
        (Join-Path $attachmentDir '<SOURCE_FILE_REDACTED>')
    )
    $outputNames = @('<SOURCE_FILE_REDACTED>', '<SOURCE_FILE_REDACTED>', '<SOURCE_FILE_REDACTED>', '<SOURCE_FILE_REDACTED>', '<SOURCE_FILE_REDACTED>')
    $wordApp = New-Object -ComObject Word.Application
    $wordApp.Visible = $false
    $wordApp.DisplayAlerts = 0
    try {
        for ($index = 0; $index -lt $sourceDocs.Count; $index++) {
            $openedDoc = $<SOURCE_FILE_REDACTED>uments.Open($sourceDocs[$index], $false, $true)
            try {
                $openedDoc.SaveAs2((Join-Path $docxDir $outputNames[$index]), 16)
            }
            finally {
                $openedDoc.Close($false)
                [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($openedDoc) | Out-Null
            }
        }
    }
    finally {
        $wordApp.Quit()
        [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordApp) | Out-Null
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}
else {
    $expectedDocxHashes = @{
        '<SOURCE_FILE_REDACTED>' = 'DC43AACD5EAEEC044A727575ECD7D822A96040298E9C48D624A628214B76C8CA'
        '<SOURCE_FILE_REDACTED>' = '02086754DB45E67BBAB256281F86EFE005384330B1A1314B54ED718372E588F2'
        '<SOURCE_FILE_REDACTED>' = '1A12E7419FD8413BDBC52DEECBF8065F64AF15CCA513708B52ABE29188C7945A'
        '<SOURCE_FILE_REDACTED>' = '085C96E984C3B0308C68468BF6E45B9E4BB4C3649CE4D230349EE20EBF6F6F5F'
        '<SOURCE_FILE_REDACTED>' = '42BA09921283A02667CE91C40B82340C66FCF0484B3BBCE0E77C44F23AE7B5FC'
    }
    foreach ($entry in $expectedDocxHashes.GetEnumerator()) {
        $cachedPath = Join-Path $docxDir $entry.Key
        if (-not (Test-Path -LiteralPath $cachedPath)) {
            throw "Audited DOCX cache is missing: $cachedPath. Use -RebuildDocx to recreate it."
        }
        $actualHash = (Get-FileHash -LiteralPath $cachedPath -Algorithm SHA256).Hash
        if ($actualHash -ne $entry.Value) {
            throw "Audited DOCX cache hash mismatch: $cachedPath"
        }
    }
}

python (Join-Path $PSScriptRoot 'extract_docx.py') --input-dir $docxDir --output-dir $extractedDir
if ($LASTEXITCODE -ne 0) { throw 'extract_docx.py failed' }

python (Join-Path $PSScriptRoot 'build_data.py') --extracted-dir $extractedDir --output-dir $dataDir
if ($LASTEXITCODE -ne 0) { throw 'build_data.py failed' }

python (Join-Path $PSScriptRoot 'solve_case.py') --data-dir $dataDir --results-dir $resultsDir --figures-dir $figuresDir --paper-dir $paperDir
if ($LASTEXITCODE -ne 0) { throw 'solve_case.py failed' }

python (Join-Path $PSScriptRoot 'check_reproducibility.py') --workspace $workspaceRoot --docx-dir $docxDir
if ($LASTEXITCODE -ne 0) { throw 'check_reproducibility.py failed' }

python (Join-Path $PSScriptRoot 'validate.py') --workspace $workspaceRoot
if ($LASTEXITCODE -ne 0) { throw 'validate.py failed' }

foreach ($name in @('main.aux', 'main.bbl', 'main.blg', 'main.log', 'main.out', '<SOURCE_FILE_REDACTED>')) {
    $buildArtifact = Join-Path $paperDir $name
    if (Test-Path -LiteralPath $buildArtifact) {
        Remove-Item -LiteralPath $buildArtifact -Force
    }
}

Push-Location $paperDir
try {
    xelatex -interaction=nonstopmode -halt-on-error main.tex | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'first XeLaTeX pass failed' }
    bibtex main | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'BibTeX failed' }
    xelatex -interaction=nonstopmode -halt-on-error main.tex | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'second XeLaTeX pass failed' }
    xelatex -interaction=nonstopmode -halt-on-error main.tex | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'third XeLaTeX pass failed' }
}
finally {
    Pop-Location
}

python (Join-Path $PSScriptRoot 'check_paper_build.py') --workspace $workspaceRoot
if ($LASTEXITCODE -ne 0) { throw 'check_paper_build.py failed' }

python (Join-Path $PSScriptRoot 'verify_results.py') --workspace $workspaceRoot
if ($LASTEXITCODE -ne 0) { throw 'verify_results.py failed' }

# This final check is deliberately read-only.  No tracked file is written
# after the staging manifest is created.
python (Join-Path $PSScriptRoot 'verify_manifest.py') --workspace $workspaceRoot
if ($LASTEXITCODE -ne 0) { throw 'verify_manifest.py failed' }
