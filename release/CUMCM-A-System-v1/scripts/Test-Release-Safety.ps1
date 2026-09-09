[CmdletBinding()]
param(
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$ReportPath,
    [string]$ProtectedHashFile
)

$ErrorActionPreference = 'Stop'
$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$textExtensions = @('.md','.txt','.json','.yaml','.yml','.toml','.py','.ps1','.cmd','.tex','.bib','.csv','.gitignore','.sha256')
$binaryWhitelist = @('.png','.jpg','.jpeg')
$ignoredParts = @('.git','.pytest_cache','__pycache__','user-cases')
$forbiddenNames = @('auth.json','credentials.json','.env','id_rsa','id_ed25519')
$secretPattern = '(?i)(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|authorization\s*:\s*bearer\s+[A-Za-z0-9._-]{12,}|access[_-]?token\s*[=:]\s*["''][^"'']{8,})'
$absolutePattern = '(?i)[A-Z]:\\(?:Users\\[^\\\r\n]+|CUMCM-A-Lab|CUMCM-A-Vaults)'

$files = Get-ChildItem -LiteralPath $resolvedRoot -Force -Recurse -File | Where-Object {
    $relative = [IO.Path]::GetRelativePath($resolvedRoot, $_.FullName)
    -not ($ignoredParts | Where-Object { ($relative -split '[\\/]') -contains $_ })
}
$forbiddenFileHits = @($files | Where-Object { $forbiddenNames -contains $_.Name -or $_.Extension -in @('.pem','.key','.pfx','.p12','.pyc') } | ForEach-Object { [IO.Path]::GetRelativePath($resolvedRoot,$_.FullName) })
$unapprovedBinaries = @($files | Where-Object { $textExtensions -notcontains $_.Extension.ToLowerInvariant() -and $binaryWhitelist -notcontains $_.Extension.ToLowerInvariant() } | ForEach-Object { [IO.Path]::GetRelativePath($resolvedRoot,$_.FullName) })
$secretHits = @()
$absolutePathHits = @()
foreach($file in $files | Where-Object { $textExtensions -contains $_.Extension.ToLowerInvariant() -or $_.Name -eq '.gitignore' }) {
    $relative = [IO.Path]::GetRelativePath($resolvedRoot,$file.FullName)
    $text = Get-Content -Raw -LiteralPath $file.FullName -ErrorAction SilentlyContinue
    if($null -eq $text){continue}
    if($text -match $secretPattern){$secretHits += $relative}
    if($text -match $absolutePattern){$absolutePathHits += $relative}
}
$protectedHashes = @()
if($ProtectedHashFile){
    $payload = Get-Content -Raw -LiteralPath $ProtectedHashFile | ConvertFrom-Json
    if($payload -is [array]){$protectedHashes = @($payload)}
    elseif($payload.hashes){$protectedHashes = @($payload.hashes)}
}
$protectedHashHits = @()
if($protectedHashes.Count -gt 0){
    foreach($file in $files){
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
        if($protectedHashes -contains $hash){$protectedHashHits += [IO.Path]::GetRelativePath($resolvedRoot,$file.FullName)}
    }
}
$passed = $forbiddenFileHits.Count -eq 0 -and $unapprovedBinaries.Count -eq 0 -and $secretHits.Count -eq 0 -and $absolutePathHits.Count -eq 0 -and $protectedHashHits.Count -eq 0
$report = [ordered]@{
    schema_version = 1
    status = if($passed){'pass'}else{'fail'}
    root = $resolvedRoot
    files_scanned = $files.Count
    forbidden_file_hits = @($forbiddenFileHits)
    unapproved_binary_hits = @($unapprovedBinaries)
    secret_hits = @($secretHits | Sort-Object -Unique)
    absolute_path_hits = @($absolutePathHits | Sort-Object -Unique)
    protected_hash_hits = @($protectedHashHits | Sort-Object -Unique)
}
$json = $report | ConvertTo-Json -Depth 6
if($ReportPath){$json | Set-Content -LiteralPath $ReportPath -Encoding utf8NoBOM}
$json
if(-not $passed){exit 1}
