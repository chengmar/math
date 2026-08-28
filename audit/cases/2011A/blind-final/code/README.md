# 盲修订复现说明

流水线只读取当前工作区 `input/`。旧版 XLS 由 Excel 12.0 COM 以只读 `Value2` 导出；导出清单同时记录当前容器哈希、CSV 哈希及其与冻结 V1 语义导出的比较。

## 数值运行

```powershell
& '.\code\run_all.ps1' -Workspace (Get-Location).Path
```

每次先校验删除目标确实位于工作区内，再清空 `results/`、`figures/`、`paper/generated/`，避免旧文件掩盖缺失输出。

## 两次完整重跑

```powershell
& '.\code\verify_reproduction.ps1' -Workspace (Get-Location).Path
```

比较两次从空生成目录得到的全部数值、表和图哈希，报告写入 `reports/reproduction-check.json`。

## 论文构建与一致性

```powershell
& '.\code\build_paper.ps1' -Workspace (Get-Location).Path
python '.\code\check_consistency.py'
```

论文脚本清空 `paper/build/` 后连续运行 XeLaTeX 两次，把同一构建的 PDF/日志复制到 `paper/`，并在 `reports/paper-build.json` 绑定 TeX、Markdown、生成表、图、PDF 和日志哈希。一致性脚本复核这些哈希，抽取 PDF 文本，核对关键数字、审计修订语义、页数和致命日志模式。

## 核心验证

- 固定种子 `20260311`；
- 3 km 块嵌套交叉验证，并有连续区域、1 km 缓冲、2 km 缓冲压力测试；
- 功能区主不确定性为 10000 次 3 km cluster bootstrap；
- NMF 的 RMS 在每个训练折内估计；
- 变异函数先去二维线性趋势，再约束拟合并做分箱敏感性；
- 方向用每元素 2000 次固定设计置换；
- 热点执行全样点 LOO、前三高值删除、支持阈值和 500 次空间块重采样。

`pass` 只表示相应执行或内部检查通过。背景空间场、传播方向、精确热点和物理排放源仍为 `needs_review`。
