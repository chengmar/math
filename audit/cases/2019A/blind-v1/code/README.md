# 复现说明

在 solve 工作区根目录运行：

```text
python code/run_all.py
python code/verify_outputs.py --record-manifest
python code/run_all.py
python code/verify_outputs.py
```

第一条命令生成 `results/` 与 `figures/`；第二条记录核心输出哈希；第三条进行独立的完整重跑；第四条比较哈希并执行数值、论文和 YAML 一致性检查。固定随机种子为 2019，当前主算法本身不使用随机抽样。

`model.py` 包含物性恢复、喷嘴/凸轮插值、问题 1 压力模型、柱塞腔耦合模型和验证量；`run_all.py` 完成基线、参数选择、独立评价、敏感性、表格和绘图；`verify_outputs.py` 只做检查和证据状态更新，不修改模型结果。
