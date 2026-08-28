param(
    [int]$SensitivityReplicates = 100
)

$ErrorActionPreference = 'Stop'
[System.Threading.Thread]::CurrentThread.CurrentCulture = [System.Globalization.CultureInfo]::InvariantCulture
[System.Threading.Thread]::CurrentThread.CurrentUICulture = [System.Globalization.CultureInfo]::InvariantCulture

$runStarted = Get-Date
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$resultsDir = Join-Path $root 'results'
$figuresDir = Join-Path $root 'figures'
$paperDir = Join-Path $root 'paper'
foreach ($directory in @($resultsDir, $figuresDir, $paperDir)) {
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory | Out-Null
    }
}

if (-not ('Cumcm2015A.SolarShadowSolver' -as [type])) {
    Add-Type -Path (Join-Path $PSScriptRoot 'SolarShadow.cs')
}
try { Add-Type -AssemblyName System.Drawing.Common -ErrorAction Stop } catch { Add-Type -AssemblyName System.Drawing }

function Export-Rows {
    param([Parameter(Mandatory)]$Rows, [Parameter(Mandatory)][string]$Path)
    @($Rows) | Export-Csv -LiteralPath $Path -NoTypeInformation -Encoding utf8
}

function Write-Json {
    param([Parameter(Mandatory)]$Value, [Parameter(Mandatory)][string]$Path, [int]$Depth = 8)
    $Value | ConvertTo-Json -Depth $Depth | Set-Content -LiteralPath $Path -Encoding utf8
}

function Format-Clock {
    param([double]$Minute)
    $hour = [Math]::Floor($Minute / 60.0)
    $remainder = $Minute - 60.0 * $hour
    $wholeMinute = [Math]::Floor($remainder)
    $second = [Math]::Round(60.0 * ($remainder - $wholeMinute))
    if ($second -ge 60) { $second = 0; $wholeMinute += 1 }
    if ($wholeMinute -ge 60) { $wholeMinute = 0; $hour += 1 }
    '{0:00}:{1:00}:{2:00}' -f $hour, $wholeMinute, $second
}

function Get-Quantile {
    param([double[]]$Values, [double]$Probability)
    $sorted = @($Values | Sort-Object)
    if ($sorted.Count -eq 0) { return [double]::NaN }
    if ($sorted.Count -eq 1) { return [double]$sorted[0] }
    $position = ($sorted.Count - 1) * $Probability
    $lower = [Math]::Floor($position)
    $upper = [Math]::Ceiling($position)
    if ($lower -eq $upper) { return [double]$sorted[$lower] }
    [double]$sorted[$lower] + ($position - $lower) * ([double]$sorted[$upper] - [double]$sorted[$lower])
}

function Get-ExcelData {
    param([string]$WorkbookPath)
    $excel = $null
    $workbook = $null
    $datasets = [ordered]@{}
    try {
        $excel = New-Object -ComObject Excel.Application
        $excel.Visible = $false
        $excel.DisplayAlerts = $false
        $excel.AutomationSecurity = 3
        $workbook = $excel.Workbooks.Open($WorkbookPath, 0, $true)
        $expectedSheetNames = @('附件1', '附件2', '附件3')
        $actualSheetNames = @()
        for ($sheetIndex = 1; $sheetIndex -le $workbook.Worksheets.Count; ++$sheetIndex) {
            $sheet = $null
            try {
                $sheet = $workbook.Worksheets.Item($sheetIndex)
                $actualSheetNames += [string]$sheet.Name
            }
            finally {
                if ($sheet) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($sheet) }
            }
        }
        if ((($actualSheetNames | Sort-Object) -join "`n") -ne (($expectedSheetNames | Sort-Object) -join "`n")) {
            throw "Workbook sheet set mismatch: actual=$($actualSheetNames -join ',')"
        }
        foreach ($sheetName in $expectedSheetNames) {
            $worksheet = $null
            $used = $null
            try {
                $worksheet = $workbook.Worksheets.Item($sheetName)
                $used = $worksheet.UsedRange
                $values = $used.Value2
                $rows = @()
                for ($row = 4; $row -le $used.Rows.Count; ++$row) {
                    $rawTime = $values[$row, 1]
                    $rawX = $values[$row, 2]
                    $rawY = $values[$row, 3]
                    foreach ($cell in @($rawTime, $rawX, $rawY)) {
                        if ($null -eq $cell -or ($cell -is [string] -and [string]::IsNullOrWhiteSpace($cell))) {
                            throw "Missing numeric cell in $sheetName row $row before conversion."
                        }
                        if ($cell -is [string] -or $cell -is [bool]) {
                            throw "Non-numeric cell in $sheetName row $row before conversion."
                        }
                    }
                    $timeValue = [double]$rawTime
                    $xValue = [double]$rawX
                    $yValue = [double]$rawY
                    if ([double]::IsNaN($timeValue) -or [double]::IsInfinity($timeValue) -or
                        [double]::IsNaN($xValue) -or [double]::IsInfinity($xValue) -or
                        [double]::IsNaN($yValue) -or [double]::IsInfinity($yValue)) {
                        throw "Non-finite numeric cell in $sheetName row $row."
                    }
                    $minute = $timeValue * 1440.0
                    $rows += [pscustomobject]@{
                        index = $row - 3
                        time_beijing = Format-Clock $minute
                        minute_of_day = $minute
                        x_m = $xValue
                        y_m = $yValue
                    }
                }
                if ($rows.Count -ne 21) { throw "$sheetName must contain exactly 21 observations; found $($rows.Count)." }
                $dataset = [pscustomobject]@{
                    Name = $sheetName
                    Time = [double[]]@($rows.minute_of_day)
                    X = [double[]]@($rows.x_m)
                    Y = [double[]]@($rows.y_m)
                    Rows = $rows
                }
                $datasets[$sheetName] = $dataset
                Export-Rows $rows (Join-Path $resultsDir ("{0}<SOURCE_FILE_REDACTED>" -f $sheetName))
            }
            finally {
                if ($used) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($used) }
                if ($worksheet) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($worksheet) }
            }
        }
    }
    finally {
        if ($workbook) { try { $workbook.Close($false) } catch {} }
        if ($excel) { try { $excel.Quit() } catch {} }
        if ($workbook) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($workbook) }
        if ($excel) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel) }
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
    $datasets
}

function Convert-FitRow {
    param($Fit, [int]$BranchRank, [int]$GlobalRmseRank, [string]$Branch)
    $date = [datetime]::new($Fit.Year, 1, 1).AddDays($Fit.DayOfYear - 1)
    [pscustomobject]@{
        branch_rank = $BranchRank
        global_rmse_rank = $GlobalRmseRank
        branch = $Branch
        date = $date.ToString('yyyy-MM-dd')
        day_of_year = $Fit.DayOfYear
        handedness = $Fit.Handedness
        latitude_deg = $Fit.LatitudeDeg
        longitude_deg = $Fit.LongitudeDeg
        inferred_height_m = $Fit.HeightM
        axis_rotation_deg = $Fit.RotationDeg
        tip_rmse_m = $Fit.TipRmseM
        max_tip_error_m = $Fit.MaxTipErrorM
        length_rmse_m = $Fit.LengthRmseM
        minimum_solar_altitude_deg = $Fit.MinAltitudeDeg
    }
}

function Get-LocalDateMinima {
    param($Profile)
    $values = @($Profile)
    $minima = @()
    for ($i = 0; $i -lt $values.Count; ++$i) {
        $previous = $values[($i + $values.Count - 1) % $values.Count].ObjectiveSse
        $next = $values[($i + 1) % $values.Count].ObjectiveSse
        if ($values[$i].ObjectiveSse -le $previous -and $values[$i].ObjectiveSse -le $next) {
            $minima += $values[$i]
        }
    }
    @($minima | Sort-Object ObjectiveSse)
}

