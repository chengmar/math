Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:TrainerRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$venvPython = Join-Path $script:TrainerRoot '.venv\Scripts\python.exe'
$script:LabPython = if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $venvPython
} else {
    $python = Get-Command python -ErrorAction Stop
    $python.Source
}

function Get-CumcmLabPaths {
    $tool = Join-Path $script:TrainerRoot 'tools\show_lab_paths.py'
    $json = & $script:LabPython $tool
    if ($LASTEXITCODE -ne 0) {
        throw "无法读取 local-paths.toml（退出码 $LASTEXITCODE）。"
    }
    return ($json | ConvertFrom-Json)
}

function Invoke-CumcmTool {
    param(
        [Parameter(Mandatory = $true)][string]$Tool,
        [string[]]$Arguments = @()
    )
    $toolPath = Join-Path $script:TrainerRoot "tools\$Tool"
    if (-not (Test-Path -LiteralPath $toolPath -PathType Leaf)) {
        throw "工具不存在：$toolPath"
    }
    & $script:LabPython $toolPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Tool 执行失败（退出码 $LASTEXITCODE）。"
    }
}
