# 唯一复现入口

在工作区根目录执行唯一权威命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File code/run_all.ps1 -MonteCarlo 100 -Seed 2008
```

该入口按固定顺序完成原题哈希校验与离线图片提取、数值求解、极端输入拒绝测试、论文—结果一致性检查、两遍 XeLaTeX 编译、运行清单生成及逐项哈希复核。任一步骤退出码非零都会使顶层命令失败；stdout 和 stderr 分别写入 `results/run_all.stdout.log` 与 `results/run_all.stderr.log`。

Python 依赖在 `requirements.txt` 中精确锁定。旧版 Word 图片提取需要 Windows Microsoft Word COM；整个过程只读取本地原题，不访问网络。

权威数值输出位于 `results/` 根目录，四幅权威图位于 `figures/`。冻结 V1 中冲突的 `results/generated/`、`--bootstrap` 命令和 `figures/01–04` 不属于本修订版流水线。`results/run_manifest.json` 是运行级代码—输入—产物来源清单；它不代替外部冻结脚本在生成 `blind-final` 时创建的整树冻结清单。

光栅坐标采用 `u` 向右、`v` 向下；相机像平面坐标采用题面常用整数主点 `(512,384)`，即 `x=u-512`、`y=384-v`、`z=1577`。若改用像素中心几何中点 `(511.5,383.5)`，则 `x` 加 `0.5 px`、`y` 减 `0.5 px`。