function Save-LinePlot {
    param(
        [Parameter(Mandatory)]$Series,
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$XLabel,
        [Parameter(Mandatory)][string]$YLabel,
        [int]$CanvasWidth = 1200,
        [int]$CanvasHeight = 720
    )
    $allPoints = @($Series | ForEach-Object { $_.Points })
    $xMin = [double](($allPoints | Measure-Object X -Minimum).Minimum)
    $xMax = [double](($allPoints | Measure-Object X -Maximum).Maximum)
    $yMin = [double](($allPoints | Measure-Object Y -Minimum).Minimum)
    $yMax = [double](($allPoints | Measure-Object Y -Maximum).Maximum)
    $rawYMin = $yMin
    if ($xMax -le $xMin) { $xMax = $xMin + 1.0 }
    if ($yMax -le $yMin) { $yMax = $yMin + 1.0 }
    $xPad = 0.03 * ($xMax - $xMin)
    $yPad = 0.08 * ($yMax - $yMin)
    $xMin -= $xPad; $xMax += $xPad
    $yMin -= $yPad; $yMax += $yPad
    if ($rawYMin -ge 0.0) { $yMin = [Math]::Max(0.0, $yMin) }

    $bitmap = [System.Drawing.Bitmap]::new($CanvasWidth, $CanvasHeight)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.Color]::White)
    $font = [System.Drawing.Font]::new('Arial', 15)
    $smallFont = [System.Drawing.Font]::new('Arial', 12)
    $titleFont = [System.Drawing.Font]::new('Arial', 20, [System.Drawing.FontStyle]::Bold)
    $axisPen = [System.Drawing.Pen]::new([System.Drawing.Color]::Black, 2)
    $gridPen = [System.Drawing.Pen]::new([System.Drawing.Color]::LightGray, 1)
    $gridPen.DashStyle = [System.Drawing.Drawing2D.DashStyle]::Dash
    $left = 150.0; $right = 40.0; $top = 75.0; $bottom = 95.0
    $plotWidth = $CanvasWidth - $left - $right
    $plotHeight = $CanvasHeight - $top - $bottom
    try {
        $graphics.DrawString($Title, $titleFont, [System.Drawing.Brushes]::Black, 25, 20)
        for ($tick = 0; $tick -le 5; ++$tick) {
            $px = $left + $plotWidth * $tick / 5.0
            $py = $top + $plotHeight * (1.0 - $tick / 5.0)
            $graphics.DrawLine($gridPen, $px, $top, $px, $top + $plotHeight)
            $graphics.DrawLine($gridPen, $left, $py, $left + $plotWidth, $py)
            $xValue = $xMin + ($xMax - $xMin) * $tick / 5.0
            $yValue = $yMin + ($yMax - $yMin) * $tick / 5.0
            $graphics.DrawString(('{0:0.###}' -f $xValue), $smallFont, [System.Drawing.Brushes]::Black, $px - 25, $top + $plotHeight + 10)
            $graphics.DrawString(('{0:0.###}' -f $yValue), $smallFont, [System.Drawing.Brushes]::Black, 70, $py - 10)
        }
        $graphics.DrawRectangle($axisPen, $left, $top, $plotWidth, $plotHeight)
        $graphics.DrawString($XLabel, $font, [System.Drawing.Brushes]::Black, $left + $plotWidth / 2 - 60, $CanvasHeight - 45)
        $graphics.TranslateTransform(22, $top + $plotHeight / 2 + 60)
        $graphics.RotateTransform(-90)
        $graphics.DrawString($YLabel, $font, [System.Drawing.Brushes]::Black, 0, 0)
        $graphics.ResetTransform()

        $legendX = $left + 15
        $legendY = $top + 12
        foreach ($item in $Series) {
            $color = [System.Drawing.ColorTranslator]::FromHtml($item.Color)
            $pen = [System.Drawing.Pen]::new($color, [float]$item.Width)
            try {
                $points = @($item.Points)
                for ($i = 1; $i -lt $points.Count; ++$i) {
                    $x1 = $left + ($points[$i - 1].X - $xMin) / ($xMax - $xMin) * $plotWidth
                    $y1 = $top + (1.0 - ($points[$i - 1].Y - $yMin) / ($yMax - $yMin)) * $plotHeight
                    $x2 = $left + ($points[$i].X - $xMin) / ($xMax - $xMin) * $plotWidth
                    $y2 = $top + (1.0 - ($points[$i].Y - $yMin) / ($yMax - $yMin)) * $plotHeight
                    $graphics.DrawLine($pen, [float]$x1, [float]$y1, [float]$x2, [float]$y2)
                }
                if ($item.Markers) {
                    $brush = [System.Drawing.SolidBrush]::new($color)
                    try {
                        foreach ($point in $points) {
                            $px = $left + ($point.X - $xMin) / ($xMax - $xMin) * $plotWidth
                            $py = $top + (1.0 - ($point.Y - $yMin) / ($yMax - $yMin)) * $plotHeight
                            $graphics.FillEllipse($brush, [float]($px - 4), [float]($py - 4), 8, 8)
                        }
                    } finally { $brush.Dispose() }
                }
                $graphics.DrawLine($pen, $legendX, $legendY + 8, $legendX + 30, $legendY + 8)
                $graphics.DrawString([string]$item.Name, $smallFont, [System.Drawing.Brushes]::Black, $legendX + 38, $legendY - 2)
                $legendY += 25
            } finally { $pen.Dispose() }
        }
        $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $axisPen.Dispose(); $gridPen.Dispose(); $font.Dispose(); $smallFont.Dispose(); $titleFont.Dispose()
        $graphics.Dispose(); $bitmap.Dispose()
    }
}

# Phase-lock conditions are repeated without claiming that the unavailable Python runner executed.
$lock = Get-Content -LiteralPath (Join-Path $root 'phase-lock.json') -Raw | ConvertFrom-Json
$requiredLockFiles = @('allowed-paths.json', 'forbidden-paths.json', 'AGENTS.override.md')
$missingLockFiles = @($requiredLockFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $root $_)) })
$equivalentPhaseStatus = if ($lock.phase -in @('solve', 'blind-revision') -and $missingLockFiles.Count -eq 0) { 'pass' } else { 'fail' }
if ($equivalentPhaseStatus -eq 'fail') { throw 'Phase-lock conditions failed.' }
$pythonRunnerStatus = if (Get-Command python -ErrorAction SilentlyContinue) { 'needs_review' } else { 'needs_review' }

