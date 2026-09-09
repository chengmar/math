[CmdletBinding()]
param([string]$CaseId)
. (Join-Path $PSScriptRoot '_Common.ps1')
if (-not $CaseId) { $CaseId = Read-Host '请输入案例 ID' }
Invoke-SystemCli status --id $CaseId
