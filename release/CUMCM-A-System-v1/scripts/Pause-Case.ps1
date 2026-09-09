[CmdletBinding()]
param([string]$CaseId)
. (Join-Path $PSScriptRoot '_Common.ps1')
if (-not $CaseId) { $CaseId = Read-Host '请输入案例 ID' }
Invoke-SystemCli pause --id $CaseId
Write-Host '已请求在当前阶段结束后的安全边界暂停。'