$expectedManifestPath = Join-Path $PSScriptRoot '<SOURCE_FILE_REDACTED>'
$expectedManifest = @(Import-Csv -LiteralPath $expectedManifestPath)
$actualInputFiles = @(Get-ChildItem -LiteralPath (Join-Path $root 'input') -Recurse -File | Sort-Object FullName)
$actualRelativePaths = @($actualInputFiles | ForEach-Object { [IO.Path]::GetRelativePath($root, $_.FullName) })
$expectedRelativePaths = @($expectedManifest.relative_path)
if ((($actualRelativePaths | Sort-Object) -join "`n") -ne (($expectedRelativePaths | Sort-Object) -join "`n")) {
    throw 'Input file set differs from code/<SOURCE_FILE_REDACTED>.'
}
$manifest = foreach ($expected in $expectedManifest) {
    $fullPath = Join-Path $root $expected.relative_path
    $actual = Get-Item -LiteralPath $fullPath
    $actualHash = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $status = if ($actual.Length -eq [long]$expected.bytes -and $actualHash -eq $expected.sha256) { 'pass' } else { 'fail' }
    [pscustomobject]@{
        relative_path = $expected.relative_path
        bytes = [long]$expected.bytes
        sha256 = $expected.sha256
        actual_bytes = $actual.Length
        actual_sha256 = $actualHash
        status = $status
    }
}
if (@($manifest | Where-Object status -eq 'fail').Count -gt 0) {
    throw 'Input bytes or SHA-256 differ from immutable expected manifest.'
}
Export-Rows $manifest (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

$workbookPath = Join-Path $root 'input\data\<SOURCE_FILE_REDACTED>'
$workbookHashBefore = (Get-FileHash -LiteralPath $workbookPath -Algorithm SHA256).Hash.ToLowerInvariant()
$workbookWorkingPath = Join-Path $resultsDir ('.workbook-read-copy-{0}.xls' -f $PID)
if (Test-Path -LiteralPath $workbookWorkingPath) { Remove-Item -LiteralPath $workbookWorkingPath -Force }
try {
    Copy-Item -LiteralPath $workbookPath -Destination $workbookWorkingPath
    $datasets = Get-ExcelData $workbookWorkingPath
}
finally {
    if (Test-Path -LiteralPath $workbookWorkingPath) { Remove-Item -LiteralPath $workbookWorkingPath -Force }
}
$workbookHashAfter = (Get-FileHash -LiteralPath $workbookPath -Algorithm SHA256).Hash.ToLowerInvariant()
$inputPreservationRows = @([pscustomobject]@{
    relative_path = [IO.Path]::GetRelativePath($root, $workbookPath)
    before_sha256 = $workbookHashBefore
    after_sha256 = $workbookHashAfter
    status = if ($workbookHashBefore -eq $workbookHashAfter) { 'pass' } else { 'fail' }
})
Export-Rows $inputPreservationRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')
if ($inputPreservationRows[0].status -eq 'fail') { throw 'Original workbook changed during extraction.' }

$dataAuditRows = @()
foreach ($name in $datasets.Keys) {
    $dataset = $datasets[$name]
    $intervals = for ($i = 1; $i -lt $dataset.Time.Count; ++$i) { $dataset.Time[$i] - $dataset.Time[$i - 1] }
    $dataAuditRows += [pscustomobject]@{
        dataset = $name
        observations = $dataset.Time.Count
        missing_numeric_cells = @($dataset.Rows | Where-Object { $null -eq $_.x_m -or $null -eq $_.y_m }).Count
        monotonic_time_status = if ((@($intervals | Where-Object { $_ -le 0 })).Count -eq 0) { 'pass' } else { 'fail' }
        interval_min_minutes = ($intervals | Measure-Object -Minimum).Minimum
        interval_max_minutes = ($intervals | Measure-Object -Maximum).Maximum
        x_min_m = ($dataset.X | Measure-Object -Minimum).Minimum
        x_max_m = ($dataset.X | Measure-Object -Maximum).Maximum
        y_min_m = ($dataset.Y | Measure-Object -Minimum).Minimum
        y_max_m = ($dataset.Y | Measure-Object -Maximum).Maximum
    }
}
Export-Rows $dataAuditRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

# Question 1: Tiananmen Square, 2015-10-22, 09:00--15:00 Beijing time.
$q1Year = 2015
$q1Day = 295
$q1Latitude = 39.0 + 54.0 / 60.0 + 26.0 / 3600.0
$q1Longitude = 116.0 + 23.0 / 60.0 + 29.0 / 3600.0
$q1Height = 3.0
$q1Rows = for ($minute = 540.0; $minute -le 900.0001; $minute += 5.0) {
    $solar = [Cumcm2015A.SolarShadowSolver]::SolarAt($q1Year, $q1Day, $minute, $q1Latitude, $q1Longitude)
    $east = $q1Height * $solar[3]
    $north = $q1Height * $solar[4]
    [pscustomobject]@{
        time_beijing = Format-Clock $minute
        minute_of_day = $minute
        solar_declination_deg = $solar[0]
        equation_of_time_min = $solar[1]
        solar_altitude_deg = $solar[2]
        shadow_east_m = $east
        shadow_north_m = $north
        shadow_length_m = [Math]::Sqrt($east * $east + $north * $north)
        shadow_azimuth_deg_from_north = (([Math]::Atan2($east, $north) * 180.0 / [Math]::PI) + 360.0) % 360.0
    }
}
Export-Rows $q1Rows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

$leftBound = 680.0; $rightBound = 760.0; $golden = 1.618033988749895
for ($iteration = 0; $iteration -lt 80; ++$iteration) {
    $c = $rightBound - ($rightBound - $leftBound) / $golden
    $d = $leftBound + ($rightBound - $leftBound) / $golden
    $altC = [Cumcm2015A.SolarShadowSolver]::SolarAt($q1Year, $q1Day, $c, $q1Latitude, $q1Longitude)[2]
    $altD = [Cumcm2015A.SolarShadowSolver]::SolarAt($q1Year, $q1Day, $d, $q1Latitude, $q1Longitude)[2]
    if ($altC -gt $altD) { $rightBound = $d } else { $leftBound = $c }
}
$solarNoonMinute = 0.5 * ($leftBound + $rightBound)
$solarNoon = [Cumcm2015A.SolarShadowSolver]::SolarAt($q1Year, $q1Day, $solarNoonMinute, $q1Latitude, $q1Longitude)
$q1MinimumLength = $q1Height / [Math]::Tan($solarNoon[2] * [Math]::PI / 180.0)
$q1Start = @($q1Rows)[0]
$q1End = @($q1Rows)[-1]
$q1Summary = [ordered]@{
    latitude_deg = $q1Latitude
    longitude_deg = $q1Longitude
    rod_height_m = $q1Height
    length_at_0900_m = $q1Start.shadow_length_m
    altitude_at_0900_deg = $q1Start.solar_altitude_deg
    solar_noon_beijing = Format-Clock $solarNoonMinute
    solar_noon_minute = $solarNoonMinute
    minimum_length_m = $q1MinimumLength
    maximum_altitude_deg = $solarNoon[2]
    length_at_1500_m = $q1End.shadow_length_m
    altitude_at_1500_deg = $q1End.solar_altitude_deg
}
Write-Json $q1Summary (Join-Path $resultsDir 'q1_summary.json')

$q1SensitivityRows = @()
$scenarios = @(
    @{ name = 'base'; day = $q1Day; lat = $q1Latitude; lon = $q1Longitude; height = 3.0 },
    @{ name = 'height_minus_10pct'; day = $q1Day; lat = $q1Latitude; lon = $q1Longitude; height = 2.7 },
    @{ name = 'height_plus_10pct'; day = $q1Day; lat = $q1Latitude; lon = $q1Longitude; height = 3.3 },
    @{ name = 'latitude_minus_1deg'; day = $q1Day; lat = $q1Latitude - 1.0; lon = $q1Longitude; height = 3.0 },
    @{ name = 'latitude_plus_1deg'; day = $q1Day; lat = $q1Latitude + 1.0; lon = $q1Longitude; height = 3.0 },
    @{ name = 'longitude_minus_1deg'; day = $q1Day; lat = $q1Latitude; lon = $q1Longitude - 1.0; height = 3.0 },
    @{ name = 'longitude_plus_1deg'; day = $q1Day; lat = $q1Latitude; lon = $q1Longitude + 1.0; height = 3.0 },
    @{ name = 'date_minus_15days'; day = $q1Day - 15; lat = $q1Latitude; lon = $q1Longitude; height = 3.0 },
    @{ name = 'date_plus_15days'; day = $q1Day + 15; lat = $q1Latitude; lon = $q1Longitude; height = 3.0 }
)
foreach ($scenario in $scenarios) {
    $lengths = @()
    for ($minute = 540.0; $minute -le 900.0001; $minute += 5.0) {
        $value = [Cumcm2015A.SolarShadowSolver]::SolarAt(2015, $scenario.day, $minute, $scenario.lat, $scenario.lon)
        $lengths += $scenario.height * [Math]::Sqrt($value[3] * $value[3] + $value[4] * $value[4])
    }
    $minimumIndex = [Array]::IndexOf($lengths, ($lengths | Measure-Object -Minimum).Minimum)
    $q1SensitivityRows += [pscustomobject]@{
        scenario = $scenario.name
        day_of_year = $scenario.day
        latitude_deg = $scenario.lat
        longitude_deg = $scenario.lon
        height_m = $scenario.height
        length_0900_m = $lengths[0]
        sampled_minimum_length_m = ($lengths | Measure-Object -Minimum).Minimum
        sampled_minimum_time = $q1Rows[$minimumIndex].time_beijing
        length_1500_m = $lengths[-1]
    }
}
Export-Rows $q1SensitivityRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

# Question 2: known date, unknown place/height/orientation.
$attachment1 = $datasets['附件1']
$q2VectorFits = @([Cumcm2015A.SolarShadowSolver]::SearchKnownDate(
    $attachment1.Time, $attachment1.X, $attachment1.Y, 2015, 108, 21, $false, $true, 16))
$q2BranchCounters = @{ '1' = 0; '-1' = 0 }
$q2VectorRows = for ($i = 0; $i -lt $q2VectorFits.Count; ++$i) {
    $sign = $q2VectorFits[$i].Handedness
    $signKey = [string]$sign
    $q2BranchCounters[$signKey] = 1 + $q2BranchCounters[$signKey]
    Convert-FitRow $q2VectorFits[$i] $q2BranchCounters[$signKey] ($i + 1) $(if ($sign -eq 1) { 'rotation' } else { 'axis_reflection' })
}
Export-Rows $q2VectorRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')
$q2Primary = @($q2VectorFits | Where-Object Handedness -eq 1 | Sort-Object ObjectiveSse)[0]

$q2LengthFits = @([Cumcm2015A.SolarShadowSolver]::SearchKnownDate(
    $attachment1.Time, $attachment1.X, $attachment1.Y, 2015, 108, 21, $true, $true, 10))
$q2LengthBranchCounters = @{ '1' = 0; '-1' = 0 }
$q2LengthRows = for ($i = 0; $i -lt $q2LengthFits.Count; ++$i) {
    $sign = $q2LengthFits[$i].Handedness
    $signKey = [string]$sign
    $q2LengthBranchCounters[$signKey] = 1 + $q2LengthBranchCounters[$signKey]
    Convert-FitRow $q2LengthFits[$i] $q2LengthBranchCounters[$signKey] ($i + 1) $(if ($sign -eq 1) { 'length_rotation' } else { 'length_axis_reflection' })
}
Export-Rows $q2LengthRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

# Question 3: date profile and paired calendar minima for attachments 2 and 3.
$q3Profiles = [ordered]@{}
$q3Candidates = [ordered]@{}
foreach ($sheetName in @('附件2', '附件3')) {
    $dataset = $datasets[$sheetName]
    $profileRows = @()
    $candidateFits = @()
    foreach ($sign in @(1, -1)) {
        $profile = @([Cumcm2015A.SolarShadowSolver]::SearchDailyProfile(
            $dataset.Time, $dataset.X, $dataset.Y, 2015, $sign, 21, $false))
        $q3Profiles["$sheetName|$sign"] = $profile
        foreach ($fit in $profile) {
            $profileRows += [pscustomobject]@{
                date = ([datetime]::new(2015, 1, 1).AddDays($fit.DayOfYear - 1)).ToString('yyyy-MM-dd')
                day_of_year = $fit.DayOfYear
                handedness = $fit.Handedness
                latitude_deg = $fit.LatitudeDeg
                longitude_deg = $fit.LongitudeDeg
                inferred_height_m = $fit.HeightM
                tip_rmse_m = $fit.TipRmseM
                max_tip_error_m = $fit.MaxTipErrorM
                log10_tip_rmse = [Math]::Log10([Math]::Max(1.0e-12, $fit.TipRmseM))
            }
        }
        $localMinima = @(Get-LocalDateMinima $profile | Select-Object -First 2)
        $candidateFits += $localMinima
    }
    $q3Candidates[$sheetName] = @($candidateFits | Sort-Object ObjectiveSse)
    Export-Rows $profileRows (Join-Path $resultsDir ("q3_{0}<SOURCE_FILE_REDACTED>" -f $sheetName))
    $candidateRows = for ($i = 0; $i -lt $q3Candidates[$sheetName].Count; ++$i) {
        $fit = $q3Candidates[$sheetName][$i]
        $sameBranch = @($q3Candidates[$sheetName] | Where-Object Handedness -eq $fit.Handedness | Sort-Object ObjectiveSse)
        $branchRank = [Array]::IndexOf($sameBranch, $fit) + 1
        Convert-FitRow $fit $branchRank ($i + 1) $(if ($fit.Handedness -eq 1) { 'rotation' } else { 'axis_reflection' })
    }
    Export-Rows $candidateRows (Join-Path $resultsDir ("q3_{0}<SOURCE_FILE_REDACTED>" -f $sheetName))
}

# Nested, time-ordered model assessment. Polynomial degree is selected only inside points 1--14;
# points 15--21 are opened after the main-model decision and never feed selection.
$validationRows = @()
$innerPolynomialRows = @()
$modelSelectionRows = @()
$heldoutModels = [ordered]@{}
$selectedMainCandidate = $null
foreach ($sheetName in @('附件1', '附件2', '附件3')) {
    $dataset = $datasets[$sheetName]
    $trainCount = 14
    $finalTestStart = 14
    $finalTestCount = 7

    $degreeScores = @()
    foreach ($degree in 1..3) {
        $weightedSse = 0.0
        $evaluatedPoints = 0
        foreach ($origin in @(8, 10, 12)) {
            $polynomial = [Cumcm2015A.SolarShadowSolver]::FitPolynomial(
                $dataset.Time, $dataset.X, $dataset.Y, $origin, $degree)
            $innerMetric = [Cumcm2015A.SolarShadowSolver]::EvaluatePolynomialRange(
                $dataset.Time, $dataset.X, $dataset.Y, $polynomial, $origin, 2)
            $innerPolynomialRows += [pscustomobject]@{
                dataset = $sheetName
                degree = $degree
                fit_points = "1_$origin"
                evaluation_points = "$($origin + 1)_$($origin + 2)"
                evaluation_tip_rmse_m = $innerMetric.TipRmseM
                status = if ([double]::IsNaN($innerMetric.TipRmseM) -or [double]::IsInfinity($innerMetric.TipRmseM)) { 'fail' } else { 'pass' }
            }
            $weightedSse += 2.0 * $innerMetric.TipRmseM * $innerMetric.TipRmseM
            $evaluatedPoints += 2
        }
        $degreeScores += [pscustomobject]@{
            degree = $degree
            rolling_origin_rmse_m = [Math]::Sqrt($weightedSse / $evaluatedPoints)
        }
    }
    $selectedDegree = [int](@($degreeScores | Sort-Object rolling_origin_rmse_m, degree)[0].degree)

    $polynomialFits = [ordered]@{}
    foreach ($degree in 1..3) {
        $polynomial = [Cumcm2015A.SolarShadowSolver]::FitPolynomial(
            $dataset.Time, $dataset.X, $dataset.Y, $trainCount, $degree)
        $polynomialFits[[string]$degree] = $polynomial
    }

    if ($sheetName -eq '附件1') {
        $mainFit = @([Cumcm2015A.SolarShadowSolver]::SearchKnownDate(
            $dataset.Time, $dataset.X, $dataset.Y, 2015, 108, $trainCount, $false, $false, 1))[0]
        $lengthFit = @([Cumcm2015A.SolarShadowSolver]::SearchKnownDate(
            $dataset.Time, $dataset.X, $dataset.Y, 2015, 108, $trainCount, $true, $false, 1))[0]
        $selectedPolynomialTrain = [Cumcm2015A.SolarShadowSolver]::EvaluatePolynomialRange(
            $dataset.Time, $dataset.X, $dataset.Y, $polynomialFits[[string]$selectedDegree], 0, $trainCount)
        $predeclaredCandidates = @(
            [pscustomobject]@{
                candidate = 'trajectory_polynomial_baseline'; parameter_output = 'trajectory_only'
                selected_polynomial_degree = $selectedDegree; train_tip_rmse_m = $selectedPolynomialTrain.TipRmseM
                task_output_status = 'fail'; training_numerical_status = if ($selectedPolynomialTrain.TipRmseM -lt 0.01) { 'pass' } else { 'fail' }
            },
            [pscustomobject]@{
                candidate = 'length_only_inverse'; parameter_output = 'partial_location_height_no_direction'
                selected_polynomial_degree = ''; train_tip_rmse_m = $lengthFit.TipRmseM
                task_output_status = 'fail'; training_numerical_status = if ($lengthFit.TipRmseM -lt 0.01 -and $lengthFit.HeightM -gt 0) { 'pass' } else { 'fail' }
            },
            [pscustomobject]@{
                candidate = 'full_vector_solar_inverse'; parameter_output = 'location_date_height_orientation'
                selected_polynomial_degree = ''; train_tip_rmse_m = $mainFit.TipRmseM
                task_output_status = 'pass'; training_numerical_status = if ($mainFit.TipRmseM -lt 0.005 -and $mainFit.HeightM -gt 0) { 'pass' } else { 'fail' }
            }
        )
        foreach ($candidate in $predeclaredCandidates) {
            $candidate | Add-Member -NotePropertyName selection_eligible_status -NotePropertyValue $(
                if ($candidate.task_output_status -eq 'pass' -and $candidate.training_numerical_status -eq 'pass') { 'pass' } else { 'fail' })
        }
        $eligible = @($predeclaredCandidates | Where-Object selection_eligible_status -eq 'pass' | Sort-Object train_tip_rmse_m)
        if ($eligible.Count -ne 1) { throw "Predeclared selection rule expected exactly one eligible main candidate; found $($eligible.Count)." }
        $selectedMainCandidate = $eligible[0].candidate
        foreach ($candidate in $predeclaredCandidates) {
            $modelSelectionRows += [pscustomobject]@{
                candidate = $candidate.candidate
                parameter_output = $candidate.parameter_output
                selected_polynomial_degree = $candidate.selected_polynomial_degree
                selection_data = 'points_1_14_only_with_rolling_origins_inside'
                task_output_status = $candidate.task_output_status
                training_numerical_status = $candidate.training_numerical_status
                selection_eligible_status = $candidate.selection_eligible_status
                train_tip_rmse_m = $candidate.train_tip_rmse_m
                selected_before_final_test = if ($candidate.candidate -eq $selectedMainCandidate) { 'pass' } else { 'fail' }
                final_test_used_for_selection = 'fail'
            }
        }
    }
    else {
        $profile = @([Cumcm2015A.SolarShadowSolver]::SearchDailyProfile(
            $dataset.Time, $dataset.X, $dataset.Y, 2015, 1, $trainCount, $false))
        $mainFit = @($profile | Sort-Object ObjectiveSse)[0]
    }
    if ($selectedMainCandidate -ne 'full_vector_solar_inverse') {
        throw 'The training-only selection rule did not select the task-complete solar inverse.'
    }
    $heldoutModels[$sheetName] = $mainFit

    # The final block is evaluated only after the preceding decision has been fixed.
    foreach ($degree in 1..3) {
        $trainMetric = [Cumcm2015A.SolarShadowSolver]::EvaluatePolynomialRange(
            $dataset.Time, $dataset.X, $dataset.Y, $polynomialFits[[string]$degree], 0, $trainCount)
        $finalMetric = [Cumcm2015A.SolarShadowSolver]::EvaluatePolynomialRange(
            $dataset.Time, $dataset.X, $dataset.Y, $polynomialFits[[string]$degree], $finalTestStart, $finalTestCount)
        $validationRows += [pscustomobject]@{
            dataset = $sheetName; model = "coordinate_polynomial_degree_$degree"; model_family = 'trajectory_polynomial_baseline'
            inner_selected = if ($degree -eq $selectedDegree) { 'pass' } else { 'fail' }
            selection_data = 'points_1_14'; evaluation_data = 'points_15_21_final_test'; final_test_used_for_selection = 'fail'
            selected_date = ''; latitude_deg = ''; longitude_deg = ''
            train_tip_rmse_m = $trainMetric.TipRmseM; validation_tip_rmse_m = $finalMetric.TipRmseM
            validation_max_tip_error_m = $finalMetric.MaxTipErrorM
        }
    }
    if ($sheetName -eq '附件1') {
        $lengthValidation = [Cumcm2015A.SolarShadowSolver]::EvaluateRange(
            $dataset.Time, $dataset.X, $dataset.Y, $lengthFit, $finalTestStart, $finalTestCount)
        $validationRows += [pscustomobject]@{
            dataset = $sheetName; model = 'length_only_inverse'; model_family = 'length_only_inverse'; inner_selected = 'fail'
            selection_data = 'points_1_14'; evaluation_data = 'points_15_21_final_test'; final_test_used_for_selection = 'fail'
            selected_date = '2015-04-18'; latitude_deg = $lengthFit.LatitudeDeg; longitude_deg = $lengthFit.LongitudeDeg
            train_tip_rmse_m = $lengthFit.TipRmseM; validation_tip_rmse_m = $lengthValidation.TipRmseM
            validation_max_tip_error_m = $lengthValidation.MaxTipErrorM
        }
    }
    $mainValidation = [Cumcm2015A.SolarShadowSolver]::EvaluateRange(
        $dataset.Time, $dataset.X, $dataset.Y, $mainFit, $finalTestStart, $finalTestCount)
    $validationRows += [pscustomobject]@{
        dataset = $sheetName; model = 'full_vector_solar_inverse'; model_family = 'full_vector_solar_inverse'; inner_selected = 'fail'
        selection_data = 'points_1_14'; evaluation_data = 'points_15_21_final_test'; final_test_used_for_selection = 'fail'
        selected_date = ([datetime]::new(2015, 1, 1).AddDays($mainFit.DayOfYear - 1)).ToString('yyyy-MM-dd')
        latitude_deg = $mainFit.LatitudeDeg; longitude_deg = $mainFit.LongitudeDeg
        train_tip_rmse_m = $mainFit.TipRmseM; validation_tip_rmse_m = $mainValidation.TipRmseM
        validation_max_tip_error_m = $mainValidation.MaxTipErrorM
    }
}
Export-Rows $innerPolynomialRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')
Export-Rows $modelSelectionRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')
Export-Rows $validationRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

$modelComparisonRows = foreach ($selection in $modelSelectionRows) {
    $holdoutRow = if ($selection.candidate -eq 'trajectory_polynomial_baseline') {
        @($validationRows | Where-Object { $_.dataset -eq '附件1' -and $_.model -eq "coordinate_polynomial_degree_$($selection.selected_polynomial_degree)" })[0]
    } else {
        @($validationRows | Where-Object { $_.dataset -eq '附件1' -and $_.model -eq $selection.candidate })[0]
    }
    [pscustomobject]@{
        candidate = $selection.candidate
        parameter_output = $selection.parameter_output
        selected_polynomial_degree = $selection.selected_polynomial_degree
        train_tip_rmse_m = $selection.train_tip_rmse_m
        task_output_status = $selection.task_output_status
        training_numerical_status = $selection.training_numerical_status
        selection_eligible_status = $selection.selection_eligible_status
        selected_for_main_results = $selection.selected_before_final_test
        terminal_holdout_tip_rmse_m = $holdoutRow.validation_tip_rmse_m
        terminal_holdout_max_error_m = $holdoutRow.validation_max_tip_error_m
        terminal_holdout_used_for_selection = 'fail'
    }
}
Export-Rows $modelComparisonRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

# Coordinate-rounding-only conditional sensitivity. It is not a total-error interval.
$random = [System.Random]::new(2015)
$sensitivityRows = @()
for ($replicate = 1; $replicate -le $SensitivityReplicates; ++$replicate) {
    [double[]]$perturbedX = [double[]]::new($attachment1.X.Count)
    [double[]]$perturbedY = [double[]]::new($attachment1.Y.Count)
    for ($i = 0; $i -lt $attachment1.X.Count; ++$i) {
        $perturbedX[$i] = $attachment1.X[$i] + 0.0001 * ($random.NextDouble() - 0.5)
        $perturbedY[$i] = $attachment1.Y[$i] + 0.0001 * ($random.NextDouble() - 0.5)
    }
    $fit = [Cumcm2015A.SolarShadowSolver]::Refine(
        $attachment1.Time, $perturbedX, $perturbedY, 2015, 108, 1,
        $q2Primary.LatitudeDeg, $q2Primary.LongitudeDeg, 21, $false, 0.25)
    $sensitivityRows += [pscustomobject]@{
        sensitivity_type = 'rounding_only_conditional'
        dataset = '附件1'
        branch = 'rotation_primary'
        replicate = $replicate
        selected_day_of_year = 108
        latitude_deg = $fit.LatitudeDeg
        longitude_deg = $fit.LongitudeDeg
        inferred_height_m = $fit.HeightM
        tip_rmse_m = $fit.TipRmseM
    }
}

foreach ($sheetName in @('附件2', '附件3')) {
    $dataset = $datasets[$sheetName]
    $rotationCandidates = @($q3Candidates[$sheetName] | Where-Object Handedness -eq 1 | Sort-Object ObjectiveSse)
    for ($branchIndex = 0; $branchIndex -lt $rotationCandidates.Count; ++$branchIndex) {
        $baseFit = $rotationCandidates[$branchIndex]
        for ($replicate = 1; $replicate -le $SensitivityReplicates; ++$replicate) {
            [double[]]$perturbedX = [double[]]::new($dataset.X.Count)
            [double[]]$perturbedY = [double[]]::new($dataset.Y.Count)
            for ($i = 0; $i -lt $dataset.X.Count; ++$i) {
                $perturbedX[$i] = $dataset.X[$i] + 0.0001 * ($random.NextDouble() - 0.5)
                $perturbedY[$i] = $dataset.Y[$i] + 0.0001 * ($random.NextDouble() - 0.5)
            }
            $best = $null
            foreach ($dayOffset in -2..2) {
                $day = $baseFit.DayOfYear + $dayOffset
                if ($day -lt 1 -or $day -gt 365) { continue }
                $trial = [Cumcm2015A.SolarShadowSolver]::Refine(
                    $dataset.Time, $perturbedX, $perturbedY, 2015, $day, 1,
                    $baseFit.LatitudeDeg, $baseFit.LongitudeDeg, 21, $false, 0.5)
                if ($null -eq $best -or $trial.ObjectiveSse -lt $best.ObjectiveSse) { $best = $trial }
            }
            $sensitivityRows += [pscustomobject]@{
                sensitivity_type = 'rounding_only_conditional'
                dataset = $sheetName
                branch = "rotation_calendar_branch_$($branchIndex + 1)"
                replicate = $replicate
                selected_day_of_year = $best.DayOfYear
                latitude_deg = $best.LatitudeDeg
                longitude_deg = $best.LongitudeDeg
                inferred_height_m = $best.HeightM
                tip_rmse_m = $best.TipRmseM
            }
        }
    }
}
Export-Rows $sensitivityRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

$sensitivitySummary = foreach ($group in ($sensitivityRows | Group-Object dataset, branch)) {
    $items = @($group.Group)
    [double[]]$latitudes = @($items.latitude_deg)
    [double[]]$longitudes = @($items.longitude_deg)
    [double[]]$heights = @($items.inferred_height_m)
    $dayGroups = @($items | Group-Object selected_day_of_year |
        Sort-Object @{Expression = 'Count'; Descending = $true}, @{Expression = 'Name'; Ascending = $true} |
        Select-Object)
    $modalDay = $dayGroups[0].Name
    [pscustomobject]@{
        sensitivity_type = 'rounding_only_conditional'
        dataset = $items[0].dataset
        branch = $items[0].branch
        replicates = $items.Count
        modal_day_of_year = $modalDay
        modal_date_2015 = ([datetime]::new(2015, 1, 1).AddDays([int]$modalDay - 1)).ToString('yyyy-MM-dd')
        modal_day_count = $dayGroups[0].Count
        modal_day_fraction = $dayGroups[0].Count / [double]$items.Count
        selected_day_counts = (($dayGroups | ForEach-Object { '{0}:{1}' -f $_.Name, $_.Count }) -join ';')
        latitude_p025_deg = Get-Quantile $latitudes 0.025
        latitude_median_deg = Get-Quantile $latitudes 0.5
        latitude_p975_deg = Get-Quantile $latitudes 0.975
        longitude_p025_deg = Get-Quantile $longitudes 0.025
        longitude_median_deg = Get-Quantile $longitudes 0.5
        longitude_p975_deg = Get-Quantile $longitudes 0.975
        height_p025_m = Get-Quantile $heights 0.025
        height_median_m = Get-Quantile $heights 0.5
        height_p975_m = Get-Quantile $heights 0.975
    }
}
Export-Rows $sensitivitySummary (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

# A common clock offset should primarily translate longitude: one minute is approximately 0.25 degree.
$timeOffsetRows = @()
$timeCases = @([pscustomobject]@{ Dataset = '附件1'; Fit = $q2Primary })
foreach ($sheetName in @('附件2', '附件3')) {
    foreach ($fit in @($q3Candidates[$sheetName] | Where-Object Handedness -eq 1)) {
        $timeCases += [pscustomobject]@{ Dataset = $sheetName; Fit = $fit }
    }
}
foreach ($case in $timeCases) {
    $dataset = $datasets[$case.Dataset]
    foreach ($offset in @(-1.0, 0.0, 1.0)) {
        [double[]]$shiftedTime = @($dataset.Time | ForEach-Object { $_ + $offset })
        $fit = [Cumcm2015A.SolarShadowSolver]::Refine(
            $shiftedTime, $dataset.X, $dataset.Y, 2015, $case.Fit.DayOfYear, 1,
            $case.Fit.LatitudeDeg, $case.Fit.LongitudeDeg, 21, $false, 0.5)
        $timeOffsetRows += [pscustomobject]@{
            dataset = $case.Dataset
            calendar_branch_day = $case.Fit.DayOfYear
            clock_offset_min = $offset
            latitude_deg = $fit.LatitudeDeg
            longitude_deg = $fit.LongitudeDeg
            inferred_height_m = $fit.HeightM
            tip_rmse_m = $fit.TipRmseM
        }
    }
}
Export-Rows $timeOffsetRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

& (Join-Path $PSScriptRoot 'revision_diagnostics.ps1') -BlockBootstrapReplicates $SensitivityReplicates
if (-not $?) { throw 'revision_diagnostics.ps1 failed.' }

# Figures generated only from result arrays above.
$q1Series = @(
    [pscustomobject]@{
        Name = 'H=3 m'; Color = '#1f77b4'; Width = 3.0; Markers = $false
        Points = @($q1Rows | ForEach-Object { [pscustomobject]@{ X = $_.minute_of_day / 60.0; Y = $_.shadow_length_m } })
    }
)
Save-LinePlot $q1Series (Join-Path $figuresDir '<SOURCE_FILE_REDACTED>') `
    'Tiananmen shadow length, 2015-10-22' 'Beijing time (hour)' 'Shadow length (m)'

$heightSeries = @()
foreach ($heightCase in @(
    @{ Height = 2.7; Name = 'H=2.7 m'; Color = '#2ca02c' },
    @{ Height = 3.0; Name = 'H=3.0 m'; Color = '#1f77b4' },
    @{ Height = 3.3; Name = 'H=3.3 m'; Color = '#d62728' })) {
    $points = foreach ($row in $q1Rows) {
        [pscustomobject]@{ X = $row.minute_of_day / 60.0; Y = $row.shadow_length_m * $heightCase.Height / 3.0 }
    }
    $heightSeries += [pscustomobject]@{ Name = $heightCase.Name; Color = $heightCase.Color; Width = 2.5; Markers = $false; Points = @($points) }
}
Save-LinePlot $heightSeries (Join-Path $figuresDir '<SOURCE_FILE_REDACTED>') `
    'Exact linear sensitivity to rod height' 'Beijing time (hour)' 'Shadow length (m)'

$q2Predictions = [Cumcm2015A.SolarShadowSolver]::Predict($attachment1.Time, $q2Primary)
$q2PlotSeries = @(
    [pscustomobject]@{
        Name = 'Observed'; Color = '#111111'; Width = 1.5; Markers = $true
        Points = @(for ($i = 0; $i -lt $attachment1.X.Count; ++$i) { [pscustomobject]@{ X = $attachment1.X[$i]; Y = $attachment1.Y[$i] } })
    },
    [pscustomobject]@{
        Name = 'Solar-vector fit'; Color = '#d62728'; Width = 2.5; Markers = $false
        Points = @(for ($i = 0; $i -lt $q2Predictions.Count; ++$i) { [pscustomobject]@{ X = $q2Predictions[$i][0]; Y = $q2Predictions[$i][1] } })
    }
)
Save-LinePlot $q2PlotSeries (Join-Path $figuresDir '<SOURCE_FILE_REDACTED>') `
    'Attachment 1: observed and fitted shadow tips' 'x (m)' 'y (m)'

foreach ($sheetName in @('附件2', '附件3')) {
    $profileSeries = @()
    foreach ($signCase in @(
        @{ Sign = 1; Name = 'rotation'; Color = '#1f77b4' },
        @{ Sign = -1; Name = 'axis reflection'; Color = '#ff7f0e' })) {
        $profile = $q3Profiles["$sheetName|$($signCase.Sign)"]
        $profileSeries += [pscustomobject]@{
            Name = $signCase.Name; Color = $signCase.Color; Width = 2.0; Markers = $false
            Points = @($profile | ForEach-Object { [pscustomobject]@{ X = $_.DayOfYear; Y = [Math]::Log10([Math]::Max(1.0e-12, $_.TipRmseM)) } })
        }
    }
    Save-LinePlot $profileSeries (Join-Path $figuresDir ("q3_{0}<SOURCE_FILE_REDACTED>" -f $sheetName)) `
        ("{0}: date-profile objective" -f $sheetName) 'Day of year (2015)' 'log10 tip RMSE (m)'

    $dataset = $datasets[$sheetName]
    $trajectorySeries = @(
        [pscustomobject]@{
            Name = 'Observed'; Color = '#111111'; Width = 1.2; Markers = $true
            Points = @(for ($i = 0; $i -lt $dataset.X.Count; ++$i) { [pscustomobject]@{ X = $dataset.X[$i]; Y = $dataset.Y[$i] } })
        }
    )
    $colors = @('#d62728', '#2ca02c')
    $rotationCandidates = @($q3Candidates[$sheetName] | Where-Object Handedness -eq 1 | Sort-Object ObjectiveSse)
    for ($candidateIndex = 0; $candidateIndex -lt $rotationCandidates.Count; ++$candidateIndex) {
        $candidate = $rotationCandidates[$candidateIndex]
        $predictions = [Cumcm2015A.SolarShadowSolver]::Predict($dataset.Time, $candidate)
        $dateText = ([datetime]::new(2015, 1, 1).AddDays($candidate.DayOfYear - 1)).ToString('MM-dd')
        $trajectorySeries += [pscustomobject]@{
            Name = "Fit $dateText"; Color = $colors[$candidateIndex]; Width = 2.5; Markers = $false
            Points = @(for ($i = 0; $i -lt $predictions.Count; ++$i) { [pscustomobject]@{ X = $predictions[$i][0]; Y = $predictions[$i][1] } })
        }
    }
    Save-LinePlot $trajectorySeries (Join-Path $figuresDir ("q3_{0}<SOURCE_FILE_REDACTED>" -f $sheetName)) `
        ("{0}: paired-date fits" -f $sheetName) 'x (m)' 'y (m)'
}

$validationPlotSeries = @()
foreach ($modelCase in @(
    @{ Model = 'trajectory_polynomial_baseline'; Name = 'Nested polynomial baseline'; Color = '#7f7f7f' },
    @{ Model = 'full_vector_solar_inverse'; Name = 'Solar inverse'; Color = '#1f77b4' })) {
    $points = @()
    $index = 0
    foreach ($sheetName in @('附件1', '附件2', '附件3')) {
        ++$index
        $row = if ($modelCase.Model -eq 'trajectory_polynomial_baseline') {
            @($validationRows | Where-Object { $_.dataset -eq $sheetName -and $_.model_family -eq $modelCase.Model -and $_.inner_selected -eq 'pass' })[0]
        } else {
            @($validationRows | Where-Object { $_.dataset -eq $sheetName -and $_.model -eq $modelCase.Model })[0]
        }
        $points += [pscustomobject]@{ X = $index; Y = 1000.0 * [double]$row.validation_tip_rmse_m }
    }
    $validationPlotSeries += [pscustomobject]@{ Name = $modelCase.Name; Color = $modelCase.Color; Width = 2.5; Markers = $true; Points = $points }
}
Save-LinePlot $validationPlotSeries (Join-Path $figuresDir '<SOURCE_FILE_REDACTED>') `
    'Final chronological test (points 15-21; not used for selection)' 'Dataset index (1,2,3)' 'Tip RMSE (mm)'

$videoFiles = @(Get-ChildItem -LiteralPath (Join-Path $root 'input') -Recurse -File |
    Where-Object Extension -in @('.mp4', '.avi', '.mov', '.wmv', '.mkv'))
$q4Status = [ordered]@{
    video_present_status = if ($videoFiles.Count -gt 0) { 'pass' } else { 'fail' }
    ground_control_calibration_status = 'fail'
    observation_equation_status = if (@(Import-Csv -LiteralPath (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>') | Where-Object status -eq 'fail').Count -eq 0) { 'pass' } else { 'fail' }
    method_status = 'needs_review'
    numerical_location_status = 'needs_review'
    video_file_count = $videoFiles.Count
    reason = if ($videoFiles.Count -eq 0) { 'Attachment 4 video is absent from the allowed workspace; only a download-link document is present, and network retrieval is prohibited.' } else { 'Video is present but requires the documented extraction pipeline.' }
    available_result = 'Both homography directions and homogeneous normalization pass synthetic unit tests. The complete video method remains needs_review without frames and metric ground controls.'
}
Write-Json $q4Status (Join-Path $resultsDir 'q4_status.json')

$attachment2Rotation = @($q3Candidates['附件2'] | Where-Object Handedness -eq 1 | Sort-Object ObjectiveSse)
$attachment3Rotation = @($q3Candidates['附件3'] | Where-Object Handedness -eq 1 | Sort-Object ObjectiveSse)
$q2Reflection = @($q2VectorFits | Where-Object Handedness -eq -1 | Sort-Object ObjectiveSse)[0]
$keyResults = [ordered]@{
    q1 = $q1Summary
    q2 = [ordered]@{
        primary_rotation = [ordered]@{
            latitude_deg = $q2Primary.LatitudeDeg
            longitude_deg = $q2Primary.LongitudeDeg
            inferred_height_m = $q2Primary.HeightM
            tip_rmse_m = $q2Primary.TipRmseM
            max_tip_error_m = $q2Primary.MaxTipErrorM
        }
        axis_reflection_candidate = [ordered]@{
            latitude_deg = $q2Reflection.LatitudeDeg
            longitude_deg = $q2Reflection.LongitudeDeg
            inferred_height_m = $q2Reflection.HeightM
            tip_rmse_m = $q2Reflection.TipRmseM
        }
    }
    q3_attachment2_rotation_candidates = @($attachment2Rotation | ForEach-Object {
        [ordered]@{
            date = ([datetime]::new(2015, 1, 1).AddDays($_.DayOfYear - 1)).ToString('yyyy-MM-dd')
            latitude_deg = $_.LatitudeDeg; longitude_deg = $_.LongitudeDeg
            inferred_height_m = $_.HeightM; tip_rmse_m = $_.TipRmseM
        }
    })
    q3_attachment3_rotation_candidates = @($attachment3Rotation | ForEach-Object {
        [ordered]@{
            date = ([datetime]::new(2015, 1, 1).AddDays($_.DayOfYear - 1)).ToString('yyyy-MM-dd')
            latitude_deg = $_.LatitudeDeg; longitude_deg = $_.LongitudeDeg
            inferred_height_m = $_.HeightM; tip_rmse_m = $_.TipRmseM
        }
    })
    q4 = $q4Status
}
Write-Json $keyResults (Join-Path $resultsDir 'key_results.json') 10

$macroLines = @(
    '% Generated by code/run_all.ps1. Do not edit numerical values by hand.',
    ('\newcommand{\QOneNineLength}{' + ('{0:F3}' -f $q1Summary.length_at_0900_m) + '}'),
    ('\newcommand{\QOneNoonTime}{' + $q1Summary.solar_noon_beijing.Substring(0, 5) + '}'),
    ('\newcommand{\QOneMinimumLength}{' + ('{0:F3}' -f $q1Summary.minimum_length_m) + '}'),
    ('\newcommand{\QOneFifteenLength}{' + ('{0:F3}' -f $q1Summary.length_at_1500_m) + '}'),
    ('\newcommand{\QTwoLatitude}{' + ('{0:F1}' -f $q2Primary.LatitudeDeg) + '}'),
    ('\newcommand{\QTwoLongitude}{' + ('{0:F1}' -f $q2Primary.LongitudeDeg) + '}'),
    ('\newcommand{\QTwoHeight}{' + ('{0:F2}' -f $q2Primary.HeightM) + '}'),
    ('\newcommand{\QTwoRmseMm}{' + ('{0:F3}' -f (1000.0 * $q2Primary.TipRmseM)) + '}'),
    ('\newcommand{\QThreeTwoDateA}{' + ([datetime]::new(2015,1,1).AddDays($attachment2Rotation[0].DayOfYear-1)).ToString('MM-dd') + '}'),
    ('\newcommand{\QThreeTwoLatA}{' + ('{0:F1}' -f $attachment2Rotation[0].LatitudeDeg) + '}'),
    ('\newcommand{\QThreeTwoLonA}{' + ('{0:F1}' -f $attachment2Rotation[0].LongitudeDeg) + '}'),
    ('\newcommand{\QThreeTwoDateB}{' + ([datetime]::new(2015,1,1).AddDays($attachment2Rotation[1].DayOfYear-1)).ToString('MM-dd') + '}'),
    ('\newcommand{\QThreeTwoLatB}{' + ('{0:F1}' -f $attachment2Rotation[1].LatitudeDeg) + '}'),
    ('\newcommand{\QThreeTwoLonB}{' + ('{0:F1}' -f $attachment2Rotation[1].LongitudeDeg) + '}'),
    ('\newcommand{\QThreeThreeDateA}{' + ([datetime]::new(2015,1,1).AddDays($attachment3Rotation[0].DayOfYear-1)).ToString('MM-dd') + '}'),
    ('\newcommand{\QThreeThreeLatA}{' + ('{0:F1}' -f $attachment3Rotation[0].LatitudeDeg) + '}'),
    ('\newcommand{\QThreeThreeLonA}{' + ('{0:F1}' -f $attachment3Rotation[0].LongitudeDeg) + '}'),
    ('\newcommand{\QThreeThreeDateB}{' + ([datetime]::new(2015,1,1).AddDays($attachment3Rotation[1].DayOfYear-1)).ToString('MM-dd') + '}'),
    ('\newcommand{\QThreeThreeLatB}{' + ('{0:F1}' -f $attachment3Rotation[1].LatitudeDeg) + '}'),
    ('\newcommand{\QThreeThreeLonB}{' + ('{0:F1}' -f $attachment3Rotation[1].LongitudeDeg) + '}')
)
$macroLines | Set-Content -LiteralPath (Join-Path $paperDir 'generated-values.tex') -Encoding utf8

$evidenceRows = @(
    [pscustomobject]@{ claim_id = 'Q1_CURVE'; claim = 'Tiananmen 09:00-15:00 shadow curve'; source = '<SOURCE_FILE_REDACTED>'; evidence_layer = 'deterministic_model_output'; status = 'pass'; limitation = 'NOAA approximation; refraction neglected' },
    [pscustomobject]@{ claim_id = 'Q2_PRIMARY'; claim = 'Primary location candidate'; source = '<SOURCE_FILE_REDACTED>'; evidence_layer = 'full_data_internal_fit'; status = 'pass'; limitation = 'External geographic truth not available; external validity needs_review' },
    [pscustomobject]@{ claim_id = 'Q2_HOLDOUT'; claim = 'Final-block trajectory prediction after training-only selection'; source = '<SOURCE_FILE_REDACTED>'; evidence_layer = 'time_ordered_final_test'; status = 'pass'; limitation = 'Trajectory prediction does not validate geographic parameters; polynomial counterexamples are reported' },
    [pscustomobject]@{ claim_id = 'Q3_PAIRED_DATES'; claim = 'Paired seasonal date-location branches'; source = 'q3_*<SOURCE_FILE_REDACTED>'; evidence_layer = 'global_search_internal_fit'; status = 'pass'; limitation = 'Calendar year and axis handedness are not identified by shadow data alone' },
    [pscustomobject]@{ claim_id = 'SENSITIVITY_ROUNDING'; claim = 'Rounding-only conditional perturbation ranges'; source = '<SOURCE_FILE_REDACTED>'; evidence_layer = 'model_internal_sensitivity'; status = 'pass'; limitation = 'Not a total-error or statistical confidence interval' },
    [pscustomobject]@{ claim_id = 'SENSITIVITY_RESIDUAL'; claim = 'Block residual, contamination, structural and prefix-drift stress tests'; source = 'uncertainty_status.json'; evidence_layer = 'stress_testing'; status = 'pass'; limitation = 'Total uncertainty and external validity remain needs_review' },
    [pscustomobject]@{ claim_id = 'JOINT_ID'; claim = 'Joint date-location clusters'; source = '<SOURCE_FILE_REDACTED>'; evidence_layer = 'joint_sensitivity'; status = 'pass'; limitation = 'Frequencies are sensitivity frequencies, not probabilities' },
    [pscustomobject]@{ claim_id = 'Q4_NUMERIC'; claim = 'Video location'; source = 'q4_status.json'; evidence_layer = 'input_availability'; status = 'needs_review'; limitation = 'Attachment 4 video missing from allowed workspace' },
    [pscustomobject]@{ claim_id = 'EXTERNAL_VALIDITY'; claim = 'Real-world correctness of inferred coordinates'; source = 'none'; evidence_layer = 'external'; status = 'needs_review'; limitation = 'Blind solve has no permitted reference truth' }
)
Export-Rows $evidenceRows (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

$validationChecks = @(
    [pscustomobject]@{ check_id = 'phase_lock_equivalent_conditions'; status = $equivalentPhaseStatus; detail = "phase=$($lock.phase); required lock files present" },
    [pscustomobject]@{ check_id = 'original_python_phase_runner'; status = $pythonRunnerStatus; detail = 'No functioning Python interpreter was available; no pass is claimed for literal script execution.' },
    [pscustomobject]@{ check_id = 'three_workbook_sheets_21_points_each'; status = if ((@($dataAuditRows | Where-Object observations -ne 21)).Count -eq 0) { 'pass' } else { 'fail' }; detail = 'Attachment 1-3 normalized row counts' },
    [pscustomobject]@{ check_id = 'strictly_increasing_times'; status = if ((@($dataAuditRows | Where-Object monotonic_time_status -ne 'pass')).Count -eq 0) { 'pass' } else { 'fail' }; detail = 'All adjacent time differences positive' },
    [pscustomobject]@{ check_id = 'q1_positive_altitude'; status = if ((@($q1Rows | Where-Object solar_altitude_deg -le 0)).Count -eq 0) { 'pass' } else { 'fail' }; detail = 'Sun above horizon for every plotted point' },
    [pscustomobject]@{ check_id = 'q2_primary_internal_rmse_below_1mm'; status = if ($q2Primary.TipRmseM -lt 0.001) { 'pass' } else { 'fail' }; detail = ('RMSE={0:R} m; engineering threshold is explicit, not a correctness proof' -f $q2Primary.TipRmseM) },
    [pscustomobject]@{ check_id = 'q3_rotation_candidates_internal_rmse_below_1mm'; status = if ((@($attachment2Rotation + $attachment3Rotation | Where-Object TipRmseM -ge 0.001)).Count -eq 0) { 'pass' } else { 'fail' }; detail = 'All retained standard-axis paired-date candidates' },
    [pscustomobject]@{ check_id = 'q4_homography_synthetic_equation'; status = $q4Status.observation_equation_status; detail = 'Pixel-to-ground, ground-to-pixel and homogeneous normalization synthetic checks' },
    [pscustomobject]@{ check_id = 'attachment4_video_present'; status = $q4Status.video_present_status; detail = $q4Status.reason },
    [pscustomobject]@{ check_id = 'external_truth_checked'; status = 'needs_review'; detail = 'References/answers are prohibited in solve phase.' }
)
Export-Rows $validationChecks (Join-Path $resultsDir '<SOURCE_FILE_REDACTED>')

$runEnded = Get-Date
$runMetadata = [ordered]@{
    status = 'pass'
    phase = $lock.phase
    phase_lock_equivalent_status = $equivalentPhaseStatus
    original_python_phase_runner_status = $pythonRunnerStatus
    script = 'code/run_all.ps1'
    solver = 'code/SolarShadow.cs'
    random_seed = 2015
    sensitivity_replicates_per_branch = $SensitivityReplicates
    started_at = $runStarted.ToString('o')
    ended_at = $runEnded.ToString('o')
    elapsed_seconds = ($runEnded - $runStarted).TotalSeconds
    powershell_version = $PSVersionTable.PSVersion.ToString()
    operating_system = [System.Runtime.InteropServices.RuntimeInformation]::OSDescription
    generated_png_count = @(Get-ChildItem -LiteralPath $figuresDir -Filter '*.png' -File).Count
}
Write-Json $runMetadata (Join-Path $resultsDir 'run_metadata.json')

Write-Host ('[PASS] run_all completed in {0:F1} s; results={1}; figures={2}' -f
    $runMetadata.elapsed_seconds, $resultsDir, $runMetadata.generated_png_count)
