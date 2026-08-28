param(
    [Parameter(Mandatory = $true)]
    [string]$InputMdb,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'

$inputPath = (Resolve-Path -LiteralPath $InputMdb).Path
$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
[System.IO.Directory]::CreateDirectory($outputPath) | Out-Null

$providers = @(
    'Microsoft.ACE.OLEDB.16.0',
    'Microsoft.ACE.OLEDB.12.0',
    'Microsoft.Jet.OLEDB.4.0'
)

$connection = $null
$selectedProvider = $null
foreach ($provider in $providers) {
    try {
        $candidate = New-Object System.Data.OleDb.OleDbConnection(
            "Provider=$provider;Data Source=$inputPath;Mode=Read;"
        )
        $candidate.Open()
        $connection = $candidate
        $selectedProvider = $provider
        break
    }
    catch {
        if ($null -ne $candidate) {
            $candidate.Dispose()
        }
    }
}

if ($null -eq $connection) {
    throw 'No compatible read-only Access OLE DB provider is available.'
}

$tableMap = [ordered]@{
    '第一次调查数据' = '<SOURCE_FILE_REDACTED>'
    '第二次调查数据' = '<SOURCE_FILE_REDACTED>'
    '第三次调查数据' = '<SOURCE_FILE_REDACTED>'
}

$manifestRows = @()
try {
    foreach ($entry in $tableMap.GetEnumerator()) {
        $command = $connection.CreateCommand()
        $command.CommandText = "SELECT * FROM [$($entry.Key)] ORDER BY [NO]"
        $adapter = New-Object System.Data.OleDb.OleDbDataAdapter($command)
        $table = New-Object System.Data.DataTable
        [void]$adapter.Fill($table)

        $normalized = foreach ($row in $table.Rows) {
            [PSCustomObject][ordered]@{
                no            = [int]$row['NO']
                gender        = [string]$row['性别']
                age_group     = [int]$row['年龄']
                bus_ns        = [int][bool]$row['公交（南北）']
                bus_ew        = [int][bool]$row['公交（东西）']
                taxi          = [int][bool]$row['出租']
                car           = [int][bool]$row['私车']
                metro_east    = [int][bool]$row['地铁（东）']
                metro_west    = [int][bool]$row['地铁（西）']
                chinese_food  = [int][bool]$row['中餐']
                western_food  = [int][bool]$row['西餐']
                mall_food     = [int][bool]$row['商场（餐饮）']
                spend_group   = [int]$row['消费额（非餐饮）']
            }
        }

        $csvPath = Join-Path $outputPath $entry.Value
        $normalized | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding utf8
        $manifestRows += [ordered]@{
            table = $entry.Key
            file = $entry.Value
            rows = $table.Rows.Count
        }

        $adapter.Dispose()
        $command.Dispose()
        $table.Dispose()
    }
}
finally {
    $connection.Close()
    $connection.Dispose()
}

$sourceHash = (Get-FileHash -LiteralPath $inputPath -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest = [ordered]@{
    status = 'pass'
    source_file = [System.IO.Path]::GetFileName($inputPath)
    source_sha256 = $sourceHash
    provider = $selectedProvider
    surveys = $manifestRows
}
$manifestJson = $manifest | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText(
    (Join-Path $outputPath 'extraction-manifest.json'),
    $manifestJson + [Environment]::NewLine,
    (New-Object System.Text.UTF8Encoding($false))
)

Write-Output '[pass] MDB extraction completed.'
