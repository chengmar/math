param(
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$workspace = Split-Path -Parent $PSScriptRoot
$archive = Join-Path $workspace 'input\attachments\<SOURCE_FILE_REDACTED>'
$problem = Join-Path $workspace 'input\problem\<SOURCE_FILE_REDACTED>'
$extractDir = Join-Path $workspace 'working\extracted'
$convertDir = Join-Path $workspace 'working\converted'

if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
    throw "Missing allowed input archive: $archive"
}
if (-not (Test-Path -LiteralPath $problem -PathType Leaf)) {
    throw "Missing allowed problem document: $problem"
}

New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
New-Item -ItemType Directory -Path $convertDir -Force | Out-Null
$expectedConverted = @('<SOURCE_FILE_REDACTED>', '<SOURCE_FILE_REDACTED>', '<SOURCE_FILE_REDACTED>', '<SOURCE_FILE_REDACTED>', '<SOURCE_FILE_REDACTED>')
$convertedReady = $true
foreach ($name in $expectedConverted) {
    $candidate = Join-Path $convertDir $name
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf) -or (Get-Item -LiteralPath $candidate).Length -eq 0) {
        $convertedReady = $false
    }
}
if ($convertedReady -and -not $Force) {
    Write-Output 'reused five existing nonempty DOCX derivatives; use -Force only when reconversion is required'
    return
}

tar -xf $archive -C $extractDir

$sources = @(
    @{ Path = $problem; Base = 'problem' },
    @{ Path = (Join-Path $extractDir '<SOURCE_FILE_REDACTED>'); Base = 'attachment1' },
    @{ Path = (Join-Path $extractDir '<SOURCE_FILE_REDACTED>'); Base = 'attachment2' },
    @{ Path = (Join-Path $extractDir '<SOURCE_FILE_REDACTED>'); Base = 'attachment3' },
    @{ Path = (Join-Path $extractDir '<SOURCE_FILE_REDACTED>'); Base = 'attachment4' }
)

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
$convertPrefix = [System.IO.Path]::GetFullPath($convertDir) + [System.IO.Path]::DirectorySeparatorChar
try {
    foreach ($item in $sources) {
        if (-not (Test-Path -LiteralPath $item.Path -PathType Leaf)) {
            throw "Missing extracted document: $($item.Path)"
        }
        $document = $<SOURCE_FILE_REDACTED>uments.Open($item.Path, $false, $true)
        try {
            $target = Join-Path $convertDir ($item.Base + '.docx')
            $temporaryTarget = Join-Path $convertDir ($item.Base + '.<SOURCE_FILE_REDACTED>')
            $resolvedTarget = [System.IO.Path]::GetFullPath($target)
            $resolvedTemporary = [System.IO.Path]::GetFullPath($temporaryTarget)
            if (-not $resolvedTarget.StartsWith($convertPrefix) -or -not $resolvedTemporary.StartsWith($convertPrefix)) {
                throw "Refusing to write outside conversion directory"
            }
            if (Test-Path -LiteralPath $temporaryTarget -PathType Leaf) {
                Remove-Item -LiteralPath $temporaryTarget -Force
            }
            $document.SaveAs2($temporaryTarget, 16)
            Write-Output "converted $($item.Base)"
        }
        finally {
            $document.Close($false)
        }
        Move-Item -LiteralPath $temporaryTarget -Destination $target -Force
    }
}
finally {
    $word.Quit()
}
