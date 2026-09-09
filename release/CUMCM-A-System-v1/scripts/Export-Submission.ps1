[CmdletBinding()]
param([string]$CaseId,[string]$Destination)
. (Join-Path $PSScriptRoot '_Common.ps1')
if (-not $CaseId) { $CaseId = Read-Host '请输入案例 ID' }
if (-not $Destination) { $Destination = Read-Host '请输入全新导出目录' }
Invoke-SystemCli export --id $CaseId --destination $Destination
