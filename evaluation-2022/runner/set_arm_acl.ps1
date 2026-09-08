[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [ValidateSet('A','B','C','none')][string]$ActiveArm = 'none',
    [Parameter(Mandatory = $true)][string[]]$ForbiddenRoots
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $RuntimeRoot).Path
$principal = "$env:COMPUTERNAME\CodexSandboxUsers"
$arms = @('A','B','C')

foreach ($forbidden in $ForbiddenRoots) {
    $resolved = (Resolve-Path -LiteralPath $forbidden).Path
    & icacls $resolved /deny "${principal}:(OI)(CI)(F)" /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to deny forbidden root: $resolved" }
}

foreach ($arm in $arms) {
    $path = Join-Path $root $arm
    $resolved = (Resolve-Path -LiteralPath $path).Path
    if (-not $resolved.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Arm escaped runtime root: $resolved"
    }
    & icacls $resolved /remove:d $principal /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to normalize arm ACL: $arm" }
    if ($ActiveArm -eq 'none' -or $arm -ne $ActiveArm) {
        & icacls $resolved /deny "${principal}:(OI)(CI)(F)" /T /C | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to deny arm: $arm" }
    }
}

[pscustomobject]@{
    status = 'pass'
    active_arm = $ActiveArm
    principal = $principal
    other_arms_denied = $true
    forbidden_roots_denied = $true
} | ConvertTo-Json
