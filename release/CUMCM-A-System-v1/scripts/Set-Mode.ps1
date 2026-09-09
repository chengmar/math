[CmdletBinding()]
param([ValidateSet('workflow-only','full-trained')][string]$Mode)
. (Join-Path $PSScriptRoot '_Common.ps1')
if (-not $Mode) { $Mode = Read-Host '输入 workflow-only 或 full-trained' }
Invoke-SystemCli set-mode --mode $Mode
