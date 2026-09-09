[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot '_Common.ps1')
Invoke-SystemCli verify
