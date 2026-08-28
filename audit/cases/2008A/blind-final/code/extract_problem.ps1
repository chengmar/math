$ErrorActionPreference = 'Stop'

# Offline extraction of the supplied legacy Word document.  Microsoft Word is
# used only as a local format converter; no network resource is accessed.
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$source = Join-Path $workspace 'input\problem\<SOURCE_FILE_REDACTED>'
$expectedHash = 'd28c0ef2aaee74e9618dbf77a022527ced5f8b723eee4cc27774f648d842e599'
$actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) {
    throw "Source hash mismatch: expected $expectedHash, got $actualHash"
}

$output = Join-Path $workspace 'working\source-extract'
$unpacked = Join-Path $output 'docx-unpacked'
$docx = Join-Path $output '<SOURCE_FILE_REDACTED>'
$expectedOutput = [System.IO.Path]::GetFullPath((Join-Path $workspace 'working\source-extract'))
if ([System.IO.Path]::GetFullPath($output) -ne $expectedOutput) {
    throw "Refusing unexpected extraction directory: $output"
}
if ([System.IO.Directory]::Exists($output)) {
    [System.IO.Directory]::Delete($output, $true)
}
New-Item -ItemType Directory -Force -Path $output | Out-Null
New-Item -ItemType Directory -Force -Path $unpacked | Out-Null

$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $<SOURCE_FILE_REDACTED>uments.Open($source, $false, $true)
    $document.SaveAs($docx, 12) # wdFormatXMLDocument (DOCX)
}
finally {
    try { if ($null -ne $document) { $document.Close($false) } } catch {}
    try { if ($null -ne $word) { $word.Quit() } } catch {}
}

tar -xf $docx -C $unpacked
$image = Join-Path $unpacked 'word\media\<SOURCE_FILE_REDACTED>'
if (-not (Test-Path -LiteralPath $image -PathType Leaf)) {
    throw "Expected target image was not extracted: $image"
}

Add-Type -AssemblyName System.Drawing
$bitmap = [System.Drawing.Image]::FromFile($image)
try {
    if (($bitmap.Width -ne 1024) -or ($bitmap.Height -ne 768)) {
        throw "Unexpected target image size: $($bitmap.Width)x$($bitmap.Height)"
    }
}
finally {
    $bitmap.Dispose()
}

Write-Output 'EXTRACTION=pass'
Write-Output "SOURCE_SHA256=$actualHash"
Write-Output "TARGET_IMAGE=$image"
