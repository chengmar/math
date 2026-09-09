[CmdletBinding()]
param([string]$CaseId)
. (Join-Path $PSScriptRoot '_Common.ps1')
if (-not $CaseId) { $CaseId = Read-Host '请输入案例 ID' }
if($CaseId -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'){throw '案例 ID 仅允许 1-64 位字母、数字、下划线和连字符。'}
$casesRoot=(Resolve-Path -LiteralPath (Join-Path $script:SystemRoot 'user-cases')).Path
$case=(Resolve-Path -LiteralPath (Join-Path $casesRoot $CaseId)).Path
if(-not $case.StartsWith($casesRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw '案例路径越界，已拒绝。'}
$files = Get-ChildItem -LiteralPath $case -Recurse -File -ErrorAction Stop | Where-Object {$_.Extension -in @('.pdf','.md','.json')} | Sort-Object LastWriteTime -Descending
if(-not $files){throw '没有可打开的报告。'}
Start-Process -FilePath $files[0].FullName
Write-Host "已打开：$($files[0].Name)"
