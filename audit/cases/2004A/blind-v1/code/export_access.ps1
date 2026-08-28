param(
    [string]$Workspace = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
$workspacePath = (Resolve-Path -LiteralPath $Workspace).Path
$archivePath = Join-Path $workspacePath 'input\attachments\<SOURCE_FILE_REDACTED>'
$derivedInput = Join-Path $workspacePath 'derived\input'
$derivedData = Join-Path $workspacePath 'derived\data'
$databasePath = Join-Path $derivedInput '<SOURCE_FILE_REDACTED>'
$csvPath = Join-Path $derivedData '<SOURCE_FILE_REDACTED>'

New-Item -ItemType Directory -Force -Path $derivedInput, $derivedData | Out-Null
tar -xf $archivePath -C $derivedInput

if (-not (Test-Path -LiteralPath $databasePath)) {
    throw "Access database was not extracted: $databasePath"
}

$tables = @('第一次调查数据', '第二次调查数据', '第三次调查数据')
$connection = New-Object -ComObject ADODB.Connection
$rows = [System.Collections.Generic.List[object]]::new()
try {
    $connection.Open("Provider=Microsoft.ACE.OLEDB.16.0;Data Source=$databasePath;Persist Security Info=False;")
    for ($batch = 0; $batch -lt $tables.Count; $batch++) {
        $table = $tables[$batch]
        $recordset = New-Object -ComObject ADODB.Recordset
        try {
            $recordset.Open("SELECT * FROM [$table] ORDER BY [NO]", $connection)
            while (-not $recordset.EOF) {
                $rows.Add([pscustomobject][ordered]@{
                    '调查批次' = $batch + 1
                    'NO' = [int]$recordset.Fields.Item('NO').Value
                    '性别' = [string]$recordset.Fields.Item('性别').Value
                    '年龄' = [int]$recordset.Fields.Item('年龄').Value
                    '公交南北' = [bool]$recordset.Fields.Item('公交（南北）').Value
                    '公交东西' = [bool]$recordset.Fields.Item('公交（东西）').Value
                    '出租' = [bool]$recordset.Fields.Item('出租').Value
                    '私车' = [bool]$recordset.Fields.Item('私车').Value
                    '地铁东' = [bool]$recordset.Fields.Item('地铁（东）').Value
                    '地铁西' = [bool]$recordset.Fields.Item('地铁（西）').Value
                    '中餐' = [bool]$recordset.Fields.Item('中餐').Value
                    '西餐' = [bool]$recordset.Fields.Item('西餐').Value
                    '商场餐饮' = [bool]$recordset.Fields.Item('商场（餐饮）').Value
                    '消费档' = [int]$recordset.Fields.Item('消费额（非餐饮）').Value
                }) | Out-Null
                $recordset.MoveNext()
            }
        }
        finally {
            if ($recordset.State -ne 0) {
                $recordset.Close()
            }
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($recordset) | Out-Null
        }
    }
}
finally {
    if ($connection.State -ne 0) {
        $connection.Close()
    }
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($connection) | Out-Null
}

$rows | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding utf8
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $csvPath).Hash
[pscustomobject]@{
    status = 'pass'
    rows = $rows.Count
    output = $csvPath
    sha256 = $hash
} | ConvertTo-Json -Compress
