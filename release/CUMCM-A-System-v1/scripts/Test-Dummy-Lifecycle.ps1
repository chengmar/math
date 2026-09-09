[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot '_Common.ps1')
$python=Resolve-SystemPython
$env:PYTHONPATH=Join-Path $script:SystemRoot 'src'
$env:PYTHONDONTWRITEBYTECODE='1'
& $python -m pytest -q -p no:cacheprovider (Join-Path $script:SystemRoot 'tests')
if($LASTEXITCODE -ne 0){throw 'Dummy 生命周期测试失败。'}
