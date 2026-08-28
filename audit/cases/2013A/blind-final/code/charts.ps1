Set-StrictMode -Version Latest

function Write-SolutionFigures {
    param(
        [string]$Workspace,
        $Central,
        [object[]]$CapacityRows,
        [object[]]$LaneRows,
        [object[]]$SignalRows,
        [int]$Lanes,
        [double]$Inflow,
        [double]$CentralCapacity,
        [double]$FreeSpeed,
        [double]$WaveSpeed,
        [double]$JamDensity,
        [double]$Q3BoundaryM,
        [double]$Q4BoundaryM
    )

    Add-Type -AssemblyName System.Drawing
    $figuresDir = Join-Path $Workspace 'figures'
    [System.IO.Directory]::CreateDirectory($figuresDir) | Out-Null
    $invariant = [System.Globalization.CultureInfo]::InvariantCulture

    function New-BaseChart {
        param([string]$Path, [string]$Title, [string]$Subtitle, [string]$XLabel, [string]$YLabel)
        $bitmap = [System.Drawing.Bitmap]::new(1100, 700)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.Clear([System.Drawing.Color]::White)
        $font = [System.Drawing.Font]::new('Microsoft YaHei', 11)
        $titleFont = [System.Drawing.Font]::new('Microsoft YaHei', 20, [System.Drawing.FontStyle]::Bold)
        $smallFont = [System.Drawing.Font]::new('Microsoft YaHei', 9)
        $axisPen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(60,60,60),2)
        $gridPen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(220,225,230),1)
        $graphics.DrawString($Title,$titleFont,[System.Drawing.Brushes]::Black,70,18)
        $graphics.DrawString($Subtitle,$smallFont,[System.Drawing.Brushes]::DimGray,72,55)
        $graphics.DrawLine($axisPen,90,610,1040,610)
        $graphics.DrawLine($axisPen,90,90,90,610)
        $graphics.DrawString($XLabel,$font,[System.Drawing.Brushes]::Black,500,650)
        $graphics.TranslateTransform(25,410); $graphics.RotateTransform(-90)
        $graphics.DrawString($YLabel,$font,[System.Drawing.Brushes]::Black,0,0)
        $graphics.ResetTransform()
        return [pscustomobject]@{ Bitmap=$bitmap; Graphics=$graphics; Font=$font; SmallFont=$smallFont; TitleFont=$titleFont; AxisPen=$axisPen; GridPen=$gridPen; Path=$Path }
    }
    function Close-Chart {
        param($Chart)
        $Chart.Bitmap.Save($Chart.Path,[System.Drawing.Imaging.ImageFormat]::Png)
        $Chart.Graphics.Dispose(); $Chart.Bitmap.Dispose(); $Chart.Font.Dispose(); $Chart.SmallFont.Dispose()
        $Chart.TitleFont.Dispose(); $Chart.AxisPen.Dispose(); $Chart.GridPen.Dispose()
    }
    function Map-X([double]$x,[double]$xmin,[double]$xmax) { return [single](90.0+950.0*($x-$xmin)/($xmax-$xmin)) }
    function Map-Y([double]$y,[double]$ymin,[double]$ymax) { return [single](610.0-520.0*($y-$ymin)/($ymax-$ymin)) }

    $queueChart = New-BaseChart (Join-Path $figuresDir '<SOURCE_FILE_REDACTED>') '问题4：首次溢出前的排队增长' '常流工程基线 C=1000 pcu/h；曲线在 140 m 边界终止' '时间 / min' '排队长度 / m'
    $gc = $queueChart.Graphics
    $xmax = [double]$Central.time_to_distance_min_raw
    $ymax = 150.0
    for($i=0;$i -le 5;$i++){ $y=30.0*$i; $py=Map-Y $y 0 $ymax; $gc.DrawLine($queueChart.GridPen,90,$py,1040,$py); $gc.DrawString($y.ToString('0',$invariant),$queueChart.SmallFont,[System.Drawing.Brushes]::DimGray,45,$py-7) }
    for($i=0;$i -le 8;$i++){ $x=$xmax*$i/8.0; $px=Map-X $x 0 $xmax; $gc.DrawLine($queueChart.GridPen,$px,90,$px,610); $gc.DrawString($x.ToString('0.0',$invariant),$queueChart.SmallFont,[System.Drawing.Brushes]::DimGray,$px-12,615) }
    $lwrPoints=[System.Collections.Generic.List[System.Drawing.PointF]]::new(); $pointPoints=[System.Collections.Generic.List[System.Drawing.PointF]]::new()
    for($i=0;$i -le 100;$i++){
        $t=$xmax*$i/100.0
        $lwr=[Math]::Min($Q4BoundaryM,1000.0*[double]$Central.signed_queue_rate_km_h_raw*$t/60.0)
        $point=1000.0*(($Inflow-$CentralCapacity)*$t/60.0)/($Lanes*$JamDensity)
        $lwrPoints.Add([System.Drawing.PointF]::new((Map-X $t 0 $xmax),(Map-Y $lwr 0 $ymax)))
        $pointPoints.Add([System.Drawing.PointF]::new((Map-X $t 0 $xmax),(Map-Y $point 0 $ymax)))
    }
    $bluePen=[System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(31,119,180),4); $orangePen=[System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(255,127,14),4); $targetPen=[System.Drawing.Pen]::new([System.Drawing.Color]::Firebrick,2); $targetPen.DashStyle=[System.Drawing.Drawing2D.DashStyle]::Dash
    $gc.DrawLines($bluePen,$lwrPoints.ToArray()); $gc.DrawLines($orangePen,$pointPoints.ToArray()); $targetY=Map-Y $Q4BoundaryM 0 $ymax; $gc.DrawLine($targetPen,90,$targetY,1040,$targetY)
    $gc.DrawString('运动波（到边界即止）',$queueChart.Font,[System.Drawing.Brushes]::SteelBlue,720,115); $gc.DrawString('点队列基线',$queueChart.Font,[System.Drawing.Brushes]::DarkOrange,720,142); $gc.DrawString('140 m',$queueChart.Font,[System.Drawing.Brushes]::Firebrick,945,$targetY-25)
    $bluePen.Dispose();$orangePen.Dispose();$targetPen.Dispose();Close-Chart $queueChart

    $capPlotRows=@($CapacityRows | Where-Object { $_.regime -eq 'growing_queue' -and [double]$_.capacity_pcu_h -le 1400 })
    $capYMax=1.1*(($capPlotRows | Measure-Object -Property time_to_140m_min -Maximum).Maximum)
    $capChart=New-BaseChart (Join-Path $figuresDir '<SOURCE_FILE_REDACTED>') '问题4：常流容量敏感性' '其余参数固定；C 接近 1500 pcu/h 时到达时间发散' '事故断面能力 C / (pcu/h)' '到达 140 m 时间 / min'
    $gc=$capChart.Graphics
    for($i=0;$i -le 5;$i++){ $y=$capYMax*$i/5.0;$py=Map-Y $y 0 $capYMax;$gc.DrawLine($capChart.GridPen,90,$py,1040,$py);$gc.DrawString($y.ToString('0.0',$invariant),$capChart.SmallFont,[System.Drawing.Brushes]::DimGray,43,$py-7) }
    for($i=0;$i -le 8;$i++){ $x=600+100*$i;$px=Map-X $x 600 1400;$gc.DrawLine($capChart.GridPen,$px,90,$px,610);$gc.DrawString($x.ToString('0',$invariant),$capChart.SmallFont,[System.Drawing.Brushes]::DimGray,$px-16,615) }
    $capPoints=[System.Collections.Generic.List[System.Drawing.PointF]]::new(); foreach($row in $capPlotRows){$capPoints.Add([System.Drawing.PointF]::new((Map-X ([double]$row.capacity_pcu_h) 600 1400),(Map-Y ([double]$row.time_to_140m_min) 0 $capYMax)))}
    $purplePen=[System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(120,70,170),4);$gc.DrawLines($purplePen,$capPoints.ToArray());foreach($pt in $capPoints){$gc.FillEllipse([System.Drawing.Brushes]::Indigo,$pt.X-4,$pt.Y-4,8,8)};$purplePen.Dispose();Close-Chart $capChart

    $q3Chart=New-BaseChart (Join-Path $figuresDir '<SOURCE_FILE_REDACTED>') '问题3：有限路段内的排队增长' 'q=1500 pcu/h；曲线在 240 m 上游路口边界终止' '事故持续时间 / min' '排队长度 / m'
    $gc=$q3Chart.Graphics;$q3YMax=$Q3BoundaryM*1.08
    for($i=0;$i -le 6;$i++){ $y=$Q3BoundaryM*$i/6.0;$py=Map-Y $y 0 $q3YMax;$gc.DrawLine($q3Chart.GridPen,90,$py,1040,$py);$gc.DrawString($y.ToString('0',$invariant),$q3Chart.SmallFont,[System.Drawing.Brushes]::DimGray,43,$py-7) }
    for($i=0;$i -le 5;$i++){ $x=2.0*$i;$px=Map-X $x 0 10;$gc.DrawLine($q3Chart.GridPen,$px,90,$px,610);$gc.DrawString($x.ToString('0',$invariant),$q3Chart.SmallFont,[System.Drawing.Brushes]::DimGray,$px-6,615) }
    $boundaryPen=[System.Drawing.Pen]::new([System.Drawing.Color]::Firebrick,2);$boundaryPen.DashStyle=[System.Drawing.Drawing2D.DashStyle]::Dash;$boundaryY=Map-Y $Q3BoundaryM 0 $q3YMax;$gc.DrawLine($boundaryPen,90,$boundaryY,1040,$boundaryY);$gc.DrawString('240 m 边界',$q3Chart.SmallFont,[System.Drawing.Brushes]::Firebrick,945,$boundaryY-22)
    $colors=@([System.Drawing.Color]::SteelBlue,[System.Drawing.Color]::DarkOrange,[System.Drawing.Color]::SeaGreen);$caps=@(800.0,1000.0,1200.0)
    for($j=0;$j -lt $caps.Count;$j++){
        $state=Get-KinematicStateRaw $Inflow $caps[$j] ($Q3BoundaryM/1000.0) $Lanes $FreeSpeed $WaveSpeed $JamDensity
        $hitMin=60.0*($Q3BoundaryM/1000.0)/[double]$state.signed_queue_rate_km_h_raw
        $endMin=[Math]::Min(10.0,$hitMin)
        $pts=[System.Collections.Generic.List[System.Drawing.PointF]]::new()
        for($i=0;$i -le 100;$i++){ $t=$endMin*$i/100.0;$len=1000.0*[double]$state.signed_queue_rate_km_h_raw*$t/60.0;$pts.Add([System.Drawing.PointF]::new((Map-X $t 0 10),(Map-Y $len 0 $q3YMax))) }
        $pen=[System.Drawing.Pen]::new($colors[$j],4);$gc.DrawLines($pen,$pts.ToArray());$labelBrush=[System.Drawing.SolidBrush]::new($colors[$j]);$gc.DrawString(('C={0:0} pcu/h' -f $caps[$j]),$q3Chart.Font,$labelBrush,760,110+28*$j);$labelBrush.Dispose();$pen.Dispose()
    }
    $boundaryPen.Dispose();Close-Chart $q3Chart

    $laneChart=New-BaseChart (Join-Path $figuresDir '<SOURCE_FILE_REDACTED>') '问题2：描述性换道需求代理' 'M=1-p 不能单独识别或排序容量；实证结果 needs_review' '剩余车道原角色' '需要重排的需求比例 M'
    $gc=$laneChart.Graphics
    for($i=0;$i -le 5;$i++){ $y=0.2*$i;$py=Map-Y $y 0 1;$gc.DrawLine($laneChart.GridPen,90,$py,1040,$py);$gc.DrawString($y.ToString('0.0',$invariant),$laneChart.SmallFont,[System.Drawing.Brushes]::DimGray,45,$py-7) }
    $laneOrder=@('right','through','left');$laneLabels=@{right='右转';through='直行';left='左转'};for($i=0;$i -lt $laneOrder.Count;$i++){$row=$LaneRows|Where-Object{$_.remaining_lane_role -eq $laneOrder[$i]};$x=260+300*$i;$barTop=Map-Y ([double]$row.mandatory_merge_proxy) 0 1;$height=610-$barTop;$gc.FillRectangle([System.Drawing.Brushes]::CadetBlue,$x-65,$barTop,130,$height);$gc.DrawString($laneLabels[$laneOrder[$i]],$laneChart.Font,[System.Drawing.Brushes]::Black,$x-25,620);$gc.DrawString(([double]$row.mandatory_merge_proxy).ToString('0.00',$invariant),$laneChart.Font,[System.Drawing.Brushes]::Black,$x-25,$barTop-28)};Close-Chart $laneChart

    $signalTimes=[double[]]@($SignalRows | ForEach-Object { [double]$_.hit_time_min })
    $signalMin=($signalTimes|Measure-Object -Minimum).Minimum;$signalMax=($signalTimes|Measure-Object -Maximum).Maximum;$padding=[Math]::Max(0.1,0.1*($signalMax-$signalMin));$signalYMin=$signalMin-$padding;$signalYMax=$signalMax+$padding
    $signalChart=New-BaseChart (Join-Path $figuresDir '<SOURCE_FILE_REDACTED>') '问题4：初始信号相位敏感性' '含右转常流及绿闪/黄灯折减的一秒相位网格；非视频标定' '上游信号初始相位 / s' '首次到达 140 m / min'
    $gc=$signalChart.Graphics
    for($i=0;$i -le 5;$i++){ $y=$signalYMin+($signalYMax-$signalYMin)*$i/5.0;$py=Map-Y $y $signalYMin $signalYMax;$gc.DrawLine($signalChart.GridPen,90,$py,1040,$py);$gc.DrawString($y.ToString('0.00',$invariant),$signalChart.SmallFont,[System.Drawing.Brushes]::DimGray,40,$py-7) }
    for($i=0;$i -le 6;$i++){ $x=10.0*$i;$px=Map-X $x 0 60;$gc.DrawLine($signalChart.GridPen,$px,90,$px,610);$gc.DrawString($x.ToString('0',$invariant),$signalChart.SmallFont,[System.Drawing.Brushes]::DimGray,$px-6,615) }
    $signalPoints=[System.Collections.Generic.List[System.Drawing.PointF]]::new();foreach($row in $SignalRows){$signalPoints.Add([System.Drawing.PointF]::new((Map-X ([double]$row.source_initial_phase_s) 0 60),(Map-Y ([double]$row.hit_time_min) $signalYMin $signalYMax)))}
    $signalPen=[System.Drawing.Pen]::new([System.Drawing.Color]::Teal,3);$gc.DrawLines($signalPen,$signalPoints.ToArray());$signalPen.Dispose();Close-Chart $signalChart
}
