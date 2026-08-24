[CmdletBinding()]
param(
    [int]$MaxCases,
    [string]$Model = 'gpt-5.6-sol',
    [ValidateSet('low', 'medium', 'high', 'xhigh', 'max')][string]$Reasoning = 'max',
    [switch]$Foreground
)

Write-Error '后台 Autopilot 已暂停；正式训练只允许使用前台队列。'
exit 1
