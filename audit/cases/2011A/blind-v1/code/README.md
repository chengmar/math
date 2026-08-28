# 复现说明

本目录只读取当前工作区的原始附件。旧版 `.xls` 由本机 Excel 12.0 COM 接口以只读方式导出，再由 Python 完成全部数值、验证、表格与图形生成。

## 一次完整运行

在工作区根目录执行：

```powershell
& '.\code\run_all.ps1' -Workspace (Get-Location).Path
```

## 两次独立重跑比较

```powershell
& '.\code\verify_reproduction.ps1' -Workspace (Get-Location).Path
```

比较结果写入 `reports/reproduction-check.json`。脚本会在内存中保留第一次运行的哈希，再完成第二次运行；任何输出哈希差异都会返回 `fail`。

## 论文—结果一致性

在论文已编译后执行：

```powershell
python '.\code\check_consistency.py'
```

结果写入 `reports/paper-consistency.json`，核对 Markdown 关键数字、TeX 生成宏/表、图引用和 13 页 XeLaTeX 编译日志。

## 关键约束

- 固定随机种子：`20260311`。
- 空间评价：3 km 网格块分组的 5 折留出；Gaussian 带宽在每个外层折内再次选择。
- 所有污染指标均由题目给出的背景值归一化，不引入外部阈值或毒性权重。
- `pass` 只表示相应内部检查通过；污染物的真实物理来源与动态传播仍为 `needs_review`。
