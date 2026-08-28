# 修订版复现说明

在 `blind-revision` 工作区根目录运行：

```text
python code/test_model.py
python code/run_all.py
python code/verify_outputs.py --record-manifest
python code/run_all.py
python code/verify_outputs.py
```

若在另一绝对路径完成同样的 `run_all.py`，可在当前工作区执行：

```text
python code/verify_outputs.py --compare-workspace <另一工作区>
```

`model.py` 实现高压侧体积流到质量流的显式转换、油管与柱塞腔方程、减压阀延迟/驻留/切换约束和质量残差。`run_all.py` 生成全部结果与图；`test_model.py` 固化 AUD-001 反例和受约束阀回归；`verify_outputs.py` 检查产物、论文数字、来源台账，并比较 `run_all.py` 生成的完整声明载荷。

生成载荷比较包括 `results/` 与 `figures/` 中由 `run_all.py` 产生的全部文件，仅排除校验器自身的 `rerun-baseline.json` 和 `verification.json`。所有路径记录为相对路径，文本写入固定为 UTF-8/LF。
