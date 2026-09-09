$ErrorActionPreference = 'Stop'
$script:SystemRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Resolve-SystemPython {
    if ($env:CUMCM_PYTHON -and (Test-Path -LiteralPath $env:CUMCM_PYTHON)) { return $env:CUMCM_PYTHON }
    $candidates = @(
        (Join-Path $script:SystemRoot 'runtime\python\python.exe'),
        (Join-Path $script:SystemRoot '.venv\Scripts\python.exe'),
        (Join-Path (Split-Path (Split-Path $script:SystemRoot -Parent) -Parent) 'runtime\python312-venv\Scripts\python.exe')
    )
    if ($env:USERPROFILE) {
        $candidates += Get-ChildItem -Path (Join-Path $env:USERPROFILE '.cache\codex-runtimes\*\dependencies\python\python.exe') -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -ExpandProperty FullName
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    foreach ($candidate in $candidates) {
        if (-not $candidate -or -not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $version = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]'3.11') { return (Resolve-Path -LiteralPath $candidate).Path }
    }
    throw '未找到 Python 3.11+；请安装 Python，或设置 CUMCM_PYTHON。'
}

function Resolve-SystemCodex {
    if ($env:CUMCM_CODEX -and (Test-Path -LiteralPath $env:CUMCM_CODEX -PathType Leaf)) { return (Resolve-Path -LiteralPath $env:CUMCM_CODEX).Path }
    $command = Get-Command codex.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    if ($env:LOCALAPPDATA) {
        $candidate = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin\*\codex.exe') -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }
    throw '未找到 Codex CLI；请安装或更新 Codex 桌面版，并确认已经登录。'
}

function Resolve-SystemXeLaTeX {
    if ($env:CUMCM_XELATEX -and (Test-Path -LiteralPath $env:CUMCM_XELATEX -PathType Leaf)) { return (Resolve-Path -LiteralPath $env:CUMCM_XELATEX).Path }
    $local = Join-Path (Split-Path (Split-Path $script:SystemRoot -Parent) -Parent) 'runtime\miktex-portable\texmfs\install\miktex\bin\x64\xelatex.exe'
    if (Test-Path -LiteralPath $local -PathType Leaf) { return (Resolve-Path -LiteralPath $local).Path }
    $command = Get-Command xelatex.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw '未找到 XeLaTeX；请安装 MiKTeX/TeX Live，或设置 CUMCM_XELATEX。'
}

function Resolve-SystemGit {
    if ($env:CUMCM_GIT -and (Test-Path -LiteralPath $env:CUMCM_GIT -PathType Leaf)) { return (Resolve-Path -LiteralPath $env:CUMCM_GIT).Path }
    $command = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw '未找到 Git；请安装 Git for Windows，或设置 CUMCM_GIT。'
}

function Initialize-SystemEnvironment {
    $env:CUMCM_PYTHON = Resolve-SystemPython
    try { $env:CUMCM_CODEX = Resolve-SystemCodex } catch { Remove-Item Env:CUMCM_CODEX -ErrorAction SilentlyContinue }
    try { $env:CUMCM_XELATEX = Resolve-SystemXeLaTeX } catch { Remove-Item Env:CUMCM_XELATEX -ErrorAction SilentlyContinue }
    try { $env:CUMCM_GIT = Resolve-SystemGit } catch { Remove-Item Env:CUMCM_GIT -ErrorAction SilentlyContinue }
    if (-not $env:CODEX_HOME) {
        if (-not $env:USERPROFILE) { throw '无法定位用户目录和 CODEX_HOME。' }
        $env:CODEX_HOME = Join-Path $env:USERPROFILE '.codex'
    }
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONDONTWRITEBYTECODE = '1'
}

function Invoke-SystemCli {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    Initialize-SystemEnvironment
    $python = $env:CUMCM_PYTHON
    $env:PYTHONPATH = Join-Path $script:SystemRoot 'src'
    & $python -m cumcm_system.cli --root $script:SystemRoot @Arguments
    if ($LASTEXITCODE -ne 0) { throw "命令失败，退出码 $LASTEXITCODE" }
}
