# 复现入口

在工作区根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File code/run_all.ps1 -Bootstrap 120 -Seed 2008
python code/check_consistency.py
```

`run_all.ps1` 先用 `extract_problem.ps1` 从允许输入中的旧版 Word 题面机械提取原图，再调用 `solve.py` 重算分割、候选比较、圆锥单应、坐标、验证和 4 幅图。旧版文档提取需要 Windows Microsoft Word COM；数值程序依赖见 `requirements.txt`。

主输出位于 `results/generated/`。光栅坐标采用 `u` 向右、`v` 向下；相机平面表格采用题目常用整数主点 `(512,384)`，即 `x=u-512`、`y=384-v`、`z=1577`。若改用像素中心几何中点 `(511.5,383.5)`，只需令 `x` 加 `0.5 px`、`y` 减 `0.5 px`。
