[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CredentialFile,
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [Parameter(Mandatory = $true)][string]$RunnerPath,
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [Parameter(Mandatory = $true)][string]$Workspace,
    [Parameter(Mandatory = $true)][string]$Prompt,
    [Parameter(Mandatory = $true)][string]$RunDir,
    [Parameter(Mandatory = $true)][string]$CodexHome,
    [Parameter(Mandatory = $true)][string]$CodexExecutable,
    [Parameter(Mandatory = $true)][ValidateSet('probe','solve','audit','blind-revision','judge','reference-adjudication')][string]$Phase,
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$credential = Import-Clixml -LiteralPath $CredentialFile
if ($credential -isnot [System.Management.Automation.PSCredential]) {
    throw 'Credential file does not contain a PSCredential.'
}
$launchLogs = Join-Path $Workspace '_launcher'
New-Item -ItemType Directory -Path $launchLogs -Force | Out-Null
$stdoutPath = Join-Path $launchLogs 'stdout.txt'
$stderrPath = Join-Path $launchLogs 'stderr.txt'
$arguments = @(
    $RunnerPath,
    '--runtime-root', $RuntimeRoot,
    '--workspace', $Workspace,
    '--prompt', $Prompt,
    '--run-dir', $RunDir,
    '--codex-home', $CodexHome,
    '--codex-executable', $CodexExecutable,
    '--phase', $Phase
)
if ($Execute) { $arguments += '--execute' }
$process = Start-Process -FilePath $PythonExecutable -ArgumentList $arguments -Credential $credential -WorkingDirectory $Workspace -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
[pscustomobject]@{ ExitCode = $process.ExitCode; Stdout = $stdoutPath; Stderr = $stderrPath } | ConvertTo-Json
exit $process.ExitCode

